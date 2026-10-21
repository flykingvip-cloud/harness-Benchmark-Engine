"""HTTP routes for the workbench surface. No route places a model call."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.api.app import create_app
from ohbe.api.surface import SURFACE_ROUTES
from ohbe.gateway.client import post_json

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_CLIENT = ROOT / "workbench" / "src" / "api.ts"


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(ROOT, environ={}))


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr("ohbe.gateway.client.post_json", blocked)


def test_surface_lists_the_sdk_routes(client: TestClient) -> None:
    response = client.get("/v1")
    assert response.status_code == 200
    body = response.json()
    assert body["surface"] == "ohbe.http.v1"
    assert body["engine"] == "ohbe"
    assert [(item["method"], item["path"]) for item in body["routes"]] == [
        (item["method"], item["path"]) for item in SURFACE_ROUTES
    ]


def test_health_does_not_need_the_fixture_catalog(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, environ={}))
    for path in ("/health", "/v1/health"):
        response = client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "ohbe.api"
        assert body["ohbe_version"] == "0.1.0"
        assert body["schema_version"] == "0.1"
        assert body["live_spend"] == "locked"
        assert body["spend_execution"] == "disabled"
        assert body["cloud_litellm"] == "fail_closed"
    assert client.get("/v1/experiments").status_code == 503


def test_status_is_locked_and_fail_closed(client: TestClient) -> None:
    response = client.get("/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "ohbe.api"
    assert body["ohbe_version"] == "0.1.0"
    assert body["schema_version"] == "0.1"
    assert body["live_spend"] == "locked"
    assert body["spend_execution"] == "disabled"
    assert body["cloud_litellm"] == "fail_closed"


def test_doctor_checklist_passes_when_spend_is_locked(client: TestClient) -> None:
    response = client.get("/v1/doctor")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["spend_execution"] == "disabled"
    ids = [item["id"] for item in body["checks"]]
    assert ids == [
        "engine_version",
        "schema_version",
        "offline_pool",
        "fixture_report",
        "egress_fail_closed",
        "cloud_litellm_fail_closed",
        "live_spend_locked",
    ]
    assert all(item["passed"] is True for item in body["checks"])


def test_doctor_reports_an_open_live_spend_env() -> None:
    client = TestClient(create_app(ROOT, environ={"OHBE_ALLOW_LIVE_SPEND": "1"}))
    body = client.get("/v1/doctor").json()
    assert body["ok"] is False
    assert body["live_spend"] == "env_set"
    assert body["spend_execution"] == "disabled"
    locked = next(item for item in body["checks"] if item["id"] == "live_spend_locked")
    assert locked["passed"] is False


def test_lists_fixture_experiments_pools_and_results(client: TestClient) -> None:
    experiments = client.get("/v1/experiments")
    assert experiments.status_code == 200
    experiment = experiments.json()["experiments"][0]
    assert experiment["experiment_id"] == "fixture-pilot"
    assert experiment["label"] == "fixture"
    assert experiment["spend"] == "not_live"
    assert experiment["task_ids"] == ["ohbe-pilot-echo", "ohbe-pilot-exact", "ohbe-pilot-match"]
    detail = client.get("/v1/experiments/fixture-pilot")
    assert detail.status_code == 200
    assert detail.json()["pool_id"] == "pool-pilot"

    pools = client.get("/v1/pools")
    assert pools.status_code == 200
    pool = pools.json()["pools"][0]
    assert pool["pool_id"] == "pool-pilot"
    assert pool["hash_matches_report"] is True
    assert [model["model_id"] for model in pool["models"]] == ["openai/gpt-test", "openai/gpt-test-mini"]
    assert client.get("/v1/pools/pool-pilot").status_code == 200

    results = client.get("/v1/results")
    assert results.status_code == 200
    index = results.json()["results"][0]
    assert index["result_id"] == "fixture-pilot"
    assert index["source_kind"] == "fixture"
    assert index["spend"] == "not_live"
    assert index["label"] == "fixture"
    assert index["task_count"] == 3
    report = client.get("/v1/results/fixture-pilot").json()
    assert report["source_kind"] == "fixture"
    echo = report["tasks"][0]["harnesses"][0]
    assert echo["success_rate"] == "1.000000"
    assert echo["success_rate_low"] == "0.342372"
    assert echo["success_rate_high"] == "1.000000"
    assert echo["uncertainty"] == "wilson95"
    assert echo["total_model_cost"] is None
    assert echo["cost_per_success"] is None
    assert echo["usage_status"] == "missing"
    assert echo["complete"] is True
    assert echo["invalid_by_category"] == {}
    match = report["tasks"][2]["harnesses"][0]
    assert match["task_failure_count"] == 0
    assert match["invalid_count"] == 1
    assert match["invalid_by_category"] == {"timeout": 1}
    assert match["cost_per_success"] == "0.00045"
    assert report["release_gates"][0]["gate_id"] == "same_task_input"


def test_unknown_catalog_ids_are_not_found(client: TestClient) -> None:
    assert client.get("/v1/experiments/live-prod").status_code == 404
    assert client.get("/v1/pools/cloud").status_code == 404
    assert client.get("/v1/results/live").status_code == 404
    assert client.get("/v1/experiments/live-prod").json()["spend"] == "none"


def test_dry_run_accepts_a_local_gateway_without_network(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network(monkeypatch)
    response = client.post(
        "/v1/dry-run",
        json={
            "gateway_url": "http://ohbe-gateway:4000",
            "experiment_id": "fixture-pilot",
            "pool_id": "pool-pilot",
            "live": False,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mode"] == "dry_run"
    assert body["label"] == "Dry-run"
    assert body["executed"] is False
    assert body["spend"] == "none"
    assert body["network"] == "not_contacted"
    assert body["gateway_host"] == "ohbe-gateway"
    assert body["gateway_port"] == 4000
    assert {item["id"] for item in body["checks"]} >= {
        "gateway_not_cloud",
        "provider_bypass_denied",
        "spend_not_executed",
        "experiment_known",
        "pool_known",
    }


def test_cloud_litellm_url_is_denied_even_when_live_flags_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)
    client = TestClient(create_app(ROOT, environ={"OHBE_ALLOW_LIVE_SPEND": "1"}))
    response = client.post(
        "/v1/dry-run",
        json={"gateway_url": "https://sk-live-secret:pw@API.LiteLLM.ai/v1", "live": True},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "cloud_litellm_denied"
    assert body["gateway_host"] == "api.litellm.ai"
    assert body["spend"] == "none"
    assert body["network"] == "not_contacted"
    assert body["executed"] is False
    assert "sk-live-secret" not in response.text


@pytest.mark.parametrize(
    "url",
    [
        "https://litellm.ai/v1",
        "https://org.proxy.litellm.ai/v1/chat/completions",
        "https://api.berri.ai/v1",
        "https://cloud.litellm.com/key/generate",
    ],
)
def test_cloud_suffixes_fail_closed(client: TestClient, url: str) -> None:
    response = client.post("/v1/dry-run", json={"gateway_url": url})
    assert response.status_code == 403
    assert response.json()["error"] == "cloud_litellm_denied"
    assert response.json()["network"] == "not_contacted"


def test_live_spend_requires_the_env_value_exactly() -> None:
    locked = TestClient(create_app(ROOT, environ={}))
    response = locked.post("/v1/dry-run", json={"gateway_url": "http://127.0.0.1:4000", "live": True})
    assert response.status_code == 403
    assert response.json()["error"] == "live_spend_locked"
    assert response.json()["executed"] is False

    almost = TestClient(create_app(ROOT, environ={"OHBE_ALLOW_LIVE_SPEND": "true"}))
    response = almost.post("/v1/dry-run", json={"gateway_url": "http://127.0.0.1:4000", "live": True})
    assert response.status_code == 403
    assert response.json()["error"] == "live_spend_locked"


def test_live_flag_and_env_still_do_not_execute_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)
    client = TestClient(create_app(ROOT, environ={"OHBE_ALLOW_LIVE_SPEND": "1"}))
    response = client.post(
        "/v1/dry-run",
        json={"gateway_url": "http://ohbe-gateway:4000", "live": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False
    assert body["spend"] == "none"
    assert body["network"] == "not_contacted"
    assert body["live_requested"] is True
    assert any(item["id"] == "live_not_executed" for item in body["checks"])


def test_provider_gateway_and_invalid_bodies_do_not_echo_secrets(client: TestClient) -> None:
    provider = client.post("/v1/dry-run", json={"gateway_url": "https://api.openai.com/v1/chat/completions"})
    assert provider.status_code == 403
    assert provider.json()["error"] == "gateway_rejected"
    assert provider.json()["network"] == "not_contacted"

    invalid = client.post(
        "/v1/dry-run",
        json={"gateway_url": "http://ohbe-gateway:4000", "live": "true", "api_key": "sk-test-secret"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"] == "invalid_request"
    assert "sk-test-secret" not in invalid.text


def test_missing_catalog_returns_503_without_spend(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path, environ={}))
    response = client.get("/v1/experiments")
    assert response.status_code == 503
    assert response.json()["error"] == "catalog_unavailable"
    assert response.json()["spend"] == "none"


def test_importing_the_api_does_not_load_provider_sdks() -> None:
    script = (
        "import sys\n"
        "import ohbe.api\n"
        "banned = [name for name in sys.modules if name.split('.', 1)[0] in {'litellm', 'inspect_ai'}]\n"
        "raise SystemExit(0 if not banned else 1)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], check=False, cwd=ROOT)
    assert result.returncode == 0


def _live_report(task_id: str = "echo") -> dict[str, object]:
    return {
        "kind": "ohbe.report",
        "label": "supplied-input",
        "spend": "as_supplied",
        "audit_passed": True,
        "observed_rule": "Observed results only. Report order is not a ranking. Oracle scoring is not computed.",
        "routing_optimality": 1,
        "model_independence": 1,
        "oracle": {"score": 1},
        "tasks": [
            {
                "task_id": task_id,
                "task_content_hash": "abc",
                "note": "observed",
                "harnesses": [
                    {
                        "harness_id": "openbot",
                        "success_rate": "1.000000",
                        "success_count": 1,
                        "valid_count": 1,
                        "invalid_count": 0,
                        "task_failure_count": 0,
                        "total_model_cost": None,
                        "cost_status": "invalid",
                        "usage_status": "missing",
                        "total_tokens": None,
                        "label": "supplied-input",
                        "complete": True,
                    }
                ],
            }
        ],
        "release_gates": [
            {
                "gate_id": "missing_usage_not_zero",
                "passed": True,
                "summary": "Missing token usage stays null.",
            }
        ],
    }


def _write_report(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def test_live_report_on_disk_is_listed_and_opened(tmp_path: Path) -> None:
    fixture = tmp_path / "results" / "fixture-pilot"
    fixture.mkdir(parents=True)
    shutil.copy(ROOT / "results" / "fixture-pilot" / "report.json", fixture / "report.json")
    _write_report(tmp_path / "results" / "live" / "live-pilot-smoke" / "report.json", _live_report("echo"))
    _write_report(
        tmp_path / "results" / "live" / "nested-smoke" / "report" / "report.json",
        _live_report("nested-echo"),
    )
    both = _live_report("direct-echo")
    nested_both = _live_report("nested-loses")
    _write_report(tmp_path / "results" / "live" / "both-smoke" / "report.json", both)
    _write_report(tmp_path / "results" / "live" / "both-smoke" / "report" / "report.json", nested_both)
    _write_report(tmp_path / "results" / "live" / "fixture-pilot" / "report.json", _live_report("shadow"))
    (tmp_path / "results" / "live" / "README.md").write_text("not a report", encoding="utf-8")
    (tmp_path / "results" / "live" / "notes-only").mkdir()
    broken = tmp_path / "results" / "live" / "broken-smoke"
    broken.mkdir()
    (broken / "report.json").write_text("{", encoding="utf-8")
    outside = tmp_path / "secret-report.json"
    outside.write_text(json.dumps(_live_report("SECRET-OUTSIDE")), encoding="utf-8")
    escaped = tmp_path / "results" / "live" / "escape-smoke"
    escaped.mkdir()
    (escaped / "report.json").symlink_to(outside)
    linked = tmp_path / "outside-live"
    linked.mkdir()
    _write_report(linked / "report.json", _live_report("LINKED-OUTSIDE"))
    (tmp_path / "results" / "live" / "linked-smoke").symlink_to(linked, target_is_directory=True)

    client = TestClient(create_app(tmp_path, environ={}))
    listed = client.get("/v1/results")
    assert listed.status_code == 200
    rows = listed.json()["results"]
    assert [row["result_id"] for row in rows] == ["fixture-pilot", "both-smoke", "live-pilot-smoke", "nested-smoke"]
    assert rows[0]["source_kind"] == "fixture"
    assert rows[0]["spend"] == "not_live"
    assert rows[0]["source"] == "results/fixture-pilot/report.json"
    live_row = next(row for row in rows if row["result_id"] == "live-pilot-smoke")
    assert live_row["source_kind"] == "live"
    assert live_row["label"] == "supplied-input"
    assert live_row["spend"] == "as_supplied"
    assert live_row["source"] == "results/live/live-pilot-smoke/report.json"
    assert live_row["task_count"] == 1
    assert "SECRET-OUTSIDE" not in listed.text
    assert "LINKED-OUTSIDE" not in listed.text

    opened = client.get("/v1/results/live-pilot-smoke")
    assert opened.status_code == 200
    body = opened.json()
    assert body["source_kind"] == "live"
    assert body["result_id"] == "live-pilot-smoke"
    assert body["tasks"][0]["task_id"] == "echo"
    harness = body["tasks"][0]["harnesses"][0]
    assert harness["usage_status"] == "missing"
    assert harness["total_tokens"] is None
    assert harness["total_model_cost"] is None
    assert "routing_optimality" not in body
    assert "model_independence" not in body
    assert "oracle" not in body
    assert "SECRET-OUTSIDE" not in opened.text

    nested = client.get("/v1/results/nested-smoke")
    assert nested.status_code == 200
    assert nested.json()["source"] == "results/live/nested-smoke/report/report.json"
    assert nested.json()["tasks"][0]["task_id"] == "nested-echo"
    direct = client.get("/v1/results/both-smoke")
    assert direct.json()["source"] == "results/live/both-smoke/report.json"
    assert direct.json()["tasks"][0]["task_id"] == "direct-echo"
    fixture_row = client.get("/v1/results/fixture-pilot").json()
    assert fixture_row["source_kind"] == "fixture"
    assert fixture_row["source"] == "results/fixture-pilot/report.json"
    assert client.get("/v1/results/broken-smoke").status_code == 503
    assert client.get("/v1/results/escape-smoke").status_code == 404
    assert client.get("/v1/results/linked-smoke").status_code == 404
    assert client.get("/v1/results/not-a-report").status_code == 404

    status = client.get("/v1/status").json()
    assert status["live_spend"] == "locked"
    assert status["spend_execution"] == "disabled"
    locked = client.post("/v1/dry-run", json={"gateway_url": "http://127.0.0.1:4000", "live": True})
    assert locked.status_code == 403
    assert locked.json()["error"] == "live_spend_locked"
    assert locked.json()["executed"] is False

    unlocked_env = TestClient(create_app(tmp_path, environ={"OHBE_ALLOW_LIVE_SPEND": "1"}))
    still = unlocked_env.get("/v1/results/live-pilot-smoke")
    assert still.status_code == 200
    assert still.json()["source_kind"] == "live"
    flagged = unlocked_env.get("/v1/status").json()
    assert flagged["live_spend"] == "env_set"
    assert flagged["spend_execution"] == "disabled"
    intake = unlocked_env.post(
        "/v1/intake/cloud",
        json={
            "display_name": "GPT",
            "model_id": "openai/gpt-4o",
            "litellm_model": "openai/gpt-4o",
            "provider": "openai",
            "api_key": "sk-intake-secret-9f3a",
            "gateway_url": "http://ohbe-gateway:4000",
            "probe": "health",
            "live": True,
        },
    )
    assert intake.status_code == 403
    assert intake.json()["error"] == "live_spend_locked"
    assert intake.json()["executed"] is False
    assert "sk-intake-secret-9f3a" not in intake.text


def test_workbench_client_binds_the_public_routes() -> None:
    text = WORKBENCH_CLIENT.read_text(encoding="utf-8")
    for route in SURFACE_ROUTES:
        if "{" in route["path"]:
            continue
        assert route["path"] in text
    assert post_json.__name__ == "post_json"
