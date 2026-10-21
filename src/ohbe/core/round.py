"""Round plans, frozen task suites, dispatch stubs, and model-fit reports.

The primary evaluation object stays the harness. ``paired_harness`` is the
default mode: two harnesses, one shared model pool, mandatory LiteLLM, and
the Docker egress policy. ``cross_model`` is the secondary mode: one harness
stays fixed, the suite stays fixed, and models vary so a later reader can
see a cost/quality frontier for adaptation. That frontier is not a model
leaderboard and not a harness ranking.

These documents do not place a model call. Live spend is not a field that
can be opened. Oracle routing is not computed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import AwareDatetime, NonNegativeInt, OHBEModel, StrictInt, build_sealed
from ohbe.core.harness import HarnessRef
from ohbe.core.integrity import require_sha256, seal_or_verify

CampaignMode = Literal["paired_harness", "cross_model"]
Difficulty = Literal["easy", "medium", "hard"]
SuiteStatus = Literal["draft", "confirmed"]
DispatchPhase = Literal["queued", "dispatching", "observing", "sealed"]
ObservationKind = Literal["queued", "dispatching", "step", "sealed"]
UsageReading = Literal["present", "missing", "invalid"]
Measurement = Literal["fixture_demo", "not_measured"]
CampaignStage = Literal["draft_plan", "confirmed_suite", "running", "round_report", "fork_plan"]

DIMENSION_IDS: tuple[str, ...] = (
    "validity",
    "effectiveness",
    "process",
    "cost",
    "diagnosability",
)
DISPATCH_PHASES: tuple[DispatchPhase, ...] = ("queued", "dispatching", "observing", "sealed")
_MAX_CELLS = 200
_TOKEN_MAX = 64


def _decimal(value: str, *, field: str) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a base-10 decimal string")
    try:
        number = Decimal(value)
    except Exception as exc:
        raise ValueError(f"{field} must be a base-10 decimal string") from exc
    if not number.is_finite():
        raise ValueError(f"{field} must be a base-10 decimal string")
    # Reject scientific notation and leading plus. Canonical form is checked next.
    if value.startswith("+") or "e" in value.lower() or " " in value:
        raise ValueError(f"{field} must be a base-10 decimal string")
    return number


def _canonicalize_decimal(value: str, *, field: str) -> str:
    number = _decimal(value, field=field)
    if number == 0 and value.startswith("-"):
        raise ValueError(f"{field} must not be negative zero")
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _unit_interval(value: str, *, field: str) -> str:
    text = _canonicalize_decimal(value, field=field)
    number = Decimal(text)
    if number < 0 or number > 1:
        raise ValueError(f"{field} must be between 0 and 1")
    return text


def _non_negative_amount(value: str, *, field: str) -> str:
    text = _canonicalize_decimal(value, field=field)
    if Decimal(text) < 0:
        raise ValueError(f"{field} must be greater than or equal to 0")
    return text


def _positive_amount(value: str, *, field: str) -> str:
    text = _canonicalize_decimal(value, field=field)
    if Decimal(text) <= 0:
        raise ValueError(f"{field} must be greater than 0")
    return text


def _token(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _TOKEN_MAX:
        raise ValueError(f"{field} must be a token of 1..{_TOKEN_MAX} characters")
    if "--" in value or "/" in value or any(character.isspace() for character in value):
        raise ValueError(f"{field} must be a single token without '--'")
    return value


def _unique(values: Sequence[str], *, field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")


class StopConditions(OHBEModel):
    """When a campaign stops. Caps do not unlock spend."""

    max_rounds: int = Field(strict=True, ge=1)
    max_invalid_rate: str | None = None
    stop_on_budget: bool = Field(default=True, strict=True)

    @field_validator("max_invalid_rate")
    @classmethod
    def _rate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = _unit_interval(value, field="max_invalid_rate")
        if Decimal(text) <= 0:
            raise ValueError("max_invalid_rate must be greater than 0")
        return text


class DifficultyWeight(OHBEModel):
    """How many repetitions of one difficulty the suite schedules."""

    difficulty: Difficulty
    weight: int = Field(strict=True, ge=1)


class TaxonomyFilter(OHBEModel):
    """Task axes: capability × difficulty × an optional domain.

    ``difficulties`` empty means every difficulty in the plan mix.
    ``domains`` empty means the domain axis is omitted.
    """

    capabilities: tuple[str, ...] = Field(min_length=1)
    difficulties: tuple[Difficulty, ...] = ()
    domains: tuple[str, ...] = ()

    @field_validator("capabilities")
    @classmethod
    def _capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_token(item, field="capabilities") for item in value)
        _unique(cleaned, field="capabilities")
        return cleaned

    @field_validator("domains")
    @classmethod
    def _domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(_token(item, field="domains") for item in value)
        _unique(cleaned, field="domains")
        return cleaned

    @model_validator(mode="after")
    def _unique_difficulties(self) -> TaxonomyFilter:
        _unique(self.difficulties, field="difficulties")
        return self


class BudgetCaps(OHBEModel):
    """Protocol caps recorded before a round runs. They are not a spend grant."""

    timeout_seconds: int = Field(strict=True, ge=1)
    max_total_tokens: int | None = Field(default=None, strict=True, ge=1)
    max_cost: str | None = None

    @field_validator("max_cost")
    @classmethod
    def _max_cost(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _positive_amount(value, field="max_cost")


class ModelPoolRef(OHBEModel):
    """Identity of a model pool. The digest is the pool content hash."""

    pool_id: str = Field(min_length=1)
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        return require_sha256(value, field="content_hash")


class RoundPlan(OHBEModel):
    """A campaign draft. Forks are new plans with ``forked_from`` set.

    ``paired_harness`` names the subject harness and a distinct reference
    harness. ``cross_model`` names one harness and leaves the reference empty:
    models vary, the harness does not.
    """

    plan_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    seed: StrictInt
    rounds: int = Field(strict=True, ge=1, le=50)
    mode: CampaignMode = "paired_harness"
    harness_ref: HarnessRef
    reference_harness_ref: HarnessRef | None = None
    model_pool_ref: ModelPoolRef
    difficulty_mix: tuple[DifficultyWeight, ...] = Field(min_length=1)
    taxonomy: TaxonomyFilter
    budget: BudgetCaps
    stop: StopConditions
    gateway: Literal["litellm"] = "litellm"
    egress_policy_version: Literal["ohbe-egress-0.1"] = "ohbe-egress-0.1"
    live_spend: Literal["locked"] = "locked"
    forked_from: str | None = None
    notes: str | None = None
    plan_hash: str

    @field_validator("plan_hash")
    @classmethod
    def _plan_hash(cls, value: str) -> str:
        return require_sha256(value, field="plan_hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> RoundPlan:
        _check_plan(self)
        seal_or_verify(self, info, "plan_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> RoundPlan:
        return build_sealed(cls, "plan_hash", data)


def _check_plan(plan: RoundPlan) -> None:
    difficulties = [item.difficulty for item in plan.difficulty_mix]
    _unique(difficulties, field="difficulty_mix")
    if plan.stop.max_rounds > plan.rounds:
        raise ValueError("stop.max_rounds must be less than or equal to rounds")
    mix = set(difficulties)
    if plan.taxonomy.difficulties and not set(plan.taxonomy.difficulties) <= mix:
        raise ValueError("taxonomy difficulties must be in the difficulty mix")
    if plan.mode == "paired_harness":
        ref = plan.reference_harness_ref
        if ref is None:
            raise ValueError("paired_harness requires a reference harness")
        if ref.harness_id == plan.harness_ref.harness_id:
            raise ValueError("paired harnesses must be distinct")
    elif plan.reference_harness_ref is not None:
        raise ValueError("cross_model keeps a single harness fixed")
    if plan.forked_from is not None and plan.forked_from == plan.plan_id:
        raise ValueError("forked_from must name a different plan")
    _selected_difficulties(plan)


def _selected_difficulties(plan: RoundPlan) -> tuple[Difficulty, ...]:
    mix = [item.difficulty for item in plan.difficulty_mix]
    if plan.taxonomy.difficulties:
        selected = tuple(item for item in mix if item in plan.taxonomy.difficulties)
    else:
        selected = tuple(mix)
    if not selected:
        raise ValueError("taxonomy filter removed every difficulty")
    return selected


class SuiteTask(OHBEModel):
    """One cell of capability × difficulty × optional domain."""

    task_id: str = Field(min_length=1)
    capability: str
    difficulty: Difficulty
    domain: str | None = None
    prompt: str = Field(min_length=1)
    repetitions: int = Field(strict=True, ge=1)
    content_hash: str

    @field_validator("capability")
    @classmethod
    def _capability(cls, value: str) -> str:
        return _token(value, field="capability")

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _token(value, field="domain")

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        return require_sha256(value, field="content_hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> SuiteTask:
        expected = cell_task_id(self.capability, self.difficulty, self.domain)
        if self.task_id != expected:
            raise ValueError("task_id must be the canonical cell id")
        seal_or_verify(self, info, "content_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> SuiteTask:
        return build_sealed(cls, "content_hash", data)


def cell_task_id(capability: str, difficulty: str, domain: str | None) -> str:
    """Stable id for one taxonomy cell. Order is capability, difficulty, domain."""

    parts = [capability, difficulty]
    if domain:
        parts.append(domain)
    return "--".join(parts)


class TaskSuite(OHBEModel):
    """Draft or confirmed tasks for one plan.

    ``suite_hash`` covers the tasks and the status. Confirming a draft
    produces a new digest. That confirmed digest is the freeze later
    documents pin. Editing a confirmed suite invalidates the digest.
    """

    suite_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_hash: str
    status: SuiteStatus
    tasks: tuple[SuiteTask, ...] = Field(min_length=1, max_length=_MAX_CELLS)
    confirmed_at: AwareDatetime | None = None
    suite_hash: str

    @field_validator("plan_hash", "suite_hash")
    @classmethod
    def _hashes(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field=info.field_name or "hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> TaskSuite:
        if self.status == "confirmed" and self.confirmed_at is None:
            raise ValueError("confirmed suite requires confirmed_at")
        if self.status == "draft" and self.confirmed_at is not None:
            raise ValueError("draft suite has no confirmed_at")
        ids = [task.task_id for task in self.tasks]
        _unique(ids, field="tasks")
        seal_or_verify(self, info, "suite_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> TaskSuite:
        return build_sealed(cls, "suite_hash", data)


def generate_suite_tasks(plan: RoundPlan) -> tuple[SuiteTask, ...]:
    """Cartesian product of the plan axes, in capability, difficulty, domain order.

    Difficulty weight becomes ``repetitions`` on the cell. It does not clone
    the cell, so task ids stay unique and the hash stays a function of the axes.
    """

    weights = {item.difficulty: item.weight for item in plan.difficulty_mix}
    difficulties = _selected_difficulties(plan)
    domains: tuple[str | None, ...] = plan.taxonomy.domains or (None,)
    cells = len(plan.taxonomy.capabilities) * len(difficulties) * len(domains)
    if cells > _MAX_CELLS:
        raise ValueError(f"suite has {cells} cells; the limit is {_MAX_CELLS}")
    tasks: list[SuiteTask] = []
    for capability in plan.taxonomy.capabilities:
        for difficulty in difficulties:
            for domain in domains:
                tasks.append(
                    SuiteTask.build(
                        task_id=cell_task_id(capability, difficulty, domain),
                        capability=capability,
                        difficulty=difficulty,
                        domain=domain,
                        prompt=_cell_prompt(plan.seed, capability, difficulty, domain),
                        repetitions=weights[difficulty],
                    )
                )
    return tuple(tasks)


def _cell_prompt(seed: int, capability: str, difficulty: str, domain: str | None) -> str:
    parts = [f"seed {seed}", capability, difficulty]
    if domain:
        parts.append(domain)
    return " · ".join(parts)


def draft_suite(*, suite_id: str, plan: RoundPlan, tasks: Sequence[SuiteTask]) -> TaskSuite:
    """A provisional suite. Its hash changes if a prompt or cell changes."""

    return TaskSuite.build(
        suite_id=suite_id,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        status="draft",
        tasks=tuple(tasks),
        confirmed_at=None,
    )


def confirm_suite(suite: TaskSuite, *, confirmed_at: datetime) -> TaskSuite:
    """Freeze the draft. The same confirmed document confirms again unchanged."""

    if suite.status == "confirmed":
        return suite
    payload = suite.model_dump(mode="json")
    payload.pop("suite_hash", None)
    payload["status"] = "confirmed"
    payload["confirmed_at"] = confirmed_at
    return TaskSuite.build(**payload)


class DispatchJob(OHBEModel):
    """What a round would dispatch. This record does not execute it.

    ``executed`` stays false and ``spend`` stays none. Polling and a one-shot
    SSE replay read :func:`observation_stub` events. They are not a live
    websocket to a model gateway.
    """

    job_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    suite_id: str = Field(min_length=1)
    suite_hash: str
    mode: CampaignMode
    harness_id: str = Field(min_length=1)
    model_ids: tuple[str, ...] = Field(min_length=1)
    focus_task_id: str = Field(min_length=1)
    reference_harness_id: str | None = None
    status: DispatchPhase
    spend: Literal["none"] = "none"
    executed: Literal[False] = False
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked"] = "locked"
    transport: Literal["poll"] = "poll"
    note: str = Field(min_length=1)
    job_hash: str

    @field_validator("suite_hash", "job_hash")
    @classmethod
    def _hashes(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field=info.field_name or "hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> DispatchJob:
        _unique(self.model_ids, field="model_ids")
        if self.mode == "paired_harness":
            if not self.reference_harness_id or self.reference_harness_id == self.harness_id:
                raise ValueError("paired dispatch names two distinct harnesses")
        elif self.reference_harness_id is not None:
            raise ValueError("cross_model dispatch keeps one harness")
        seal_or_verify(self, info, "job_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> DispatchJob:
        return build_sealed(cls, "job_hash", data)


class ObservationEvent(OHBEModel):
    """One immutable stub event. The sequence is not a provider stream."""

    job_id: str = Field(min_length=1)
    seq: NonNegativeInt
    pane_id: str = Field(min_length=1)
    model_id: str | None = None
    harness_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    kind: ObservationKind
    message: str = Field(min_length=1)
    recorded_at: AwareDatetime
    event_hash: str

    @field_validator("event_hash")
    @classmethod
    def _event_hash(cls, value: str) -> str:
        return require_sha256(value, field="event_hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> ObservationEvent:
        if self.kind == "step":
            if not self.model_id:
                raise ValueError("a step event names a model")
        elif self.pane_id != "dispatch" or self.model_id is not None:
            raise ValueError("dispatcher events use the dispatch pane and no model")
        seal_or_verify(self, info, "event_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> ObservationEvent:
        return build_sealed(cls, "event_hash", data)


def observation_stub(
    *,
    job_id: str,
    subject_harness_id: str,
    task_id: str,
    panes: Sequence[tuple[str, str, str]],
    recorded_at: datetime,
) -> tuple[ObservationEvent, ...]:
    """Deterministic events for polling and a one-shot SSE replay.

    Each pane is ``(pane_id, model_id, harness_id)``. Cross-model panes share
    one harness and vary the model. Paired panes share one model and vary the
    harness. No event records a provider payload or a live price.
    """

    if not panes:
        raise ValueError("observation stub requires at least one pane")
    pane_ids = [pane_id for pane_id, _model_id, _harness_id in panes]
    _unique(tuple(pane_ids), field="panes")
    specs: list[tuple[ObservationKind, str, str | None, str, str]] = [
        ("queued", "dispatch", None, subject_harness_id, "Dispatcher queued. No model call is placed."),
        (
            "dispatching",
            "dispatch",
            None,
            subject_harness_id,
            f"Panes assigned on task {task_id}. Spend stays none.",
        ),
    ]
    for pane_id, model_id, harness_id in panes:
        specs.append(
            (
                "step",
                pane_id,
                model_id,
                harness_id,
                f"Fixture step for pane {pane_id} under harness {harness_id} on task {task_id}. Spend none.",
            )
        )
    specs.append(("sealed", "dispatch", None, subject_harness_id, "Round sealed offline. The stub trace is not rewritten."))
    events: list[ObservationEvent] = []
    for seq, (kind, pane_id, model_id, harness_id, message) in enumerate(specs):
        events.append(
            ObservationEvent.build(
                job_id=job_id,
                seq=seq,
                pane_id=pane_id,
                model_id=model_id,
                harness_id=harness_id,
                task_id=task_id,
                kind=kind,
                message=message,
                recorded_at=recorded_at,
            )
        )
    return tuple(events)


def visible_events(events: Sequence[ObservationEvent], phase: DispatchPhase) -> tuple[ObservationEvent, ...]:
    """Events whose phase is at or before ``phase``. Later stub rows stay hidden."""

    limit = DISPATCH_PHASES.index(phase)
    return tuple(event for event in events if DISPATCH_PHASES.index(_event_phase(event.kind)) <= limit)


def _event_phase(kind: ObservationKind) -> DispatchPhase:
    if kind == "step":
        return "observing"
    return kind


class DimensionSummary(OHBEModel):
    """One of the five readings. Missing coverage stays null."""

    dimension: Literal["validity", "effectiveness", "process", "cost", "diagnosability"]
    reading: str = Field(min_length=1)
    coverage: str | None = None

    @field_validator("coverage")
    @classmethod
    def _coverage(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _unit_interval(value, field="coverage")


class FixedHarnessModelMetrics(OHBEModel):
    """Per-model metrics under one fixed harness. Not a harness score."""

    model_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    task_class: str = Field(min_length=1)
    success_rate: str | None
    success_count: NonNegativeInt
    valid_count: NonNegativeInt
    total_model_cost: str | None
    cost_per_success: str | None
    steps: int | None = Field(default=None, strict=True, ge=0)
    usage_status: UsageReading

    @field_validator("success_rate")
    @classmethod
    def _success_rate(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _unit_interval(value, field="success_rate")

    @field_validator("total_model_cost", "cost_per_success")
    @classmethod
    def _costs(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        return _non_negative_amount(value, field=info.field_name or "cost")

    @model_validator(mode="after")
    def _counts(self) -> FixedHarnessModelMetrics:
        if self.usage_status != "present" and (self.total_model_cost is not None or self.cost_per_success is not None):
            raise ValueError("missing or invalid usage has no cost")
        if self.success_count > self.valid_count:
            raise ValueError("success_count cannot exceed valid_count")
        if self.success_rate is None:
            return self
        if self.valid_count < 1:
            raise ValueError("success_rate requires a positive valid_count")
        expected = _canonicalize_decimal(str(Decimal(self.success_count) / Decimal(self.valid_count)), field="success_rate")
        if self.success_rate != expected:
            raise ValueError("success_rate does not match success_count / valid_count")
        return self


class ModelFitRecommendation(OHBEModel):
    """An adaptation signal for one model on one task class under a fixed harness.

    ``score_quality`` is the observed success rate. ``score_cost`` is relative
    thrift inside the task class: 1 is the cheapest present cost per success.
    ``pareto_flag`` marks the cost/quality frontier. It is not a rank.
    """

    model_id: str = Field(min_length=1)
    task_class: str = Field(min_length=1)
    score_quality: str
    score_cost: str
    pareto_flag: bool = Field(strict=True)
    rationale: str = Field(min_length=1)

    @field_validator("score_quality", "score_cost")
    @classmethod
    def _scores(cls, value: str, info: ValidationInfo) -> str:
        return _unit_interval(value, field=info.field_name or "score")


def recommend_model_fit(
    metrics: Sequence[FixedHarnessModelMetrics],
) -> tuple[ModelFitRecommendation, ...]:
    """Frontier inside each task class. Rows without present cost are omitted.

    Higher success is better. Lower dollars per success is better. A missing
    cost is not zero and cannot dominate or be dominated.
    """

    grouped: dict[str, list[FixedHarnessModelMetrics]] = {}
    for row in metrics:
        grouped.setdefault(row.task_class, []).append(row)
    recommendations: list[ModelFitRecommendation] = []
    for task_class, rows in grouped.items():
        harnesses = {row.harness_id for row in rows}
        if len(harnesses) != 1:
            raise ValueError("model fit compares models under one harness")
        harness_id = next(iter(harnesses))
        eligible = [
            row
            for row in rows
            if row.usage_status == "present" and row.success_rate is not None and row.cost_per_success is not None
        ]
        costs = [Decimal(row.cost_per_success) for row in eligible if row.cost_per_success is not None]
        minimum = min(costs) if costs else None
        frontier = _pareto(eligible)
        for row in eligible:
            if row.cost_per_success is None or row.success_rate is None or minimum is None:
                continue
            cost = Decimal(row.cost_per_success)
            if cost == 0 or minimum == 0:
                score_cost = Decimal(1) if cost == 0 else Decimal(0)
            else:
                score_cost = minimum / cost
            on_frontier = (row.model_id, task_class) in frontier
            if on_frontier:
                rationale = (
                    f"{row.model_id} is on the cost/quality frontier for {task_class} "
                    f"under harness {harness_id}. Adaptation signal only."
                )
            else:
                rationale = (
                    f"{row.model_id} is inside the frontier for {task_class} under harness {harness_id}: "
                    "another model is at least as effective at an equal or lower cost per success."
                )
            recommendations.append(
                ModelFitRecommendation(
                    model_id=row.model_id,
                    task_class=task_class,
                    score_quality=row.success_rate,
                    score_cost=_canonicalize_decimal(format(score_cost, "f"), field="score_cost"),
                    pareto_flag=on_frontier,
                    rationale=rationale,
                )
            )
    return tuple(recommendations)


def _pareto(rows: Sequence[FixedHarnessModelMetrics]) -> set[tuple[str, str]]:
    selected: set[tuple[str, str]] = set()
    for row in rows:
        if row.success_rate is None or row.cost_per_success is None:
            continue
        quality = Decimal(row.success_rate)
        cost = Decimal(row.cost_per_success)
        dominated = False
        for other in rows:
            if other is row or other.success_rate is None or other.cost_per_success is None:
                continue
            other_quality = Decimal(other.success_rate)
            other_cost = Decimal(other.cost_per_success)
            if other_quality >= quality and other_cost <= cost and (other_quality > quality or other_cost < cost):
                dominated = True
                break
        if not dominated:
            selected.add((row.model_id, row.task_class))
    return selected


class RoundReport(OHBEModel):
    """Findings plus the five readings. Model rows exist only for cross_model.

    ``routing_optimality`` and ``model_independence`` stay null. A fixture
    demo is labeled ``fixture_demo`` and is not live spend.
    """

    report_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    suite_id: str = Field(min_length=1)
    suite_hash: str
    mode: CampaignMode
    harness_id: str = Field(min_length=1)
    findings: str = Field(min_length=1)
    frontier_note: str = Field(min_length=1)
    dimensions: tuple[DimensionSummary, ...]
    model_metrics: tuple[FixedHarnessModelMetrics, ...] = ()
    recommendations: tuple[ModelFitRecommendation, ...] = ()
    measurement: Measurement
    spend: Literal["not_live"] = "not_live"
    label: Literal["fixture"] = "fixture"
    routing_optimality: None = None
    model_independence: None = None
    report_hash: str

    @field_validator("suite_hash", "report_hash")
    @classmethod
    def _hashes(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field=info.field_name or "hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> RoundReport:
        _check_report(self)
        seal_or_verify(self, info, "report_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> RoundReport:
        return build_sealed(cls, "report_hash", data)


def _check_report(report: RoundReport) -> None:
    names = tuple(item.dimension for item in report.dimensions)
    if names != DIMENSION_IDS:
        raise ValueError("dimensions must be the five readings in order")
    keys = [(row.model_id, row.task_class) for row in report.model_metrics]
    if len(keys) != len(set(keys)):
        raise ValueError("model metrics repeat a model on one task class")
    for row in report.model_metrics:
        if row.harness_id != report.harness_id:
            raise ValueError("model metrics must use the report harness")
    if report.mode == "paired_harness" and (report.model_metrics or report.recommendations):
        raise ValueError("paired_harness reports do not carry a model-fit table")
    if report.measurement == "not_measured" and (report.model_metrics or report.recommendations):
        raise ValueError("not_measured report has no model metrics")
    if report.measurement == "fixture_demo":
        if report.mode != "cross_model" or not report.model_metrics:
            raise ValueError("fixture_demo is a cross_model report with per-model metrics")
    expected = recommend_model_fit(report.model_metrics)
    if tuple(report.recommendations) != expected:
        raise ValueError("recommendations do not match the cost/quality frontier")


def build_round_report(**data: Any) -> RoundReport:
    """Fill recommendations from the metric rows, then seal the report."""

    metrics = data.get("model_metrics") or ()
    payload = dict(data)
    payload["recommendations"] = recommend_model_fit(tuple(metrics))
    return RoundReport.build(**payload)


def not_measured_report(
    *,
    report_id: str,
    plan_id: str,
    suite_id: str,
    suite_hash: str,
    mode: CampaignMode,
    harness_id: str,
    rounds: int,
) -> RoundReport:
    """A sealed dry-run report. Counts stay absent. The frontier is not drawn."""

    findings = (
        f"Dry-run sealed {rounds} scheduled round(s) for harness {harness_id} without a model call. "
        "Success, cost, and steps were not measured. An infrastructure failure is not a task failure. "
        "Oracle routing was not computed."
    )
    frontier = (
        "No cost/quality frontier is drawn until usage is present and priced offline. "
        "Missing usage is not a cost of zero. This note is not a model-arena ranking."
    )
    return build_round_report(
        report_id=report_id,
        plan_id=plan_id,
        suite_id=suite_id,
        suite_hash=suite_hash,
        mode=mode,
        harness_id=harness_id,
        findings=findings,
        frontier_note=frontier,
        dimensions=tuple(DimensionSummary(dimension=name, reading="not_measured") for name in DIMENSION_IDS),
        model_metrics=(),
        measurement="not_measured",
    )


def campaign_stage(
    *,
    plan: RoundPlan,
    suite: TaskSuite | None = None,
    job: DispatchJob | None = None,
    report: RoundReport | None = None,
) -> CampaignStage:
    """Where one plan sits in DraftPlan → ConfirmedSuite → Running → RoundReport → ForkPlan."""

    if report is not None:
        return "round_report"
    if job is not None:
        return "running"
    if suite is not None and suite.status == "confirmed":
        return "confirmed_suite"
    if plan.forked_from:
        return "fork_plan"
    return "draft_plan"


def fork_plan(plan: RoundPlan, *, plan_id: str, name: str, created_at: datetime) -> RoundPlan:
    """A new draft that records its parent. The suite is not copied."""

    payload = plan.model_dump(mode="json")
    payload.pop("plan_hash", None)
    payload["plan_id"] = plan_id
    payload["name"] = name
    payload["created_at"] = created_at
    payload["forked_from"] = plan.plan_id
    return RoundPlan.build(**payload)


def suite_tasks_from_payload(items: Sequence[Mapping[str, Any]]) -> tuple[SuiteTask, ...]:
    """Rebuild suite cells from JSON objects that already carry content hashes."""

    return tuple(SuiteTask.model_validate(dict(item)) for item in items)
