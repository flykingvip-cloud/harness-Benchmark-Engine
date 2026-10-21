"""Round and model-fit routes. They do not contact a gateway."""

from __future__ import annotations

import socket
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.api.app import create_app
from ohbe.api.harnesses import StaticHarnessDetector
from ohbe.api.rounds import FIXTURE_CROSS_PLAN, FIXTURE_CROSS_REPORT, FIXTURE_PAIRED_PLAN
from ohbe.core.round import RoundPlan, TaskSuite

ROOT = Path(__file__).resolve().parents[1]


def _pinned_harnesses() -> StaticHarnessDetector:
    return StaticHarnessDetector({"openbot": "fixture", "inspect-react": "0.3.277"})


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(ROOT, environ={}, harness_detector=_pinned_harnesses()))


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


def _body(mode: str = "cross_model", *, live: bool = False, reference: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Session round",
        "seed": 7,
        "rounds": 2,
        "mode": mode,
        "harness": {
            "harness_id": "openbot",
            "name": "OpenBot",
            "version": "fixture",
            "parameters": {"profile": "litellm-only"},
        },
        "pool_id": "pool-pilot",
        "difficulty_mix": [
            {"difficulty": "easy", "weight": 1},
            {"difficulty": "medium", "weight": 1},
        ],
        "capabilities": ["echo", "exact"],
        "domains": ["tool"],
        "timeout_seconds": 30,
        "live": live,
    }
    if mode == "paired_harness" or reference:
        payload["reference_harness"] = {
            "harness_id": "inspect-react",
            "name": "Inspect react()",
            "version": "0.3.277",
            "parameters": {"solver": "react"},
        }
    return payload


def test_lists_fixture_plans_and_model_fit_without_network(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)
    listed = client.get("/v1/rounds/plans")
    assert listed.status_code == 200
    body = listed.json()
    assert body["plans"][0]["spend"] == "not_live"
    assert body["plans"][0]["live_spend"] == "locked"
    by_id = {item["plan_id"]: item for item in body["plans"]}
    assert by_id[FIXTURE_PAIRED_PLAN]["mode"] == "paired_harness"
    assert by_id[FIXTURE_PAIRED_PLAN]["lifecycle"] == "draft_plan"
    assert by_id[FIXTURE_PAIRED_PLAN]["purpose"] == "fixture_demo"
    assert by_id[FIXTURE_PAIRED_PLAN]["industry"] == "general"
    assert "office_assistant" in by_id[FIXTURE_PAIRED_PLAN]["scenario_tags"]
    assert by_id[FIXTURE_CROSS_PLAN]["purpose"] == "fixture_demo"
    assert "cost_sensitive" in by_id[FIXTURE_CROSS_PLAN]["scenario_tags"]
    assert by_id[FIXTURE_CROSS_PLAN]["mode"] == "cross_model"
    assert by_id[FIXTURE_CROSS_PLAN]["lifecycle"] == "round_report"
    assert "not a model-arena leaderboard" in body["note"]

    detail = client.get(f"/v1/rounds/plans/{FIXTURE_CROSS_PLAN}")
    assert detail.status_code == 200
    assert detail.json()["comparison_model_ids"] == []
    assert detail.json()["slots_status"] == "frozen"
    assert detail.json()["pool_kind"] == "fixture"
    paired = client.get(f"/v1/rounds/plans/{FIXTURE_PAIRED_PLAN}")
    assert paired.json()["comparison_model_ids"] == []
    assert paired.json()["slots_status"] == "draft"
    assert paired.json()["suite_id"] is None
    assert paired.json()["job_id"] is None
    assert paired.json()["report_id"] is None
    cross_slots = client.get(f"/v1/rounds/plans/{FIXTURE_CROSS_PLAN}/slots")
    assert cross_slots.status_code == 200
    assert cross_slots.json()["status"] == "frozen"
    assert cross_slots.json()["source"] == "plan_slots"
    assert cross_slots.json()["model_ids"] == []
    assert client.post("/v1/rounds/comparison-slots", json={"model_ids": []}).status_code == 409
    plan = RoundPlan.model_validate(detail.json()["plan"])
    assert plan.live_spend == "locked"
    assert plan.gateway == "litellm"
    assert plan.reference_harness_ref is None
    assert detail.json()["executed"] is False
    assert detail.json()["network"] == "not_contacted"

    fit = client.get("/v1/analysis/model-fit")
    assert fit.status_code == 200
    fit_body = fit.json()
    assert fit_body["mode"] == "cross_model"
    assert fit_body["harness_id"] == "openbot"
    assert fit_body["spend"] == "not_live"
    assert fit_body["routing_optimality"] is None
    assert fit_body["model_independence"] is None
    assert "Adaptation signal only." in fit_body["recommendations"][0]["rationale"]
    flags = {(item["model_id"], item["task_class"]): item["pareto_flag"] for item in fit_body["recommendations"]}
    assert flags[("openai/gpt-test", "exact--medium")] is True
    assert flags[("openai/gpt-test-mini", "exact--medium")] is True
    assert flags[("openai/gpt-test", "match--hard")] is True
    assert flags[("openai/gpt-test-mini", "match--hard")] is False
    assert "echo--easy" not in {item["task_class"] for item in fit_body["recommendations"]}
    assert [item["dimension"] for item in fit_body["dimensions"]] == [
        "validity",
        "effectiveness",
        "process",
        "cost",
        "diagnosability",
    ]
    assert "adaptation" in fit_body["findings"].lower()
    assert "frontier" in fit_body["frontier_note"].lower()

    report = client.get(f"/v1/rounds/reports/{FIXTURE_CROSS_REPORT}")
    assert report.status_code == 200
    assert report.json()["report"]["suite_hash"] == fit_body["suite_hash"]
    assert report.json()["spend"] == "not_live"
    suites = client.get("/v1/rounds/suites")
    assert suites.status_code == 200
    assert suites.json()["spend"] == "not_live"
    suite_id = detail.json()["suite_id"]
    suite = client.get(f"/v1/rounds/suites/{suite_id}")
    assert suite.status_code == 200
    frozen = TaskSuite.model_validate(suite.json()["suite"])
    assert frozen.status == "confirmed"
    assert frozen.suite_hash == fit_body["suite_hash"]


def test_cross_model_dry_run_lifecycle_stays_offline(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)
    created = client.post("/v1/rounds/plans", json=_body("cross_model"))
    assert created.status_code == 200
    created_body = created.json()
    assert created_body["executed"] is False
    assert created_body["spend"] == "none"
    assert created_body["lifecycle"] == "draft_plan"
    assert created_body["comparison_model_ids"] == []
    assert created_body["slots_status"] == "draft"
    assert created_body["pool_kind"] == "fixture"
    plan_id = created_body["plan"]["plan_id"]
    assert RoundPlan.model_validate(created_body["plan"]).plan_hash == created_body["plan"]["plan_hash"]

    drafted = client.post(f"/v1/rounds/plans/{plan_id}/suite", json={})
    assert drafted.status_code == 200
    tasks = drafted.json()["suite"]["tasks"]
    assert [task["task_id"] for task in tasks] == [
        "echo--easy--tool",
        "echo--medium--tool",
        "exact--easy--tool",
        "exact--medium--tool",
    ]
    draft_tasks = [
        {
            "task_id": task["task_id"],
            "capability": task["capability"],
            "difficulty": task["difficulty"],
            "domain": task["domain"],
            "prompt": "edited cell" if index == 0 else task["prompt"],
            "repetitions": task["repetitions"],
        }
        for index, task in enumerate(tasks)
    ]
    edited = client.post(f"/v1/rounds/plans/{plan_id}/suite", json={"tasks": draft_tasks})
    assert edited.status_code == 200
    assert edited.json()["suite"]["tasks"][0]["prompt"] == "edited cell"
    assert edited.json()["suite"]["suite_hash"] != drafted.json()["suite"]["suite_hash"]

    confirmed = client.post(f"/v1/rounds/plans/{plan_id}/confirm", json={})
    assert confirmed.status_code == 200
    assert confirmed.json()["suite"]["status"] == "confirmed"
    frozen_hash = confirmed.json()["suite"]["suite_hash"]
    assert TaskSuite.model_validate(confirmed.json()["suite"]).suite_hash == frozen_hash
    again = client.post(f"/v1/rounds/plans/{plan_id}/confirm", json={})
    assert again.json()["suite"]["suite_hash"] == frozen_hash
    assert client.post(f"/v1/rounds/plans/{plan_id}/suite", json={}).status_code == 409

    started = client.post(f"/v1/rounds/plans/{plan_id}/start", json={"live": False})
    assert started.status_code == 200
    start_body = started.json()
    assert start_body["executed"] is False
    assert start_body["network"] == "not_contacted"
    assert start_body["label"] == "Dry-run"
    assert start_body["fixture_pool_id"] == "pool-pilot"
    assert "Dry-run" in start_body["job"]["note"]
    assert start_body["job"]["status"] == "queued"
    assert start_body["lifecycle"] == "running"
    assert [event["kind"] for event in start_body["events"]] == ["queued"]
    job_id = start_body["job"]["job_id"]
    panes = {event["pane_id"] for event in client.get(f"/v1/rounds/jobs/{job_id}").json()["events"]}
    assert panes == {"dispatch"}

    phases = ["queued"]
    report_id = None
    for _ in range(4):
        ticked = client.post(f"/v1/rounds/jobs/{job_id}/tick", json={})
        assert ticked.status_code == 200
        phases.append(ticked.json()["job"]["status"])
        report_id = ticked.json()["report_id"]
        if ticked.json()["job"]["status"] == "sealed":
            break
    assert phases == ["queued", "dispatching", "observing", "sealed"]
    sealed = client.get(f"/v1/rounds/jobs/{job_id}/events")
    kinds = [event["kind"] for event in sealed.json()["events"]]
    assert kinds[0] == "queued"
    assert "step" in kinds
    assert kinds[-1] == "sealed"
    assert {event["harness_id"] for event in sealed.json()["events"] if event["kind"] == "step"} == {"openbot"}
    assert len({event["model_id"] for event in sealed.json()["events"] if event["kind"] == "step"}) == 2

    stream = client.get(f"/v1/rounds/jobs/{job_id}/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: observation" in stream.text
    assert '"spend":"none"' in stream.text
    assert '"label":"Dry-run"' in stream.text
    assert "api.openai.com" not in stream.text

    report = client.get(f"/v1/rounds/reports/{report_id}")
    assert report.status_code == 200
    document = report.json()["report"]
    assert document["measurement"] == "not_measured"
    assert document["model_metrics"] == []
    assert document["recommendations"] == []
    assert document["routing_optimality"] is None
    assert "frontier" in document["frontier_note"].lower()
    assert document["suite_hash"] == frozen_hash
    assert client.get(f"/v1/rounds/plans/{plan_id}").json()["lifecycle"] == "round_report"

    forked = client.post(f"/v1/rounds/plans/{plan_id}/fork", json={"name": "Next draft"})
    assert forked.status_code == 200
    assert forked.json()["lifecycle"] == "fork_plan"
    assert forked.json()["plan"]["forked_from"] == plan_id
    assert forked.json()["suite_id"] is None
    assert forked.json()["job_id"] is None
    assert forked.json()["report_id"] is None
    assert forked.json()["slots_status"] == "draft"
    assert forked.json()["comparison_model_ids"] == []
    assert client.get(f"/v1/rounds/plans/{plan_id}").json()["slots_status"] == "frozen"


def test_start_before_confirm_and_unknown_ids(client: TestClient) -> None:
    created = client.post("/v1/rounds/plans", json=_body("paired_harness"))
    plan_id = created.json()["plan"]["plan_id"]
    assert created.json()["plan"]["reference_harness_ref"]["harness_id"] == "inspect-react"
    assert client.post(f"/v1/rounds/plans/{plan_id}/start", json={}).status_code == 409
    assert client.post(f"/v1/rounds/plans/{plan_id}/fork", json={}).status_code == 409
    assert client.get("/v1/rounds/plans/missing").status_code == 404
    assert client.get("/v1/rounds/suites/missing").status_code == 404
    assert client.get("/v1/rounds/reports/missing").status_code == 404
    assert client.get("/v1/rounds/jobs/missing").status_code == 404
    assert client.get("/v1/rounds/plans/missing").json()["spend"] == "none"


def test_cross_model_rejects_a_second_harness_and_live_spend_stays_locked(client: TestClient) -> None:
    rejected = client.post("/v1/rounds/plans", json=_body("cross_model", reference=True))
    assert rejected.status_code == 422
    assert rejected.json()["executed"] is False
    assert rejected.json()["spend"] == "none"

    locked = client.post("/v1/rounds/plans", json=_body("paired_harness", live=True))
    assert locked.status_code == 403
    assert locked.json()["error"] == "live_spend_locked"
    assert locked.json()["executed"] is False
    assert len(client.get("/v1/rounds/plans").json()["plans"]) == 2


def test_live_flag_with_env_still_does_not_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)
    client = TestClient(
        create_app(ROOT, environ={"OHBE_ALLOW_LIVE_SPEND": "1"}, harness_detector=_pinned_harnesses())
    )
    created = client.post("/v1/rounds/plans", json=_body("cross_model", live=True))
    assert created.status_code == 200
    assert created.json()["live_requested"] is True
    assert created.json()["executed"] is False
    plan_id = created.json()["plan"]["plan_id"]
    assert created.json()["plan"]["live_spend"] == "locked"
    client.post(f"/v1/rounds/plans/{plan_id}/suite", json={})
    client.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": True})
    started = client.post(f"/v1/rounds/plans/{plan_id}/start", json={"live": True})
    assert started.status_code == 200
    assert started.json()["executed"] is False
    assert started.json()["spend"] == "none"
    assert any(item["id"] == "live_not_executed" for item in started.json()["checks"])


def test_missing_catalog_does_not_spend(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, environ={}))
    response = client.get("/v1/rounds/plans")
    assert response.status_code == 503
    assert response.json()["error"] == "catalog_unavailable"
    assert response.json()["spend"] == "none"
    assert client.get("/v1/analysis/model-fit").status_code == 503

def test_multi_reference_labels_expand_monitor_panes(client: TestClient) -> None:
    """Extra refs live in PlanLabels; sealed RoundPlan keeps a single primary. Dry-run panes cover all."""

    body = _body("paired_harness")
    body["labels"] = {
        "scenario_tags": ["office_assistant"],
        "industry": "finance",
        "purpose": "customer_delivery",
        "customer": "acme",
        "requirements": "multi-ref follow-up",
        "reference_harness_ids": ["inspect-react", "openclaw", "workbuddy"],
    }
    created = client.post("/v1/rounds/plans", json=body)
    assert created.status_code == 200
    detail = created.json()
    plan_id = detail["plan"]["plan_id"]
    assert detail["plan"]["reference_harness_ref"]["harness_id"] == "inspect-react"
    assert detail["labels"]["reference_harness_ids"] == ["inspect-react", "openclaw", "workbuddy"]
    assert detail["labels"]["industry"] == "finance"
    assert detail["plan"]["live_spend"] == "locked"

    client.post(f"/v1/rounds/plans/{plan_id}/suite", json={})
    confirmed = client.post(f"/v1/rounds/plans/{plan_id}/confirm", json={})
    assert confirmed.status_code == 200
    started = client.post(f"/v1/rounds/plans/{plan_id}/start", json={})
    assert started.status_code == 200
    assert started.json()["spend"] == "none"
    assert started.json()["executed"] is False
    job = started.json()["job"]
    assert job["reference_harness_id"] == "inspect-react"
    assert job["live_spend"] == "locked"

    # Advance to observing so step events for every pane are visible.
    job_id = job["job_id"]
    observing = None
    for _ in range(4):
        observing = client.post(f"/v1/rounds/jobs/{job_id}/tick", json={})
        assert observing.status_code == 200
        if observing.json()["job"]["status"] == "observing":
            break
    assert observing is not None
    step_harnesses = {
        event["harness_id"] for event in observing.json()["events"] if event["kind"] == "step"
    }
    assert step_harnesses == {"openbot", "inspect-react", "openclaw", "workbuddy"}
    assert {event["pane_id"] for event in observing.json()["events"] if event["kind"] == "step"} == step_harnesses

    # Labels round-trip on detail after start; sealed plan_hash field unchanged shape.
    again = client.get(f"/v1/rounds/plans/{plan_id}")
    assert again.json()["labels"]["reference_harness_ids"] == ["inspect-react", "openclaw", "workbuddy"]
    assert again.json()["plan"]["reference_harness_ref"]["harness_id"] == "inspect-react"

