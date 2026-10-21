"""Round-trip and hash stability for campaign documents."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ohbe.core import SCHEMA_VERSION
from ohbe.core.round import (
    DIMENSION_IDS,
    SuiteTask,
    campaign_stage,
    confirm_suite,
    generate_suite_tasks,
    recommend_model_fit,
    visible_events,
)
from tests.round_samples import (
    sample_confirmed_suite,
    sample_cross_plan,
    sample_draft_suite,
    sample_events,
    sample_job,
    sample_model_metrics,
    sample_not_measured,
    sample_round_plan,
    sample_round_report,
)
from tests.samples import NOW


def test_round_documents_round_trip_and_keep_schema_version() -> None:
    documents = (
        sample_round_plan(),
        sample_cross_plan(),
        sample_draft_suite(),
        sample_confirmed_suite(),
        sample_job(),
        sample_events()[0],
        sample_round_report(),
        sample_not_measured(),
    )
    for document in documents:
        raw = document.model_dump_json()
        revived = type(document).model_validate_json(raw)
        assert revived == document
        payload = json.loads(raw)
        assert payload["schema_version"] == SCHEMA_VERSION
        assert "schema_version" in type(document).model_json_schema()["properties"]


def test_plan_hash_is_stable_when_rebuilt_from_the_same_fields() -> None:
    first = sample_cross_plan()
    payload = first.model_dump(mode="json")
    payload.pop("plan_hash")
    second = type(first).build(**payload)
    assert second.plan_hash == first.plan_hash
    assert second.model_dump(mode="json") == first.model_dump(mode="json")


def test_suite_hash_changes_when_a_prompt_changes_and_survives_reload() -> None:
    draft = sample_draft_suite()
    original = draft.suite_hash
    with pytest.raises(TypeError):
        SuiteTask.build(**{**draft.tasks[0].model_dump(mode="json"), "prompt": "edited prompt"})
    first = draft.tasks[0]
    payload = first.model_dump(mode="json")
    payload.pop("content_hash")
    payload["prompt"] = "edited prompt"
    replacement = SuiteTask.build(**payload)
    assert replacement.content_hash != first.content_hash
    body = draft.model_dump(mode="json")
    body.pop("suite_hash")
    body["tasks"] = [replacement.model_dump(mode="json"), *[task.model_dump(mode="json") for task in draft.tasks[1:]]]
    rebuilt = type(draft).build(**body)
    assert rebuilt.suite_hash != original
    assert type(rebuilt).model_validate_json(rebuilt.model_dump_json()).suite_hash == rebuilt.suite_hash


def test_confirm_freezes_a_new_hash_and_is_idempotent() -> None:
    draft = sample_draft_suite()
    confirmed = confirm_suite(draft, confirmed_at=NOW)
    assert confirmed.status == "confirmed"
    assert confirmed.suite_hash != draft.suite_hash
    assert confirm_suite(confirmed, confirmed_at=NOW) == confirmed
    assert confirmed.model_validate_json(confirmed.model_dump_json()).suite_hash == confirmed.suite_hash
    tasks = generate_suite_tasks(sample_cross_plan())
    assert [task.task_id for task in tasks] == [task.task_id for task in draft.tasks]
    assert tasks[0].content_hash == draft.tasks[0].content_hash


def test_combinatorial_ids_follow_capability_difficulty_domain() -> None:
    tasks = generate_suite_tasks(sample_cross_plan())
    assert [task.task_id for task in tasks] == [
        "echo--easy--tool",
        "echo--medium--tool",
        "exact--easy--tool",
        "exact--medium--tool",
    ]
    assert tasks[1].repetitions == 2
    assert tasks[0].repetitions == 1


def test_modes_reject_the_wrong_harness_shape() -> None:
    paired = sample_round_plan().model_dump(mode="json")
    paired.pop("plan_hash")
    paired["reference_harness_ref"] = None
    with pytest.raises(ValidationError):
        type(sample_round_plan()).build(**paired)

    cross = sample_cross_plan().model_dump(mode="json")
    cross.pop("plan_hash")
    cross["reference_harness_ref"] = sample_round_plan().reference_harness_ref.model_dump(mode="json")
    with pytest.raises(ValidationError):
        type(sample_cross_plan()).build(**cross)

    same = sample_round_plan().model_dump(mode="json")
    same.pop("plan_hash")
    same["reference_harness_ref"] = same["harness_ref"]
    with pytest.raises(ValidationError):
        type(sample_round_plan()).build(**same)


def test_live_spend_cannot_be_unlocked_on_a_plan() -> None:
    payload = sample_round_plan().model_dump(mode="json")
    payload["live_spend"] = "env_set"
    with pytest.raises(ValidationError):
        type(sample_round_plan()).model_validate(payload)


def test_frontier_flags_and_recommendation_lock() -> None:
    report = sample_round_report()
    by_key = {(item.model_id, item.task_class): item for item in report.recommendations}
    assert by_key[("openai/gpt-test", "exact/medium")].pareto_flag is True
    assert by_key[("openai/gpt-test-mini", "exact/medium")].pareto_flag is True
    assert by_key[("openai/gpt-test", "match/hard")].pareto_flag is True
    assert by_key[("openai/gpt-test-mini", "match/hard")].pareto_flag is False
    assert ("openai/gpt-test", "echo/easy") not in by_key
    assert by_key[("openai/gpt-test", "exact/medium")].score_quality == "1"
    assert by_key[("openai/gpt-test", "exact/medium")].score_cost == "0.5"
    assert by_key[("openai/gpt-test-mini", "exact/medium")].score_cost == "1"
    assert "under harness harness-echo" in by_key[("openai/gpt-test", "exact/medium")].rationale
    assert report.routing_optimality is None
    assert report.model_independence is None
    assert [item.dimension for item in report.dimensions] == list(DIMENSION_IDS)

    forged = report.model_dump(mode="json")
    forged["recommendations"][0]["pareto_flag"] = False
    with pytest.raises(ValidationError):
        type(report).model_validate(forged)

    assert recommend_model_fit(sample_model_metrics()) == report.recommendations


def test_paired_report_rejects_a_model_table() -> None:
    report = sample_not_measured()
    payload = report.model_dump(mode="json")
    payload.pop("report_hash")
    payload["mode"] = "paired_harness"
    payload["measurement"] = "fixture_demo"
    with pytest.raises(ValidationError):
        type(report).build(**payload)


def test_campaign_stage_follows_the_lifecycle() -> None:
    plan = sample_cross_plan()
    draft = sample_draft_suite()
    confirmed = sample_confirmed_suite()
    job = sample_job()
    report = sample_round_report()
    assert campaign_stage(plan=plan) == "draft_plan"
    assert campaign_stage(plan=plan, suite=draft) == "draft_plan"
    assert campaign_stage(plan=plan, suite=confirmed) == "confirmed_suite"
    assert campaign_stage(plan=plan, suite=confirmed, job=job) == "running"
    assert campaign_stage(plan=plan, suite=confirmed, job=job, report=report) == "round_report"
    body = plan.model_dump(mode="json")
    body.pop("plan_hash")
    body["plan_id"] = "plan-fork"
    body["forked_from"] = plan.plan_id
    forked = type(plan).build(**body)
    assert campaign_stage(plan=forked) == "fork_plan"


def test_visible_events_follow_the_phase_ladder() -> None:
    events = sample_events()
    assert [event.kind for event in visible_events(events, "queued")] == ["queued"]
    assert [event.kind for event in visible_events(events, "dispatching")] == ["queued", "dispatching"]
    observed = visible_events(events, "observing")
    assert observed[-1].kind == "step"
    assert all(event.kind != "sealed" for event in observed)
    assert [event.kind for event in visible_events(events, "sealed")] == [event.kind for event in events]
    assert visible_events(events, "sealed")[2].pane_id == visible_events(events, "sealed")[2].model_id
