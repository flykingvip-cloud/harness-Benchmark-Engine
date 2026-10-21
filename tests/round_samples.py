"""Deterministic round documents for schema round-trips."""

from __future__ import annotations

from ohbe.core.round import (
    BudgetCaps,
    DifficultyWeight,
    DimensionSummary,
    DispatchJob,
    FixedHarnessModelMetrics,
    ModelPoolRef,
    RoundPlan,
    StopConditions,
    TaxonomyFilter,
    build_round_report,
    confirm_suite,
    draft_suite,
    generate_suite_tasks,
    not_measured_report,
    observation_stub,
)
from tests.samples import NOW, sample_harness, sample_pool


def sample_round_plan(**overrides: object) -> RoundPlan:
    pool = sample_pool()
    subject = sample_harness()
    reference = sample_harness(
        harness_id="harness-ref",
        name="reference-harness",
        parameters={"max_steps": 2},
    )
    data: dict[str, object] = {
        "plan_id": "plan-paired",
        "name": "Paired round",
        "created_at": NOW,
        "seed": 7,
        "rounds": 2,
        "mode": "paired_harness",
        "harness_ref": subject.to_ref(),
        "reference_harness_ref": reference.to_ref(),
        "model_pool_ref": ModelPoolRef(pool_id=pool.pool_id, content_hash=pool.content_hash),
        "difficulty_mix": (
            DifficultyWeight(difficulty="easy", weight=1),
            DifficultyWeight(difficulty="medium", weight=2),
        ),
        "taxonomy": TaxonomyFilter(capabilities=("echo", "exact"), domains=("tool",)),
        "budget": BudgetCaps(timeout_seconds=30, max_cost="1.50"),
        "stop": StopConditions(max_rounds=2, max_invalid_rate="0.5"),
        "notes": "fixture draft",
    }
    data.update(overrides)
    return RoundPlan.build(**data)


def sample_cross_plan() -> RoundPlan:
    plan = sample_round_plan()
    payload = plan.model_dump(mode="json")
    payload.pop("plan_hash")
    payload["plan_id"] = "plan-cross"
    payload["name"] = "Cross-model round"
    payload["mode"] = "cross_model"
    payload["reference_harness_ref"] = None
    payload["notes"] = "fixed harness"
    return RoundPlan.build(**payload)


def sample_draft_suite():
    plan = sample_cross_plan()
    return draft_suite(suite_id="suite-cross", plan=plan, tasks=generate_suite_tasks(plan))


def sample_confirmed_suite():
    return confirm_suite(sample_draft_suite(), confirmed_at=NOW)


def sample_job() -> DispatchJob:
    suite = sample_confirmed_suite()
    plan = sample_cross_plan()
    pool = sample_pool()
    return DispatchJob.build(
        job_id="job-cross",
        plan_id=plan.plan_id,
        suite_id=suite.suite_id,
        suite_hash=suite.suite_hash,
        mode="cross_model",
        harness_id=plan.harness_ref.harness_id,
        model_ids=tuple(model.model_id for model in pool.models),
        focus_task_id=suite.tasks[0].task_id,
        status="queued",
        note="Dry-run dispatch. The gateway is not contacted.",
    )


def sample_events():
    job = sample_job()
    return observation_stub(
        job_id=job.job_id,
        subject_harness_id=job.harness_id,
        task_id=job.focus_task_id,
        panes=tuple((model_id, model_id, job.harness_id) for model_id in job.model_ids),
        recorded_at=NOW,
    )


def _metric(model_id: str, task_class: str, success: int, valid: int, cost: str | None, steps: int | None) -> FixedHarnessModelMetrics:
    usage = "present" if cost is not None else "missing"
    rate = None if valid == 0 else str(success / valid)
    return FixedHarnessModelMetrics(
        model_id=model_id,
        harness_id="harness-echo",
        task_class=task_class,
        success_rate=rate,
        success_count=success,
        valid_count=valid,
        total_model_cost=cost,
        cost_per_success=cost,
        steps=steps,
        usage_status=usage,
    )


def sample_model_metrics() -> tuple[FixedHarnessModelMetrics, ...]:
    return (
        _metric("openai/gpt-test", "echo/easy", 2, 2, None, None),
        _metric("openai/gpt-test-mini", "echo/easy", 2, 2, None, None),
        _metric("openai/gpt-test", "exact/medium", 2, 2, "0.0009", 4),
        _metric("openai/gpt-test-mini", "exact/medium", 1, 2, "0.00045", 3),
        _metric("openai/gpt-test", "match/hard", 1, 2, "0.00045", 5),
        _metric("openai/gpt-test-mini", "match/hard", 1, 2, "0.0009", 7),
    )


def sample_round_report():
    suite = sample_confirmed_suite()
    plan = sample_cross_plan()
    return build_round_report(
        report_id="report-cross",
        plan_id=plan.plan_id,
        suite_id=suite.suite_id,
        suite_hash=suite.suite_hash,
        mode="cross_model",
        harness_id=plan.harness_ref.harness_id,
        findings="Fixture findings under one harness. Not a model leaderboard.",
        frontier_note="exact/medium trades success against dollars per success. match/hard has a dominated point.",
        dimensions=tuple(
            DimensionSummary(dimension=name, reading="fixture", coverage="1")
            for name in ("validity", "effectiveness", "process", "cost", "diagnosability")
        ),
        model_metrics=sample_model_metrics(),
        measurement="fixture_demo",
    )


def sample_not_measured():
    suite = sample_confirmed_suite()
    plan = sample_cross_plan()
    return not_measured_report(
        report_id="report-dry",
        plan_id=plan.plan_id,
        suite_id=suite.suite_id,
        suite_hash=suite.suite_hash,
        mode=plan.mode,
        harness_id=plan.harness_ref.harness_id,
        rounds=plan.rounds,
    )


def round_documents() -> list[tuple[str, object]]:
    events = sample_events()
    return [
        ("round-plan-paired", sample_round_plan()),
        ("round-plan-cross", sample_cross_plan()),
        ("task-suite-draft", sample_draft_suite()),
        ("task-suite-confirmed", sample_confirmed_suite()),
        ("dispatch-job", sample_job()),
        ("observation-event", events[0]),
        ("observation-step", events[2]),
        ("round-report-demo", sample_round_report()),
        ("round-report-unmeasured", sample_not_measured()),
    ]
