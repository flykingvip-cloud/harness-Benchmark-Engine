"""Generate and confirm bind the admitted catalog. Dry-run jobs stay labeled."""

from __future__ import annotations

import shutil
import socket
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ohbe.api.admitted_pool import ADMITTED_POOL_ID, DRY_RUN_LABEL, FIXTURE_POOL_ID
from ohbe.api.app import create_app
from ohbe.api.harnesses import StaticHarnessDetector
from ohbe.api.rounds import POOL_MODELS

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-real-pool-secret"
GATEWAY = "http://ohbe-gateway:4000"


class ScriptedProbe:
    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        del timeout, bearer
        assert url == f"{GATEWAY}/v1/models"
        return 200, {"data": []}


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


def _client(probe: ScriptedProbe | None = None) -> TestClient:
    return TestClient(
        create_app(
            ROOT,
            environ={},
            probe=probe,
            harness_detector=StaticHarnessDetector({"openbot": "fixture", "inspect-react": "0.3.277"}),
        )
    )


def _plan(pool_id: str = FIXTURE_POOL_ID, model_ids: list[str] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Admitted round",
        "seed": 7,
        "rounds": 1,
        "mode": "cross_model",
        "harness": {
            "harness_id": "openbot",
            "name": "OpenBot",
            "version": "fixture",
            "parameters": {"profile": "litellm-only"},
        },
        "pool_id": pool_id,
        "difficulty_mix": [{"difficulty": "easy", "weight": 1}],
        "capabilities": ["echo"],
        "timeout_seconds": 30,
        "live": False,
    }
    if model_ids is not None:
        payload["model_ids"] = model_ids
    return payload


def test_generate_and_confirm_use_the_admitted_pool_and_jobs_stay_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network(monkeypatch)
    client = _client(ScriptedProbe())
    admitted = client.post(
        "/v1/intake/cloud",
        json={
            "display_name": "GPT",
            "model_id": "openai/gpt-4o",
            "litellm_model": "openai/gpt-4o",
            "provider": "openai",
            "api_key": SECRET,
            "gateway_url": GATEWAY,
            "probe": "health",
            "live": False,
        },
    )
    assert admitted.status_code == 200
    assert admitted.json()["accepted"] is True
    assert SECRET not in admitted.text
    assert not (ROOT / "models" / "pools" / "pool-admitted.yaml").exists()

    empty = client.post("/v1/rounds/plans", json=_plan(ADMITTED_POOL_ID))
    assert empty.status_code == 409
    assert empty.json()["error"] == "slots_required"
    assert empty.json()["spend"] == "none"
    assert client.post("/v1/rounds/comparison-slots", json={"model_ids": ["openai/gpt-4o"]}).status_code == 409

    chosen = ["openai/gpt-4o"]
    mismatch = client.post("/v1/rounds/plans", json=_plan(FIXTURE_POOL_ID, ["openai/other"]))
    assert mismatch.status_code == 404
    assert mismatch.json()["error"] == "not_in_catalog"
    assert len(client.get("/v1/rounds/plans").json()["plans"]) == 2

    created = client.post("/v1/rounds/plans", json=_plan(FIXTURE_POOL_ID, chosen))
    assert created.status_code == 200
    body = created.json()
    plan_id = body["plan"]["plan_id"]
    assert body["pool_kind"] == "admitted"
    assert body["pool_model_ids"] == chosen
    assert body["plan"]["model_pool_ref"]["pool_id"] == ADMITTED_POOL_ID
    assert body["plan"]["model_pool_ref"]["pool_id"] != FIXTURE_POOL_ID
    assert body["comparison_model_ids"] == chosen
    assert body["slots_status"] == "draft"
    assert body["plan"]["live_spend"] == "locked"
    assert body["spend"] == "none"
    assert SECRET not in created.text
    first_hash = body["plan"]["model_pool_ref"]["content_hash"]

    again = client.post("/v1/rounds/plans", json=_plan(ADMITTED_POOL_ID, chosen))
    assert again.status_code == 200
    assert again.json()["plan"]["model_pool_ref"]["content_hash"] == first_hash
    assert again.json()["comparison_model_ids"] == chosen

    drafted = client.post(f"/v1/rounds/plans/{plan_id}/suite", json={"live": False})
    assert drafted.status_code == 200
    suite_hash = drafted.json()["suite"]["suite_hash"]

    confirmed = client.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": False, "model_ids": chosen})
    assert confirmed.status_code == 200
    assert confirmed.json()["comparison_model_ids"] == chosen
    assert confirmed.json()["slots_status"] == "frozen"
    assert confirmed.json()["suite"]["status"] == "confirmed"
    assert confirmed.json()["spend"] == "none"
    frozen_hash = confirmed.json()["suite"]["suite_hash"]
    assert frozen_hash != suite_hash

    assert client.put(f"/v1/rounds/plans/{plan_id}/slots", json={"model_ids": []}).status_code == 409
    reconfirmed = client.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": False})
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["comparison_model_ids"] == chosen
    assert reconfirmed.json()["slots_status"] == "frozen"
    assert reconfirmed.json()["suite"]["suite_hash"] == frozen_hash
    detail = client.get(f"/v1/rounds/plans/{plan_id}").json()
    assert detail["pool_model_ids"] == chosen
    assert detail["comparison_model_ids"] == chosen
    assert detail["slots_status"] == "frozen"
    assert detail["plan"]["model_pool_ref"]["content_hash"] == first_hash
    assert detail["plan"]["live_spend"] == "locked"

    started = client.post(f"/v1/rounds/plans/{plan_id}/start", json={"live": False})
    assert started.status_code == 200
    start_body = started.json()
    assert start_body["label"] == DRY_RUN_LABEL
    assert start_body["fixture_pool_id"] == FIXTURE_POOL_ID
    assert start_body["job"]["model_ids"] == list(POOL_MODELS)
    assert "openai/gpt-4o" not in start_body["job"]["model_ids"]
    assert start_body["executed"] is False
    assert start_body["spend"] == "none"
    assert start_body["network"] == "not_contacted"
    assert "Dry-run" in start_body["job"]["note"]
    job_id = start_body["job"]["job_id"]
    viewed = client.get(f"/v1/rounds/jobs/{job_id}")
    assert viewed.json()["label"] == "Dry-run"
    assert viewed.json()["fixture_pool_id"] == "pool-pilot"

    phases = [start_body["job"]["status"]]
    report_id = None
    for _ in range(4):
        ticked = client.post(f"/v1/rounds/jobs/{job_id}/tick", json={})
        assert ticked.status_code == 200
        assert ticked.json()["label"] == "Dry-run"
        phases.append(ticked.json()["job"]["status"])
        report_id = ticked.json()["report_id"]
        if ticked.json()["job"]["status"] == "sealed":
            break
    assert phases[-1] == "sealed"
    report = client.get(f"/v1/rounds/reports/{report_id}").json()["report"]
    assert report["measurement"] == "not_measured"
    assert report["model_metrics"] == []
    assert report["routing_optimality"] is None
    assert report["model_independence"] is None
    assert "not a cost of zero" in report["frontier_note"].lower() or "missing usage is not a cost of zero" in report["frontier_note"].lower()
    forked = client.post(f"/v1/rounds/plans/{plan_id}/fork", json={"name": "Slot fork"})
    assert forked.status_code == 200
    child_id = forked.json()["plan"]["plan_id"]
    assert child_id != plan_id
    assert forked.json()["slots_status"] == "draft"
    assert forked.json()["comparison_model_ids"] == chosen
    assert forked.json()["suite_id"] is None
    assert forked.json()["job_id"] is None
    assert forked.json()["report_id"] is None
    cleared_child = client.put(f"/v1/rounds/plans/{child_id}/slots", json={"model_ids": []})
    assert cleared_child.status_code == 200
    assert cleared_child.json()["model_ids"] == []
    assert cleared_child.json()["status"] == "draft"
    parent_slots = client.get(f"/v1/rounds/plans/{plan_id}/slots").json()
    assert parent_slots["model_ids"] == chosen
    assert parent_slots["status"] == "frozen"
    assert parent_slots["source"] == "plan_slots"
    assert "not computed" in report["findings"].lower()
    health = client.get("/v1/health").json()
    assert health["live_spend"] == "locked"
    assert health["spend_execution"] == "disabled"
    assert health["cloud_litellm"] == "fail_closed"
    assert SECRET not in started.text
    assert SECRET not in viewed.text


def _fixture_root(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    pricing = tmp_path / "pricing"
    pricing.mkdir()
    shutil.copy(ROOT / "pricing" / "ohbe-offline-2026-10-09.yaml", pricing / "ohbe-offline-2026-10-09.yaml")
    report_dir = tmp_path / "results" / "fixture-pilot"
    report_dir.mkdir(parents=True)
    shutil.copy(ROOT / "results" / "fixture-pilot" / "report.json", report_dir / "report.json")
    return tmp_path


def _tmp_client(root: Path) -> TestClient:
    return TestClient(
        create_app(
            root,
            environ={},
            harness_detector=StaticHarnessDetector({"openbot": "fixture", "inspect-react": "0.3.277"}),
        )
    )


def test_confirm_rejects_a_slot_change_and_a_fixture_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _block_network(monkeypatch)
    root = _fixture_root(tmp_path)
    client = _tmp_client(root)
    catalog = {
        "model_id": "vllm/lab",
        "provider": "vllm",
        "name": "lab",
        "revision": "2026.04",
        "litellm_model": "hosted_vllm/lab",
        "modality": "code",
        "strengths": ["code"],
        "placement": "local",
    }
    other = {
        "model_id": "ollama/qwen",
        "provider": "ollama",
        "name": "qwen",
        "revision": "2026.04",
        "litellm_model": "ollama/qwen",
        "modality": "text",
        "strengths": ["reasoning"],
        "placement": "gateway",
    }
    assert client.post("/v1/model-catalog", json=catalog).status_code == 200
    assert client.post("/v1/model-catalog", json=other).status_code == 200
    written = (root / "models" / "catalog" / "vllm__lab.yaml").read_text(encoding="utf-8")
    assert "api_key" not in written
    assert "://" not in written
    assert not (root / "models" / "pools").exists()
    created = client.post("/v1/rounds/plans", json=_plan(FIXTURE_POOL_ID, ["vllm/lab"]))
    assert created.status_code == 200
    plan_id = created.json()["plan"]["plan_id"]
    bound_hash = created.json()["plan"]["model_pool_ref"]["content_hash"]
    assert created.json()["pool_model_ids"] == ["vllm/lab"]
    assert created.json()["comparison_model_ids"] == ["vllm/lab"]
    assert created.json()["slots_status"] == "draft"
    assert client.post(f"/v1/rounds/plans/{plan_id}/suite", json={}).status_code == 200

    replaced = client.put(
        f"/v1/rounds/plans/{plan_id}/slots",
        json={"model_ids": ["ollama/qwen", "vllm/lab"]},
    )
    assert replaced.status_code == 200
    assert replaced.json()["model_ids"] == ["ollama/qwen", "vllm/lab"]
    changed = client.post(
        f"/v1/rounds/plans/{plan_id}/confirm",
        json={"live": False, "model_ids": ["ollama/qwen", "vllm/lab"]},
    )
    assert changed.status_code == 409
    assert changed.json()["error"] == "slot_mismatch"
    assert client.get(f"/v1/rounds/plans/{plan_id}").json()["comparison_model_ids"] == ["ollama/qwen", "vllm/lab"]
    assert client.get(f"/v1/rounds/plans/{plan_id}").json()["slots_status"] == "draft"
    assert client.get(f"/v1/rounds/plans/{plan_id}").json()["lifecycle"] == "draft_plan"
    assert client.get(f"/v1/rounds/plans/{plan_id}").json()["plan"]["model_pool_ref"]["content_hash"] == bound_hash

    swapped = client.post("/v1/rounds/plans", json=_plan(ADMITTED_POOL_ID, ["ollama/qwen"]))
    assert swapped.status_code == 200
    assert swapped.json()["plan"]["model_pool_ref"]["content_hash"] != bound_hash
    assert swapped.json()["pool_model_ids"] == ["ollama/qwen"]
    assert swapped.json()["comparison_model_ids"] == ["ollama/qwen"]
    assert client.get(f"/v1/rounds/plans/{plan_id}/slots").json()["model_ids"] == ["ollama/qwen", "vllm/lab"]

    bare = client.post("/v1/rounds/plans", json=_plan(FIXTURE_POOL_ID))
    assert bare.status_code == 200
    assert bare.json()["pool_kind"] == "fixture"
    assert bare.json()["comparison_model_ids"] == []
    assert bare.json()["slots_status"] == "draft"
    assert bare.json()["plan"]["model_pool_ref"]["pool_id"] == FIXTURE_POOL_ID

    isolated = _tmp_client(_fixture_root(tmp_path / "bare"))
    opened = isolated.post("/v1/rounds/plans", json=_plan(FIXTURE_POOL_ID))
    assert opened.status_code == 200
    assert opened.json()["pool_kind"] == "fixture"
    assert opened.json()["plan"]["model_pool_ref"]["pool_id"] == FIXTURE_POOL_ID
    bare_id = opened.json()["plan"]["plan_id"]
    assert isolated.post(f"/v1/rounds/plans/{bare_id}/suite", json={}).status_code == 200
    assert isolated.post("/v1/model-catalog", json=catalog).status_code == 200
    blocked = isolated.put(f"/v1/rounds/plans/{bare_id}/slots", json={"model_ids": ["vllm/lab"]})
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "fixture_pool"
    refused = isolated.post(f"/v1/rounds/plans/{bare_id}/confirm", json={"live": False, "model_ids": ["vllm/lab"]})
    assert refused.status_code == 409
    assert refused.json()["error"] == "slot_mismatch"
    assert isolated.get(f"/v1/rounds/plans/{bare_id}").json()["comparison_model_ids"] == []
    locked = isolated.post(f"/v1/rounds/plans/{bare_id}/confirm", json={"live": True, "model_ids": ["vllm/lab"]})
    assert locked.status_code == 403
    assert locked.json()["error"] == "live_spend_locked"
    assert isolated.get(f"/v1/rounds/plans/{bare_id}").json()["lifecycle"] == "draft_plan"
    assert isolated.get("/v1/health").json()["live_spend"] == "locked"
    assert isolated.get("/v1/health").json()["spend_execution"] == "disabled"
    assert not (tmp_path / "bare" / "models" / "pools").exists()
