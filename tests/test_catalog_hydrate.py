"""Disk catalog files and comparison slots share one admitted set.

Restart loads ``models/catalog/*.yaml`` back into the intake catalog. Generate
binds ``pool-admitted`` to those slot ids. Confirm copies the same list onto
the plan and suite. A dry-run job still dispatches the fixture pool, is
labeled Dry-run, and does not spend.
"""

from __future__ import annotations

import shutil
import socket
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ohbe.api.app import create_app
from ohbe.api.harnesses import StaticHarnessDetector
from ohbe.api.rounds import POOL_MODELS

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-hydrate-secret"
GATEWAY = "http://ohbe-gateway:4000"
LAB = {
    "model_id": "vllm/lab",
    "provider": "vllm",
    "name": "lab",
    "revision": "2026.04",
    "litellm_model": "hosted_vllm/lab",
    "modality": "code",
    "strengths": ["code"],
    "placement": "local",
}
QWEN = {
    "model_id": "ollama/qwen",
    "provider": "ollama",
    "name": "qwen",
    "revision": "2026.04",
    "litellm_model": "ollama/qwen",
    "modality": "text",
    "strengths": ["reasoning"],
    "placement": "gateway",
}


class ScriptedProbe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        del timeout, bearer
        self.calls.append(url)
        return 200, {"status": "healthy"}


class BoomProbe:
    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        del timeout, bearer
        raise AssertionError(f"probe invoked for {url}")


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


def _fixture_root(tmp_path: Path) -> Path:
    pricing = tmp_path / "pricing"
    pricing.mkdir()
    shutil.copy(ROOT / "pricing" / "ohbe-offline-2026-10-09.yaml", pricing / "ohbe-offline-2026-10-09.yaml")
    report_dir = tmp_path / "results" / "fixture-pilot"
    report_dir.mkdir(parents=True)
    shutil.copy(ROOT / "results" / "fixture-pilot" / "report.json", report_dir / "report.json")
    return tmp_path


def _client(root: Path, probe: ScriptedProbe | BoomProbe | None = None) -> TestClient:
    return TestClient(
        create_app(
            root,
            environ={},
            probe=probe,
            harness_detector=StaticHarnessDetector({"openbot": "fixture", "inspect-react": "0.3.277"}),
        )
    )


def _plan() -> dict[str, object]:
    return {
        "name": "Slot confirm",
        "seed": 7,
        "rounds": 2,
        "mode": "cross_model",
        "harness": {
            "harness_id": "openbot",
            "name": "OpenBot",
            "version": "fixture",
            "parameters": {"profile": "litellm-only"},
        },
        "pool_id": "pool-pilot",
        "difficulty_mix": [{"difficulty": "easy", "weight": 1}],
        "capabilities": ["echo"],
        "timeout_seconds": 30,
        "live": False,
    }


def test_catalog_files_hydrate_after_restart_and_confirm_copies_unified_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network(monkeypatch)
    root = _fixture_root(tmp_path)
    catalog = root / "models" / "catalog"
    catalog.mkdir(parents=True)
    (catalog / "bad.yaml").write_text(f"token: {SECRET}\n", encoding="utf-8")

    first = _client(root, BoomProbe())
    saved = first.post("/v1/model-catalog", json=LAB)
    assert saved.status_code == 200
    assert SECRET not in saved.text
    text = (catalog / "vllm__lab.yaml").read_text(encoding="utf-8")
    assert "live_run: rejected" in text
    assert SECRET not in text
    assert "://" not in text
    qwen = first.post("/v1/model-catalog", json=QWEN)
    assert qwen.status_code == 200
    admitted = first.get("/v1/catalog/models").json()["models"]
    assert [item["model_id"] for item in admitted] == ["vllm/lab", "ollama/qwen"]
    by_id = {item["model_id"]: item for item in admitted}
    for model_id in ("ollama/qwen", "vllm/lab"):
        row = by_id[model_id]
        assert row["accepted"] is True
        assert row["catalog_member"] is True
        assert row["credential"] == "not_applicable"
        assert row["credential_fingerprint"] is None
        assert row["benchmark_unlocked"] is False
        assert row["spend"] == "none"
        assert row["executed"] is False
        assert row["network"] == "not_contacted"
        assert row["policy_checks"][0]["id"] == "catalog_hydrated"
    assert SECRET not in first.get("/v1/catalog/models").text
    assert SECRET not in first.get("/v1/model-catalog").text
    assert not (root / "models" / "pools").exists()

    restarted = _client(root, BoomProbe())
    again = restarted.get("/v1/catalog/models").json()["models"]
    assert {item["model_id"] for item in again} == {"ollama/qwen", "vllm/lab"}
    assert {item["model_id"]: item["record_id"] for item in again} == {
        item["model_id"]: item["record_id"] for item in admitted
    }
    residual = restarted.get("/v1/rounds/comparison-slots").json()
    assert residual["model_ids"] == []
    assert residual["source"] == "round_comparison_slots"
    unscoped = restarted.post("/v1/rounds/comparison-slots", json={"model_ids": ["vllm/lab"]})
    assert unscoped.status_code == 409
    assert unscoped.json()["error"] == "slots_unscoped"
    assert restarted.get("/v1/rounds/comparison-slots").json()["model_ids"] == []
    duplicate = restarted.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": ["vllm/lab", "vllm/lab"]})
    assert duplicate.status_code == 422
    missing = restarted.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": ["openai/gpt-test"]})
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_in_catalog"
    too_many = restarted.put(
        "/v1/rounds/plans/fixture-paired/slots",
        json={"model_ids": [f"vllm/n{index}" for index in range(17)]},
    )
    assert too_many.status_code == 422
    chosen = ["ollama/qwen", "vllm/lab"]
    fixture_blocked = restarted.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": chosen})
    assert fixture_blocked.status_code == 409
    assert fixture_blocked.json()["error"] == "fixture_pool"
    paired_slots = restarted.get("/v1/rounds/plans/fixture-paired/slots").json()
    assert paired_slots["model_ids"] == []
    assert paired_slots["status"] == "draft"
    assert paired_slots["source"] == "plan_slots"
    assert paired_slots["spend"] == "none"

    created = restarted.post("/v1/rounds/plans", json={**_plan(), "model_ids": chosen})
    assert created.status_code == 200
    plan_id = created.json()["plan"]["plan_id"]
    assert created.json()["comparison_model_ids"] == chosen
    assert created.json()["slots_status"] == "draft"
    assert created.json()["plan"]["model_pool_ref"]["pool_id"] == "pool-admitted"
    assert created.json()["pool_kind"] == "admitted"
    assert created.json()["pool_model_ids"] == chosen
    assert created.json()["spend"] == "none"
    assert "pool-pilot" not in created.json()["plan"]["model_pool_ref"]["pool_id"]
    drafted = restarted.post(f"/v1/rounds/plans/{plan_id}/suite", json={"live": False})
    assert drafted.status_code == 200
    assert drafted.json()["comparison_model_ids"] == chosen
    assert drafted.json()["slots_status"] == "draft"
    assert drafted.json()["suite"]["status"] == "draft"

    locked = restarted.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": True, "model_ids": chosen})
    assert locked.status_code == 403
    assert locked.json()["error"] == "live_spend_locked"
    assert restarted.get(f"/v1/rounds/plans/{plan_id}").json()["lifecycle"] == "draft_plan"

    mismatch = restarted.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": False, "model_ids": ["vllm/lab"]})
    assert mismatch.status_code == 409
    assert mismatch.json()["error"] == "slot_mismatch"
    assert restarted.get(f"/v1/rounds/plans/{plan_id}").json()["comparison_model_ids"] == chosen
    assert restarted.get(f"/v1/rounds/plans/{plan_id}").json()["slots_status"] == "draft"

    confirmed = restarted.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": False, "model_ids": chosen})
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["comparison_model_ids"] == chosen
    assert body["slots_status"] == "frozen"
    assert body["suite"]["status"] == "confirmed"
    assert body["spend"] == "none"
    assert body["executed"] is False
    assert body["network"] == "not_contacted"
    assert body["lifecycle"] == "confirmed_suite"
    detail = restarted.get(f"/v1/rounds/plans/{plan_id}").json()
    assert detail["comparison_model_ids"] == chosen
    assert detail["slots_status"] == "frozen"
    assert detail["plan"]["model_pool_ref"]["pool_id"] == "pool-admitted"
    assert detail["pool_model_ids"] == chosen
    assert detail["plan"]["live_spend"] == "locked"
    suite = restarted.get(f"/v1/rounds/suites/{body['suite']['suite_id']}").json()
    assert suite["comparison_model_ids"] == chosen
    assert suite["slots_status"] == "frozen"

    other = restarted.post("/v1/rounds/plans", json={**_plan(), "name": "Other", "model_ids": ["vllm/lab"]})
    assert other.status_code == 200
    other_id = other.json()["plan"]["plan_id"]
    moved = restarted.put(f"/v1/rounds/plans/{other_id}/slots", json={"model_ids": ["ollama/qwen"]})
    assert moved.status_code == 200
    assert moved.json()["model_ids"] == ["ollama/qwen"]
    assert moved.json()["status"] == "draft"
    assert restarted.get(f"/v1/rounds/plans/{plan_id}/slots").json()["model_ids"] == chosen
    frozen_put = restarted.put(f"/v1/rounds/plans/{plan_id}/slots", json={"model_ids": ["vllm/lab"]})
    assert frozen_put.status_code == 409
    assert frozen_put.json()["error"] == "slots_frozen"
    cleared = restarted.post("/v1/rounds/comparison-slots", json={"model_ids": []})
    assert cleared.status_code == 409
    assert cleared.json()["error"] == "slots_unscoped"
    rewritten = restarted.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": False, "model_ids": []})
    assert rewritten.status_code == 409
    assert rewritten.json()["error"] == "slot_mismatch"
    frozen = restarted.post(f"/v1/rounds/plans/{plan_id}/confirm", json={"live": False})
    assert frozen.status_code == 200
    assert frozen.json()["comparison_model_ids"] == chosen
    assert frozen.json()["slots_status"] == "frozen"
    assert frozen.json()["suite"]["suite_hash"] == body["suite"]["suite_hash"]
    frozen_plan = restarted.get(f"/v1/rounds/plans/{plan_id}").json()
    assert frozen_plan["pool_model_ids"] == chosen
    assert frozen_plan["plan"]["plan_hash"] == detail["plan"]["plan_hash"]
    assert frozen_plan["comparison_model_ids"] == chosen
    assert frozen_plan["slots_status"] == "frozen"

    started = restarted.post(f"/v1/rounds/plans/{plan_id}/start", json={"live": False})
    assert started.status_code == 200
    start_body = started.json()
    assert start_body["executed"] is False
    assert start_body["spend"] == "none"
    assert start_body["network"] == "not_contacted"
    assert start_body["label"] == "Dry-run"
    assert start_body["fixture_pool_id"] == "pool-pilot"
    assert start_body["job"]["model_ids"] == list(POOL_MODELS)
    assert "Dry-run" in start_body["job"]["note"]
    assert "vllm/lab" not in start_body["job"]["model_ids"]
    assert "ollama/qwen" not in start_body["job"]["model_ids"]
    assert not (root / "models" / "pools").exists()
    health = restarted.get("/v1/health").json()
    assert health["live_spend"] == "locked"
    assert health["spend_execution"] == "disabled"
    assert health["cloud_litellm"] == "fail_closed"
    assert SECRET not in started.text


def test_cloud_provider_row_without_a_catalog_file_does_not_survive_restart(tmp_path: Path) -> None:
    probe = ScriptedProbe()
    first = TestClient(create_app(tmp_path, environ={}, probe=probe))
    admitted = first.post(
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
    assert probe.calls == [f"{GATEWAY}/v1/models"]
    assert not (tmp_path / "models" / "catalog").exists()

    _fixture_root(tmp_path)
    restarted = TestClient(create_app(tmp_path, environ={}, probe=BoomProbe()))
    assert restarted.get("/v1/catalog/models").json()["models"] == []
    blocked = restarted.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": ["openai/gpt-4o"]})
    assert blocked.status_code == 404
    assert blocked.json()["error"] == "not_in_catalog"
    assert restarted.post("/v1/rounds/comparison-slots", json={"model_ids": ["openai/gpt-4o"]}).status_code == 409
    assert SECRET not in blocked.text
