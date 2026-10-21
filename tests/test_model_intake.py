"""Catalog intake needs both gates. Policy failure is saved as pending and does not spend."""

from __future__ import annotations

import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ohbe.api.app import create_app
from ohbe.api.intake import HEALTH_PROBE_PATH, LIST_PROBE_PATH, ProbeError, UrllibProbe, catalog_admission, guard_probe_url
from ohbe.api.schemas import CatalogModel

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-intake-secret-9f3a"
GATEWAY = "http://ohbe-gateway:4000"
LISTING = {
    "data": [
        {"id": "hosted_vllm/qwen2", "object": "model"},
        {"id": "ollama/llama3", "object": "model"},
        {"id": "openai/gpt-4o-mini", "object": "model"},
    ]
}


class ScriptedProbe:
    def __init__(self, status: int = 200, body: Mapping[str, Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.bearers: list[str | None] = []
        self.status = status
        self.body = {} if body is None else body
        self.error = error

    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        self.calls.append(url)
        self.bearers.append(bearer)
        if self.error is not None:
            raise self.error
        return self.status, self.body


class BoomProbe:
    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        raise AssertionError(f"probe invoked for {url}")


def _client(probe: ScriptedProbe | BoomProbe, *, live: str | None = None) -> TestClient:
    environ = {} if live is None else {"OHBE_ALLOW_LIVE_SPEND": live}
    return TestClient(create_app(ROOT, environ=environ, probe=probe))


def _cloud(client: TestClient, **overrides: object) -> Any:
    body: dict[str, object] = {
        "display_name": "GPT",
        "model_id": "openai/gpt-4o",
        "provider": "openai",
        "api_key": SECRET,
        "gateway_url": GATEWAY,
        "probe": "health",
        "live": False,
    }
    body.update(overrides)
    if "litellm_model" not in overrides:
        body["litellm_model"] = body["model_id"]
    return client.post("/v1/intake/cloud", json=body)


def test_catalog_admission_requires_both_gates() -> None:
    assert catalog_admission(gate_ok=True, policy_match=True) == "admitted"
    assert catalog_admission(gate_ok=True, policy_match=False) == "pending"
    assert catalog_admission(gate_ok=False, policy_match=True) == "pending"
    assert catalog_admission(gate_ok=False, policy_match=False) == "pending"


def test_schema_rejects_catalog_entry_when_a_gate_failed() -> None:
    with pytest.raises(ValidationError):
        CatalogModel(
            record_id="rec-1",
            model_id="openai/gpt-4o",
            display_name="GPT",
            provider="openai",
            intake_path="cloud_api_key",
            admission="admitted",
            accepted=True,
            catalog_member=True,
            connectivity_or_handshake=True,
            connectivity="ok",
            handshake="not_applicable",
            policy_match=False,
            policy_checks=[],
            route="rejected",
            gateway_host="ohbe-gateway",
            gateway_port=4000,
            backend="remote",
            reason="policy_mismatch",
            credential="api_key_accepted",
            network="probed",
            scope="connectivity",
        )


def test_cloud_intake_admits_only_when_health_and_policy_match() -> None:
    probe = ScriptedProbe(body={"status": "healthy"})
    client = _client(probe)
    response = _cloud(client)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["admission"] == "admitted"
    assert body["catalog_member"] is True
    assert body["connectivity_or_handshake"] is True
    assert body["policy_match"] is True
    assert body["spend"] == "none"
    assert body["benchmark_unlocked"] is False
    assert body["executed"] is False
    assert body["network"] == "probed"
    assert body["route"] == "litellm_gateway"
    assert probe.calls == [f"{GATEWAY}{LIST_PROBE_PATH}"]
    assert probe.bearers == [SECRET]
    assert HEALTH_PROBE_PATH not in probe.calls[0]
    assert SECRET not in response.text
    assert body["credential_fingerprint"] != SECRET
    catalog = client.get("/v1/catalog/models").json()
    assert [item["model_id"] for item in catalog["models"]] == ["openai/gpt-4o"]
    assert client.get("/v1/intake/pending").json()["models"] == []
    assert client.get("/v1/rounds/comparison-slots").json()["model_ids"] == []
    assert client.get("/v1/pools").json()["pools"][0]["pool_id"] == "pool-pilot"


def test_cloud_policy_failure_is_pending_and_does_not_open_a_probe() -> None:
    client = _client(BoomProbe())
    response = _cloud(client, gateway_url="https://sk-live-secret:pw@API.LiteLLM.ai/v1")
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["admission"] == "pending"
    assert body["catalog_member"] is False
    assert body["policy_match"] is False
    assert body["connectivity"] == "not_run"
    assert body["network"] == "not_contacted"
    assert body["reason"] == "url_userinfo_denied"
    assert body["benchmark_unlocked"] is False
    assert "sk-live-secret" not in response.text
    assert SECRET not in response.text
    assert client.get("/v1/catalog/models").json()["models"] == []
    pending = client.get("/v1/intake/pending").json()["models"]
    assert len(pending) == 1
    assert pending[0]["model_id"] == "openai/gpt-4o"

    denied = _cloud(client, gateway_url="https://api.litellm.ai/v1", model_id="openai/gpt-4o-mini")
    assert denied.json()["reason"] == "cloud_litellm_denied"
    assert denied.json()["accepted"] is False
    provider = _cloud(client, gateway_url="https://api.openai.com/v1", model_id="openai/gpt-4.1")
    assert provider.json()["reason"] == "gateway_rejected"
    assert provider.json()["network"] == "not_contacted"
    completions = _cloud(client, gateway_url=f"{GATEWAY}/v1/chat/completions", model_id="openai/gpt-4.1-mini")
    assert completions.json()["accepted"] is False
    assert completions.json()["reason"] == "gateway_rejected"


def test_cloud_connectivity_failure_stays_pending_when_policy_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket")

    monkeypatch.setattr(socket, "create_connection", blocked)
    probe = ScriptedProbe(error=ProbeError("gateway unreachable"))
    client = _client(probe)
    response = _cloud(client)
    body = response.json()
    assert body["accepted"] is False
    assert body["policy_match"] is True
    assert body["connectivity"] == "failed"
    assert body["reason"] == "connectivity_failed"
    assert body["network"] == "unreachable"
    assert client.get("/v1/catalog/models").json()["models"] == []
    assert client.get("/v1/intake/pending").json()["models"][0]["model_id"] == "openai/gpt-4o"


def test_live_flag_does_not_unlock_benchmarking_or_probe() -> None:
    client = _client(BoomProbe(), live="1")
    response = _cloud(client, live=True)
    assert response.status_code == 403
    assert response.json()["error"] == "live_spend_locked"
    assert response.json()["spend"] == "none"
    assert response.json()["executed"] is False
    assert SECRET not in response.text
    assert client.get("/v1/catalog/models").json()["models"] == []
    assert client.get("/v1/intake/pending").json()["models"] == []


def test_server_scan_lists_local_models_and_handshake_admits_only_policy_matches() -> None:
    probe = ScriptedProbe(body=LISTING)
    client = _client(probe)
    scanned = client.post("/v1/intake/server/scan", json={"gateway_url": f"{GATEWAY}/v1", "probe": "list", "live": False})
    assert scanned.status_code == 200
    body = scanned.json()
    assert body["accepted"] is False
    assert body["catalog_member"] is False
    assert body["admission"] == "listed"
    assert body["policy_match"] is True
    assert body["connectivity"] == "ok"
    assert body["benchmark_unlocked"] is False
    assert probe.calls == [f"{GATEWAY}{LIST_PROBE_PATH}"]
    assert probe.bearers == [None]
    by_id = {item["model_id"]: item for item in body["models"]}
    assert by_id["vllm/qwen2"]["admissible"] is True
    assert by_id["vllm/qwen2"]["policy_match"] is True
    assert by_id["vllm/qwen2"]["litellm_model"] == "hosted_vllm/qwen2"
    assert by_id["ollama/llama3"]["backend"] == "ollama"
    assert by_id["openai/gpt-4o-mini"]["admissible"] is False
    assert by_id["openai/gpt-4o-mini"]["policy_match"] is False
    assert client.get("/v1/catalog/models").json()["models"] == []

    handshake = client.post(
        "/v1/intake/server/handshake",
        json={"scan_id": body["scan_id"], "model_ids": ["vllm/qwen2", "openai/gpt-4o-mini"], "live": False},
    )
    assert handshake.status_code == 200
    results = {item["model_id"]: item for item in handshake.json()["results"]}
    assert results["vllm/qwen2"]["accepted"] is True
    assert results["vllm/qwen2"]["handshake"] == "confirmed"
    assert results["vllm/qwen2"]["policy_match"] is True
    assert results["openai/gpt-4o-mini"]["accepted"] is False
    assert results["openai/gpt-4o-mini"]["admission"] == "pending"
    assert results["openai/gpt-4o-mini"]["reason"] == "local_backend_required"
    assert handshake.json()["benchmark_unlocked"] is False
    assert handshake.json()["network"] == "not_contacted"
    catalog = client.get("/v1/catalog/models").json()["models"]
    assert [item["model_id"] for item in catalog] == ["vllm/qwen2"]
    pending = [item["model_id"] for item in client.get("/v1/intake/pending").json()["models"]]
    assert pending == ["openai/gpt-4o-mini"]
    assert client.get("/v1/rounds/comparison-slots").json()["model_ids"] == []


def test_server_policy_failure_is_pending_and_blocks_handshake() -> None:
    client = _client(BoomProbe())
    scanned = client.post(
        "/v1/intake/server/scan",
        json={"gateway_url": "https://api.litellm.ai/v1", "probe": "list", "live": False},
    )
    body = scanned.json()
    assert body["admission"] == "pending"
    assert body["policy_match"] is False
    assert body["models"] == []
    assert body["network"] == "not_contacted"
    assert body["reason"] == "cloud_litellm_denied"
    pending = client.get("/v1/intake/pending").json()["models"]
    assert len(pending) == 1
    assert pending[0]["accepted"] is False
    refused = client.post(
        "/v1/intake/server/handshake",
        json={"scan_id": body["scan_id"], "model_ids": ["vllm/qwen2"], "live": False},
    )
    assert refused.status_code == 409
    assert refused.json()["error"] == "scan_not_ready"
    assert client.get("/v1/catalog/models").json()["models"] == []


def test_comparison_slots_stay_separate_from_the_catalog() -> None:
    probe = ScriptedProbe(body={"status": "healthy"})
    client = _client(probe)
    _cloud(client)
    residual = client.get("/v1/rounds/comparison-slots").json()
    assert residual["model_ids"] == []
    assert residual["source"] == "round_comparison_slots"
    blocked = client.post("/v1/rounds/comparison-slots", json={"model_ids": ["openai/not-admitted"]})
    assert blocked.status_code == 409
    assert blocked.json()["error"] == "slots_unscoped"
    missing = client.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": ["openai/not-admitted"]})
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_in_catalog"
    fixture_pool = client.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": ["openai/gpt-4o"]})
    assert fixture_pool.status_code == 409
    assert fixture_pool.json()["error"] == "fixture_pool"
    assert client.get("/v1/rounds/comparison-slots").json()["model_ids"] == []
    assert [item["model_id"] for item in client.get("/v1/catalog/models").json()["models"]] == ["openai/gpt-4o"]
    assert client.get("/v1/catalog/models").json()["models"][0]["accepted"] is True


def test_pending_model_cannot_fill_a_comparison_slot() -> None:
    client = _client(ScriptedProbe(status=503, body={"error": "down"}))
    _cloud(client)
    response = client.post("/v1/rounds/comparison-slots", json={"model_ids": ["openai/gpt-4o"]})
    assert response.status_code == 409
    assert response.json()["error"] == "slots_unscoped"
    refused = client.put("/v1/rounds/plans/fixture-paired/slots", json={"model_ids": ["openai/gpt-4o"]})
    assert refused.status_code == 404
    assert refused.json()["error"] == "not_in_catalog"


def test_probe_refuses_completions_before_any_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket")

    monkeypatch.setattr(socket, "create_connection", blocked)
    with pytest.raises(ProbeError):
        guard_probe_url(f"{GATEWAY}/v1/chat/completions")
    with pytest.raises(ProbeError):
        UrllibProbe().get_json(f"{GATEWAY}/v1/chat/completions")


def test_intake_works_without_the_fixture_catalog(tmp_path: Path) -> None:
    probe = ScriptedProbe(body={"status": "healthy"})
    client = TestClient(create_app(tmp_path, environ={}, probe=probe))
    response = _cloud(client)
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert client.get("/v1/experiments").status_code == 503


def test_model_pool_intake_spec_is_the_shared_standard() -> None:
    text = (ROOT / "docs" / "model-pool-intake.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "平台端" in text and "企业端" in text
    assert "model_id" in text and "litellm_model" in text
    assert "pricing" in text
    assert "OHBE-only" in text
    assert "/v1/models" in text
    assert "Bearer" in text
    assert "pool YAML" in text
    assert "docs/model-pool-intake.md" in readme


def test_keyed_probe_uses_bearer_on_models_and_not_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    seen: dict[str, str | None] = {}

    class _Resp:
        status = 200

        def read(self) -> bytes:
            return b'{"data":[]}'

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def _open(self: object, request: urllib.request.Request, timeout: float | None = None) -> _Resp:
        del self, timeout
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _open)
    status, body = UrllibProbe().get_json(f"{GATEWAY}{LIST_PROBE_PATH}", bearer=SECRET)
    assert status == 200
    assert body == {"data": []}
    assert seen["url"] == f"{GATEWAY}{LIST_PROBE_PATH}"
    assert seen["authorization"] == f"Bearer {SECRET}"
    assert SECRET not in str(seen["url"])

    seen.clear()
    UrllibProbe().get_json(f"{GATEWAY}{HEALTH_PROBE_PATH}")
    assert seen["authorization"] is None
    assert seen["url"] == f"{GATEWAY}{HEALTH_PROBE_PATH}"


def test_urllib_probe_maps_remote_disconnected_to_probe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client
    import urllib.request

    def _open(self: object, request: urllib.request.Request, timeout: float | None = None) -> object:
        del self, request, timeout
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _open)
    with pytest.raises(ProbeError, match="gateway unreachable"):
        UrllibProbe().get_json(f"{GATEWAY}{HEALTH_PROBE_PATH}")


def test_cloud_intake_remote_disconnected_is_connectivity_failed_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import http.client
    import urllib.request

    def _open(self: object, request: urllib.request.Request, timeout: float | None = None) -> object:
        del self, request, timeout
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _open)
    client = TestClient(create_app(ROOT, environ={}, probe=UrllibProbe()))
    response = _cloud(client)
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["reason"] == "connectivity_failed"
    assert body["connectivity"] == "failed"
    assert body["network"] == "unreachable"


def test_intake_doc_states_both_gates_and_the_product_paths() -> None:
    text = (ROOT / "docs" / "model-intake.md").read_text(encoding="utf-8")
    assert "模型入库" in text
    assert "Model Intake" in text
    assert "Harness 预装" in text
    assert "Harness Preinstall" in text
    assert "添加云端" in text
    assert "添加本地" in text
    assert "扫描本机/服务器已部署模型" in text
    assert "pending" in text
    assert "fail-closed" in text
    assert "/v1/chat/completions" in text
