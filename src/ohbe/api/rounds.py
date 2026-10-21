"""In-memory round catalog. Dispatch stays a labeled dry-run.

Creating a plan, confirming a suite, and starting a job never open a socket
and never place a model call. A plan stores its own slot snapshot from
creation. Non-empty catalog ids bind ``pool-admitted``. Confirm freezes that
snapshot and does not read the residual global list. Dry-run dispatch still
uses the fixture pool ``pool-pilot`` and is labeled Dry-run.
The spend lock is unchanged. The seeded cross-model report reuses cost
figures already present on the fixture pilot aggregate. It is an adaptation
illustration under one harness, not a new measurement and not a leaderboard.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from ohbe.api.admitted_pool import FIXTURE_POOL_ID
from ohbe.api.catalog import Catalog, CatalogUnavailable
from ohbe.api.doctor import live_spend_state
from ohbe.api.harnesses import HarnessInventory
from ohbe.api.schemas import (
    Check,
    CreateRoundPlanRequest,
    ForkPlanRequest,
    JobView,
    LiveFlagRequest,
    ModelFitResponse,
    PlanLabels,
    PlanSlotsResponse,
    RoundPlanDetail,
    RoundPlanList,
    RoundPlanSummary,
    RoundReportDetail,
    RoundReportList,
    RoundStartResponse,
    SuiteTaskDraft,
    SuiteUpsertRequest,
    SuiteView,
    SuiteViewList,
)
from ohbe.api.surface import LIVE_SPEND_ENV
from ohbe.api.validate import ValidationRejected
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelPool
from ohbe.core.round import (
    DISPATCH_PHASES,
    BudgetCaps,
    DifficultyWeight,
    DimensionSummary,
    DispatchJob,
    DispatchPhase,
    FixedHarnessModelMetrics,
    ModelPoolRef,
    ObservationEvent,
    RoundPlan,
    RoundReport,
    StopConditions,
    SuiteTask,
    TaskSuite,
    TaxonomyFilter,
    build_round_report,
    campaign_stage,
    confirm_suite,
    draft_suite,
    fork_plan,
    generate_suite_tasks,
    not_measured_report,
    observation_stub,
    visible_events,
)

FIXTURE_PAIRED_PLAN = "fixture-paired"
FIXTURE_CROSS_PLAN = "fixture-cross-model"
FIXTURE_CROSS_SUITE = "fixture-cross-model-suite"
FIXTURE_CROSS_JOB = "fixture-cross-model-job"
FIXTURE_CROSS_REPORT = "fixture-cross-model-report"
FIXTURE_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
EXACT_COST = "0.0009"
MATCH_COST = "0.00045"
POOL_MODELS = ("openai/gpt-test", "openai/gpt-test-mini")

_LIST_NOTE = (
    "paired_harness is the default: two harnesses share one model pool, LiteLLM, and the Docker egress policy. "
    "cross_model holds one harness fixed and varies models for cost/quality selection and OpenBot adaptation. "
    "It is not a model-arena leaderboard. These routes do not spend."
)
_FIT_NOTE = (
    "Recommendations compare models under one fixed harness and one frozen suite. "
    "They support cost/quality selection and future OpenBot adaptation. "
    "They are not a model-arena leaderboard and not a harness ranking. "
    "routing_optimality is null."
)


class RoundStore:
    """Process-local plans. The fixture rows are rebuilt from the catalog."""

    def __init__(self, catalog: Catalog, harnesses: HarnessInventory) -> None:
        self.catalog = catalog
        self.harnesses = harnesses
        self.plans: dict[str, RoundPlan] = {}
        self.origins: dict[str, Literal["fixture", "session"]] = {}
        self.suites: dict[str, TaskSuite] = {}
        self.suite_by_plan: dict[str, str] = {}
        self.jobs: dict[str, DispatchJob] = {}
        self.job_by_plan: dict[str, str] = {}
        self.events: dict[str, tuple[ObservationEvent, ...]] = {}
        self.reports: dict[str, RoundReport] = {}
        self.report_by_plan: dict[str, str] = {}
        self.comparison_by_plan: dict[str, tuple[str, ...]] = {}
        self.slots_status: dict[str, Literal["draft", "frozen"]] = {}
        self.pool_kind: dict[str, Literal["admitted", "fixture"]] = {}
        self.pool_model_ids: dict[str, tuple[str, ...]] = {}
        self.labels: dict[str, PlanLabels] = {}
        self._ready = False

    def ensure(self) -> None:
        if self._ready:
            return
        self._seed()
        self._ready = True

    def list_plans(self) -> RoundPlanList:
        self.ensure()
        return RoundPlanList(
            plans=[self._summary(plan_id) for plan_id in self.plans],
            note=_LIST_NOTE,
        )

    def plan_detail(self, plan_id: str, *, live_requested: bool = False) -> RoundPlanDetail:
        self.ensure()
        plan = self._plan(plan_id)
        return RoundPlanDetail(
            live_requested=live_requested,
            lifecycle=self._stage(plan_id),
            origin=self.origins[plan_id],
            plan=plan,
            suite_id=self.suite_by_plan.get(plan_id),
            job_id=self.job_by_plan.get(plan_id),
            report_id=self.report_by_plan.get(plan_id),
            comparison_model_ids=self._comparison_ids(plan_id),
            slots_status=self._slots_status_of(plan_id),
            pool_kind=self.pool_kind[plan_id],
            pool_model_ids=list(self.pool_model_ids[plan_id]),
            labels=self._labels_of(plan_id),
        )

    def create_plan(
        self,
        body: CreateRoundPlanRequest,
        environ: Mapping[str, str],
        *,
        admitted_pool: ModelPool | None = None,
        slot_ids: Sequence[str] = (),
    ) -> RoundPlanDetail:
        self.ensure()
        live = self._live(body.live, environ)
        self.harnesses.require_plan(body.harness.harness_id, body.harness.name, body.harness.version)
        if body.reference_harness is not None:
            self.harnesses.require_plan(
                body.reference_harness.harness_id,
                body.reference_harness.name,
                body.reference_harness.version,
            )
        plan = self._plan_from_request(body, admitted_pool=admitted_pool)
        self.plans[plan.plan_id] = plan
        self.origins[plan.plan_id] = "session"
        if admitted_pool is not None:
            self._bind_pool(
                plan.plan_id,
                kind="admitted",
                model_ids=tuple(model.model_id for model in admitted_pool.models),
            )
        else:
            pool = self.catalog.pool(plan.model_pool_ref.pool_id)
            self._bind_pool(
                plan.plan_id,
                kind="fixture",
                model_ids=tuple(model.model_id for model in pool.models),
            )
        self._remember_slots(plan.plan_id, slot_ids, "draft")
        self._remember_labels(plan.plan_id, body.labels)
        self._ensure_reference_label_ids(plan.plan_id)
        return self.plan_detail(plan.plan_id, live_requested=live)

    def upsert_suite(self, plan_id: str, body: SuiteUpsertRequest, environ: Mapping[str, str]) -> SuiteView:
        self.ensure()
        live = self._live(body.live, environ)
        plan = self._plan(plan_id)
        current = self._suite_for(plan_id)
        if current is not None and current.status == "confirmed":
            raise ValidationRejected(
                409,
                "suite_already_confirmed",
                "Confirmed suite_hash is frozen. Fork the plan to change tasks.",
            )
        tasks = generate_suite_tasks(plan) if body.tasks is None else _tasks_from_drafts(body.tasks)
        suite_id = current.suite_id if current is not None else f"suite-{uuid4().hex[:12]}"
        try:
            suite = draft_suite(suite_id=suite_id, plan=plan, tasks=tasks)
        except (ValidationError, ValueError) as exc:
            raise ValidationRejected(422, "invalid_request", "suite draft is not valid") from exc
        self.suites[suite.suite_id] = suite
        self.suite_by_plan[plan_id] = suite.suite_id
        return self._suite_view(suite, live_requested=live)

    def confirm(
        self,
        plan_id: str,
        body: LiveFlagRequest,
        environ: Mapping[str, str],
        *,
        comparison_model_ids: Sequence[str] = (),
    ) -> SuiteView:
        self.ensure()
        live = self._live(body.live, environ)
        plan = self._plan(plan_id)
        del plan
        current = self._suite_for(plan_id)
        if current is None:
            raise ValidationRejected(409, "suite_missing", "Generate a draft suite before confirming it.")
        was_draft = current.status != "confirmed"
        if was_draft:
            self._require_confirm_pool(plan_id, tuple(comparison_model_ids))
        try:
            suite = confirm_suite(current, confirmed_at=datetime.now(UTC))
        except (ValidationError, ValueError) as exc:
            raise ValidationRejected(422, "invalid_request", "suite could not be confirmed") from exc
        self.suites[suite.suite_id] = suite
        if was_draft:
            self._remember_slots(plan_id, comparison_model_ids, "frozen")
        return self._suite_view(suite, live_requested=live)

    def start(self, plan_id: str, body: LiveFlagRequest, environ: Mapping[str, str]) -> RoundStartResponse:
        self.ensure()
        live = self._live(body.live, environ)
        plan = self._plan(plan_id)
        suite = self._require_confirmed(plan_id)
        existing = self.job_by_plan.get(plan_id)
        if existing is None:
            self._open_job(plan, suite)
        return self._job_response(plan_id, live_requested=live)

    def tick(self, job_id: str, body: LiveFlagRequest, environ: Mapping[str, str]) -> RoundStartResponse:
        self.ensure()
        live = self._live(body.live, environ)
        job = self._job(job_id)
        nxt = _advance(job.status)
        if nxt != job.status:
            payload = job.model_dump(mode="json")
            payload.pop("job_hash", None)
            payload["status"] = nxt
            try:
                updated = DispatchJob.build(**payload)
            except ValidationError as exc:
                raise ValidationRejected(422, "invalid_request", "dispatch job is not valid") from exc
            self.jobs[job.job_id] = updated
            if updated.status == "sealed" and job.plan_id not in self.report_by_plan:
                self._seal_unmeasured(updated)
        return self._job_response(job.plan_id, live_requested=live)

    def fork(self, plan_id: str, body: ForkPlanRequest, environ: Mapping[str, str]) -> RoundPlanDetail:
        self.ensure()
        live = self._live(body.live, environ)
        plan = self._plan(plan_id)
        if plan_id not in self.report_by_plan:
            raise ValidationRejected(409, "report_required", "Fork a plan after its round report exists.")
        name = body.name or f"{plan.name} fork"
        try:
            child = fork_plan(plan, plan_id=f"plan-{uuid4().hex[:12]}", name=name, created_at=datetime.now(UTC))
        except (ValidationError, ValueError) as exc:
            raise ValidationRejected(422, "invalid_request", "fork plan is not valid") from exc
        self.plans[child.plan_id] = child
        self.origins[child.plan_id] = "session"
        self._bind_pool(
            child.plan_id,
            kind=self.pool_kind[plan.plan_id],
            model_ids=self.pool_model_ids[plan.plan_id],
        )
        self._remember_slots(child.plan_id, self.comparison_by_plan.get(plan.plan_id, ()), "draft")
        self._remember_labels(child.plan_id, self._labels_of(plan.plan_id))
        self._ensure_reference_label_ids(child.plan_id)
        return self.plan_detail(child.plan_id, live_requested=live)

    def list_suites(self) -> SuiteViewList:
        self.ensure()
        return SuiteViewList(suites=[self.suites[suite_id] for suite_id in self.suites])

    def suite(self, suite_id: str) -> SuiteView:
        self.ensure()
        found = self.suites.get(suite_id)
        if found is None:
            raise ValidationRejected(404, "suite_not_found", f"Unknown suite: {suite_id}")
        return self._suite_view(found)

    def list_reports(self) -> RoundReportList:
        self.ensure()
        return RoundReportList(reports=list(self.reports.values()))

    def report(self, report_id: str) -> RoundReportDetail:
        self.ensure()
        found = self.reports.get(report_id)
        if found is None:
            raise ValidationRejected(404, "report_not_found", f"Unknown round report: {report_id}")
        return RoundReportDetail(report=found, lifecycle=self._stage(found.plan_id))

    def job_view(self, job_id: str) -> JobView:
        self.ensure()
        job = self._job(job_id)
        return JobView(job=job, events=list(visible_events(self.events[job_id], job.status)))

    def model_fit(self) -> ModelFitResponse:
        self.ensure()
        report = self.reports.get(FIXTURE_CROSS_REPORT)
        if report is None:
            raise ValidationRejected(404, "report_not_found", "Fixture model-fit report is not loaded")
        return ModelFitResponse(
            harness_id=report.harness_id,
            suite_id=report.suite_id,
            suite_hash=report.suite_hash,
            findings=report.findings,
            frontier_note=report.frontier_note,
            recommendations=list(report.recommendations),
            model_metrics=list(report.model_metrics),
            dimensions=list(report.dimensions),
            routing_optimality=None,
            model_independence=None,
            note=_FIT_NOTE,
            source="ohbe.api.rounds fixture cross_model report",
        )

    def _seed(self) -> None:
        try:
            pool = self.catalog.pool(FIXTURE_POOL_ID)
            pilot = self.catalog.result("fixture-pilot")
        except KeyError as exc:
            raise CatalogUnavailable("fixture catalog is missing the pilot pool or report") from exc
        except CatalogUnavailable:
            raise
        model_ids = tuple(model.model_id for model in pool.models)
        if model_ids != POOL_MODELS:
            raise CatalogUnavailable("pilot pool does not match the pinned model-fit fixture")
        amounts = {
            value
            for task in pilot.tasks
            for harness in task.harnesses
            for value in (harness.total_model_cost, harness.cost_per_success)
            if value
        }
        if EXACT_COST not in amounts or MATCH_COST not in amounts:
            raise CatalogUnavailable("fixture report is missing the cost figures cited by the model-fit demo")
        openbot = _harness("openbot", "OpenBot", "fixture", {"profile": "litellm-only"})
        inspect = _harness("inspect-react", "Inspect react()", "0.3.277", {"solver": "react"})
        paired = _plan(
            plan_id=FIXTURE_PAIRED_PLAN,
            name="Fixture paired harness",
            mode="paired_harness",
            subject=openbot,
            reference=inspect,
            pool_id=pool.pool_id,
            pool_hash=pool.content_hash,
            notes="Default mode. OpenBot versus Inspect react() on one shared pool.",
        )
        cross = _plan(
            plan_id=FIXTURE_CROSS_PLAN,
            name="Fixture cross-model",
            mode="cross_model",
            subject=openbot,
            reference=None,
            pool_id=pool.pool_id,
            pool_hash=pool.content_hash,
            notes="Fixed OpenBot harness. Models vary for adaptation signals.",
        )
        suite = confirm_suite(
            draft_suite(suite_id=FIXTURE_CROSS_SUITE, plan=cross, tasks=generate_suite_tasks(cross)),
            confirmed_at=FIXTURE_AT,
        )
        job = DispatchJob.build(
            job_id=FIXTURE_CROSS_JOB,
            plan_id=cross.plan_id,
            suite_id=suite.suite_id,
            suite_hash=suite.suite_hash,
            mode="cross_model",
            harness_id=openbot.harness_id,
            model_ids=model_ids,
            focus_task_id=suite.tasks[0].task_id,
            status="sealed",
            note="Dry-run fixture dispatch sealed offline. The gateway was not contacted.",
        )
        events = observation_stub(
            job_id=job.job_id,
            subject_harness_id=job.harness_id,
            task_id=job.focus_task_id,
            panes=tuple((model_id, model_id, job.harness_id) for model_id in model_ids),
            recorded_at=FIXTURE_AT,
        )
        report = _demo_report(plan=cross, suite=suite, model_ids=model_ids)
        self.plans[paired.plan_id] = paired
        self.plans[cross.plan_id] = cross
        self.origins[paired.plan_id] = "fixture"
        self.origins[cross.plan_id] = "fixture"
        self.suites[suite.suite_id] = suite
        self.suite_by_plan[cross.plan_id] = suite.suite_id
        self.jobs[job.job_id] = job
        self.job_by_plan[cross.plan_id] = job.job_id
        self.events[job.job_id] = events
        self.reports[report.report_id] = report
        self.report_by_plan[cross.plan_id] = report.report_id
        self._bind_pool(paired.plan_id, kind="fixture", model_ids=model_ids)
        self._bind_pool(cross.plan_id, kind="fixture", model_ids=model_ids)
        self._remember_slots(paired.plan_id, (), "draft")
        self._remember_slots(cross.plan_id, (), "frozen")
        self._remember_labels(
            paired.plan_id,
            PlanLabels(purpose="fixture_demo", industry="general", scenario_tags=["office_assistant"]),
        )
        self._remember_labels(
            cross.plan_id,
            PlanLabels(purpose="fixture_demo", industry="general", scenario_tags=["cost_sensitive"]),
        )
        self._ensure_reference_label_ids(paired.plan_id)

    def _plan_from_request(self, body: CreateRoundPlanRequest, *, admitted_pool: ModelPool | None) -> RoundPlan:
        if admitted_pool is not None:
            pool_id = admitted_pool.pool_id
            pool_hash = admitted_pool.content_hash
        else:
            try:
                looked_up = self.catalog.pool(body.pool_id)
            except KeyError:
                raise ValidationRejected(404, "pool_not_found", f"Unknown model pool: {body.pool_id}") from None
            pool_id = looked_up.pool_id
            pool_hash = looked_up.content_hash
        subject = _harness(body.harness.harness_id, body.harness.name, body.harness.version, body.harness.parameters)
        reference = None
        if body.reference_harness is not None:
            reference = _harness(
                body.reference_harness.harness_id,
                body.reference_harness.name,
                body.reference_harness.version,
                body.reference_harness.parameters,
            )
        try:
            return _plan(
                plan_id=f"plan-{uuid4().hex[:12]}",
                name=body.name,
                mode=body.mode,
                subject=subject,
                reference=reference,
                pool_id=pool_id,
                pool_hash=pool_hash,
                notes=None,
                seed=body.seed,
                rounds=body.rounds,
                difficulty_mix=tuple(DifficultyWeight(difficulty=item.difficulty, weight=item.weight) for item in body.difficulty_mix),
                capabilities=tuple(body.capabilities),
                difficulties=tuple(body.difficulties),
                domains=tuple(body.domains),
                timeout_seconds=body.timeout_seconds,
                max_total_tokens=body.max_total_tokens,
                max_cost=body.max_cost,
                stop_max_rounds=body.stop_max_rounds or body.rounds,
                max_invalid_rate=body.max_invalid_rate,
                stop_on_budget=body.stop_on_budget,
                created_at=datetime.now(UTC),
            )
        except (ValidationError, ValueError) as exc:
            raise ValidationRejected(422, "invalid_request", "round plan is not valid") from exc

    def _open_job(self, plan: RoundPlan, suite: TaskSuite) -> DispatchJob:
        pool = self.catalog.pool(FIXTURE_POOL_ID)
        model_ids = tuple(model.model_id for model in pool.models)
        labels = self._labels_of(plan.plan_id)
        panes = _panes(plan, model_ids, labels.reference_harness_ids)
        job = DispatchJob.build(
            job_id=f"job-{uuid4().hex[:12]}",
            plan_id=plan.plan_id,
            suite_id=suite.suite_id,
            suite_hash=suite.suite_hash,
            mode=plan.mode,
            harness_id=plan.harness_ref.harness_id,
            model_ids=model_ids,
            focus_task_id=suite.tasks[0].task_id,
            reference_harness_id=None if plan.reference_harness_ref is None else plan.reference_harness_ref.harness_id,
            status="queued",
            note="Dry-run dispatch on the fixture pool. The gateway is not contacted.",
        )
        self.jobs[job.job_id] = job
        self.job_by_plan[plan.plan_id] = job.job_id
        self.events[job.job_id] = observation_stub(
            job_id=job.job_id,
            subject_harness_id=job.harness_id,
            task_id=job.focus_task_id,
            panes=panes,
            recorded_at=datetime.now(UTC),
        )
        return job

    def _seal_unmeasured(self, job: DispatchJob) -> None:
        plan = self._plan(job.plan_id)
        report = not_measured_report(
            report_id=f"report-{uuid4().hex[:12]}",
            plan_id=plan.plan_id,
            suite_id=job.suite_id,
            suite_hash=job.suite_hash,
            mode=plan.mode,
            harness_id=plan.harness_ref.harness_id,
            rounds=plan.rounds,
        )
        self.reports[report.report_id] = report
        self.report_by_plan[plan.plan_id] = report.report_id

    def _job_response(self, plan_id: str, *, live_requested: bool) -> RoundStartResponse:
        job_id = self.job_by_plan[plan_id]
        job = self.jobs[job_id]
        checks = [
            Check(
                id="spend_not_executed",
                passed=True,
                summary="This surface did not place a model call.",
                evidence=["executed:false", "spend:none", "network:not_contacted"],
            )
        ]
        if live_requested:
            checks.append(
                Check(
                    id="live_not_executed",
                    passed=True,
                    summary=f"Live was requested. {LIVE_SPEND_ENV} does not make this route spend.",
                    evidence=["live_requested:true"],
                )
            )
        return RoundStartResponse(
            live_requested=live_requested,
            checks=checks,
            job=job,
            events=list(visible_events(self.events[job_id], job.status)),
            report_id=self.report_by_plan.get(plan_id),
            lifecycle=self._stage(plan_id),
        )

    def _summary(self, plan_id: str) -> RoundPlanSummary:
        plan = self.plans[plan_id]
        labels = self._labels_of(plan_id)
        return RoundPlanSummary(
            plan_id=plan.plan_id,
            name=plan.name,
            mode=plan.mode,
            lifecycle=self._stage(plan_id),
            harness_id=plan.harness_ref.harness_id,
            seed=plan.seed,
            rounds=plan.rounds,
            origin=self.origins[plan_id],
            industry=labels.industry,
            purpose=labels.purpose,
            scenario_tags=list(labels.scenario_tags),
            customer=labels.customer,
        )

    def _stage(self, plan_id: str):
        return campaign_stage(
            plan=self.plans[plan_id],
            suite=self._suite_for(plan_id),
            job=self.jobs.get(self.job_by_plan[plan_id]) if plan_id in self.job_by_plan else None,
            report=self.reports.get(self.report_by_plan[plan_id]) if plan_id in self.report_by_plan else None,
        )

    def _suite_for(self, plan_id: str) -> TaskSuite | None:
        suite_id = self.suite_by_plan.get(plan_id)
        if suite_id is None:
            return None
        return self.suites[suite_id]

    def _require_confirmed(self, plan_id: str) -> TaskSuite:
        suite = self._suite_for(plan_id)
        if suite is None or suite.status != "confirmed":
            raise ValidationRejected(409, "suite_not_confirmed", "Confirm the suite before a dry-run start.")
        return suite

    def _plan(self, plan_id: str) -> RoundPlan:
        found = self.plans.get(plan_id)
        if found is None:
            raise ValidationRejected(404, "plan_not_found", f"Unknown round plan: {plan_id}")
        return found

    def _job(self, job_id: str) -> DispatchJob:
        found = self.jobs.get(job_id)
        if found is None:
            raise ValidationRejected(404, "job_not_found", f"Unknown dispatch job: {job_id}")
        return found

    def stored_slot_ids(self, plan_id: str) -> list[str]:
        self.ensure()
        self._plan(plan_id)
        return self._comparison_ids(plan_id)

    def slot_view(self, plan_id: str) -> PlanSlotsResponse:
        self.ensure()
        self._plan(plan_id)
        return PlanSlotsResponse(
            plan_id=plan_id,
            model_ids=self._comparison_ids(plan_id),
            status=self._slots_status_of(plan_id),
        )

    def replace_plan_slots(self, plan_id: str, model_ids: Sequence[str]) -> PlanSlotsResponse:
        """Replace one draft snapshot. Confirmed and fixture pools stay put."""

        self.ensure()
        self._plan(plan_id)
        current = self._suite_for(plan_id)
        if (current is not None and current.status == "confirmed") or self._slots_status_of(plan_id) == "frozen":
            raise ValidationRejected(
                409,
                "slots_frozen",
                "Confirmed suite slots are frozen. Fork the plan to change slots.",
            )
        ids = tuple(model_ids)
        if self.pool_kind[plan_id] == "fixture" and ids:
            raise ValidationRejected(
                409,
                "fixture_pool",
                "This plan is bound to the fixture pool. Generate again from the admitted comparison slots.",
            )
        self._remember_slots(plan_id, ids, "draft")
        return self.slot_view(plan_id)

    def _comparison_ids(self, plan_id: str) -> list[str]:
        return list(self.comparison_by_plan.get(plan_id, ()))

    def _slots_status_of(self, plan_id: str) -> Literal["draft", "frozen"]:
        return self.slots_status[plan_id]


    def _labels_of(self, plan_id: str) -> PlanLabels:
        return self.labels.get(plan_id) or PlanLabels()

    def _remember_labels(self, plan_id: str, labels: PlanLabels | None) -> None:
        if labels is None:
            origin = self.origins.get(plan_id)
            if origin == "fixture":
                self.labels[plan_id] = PlanLabels(purpose="fixture_demo", industry="general")
            else:
                self.labels[plan_id] = PlanLabels()
            return
        self.labels[plan_id] = labels

    def _ensure_reference_label_ids(self, plan_id: str) -> None:
        """Keep PlanLabels.reference_harness_ids aligned with sealed primary without changing plan_hash.

        Create UI may store several refs in labels while RoundPlan seals only the first.
        Empty labels fall back to the sealed primary so monitor panes and dry-run stubs agree.
        """

        plan = self.plans[plan_id]
        labels = self._labels_of(plan_id)
        if plan.mode != "paired_harness" or plan.reference_harness_ref is None:
            return
        primary = plan.reference_harness_ref.harness_id
        seen: set[str] = set()
        ordered: list[str] = []
        for harness_id in [primary, *labels.reference_harness_ids]:
            if not harness_id or harness_id in seen or harness_id == plan.harness_ref.harness_id:
                continue
            seen.add(harness_id)
            ordered.append(harness_id)
        if ordered == list(labels.reference_harness_ids):
            return
        self.labels[plan_id] = labels.model_copy(update={"reference_harness_ids": ordered})

    def _remember_slots(
        self,
        plan_id: str,
        model_ids: Sequence[str],
        status: Literal["draft", "frozen"],
    ) -> None:
        self.comparison_by_plan[plan_id] = tuple(model_ids)
        self.slots_status[plan_id] = status

    def _suite_view(self, suite: TaskSuite, *, live_requested: bool = False) -> SuiteView:
        return SuiteView(
            live_requested=live_requested,
            suite=suite,
            lifecycle=self._stage(suite.plan_id),
            comparison_model_ids=self._comparison_ids(suite.plan_id),
            slots_status=self._slots_status_of(suite.plan_id),
        )

    def _bind_pool(self, plan_id: str, *, kind: Literal["admitted", "fixture"], model_ids: tuple[str, ...]) -> None:
        self.pool_kind[plan_id] = kind
        self.pool_model_ids[plan_id] = model_ids

    def _require_confirm_pool(self, plan_id: str, comparison_model_ids: tuple[str, ...]) -> None:
        kind = self.pool_kind[plan_id]
        bound = self.pool_model_ids[plan_id]
        if kind == "admitted" and comparison_model_ids != bound:
            raise ValidationRejected(
                409,
                "slot_mismatch",
                "Comparison slots changed after the plan was generated. Generate again before confirming.",
            )
        if kind == "fixture" and comparison_model_ids:
            raise ValidationRejected(
                409,
                "fixture_pool",
                "This plan is bound to the fixture pool. Generate again from the admitted comparison slots.",
            )

    def _live(self, live: bool, environ: Mapping[str, str]) -> bool:
        if live and live_spend_state(environ) != "env_set":
            raise ValidationRejected(
                403,
                "live_spend_locked",
                f"Live spend requires JSON live=true and {LIVE_SPEND_ENV}=1. The request was not executed.",
            )
        return live


def _harness(harness_id: str, name: str, version: str, parameters: dict[str, object]) -> HarnessConfig:
    return HarnessConfig.build(harness_id=harness_id, name=name, version=version, parameters=parameters)


def _plan(
    *,
    plan_id: str,
    name: str,
    mode: Literal["paired_harness", "cross_model"],
    subject: HarnessConfig,
    reference: HarnessConfig | None,
    pool_id: str,
    pool_hash: str,
    notes: str | None,
    seed: int = 7,
    rounds: int = 2,
    difficulty_mix: tuple[DifficultyWeight, ...] | None = None,
    capabilities: tuple[str, ...] = ("echo", "exact", "match"),
    difficulties: tuple[str, ...] = (),
    domains: tuple[str, ...] = (),
    timeout_seconds: int = 30,
    max_total_tokens: int | None = None,
    max_cost: str | None = None,
    stop_max_rounds: int = 2,
    max_invalid_rate: str | None = None,
    stop_on_budget: bool = True,
    created_at: datetime = FIXTURE_AT,
) -> RoundPlan:
    mix = difficulty_mix or (
        DifficultyWeight(difficulty="easy", weight=1),
        DifficultyWeight(difficulty="medium", weight=1),
        DifficultyWeight(difficulty="hard", weight=1),
    )
    return RoundPlan.build(
        plan_id=plan_id,
        name=name,
        created_at=created_at,
        seed=seed,
        rounds=rounds,
        mode=mode,
        harness_ref=subject.to_ref(),
        reference_harness_ref=None if reference is None else reference.to_ref(),
        model_pool_ref=ModelPoolRef(pool_id=pool_id, content_hash=pool_hash),
        difficulty_mix=mix,
        taxonomy=TaxonomyFilter(capabilities=capabilities, difficulties=difficulties, domains=domains),
        budget=BudgetCaps(timeout_seconds=timeout_seconds, max_total_tokens=max_total_tokens, max_cost=max_cost),
        stop=StopConditions(max_rounds=stop_max_rounds, max_invalid_rate=max_invalid_rate, stop_on_budget=stop_on_budget),
        notes=notes,
    )


def _metric(
    model_id: str,
    task_class: str,
    success: int,
    valid: int,
    cost: str | None,
    steps: int | None,
) -> FixedHarnessModelMetrics:
    return FixedHarnessModelMetrics(
        model_id=model_id,
        harness_id="openbot",
        task_class=task_class,
        success_rate=None if valid == 0 else _rate(success, valid),
        success_count=success,
        valid_count=valid,
        total_model_cost=cost,
        cost_per_success=cost,
        steps=steps,
        usage_status="present" if cost is not None else "missing",
    )


def _rate(success: int, valid: int) -> str:
    if success == valid:
        return "1"
    if success * 2 == valid:
        return "0.5"
    return str(success)


def _demo_report(*, plan: RoundPlan, suite: TaskSuite, model_ids: tuple[str, ...]) -> RoundReport:
    primary, secondary = model_ids
    metrics = (
        _metric(primary, "echo--easy", 2, 2, None, None),
        _metric(secondary, "echo--easy", 2, 2, None, None),
        _metric(primary, "exact--medium", 2, 2, EXACT_COST, 4),
        _metric(secondary, "exact--medium", 1, 2, MATCH_COST, 3),
        _metric(primary, "match--hard", 1, 2, MATCH_COST, 5),
        _metric(secondary, "match--hard", 1, 2, EXACT_COST, 7),
    )
    return build_round_report(
        report_id=FIXTURE_CROSS_REPORT,
        plan_id=plan.plan_id,
        suite_id=suite.suite_id,
        suite_hash=suite.suite_hash,
        mode="cross_model",
        harness_id="openbot",
        findings=(
            "Under the fixed OpenBot harness, the fixture pool's two models sit on different "
            "cost/quality points for the same task classes. echo--easy usage is missing, so that "
            "class is not on the frontier. These figures reuse the fixture pilot amounts "
            f"{EXACT_COST} and {MATCH_COST}. They are an adaptation illustration, not a live measurement."
        ),
        frontier_note=(
            "exact--medium: both models are on the frontier, higher success against lower dollars per success. "
            "match--hard: openai/gpt-test dominates openai/gpt-test-mini at equal success and lower cost per success. "
            "The frontier is an adaptation signal under this harness. It is not a model-arena leaderboard "
            "and not a harness ranking."
        ),
        dimensions=(
            DimensionSummary(
                dimension="validity",
                reading="Same frozen suite, mandatory LiteLLM, egress policy ohbe-egress-0.1. No egress violation is recorded in this fixture.",
                coverage="1",
            ),
            DimensionSummary(
                dimension="effectiveness",
                reading="Success is reported per model under harness openbot. It is not a paired harness delta.",
                coverage="1",
            ),
            DimensionSummary(
                dimension="process",
                reading="Step counts are fixture annotations on the sealed stub. They are not a live tool trace.",
            ),
            DimensionSummary(
                dimension="cost",
                reading=f"Dollars per success reuse fixture amounts {EXACT_COST} and {MATCH_COST}. echo usage is missing and is not priced as zero.",
            ),
            DimensionSummary(
                dimension="diagnosability",
                reading="suite_hash is frozen. routing_optimality is null. The frontier can be recomputed from the metric rows.",
                coverage="1",
            ),
        ),
        model_metrics=metrics,
        measurement="fixture_demo",
    )


def _tasks_from_drafts(items: list[SuiteTaskDraft]) -> tuple[SuiteTask, ...]:
    if not items:
        raise ValidationRejected(422, "invalid_request", "suite draft needs at least one task")
    built: list[SuiteTask] = []
    for item in items:
        try:
            built.append(
                SuiteTask.build(
                    task_id=item.task_id,
                    capability=item.capability,
                    difficulty=item.difficulty,
                    domain=item.domain,
                    prompt=item.prompt,
                    repetitions=item.repetitions,
                )
            )
        except (ValidationError, ValueError) as exc:
            raise ValidationRejected(422, "invalid_request", "suite task is not valid") from exc
    return tuple(built)


def _panes(
    plan: RoundPlan,
    model_ids: tuple[str, ...],
    reference_harness_ids: Sequence[str] = (),
) -> tuple[tuple[str, str, str], ...]:
    if plan.mode == "cross_model":
        return tuple((model_id, model_id, plan.harness_ref.harness_id) for model_id in model_ids)
    shared = model_ids[0]
    reference = plan.reference_harness_ref
    if reference is None:
        raise ValidationRejected(422, "invalid_request", "paired dispatch requires a reference harness")
    # Subject + every reference from labels (sealed primary always included). Sealed plan_hash stays single-ref.
    seen: set[str] = set()
    harness_ids: list[str] = []
    for harness_id in [plan.harness_ref.harness_id, reference.harness_id, *reference_harness_ids]:
        if not harness_id or harness_id in seen:
            continue
        seen.add(harness_id)
        harness_ids.append(harness_id)
    return tuple((harness_id, shared, harness_id) for harness_id in harness_ids)


def _advance(status: DispatchPhase) -> DispatchPhase:
    index = DISPATCH_PHASES.index(status)
    if index + 1 >= len(DISPATCH_PHASES):
        return status
    return DISPATCH_PHASES[index + 1]
