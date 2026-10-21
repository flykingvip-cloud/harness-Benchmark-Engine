"""Harness install inventory. Detection and selection stay version-gated."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.adapters.inspect_react.plan import INSPECT_AI_VERSION
from ohbe.api.app import create_app
from ohbe.api.harnesses import OPENCLAW_VERSION, HostHarnessDetector, StaticHarnessDetector
from ohbe.live.spec import HARNESS_ORDER

ROOT = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"


def _client(found: dict[str, str] | None = None) -> TestClient:
    return TestClient(create_app(ROOT, environ={}, harness_detector=StaticHarnessDetector(found or {})))


def _row(body: dict[str, object], harness_id: str) -> dict[str, object]:
    rows = body["harnesses"]
    assert isinstance(rows, list)
    found = next(item for item in rows if item["harness_id"] == harness_id)
    assert isinstance(found, dict)
    return found


def _plan(harness_id: str, name: str, version: str) -> dict[str, object]:
    return {
        "name": "Session round",
        "seed": 7,
        "rounds": 2,
        "mode": "cross_model",
        "harness": {
            "harness_id": harness_id,
            "name": name,
            "version": version,
            "parameters": {"profile": "litellm-only"},
        },
        "pool_id": "pool-pilot",
        "difficulty_mix": [{"difficulty": "easy", "weight": 1}],
        "capabilities": ["echo"],
        "timeout_seconds": 30,
        "live": False,
    }


def test_empty_host_lists_the_registry_and_keeps_live_order() -> None:
    body = _client().get("/v1/catalog/harnesses").json()
    assert [item["harness_id"] for item in body["harnesses"]] == ["openbot", "inspect-react", "openclaw"]
    assert [item["presence"] for item in body["harnesses"]] == ["absent", "absent", "absent"]
    assert body["plans_whitelist"] == []
    assert body["live_whitelist"] == []
    assert body["live_order"] == ["openbot", "inspect_react"]
    assert body["live_order"] == list(HARNESS_ORDER)
    assert body["spend"] == "none"
    assert _row(body, "inspect-react")["allowed_versions"] == [INSPECT_AI_VERSION]
    assert _row(body, "openclaw")["allowed_versions"] == [OPENCLAW_VERSION]
    assert _row(body, "openclaw")["live_name"] is None


def test_allowed_detection_is_preinstalled_without_a_post() -> None:
    body = _client({"inspect-react": INSPECT_AI_VERSION, "openbot": "fixture"}).get("/v1/catalog/harnesses").json()
    inspect = _row(body, "inspect-react")
    assert inspect["presence"] == "preinstalled"
    assert inspect["detected_version"] == INSPECT_AI_VERSION
    assert inspect["plans_whitelisted"] is True
    assert inspect["live_whitelisted"] is True
    assert inspect["whitelist_version"] == INSPECT_AI_VERSION
    assert body["plans_whitelist"] == ["openbot", "inspect-react"]
    assert body["live_whitelist"] == ["openbot", "inspect_react"]


def test_rejected_detection_stays_off_the_whitelist() -> None:
    body = _client({"inspect-react": "9.9.9"}).get("/v1/catalog/harnesses").json()
    inspect = _row(body, "inspect-react")
    assert inspect["presence"] == "version_rejected"
    assert inspect["detected_version"] == "9.9.9"
    assert inspect["plans_whitelisted"] is False
    assert inspect["live_whitelisted"] is False
    assert inspect["whitelist_version"] is None
    assert body["plans_whitelist"] == []
    assert body["live_whitelist"] == []


def test_selection_order_does_not_reorder_live() -> None:
    client = _client()
    saved = client.post(
        "/v1/catalog/harnesses",
        json={
            "selections": [
                {"harness_id": "inspect-react", "version": INSPECT_AI_VERSION},
                {"harness_id": "openbot", "version": "fixture"},
            ]
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["plans_whitelist"] == ["openbot", "inspect-react"]
    assert body["live_whitelist"] == ["openbot", "inspect_react"]
    assert body["live_order"] == list(HARNESS_ORDER)


def test_openclaw_enters_plans_and_not_live() -> None:
    client = _client()
    saved = client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openclaw", "version": OPENCLAW_VERSION}]},
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["plans_whitelist"] == ["openclaw"]
    assert body["live_whitelist"] == []
    assert _row(body, "openclaw")["plans_whitelisted"] is True
    assert _row(body, "openclaw")["live_whitelisted"] is False
    assert _row(body, "openclaw")["presence"] == "absent"
    assert body["live_order"] == list(HARNESS_ORDER)


def test_unknown_harness_does_not_change_state() -> None:
    client = _client()
    kept = client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openclaw", "version": OPENCLAW_VERSION}]},
    )
    assert kept.status_code == 200
    refused = client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "custom-loop", "version": "0"}]},
    )
    assert refused.status_code == 403
    assert refused.json()["error"] == "harness_not_registered"
    again = client.get("/v1/catalog/harnesses").json()
    assert again["plans_whitelist"] == ["openclaw"]


def test_bad_version_does_not_change_state() -> None:
    client = _client()
    client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openbot", "version": "fixture"}]},
    )
    refused = client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openbot", "version": "preinstalled"}]},
    )
    assert refused.status_code == 422
    assert refused.json()["error"] == "version_not_allowed"
    assert client.get("/v1/catalog/harnesses").json()["plans_whitelist"] == ["openbot"]


def test_duplicate_selection_is_rejected() -> None:
    response = _client().post(
        "/v1/catalog/harnesses",
        json={
            "selections": [
                {"harness_id": "openbot", "version": "fixture"},
                {"harness_id": "openbot", "version": SHA},
            ]
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_empty_selection_clears_choices_and_keeps_detected_installs() -> None:
    client = _client({"openbot": "fixture"})
    selected = client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "inspect-react", "version": INSPECT_AI_VERSION}]},
    )
    assert selected.json()["plans_whitelist"] == ["openbot", "inspect-react"]
    cleared = client.post("/v1/catalog/harnesses", json={"selections": []})
    assert cleared.status_code == 200
    body = cleared.json()
    assert body["plans_whitelist"] == ["openbot"]
    assert body["live_whitelist"] == ["openbot"]
    assert _row(body, "openbot")["presence"] == "preinstalled"
    assert _row(body, "inspect-react")["selected_version"] is None
    assert _row(body, "inspect-react")["plans_whitelisted"] is False


def test_openbot_accepts_a_git_sha_and_a_selected_sha_replaces_the_detected_pin() -> None:
    client = _client({"openbot": "fixture"})
    saved = client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openbot", "version": SHA}]},
    )
    assert saved.status_code == 200
    row = _row(saved.json(), "openbot")
    assert row["presence"] == "preinstalled"
    assert row["detected_version"] == "fixture"
    assert row["whitelist_version"] == SHA
    created = client.post("/v1/rounds/plans", json=_plan("openbot", "OpenBot", SHA))
    assert created.status_code == 200
    refused = client.post("/v1/rounds/plans", json=_plan("openbot", "OpenBot", "fixture"))
    assert refused.status_code == 403
    assert refused.json()["error"] == "harness_not_whitelisted"


def test_plan_create_requires_the_whitelist_version_and_registered_name() -> None:
    client = _client()
    blocked = client.post("/v1/rounds/plans", json=_plan("openbot", "OpenBot", "fixture"))
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "harness_not_whitelisted"
    client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openbot", "version": "fixture"}]},
    )
    mismatch = client.post("/v1/rounds/plans", json=_plan("openbot", "Other", "fixture"))
    assert mismatch.status_code == 422
    created = client.post("/v1/rounds/plans", json=_plan("openbot", "OpenBot", "fixture"))
    assert created.status_code == 200
    assert created.json()["plan"]["harness_ref"]["version"] == "fixture"
    client.post(
        "/v1/catalog/harnesses",
        json={"selections": [{"harness_id": "openclaw", "version": OPENCLAW_VERSION}]},
    )
    claw = client.post("/v1/rounds/plans", json=_plan("openclaw", "OpenClaw", OPENCLAW_VERSION))
    assert claw.status_code == 200


def test_a_raising_detector_leaves_every_row_absent() -> None:
    class Boom:
        def detect(self) -> dict[str, str]:
            raise RuntimeError("disk")

    client = TestClient(create_app(ROOT, environ={}, harness_detector=Boom()))
    body = client.get("/v1/catalog/harnesses").json()
    assert [item["presence"] for item in body["harnesses"]] == ["absent", "absent", "absent"]
    assert body["plans_whitelist"] == []


def test_host_detector_prefers_env_and_survives_a_failing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="openbot", timeout=1)

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/openbot")
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(
        "ohbe.adapters.inspect_react.version.installed_inspect_version",
        lambda: None,
    )
    found = HostHarnessDetector(environ={"OHBE_HARNESS_OPENCLAW_VERSION": OPENCLAW_VERSION}).detect()
    assert found == {"openclaw": OPENCLAW_VERSION}


def test_host_detector_parses_a_commit_from_version_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name: str) -> str | None:
        return "/usr/bin/openbot" if name == "openbot" else None

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 1
        assert argv == ["/usr/bin/openbot", "--version"]
        return subprocess.CompletedProcess(argv, 0, stdout=f"openbot {SHA}\n", stderr="")

    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        "ohbe.adapters.inspect_react.version.installed_inspect_version",
        lambda: None,
    )
    assert HostHarnessDetector(environ={}).detect() == {"openbot": SHA}


def test_inventory_doc_keeps_live_order_and_the_preinstalled_mark() -> None:
    text = (ROOT / "docs" / "model-intake.md").read_text(encoding="utf-8")
    assert "已预装" in text
    assert "HARNESS_ORDER" in text
    assert "openclaw" in text
    assert "harness_not_registered" in text
    assert "version_not_allowed" in text
    assert "harness_not_whitelisted" in text
