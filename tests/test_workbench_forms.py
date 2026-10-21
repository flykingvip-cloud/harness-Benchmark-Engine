"""Workbench pool drafts and harness profiles. They do not spend or bypass the gateway."""

from __future__ import annotations

import socket
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.adapters.inspect_react.plan import INSPECT_AI_VERSION
from ohbe.adapters.openbot.profile import OPENBOT_COMMIT_UNAVAILABLE
from ohbe.api.app import create_app
from ohbe.api.forms import (
    INSPECT_PROFILE_VERSION,
    LIVE_HARNESS_ORDER,
    OPENBOT_VERSION_PLACEHOLDER,
    PROFILE_NOTE,
    profile_live_decision,
)
from ohbe.live.spec import HARNESS_ORDER
from ohbe.pools.template import load_pool_template

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "models" / "pools" / "pool-gpu-farm.example.yaml"


def _client(tmp_path: Path, environ: dict[str, str] | None = None) -> TestClient:
    return TestClient(create_app(tmp_path, environ=environ or {}))


def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)


def _pool_body() -> dict[str, object]:
    return {
        "pool_id": "pool-lab",
        "models": [
            {
                "model_id": "vllm/lab-instruct",
                "provider": "vllm",
                "name": "lab-instruct",
                "revision": "unpinned",
                "litellm_model": "hosted_vllm/lab-instruct",
            },
            {
                "model_id": "ollama/lab",
                "provider": "ollama",
                "name": "lab",
                "revision": "unpinned",
                "litellm_model": "ollama/lab",
            },
        ],
    }


def _openbot_body() -> dict[str, object]:
    return {
        "profile_id": "openbot-benchmark",
        "adapter": "openbot",
        "name": "openbot-benchmark",
        "version": OPENBOT_VERSION_PLACEHOLDER,
        "timeout_seconds": 600,
        "draft_adapter_name": None,
    }


def test_live_order_constants_match_the_runner() -> None:
    assert LIVE_HARNESS_ORDER == HARNESS_ORDER == ("openbot", "inspect_react")
    assert INSPECT_PROFILE_VERSION == INSPECT_AI_VERSION
    assert OPENBOT_VERSION_PLACEHOLDER == OPENBOT_COMMIT_UNAVAILABLE
    runner = (ROOT / "src" / "ohbe" / "live" / "runner.py").read_text(encoding="utf-8")
    assert "harnesses/profiles" not in runner
    assert "pool-drafts" not in runner
    assert "model-catalog" not in runner
    assert "models/catalog" not in runner


def test_pool_draft_validates_and_saves_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network(monkeypatch)
    client = _client(tmp_path)
    body = _pool_body()
    checked = client.post("/v1/pool-drafts/validate", json=body)
    assert checked.status_code == 200
    payload = checked.json()
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["spend"] == "none"
    assert payload["network"] == "not_contacted"
    assert payload["live_spend"] == "locked"
    assert payload["spend_execution"] == "disabled"
    assert payload["destination"] == "models/pools/pool-lab.yaml"
    assert {item["id"] for item in payload["checks"]} >= {
        "pool_fail_closed",
        "pool_forbid_hosts",
        "pool_gateway_env",
        "pool_local_provider",
        "pool_no_secret",
        "pool_no_url",
        "live_spend_not_unlocked",
        "spend_not_executed",
    }
    assert not (tmp_path / "models" / "pools" / "pool-lab.yaml").exists()

    saved = client.post("/v1/pool-drafts", json=body)
    assert saved.status_code == 200
    written = tmp_path / "models" / "pools" / "pool-lab.yaml"
    text = written.read_text(encoding="utf-8")
    assert "fail_closed: true" in text
    assert "LITELLM_BASE_URL" in text
    assert "LITELLM_API_KEY" in text
    assert "sk-" not in text.lower()
    assert "://" not in text
    assert "api.openai.com" in text
    assert "api.anthropic.com" in text
    template = load_pool_template(written)
    assert template.constraints.fail_closed is True
    assert template.gateway.base_url_env == "LITELLM_BASE_URL"
    assert template.gateway.api_key_env == "LITELLM_API_KEY"
    assert saved.json()["content_hash"] == template.pool.content_hash
    assert saved.json()["spend"] == "none"
    listed = client.get("/v1/pool-drafts")
    assert listed.status_code == 200
    assert listed.json()["live_spend"] == "locked"
    assert listed.json()["pools"][0]["pool_id"] == "pool-lab"
    detail = client.get("/v1/pool-drafts/pool-lab.yaml")
    assert detail.status_code == 200
    assert detail.json()["models"][0]["litellm_model"] == "hosted_vllm/lab-instruct"
    assert detail.json()["executed"] is False


def test_pool_rules_reject_cloud_providers_secrets_and_urls(tmp_path: Path) -> None:
    client = _client(tmp_path)
    cloud = _pool_body()
    cloud["models"] = [
        {
            "model_id": "vllm/lab",
            "provider": "vllm",
            "name": "lab",
            "revision": "unpinned",
            "litellm_model": "hosted_vllm/lab.openai.com",
        }
    ]
    response = client.post("/v1/pool-drafts/validate", json=cloud)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["checks"][0]["id"] == "pool_forbid_hosts"
    refused = client.post("/v1/pool-drafts", json=cloud)
    assert refused.status_code == 422
    assert refused.json()["error"] == "pool_template_rejected"
    assert refused.json()["spend"] == "none"
    assert not (tmp_path / "models" / "pools" / "pool-lab.yaml").exists()

    secret = _pool_body()
    secret["models"][0]["name"] = "sk-live-secret"
    secret_response = client.post("/v1/pool-drafts/validate", json=secret)
    assert secret_response.json()["ok"] is False
    assert "sk-live-secret" not in secret_response.text

    url = _pool_body()
    url["models"][0]["revision"] = "http://gpu-host/v1"
    url_response = client.post("/v1/pool-drafts", json=url)
    assert url_response.status_code == 422
    assert "http://gpu-host/v1" not in url_response.text
    assert not (tmp_path / "models" / "pools").exists() or not any(
        (tmp_path / "models" / "pools").glob("*.yaml")
    )

    provider = client.post(
        "/v1/pool-drafts/validate",
        json={
            "pool_id": "pool-lab",
            "models": [
                {
                    "model_id": "openai/gpt",
                    "provider": "openai",
                    "name": "gpt",
                    "revision": "unpinned",
                    "litellm_model": "openai/gpt",
                }
            ],
        },
    )
    assert provider.status_code == 422
    assert provider.json()["error"] == "invalid_request"

    extra = client.post(
        "/v1/pool-drafts",
        json={**_pool_body(), "api_key": "sk-test-secret", "gateway": {"base_url_env": "OPENAI_BASE_URL"}, "live": True},
    )
    assert extra.status_code == 422
    assert extra.json()["error"] == "invalid_request"
    assert "sk-test-secret" not in extra.text


def test_save_does_not_overwrite_example_or_unlock_spend(tmp_path: Path) -> None:
    example_dir = tmp_path / "models" / "pools"
    example_dir.mkdir(parents=True)
    example = example_dir / "pool-gpu-farm.example.yaml"
    example.write_bytes(EXAMPLE.read_bytes())
    before = example.read_bytes()
    client = _client(tmp_path, {"OHBE_ALLOW_LIVE_SPEND": "1"})
    body = _pool_body()
    body["pool_id"] = "pool-gpu-farm"
    saved = client.post("/v1/pool-drafts", json=body)
    assert saved.status_code == 200
    assert saved.json()["live_spend"] == "env_set"
    assert saved.json()["spend_execution"] == "disabled"
    assert saved.json()["executed"] is False
    assert saved.json()["network"] == "not_contacted"
    assert example.read_bytes() == before
    copy = example_dir / "pool-gpu-farm.yaml"
    assert copy.is_file()
    assert "example" not in copy.name
    listed = client.get("/v1/pool-drafts").json()["pools"]
    flags = {item["filename"]: item["read_only"] for item in listed}
    assert flags["pool-gpu-farm.example.yaml"] is True
    assert flags["pool-gpu-farm.yaml"] is False


def test_harness_profile_registers_existing_adapters_and_rejects_a_third_for_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network(monkeypatch)
    client = _client(tmp_path, {"OHBE_ALLOW_LIVE_SPEND": "1"})
    checked = client.post("/v1/harness-configs/validate", json=_openbot_body())
    assert checked.status_code == 200
    body = checked.json()
    assert body["ok"] is True
    assert body["live_run"] == "rejected"
    assert body["status"] == "registered"
    assert body["dev_only"] is False
    assert body["harness_id"] == "openbot"
    assert body["executed"] is False
    assert body["spend"] == "none"
    assert body["ref"]["config_hash"] == body["config_hash"]
    assert body["ref"]["harness_id"] == "openbot"
    assert "://" not in checked.text
    assert set(body["parameters"]) == {
        "base_url_env",
        "channels",
        "deliver",
        "elevated",
        "local",
        "profile",
        "provider",
        "timeout_seconds",
    }
    assert body["parameters"]["provider"] == "litellm"
    assert body["parameters"]["base_url_env"] == "LITELLM_BASE_URL"
    assert body["parameters"]["deliver"] is False

    saved = client.post("/v1/harness-configs", json=_openbot_body())
    assert saved.status_code == 200
    assert saved.json()["live_spend"] == "env_set"
    assert saved.json()["spend_execution"] == "disabled"
    text = (tmp_path / "harnesses" / "profiles" / "openbot-benchmark.yaml").read_text(encoding="utf-8")
    assert "live_run: rejected" in text
    assert "sk-" not in text.lower()
    assert "://" not in text

    inspect_body = {
        "profile_id": "inspect-react-pin",
        "adapter": "inspect_react",
        "name": "inspect-react",
        "version": INSPECT_PROFILE_VERSION,
        "timeout_seconds": 30,
    }
    inspect_saved = client.post("/v1/harness-configs", json=inspect_body)
    assert inspect_saved.status_code == 200
    assert inspect_saved.json()["harness_id"] == "inspect-react"
    assert inspect_saved.json()["parameters"]["solver"] == "react"
    assert inspect_saved.json()["parameters"]["provider"] == "litellm-proxy"

    wrong_version = dict(inspect_body)
    wrong_version["version"] = "9.9.9"
    rejected_version = client.post("/v1/harness-configs", json=wrong_version)
    assert rejected_version.status_code == 422
    assert rejected_version.json()["spend"] == "none"

    draft = {
        "profile_id": "custom-loop",
        "adapter": "draft",
        "name": "custom-loop",
        "version": "0.0.0-draft",
        "timeout_seconds": 10,
        "draft_adapter_name": "custom-loop",
    }
    draft_saved = client.post("/v1/harness-configs", json=draft)
    assert draft_saved.status_code == 422
    assert draft_saved.json()["error"] == "invalid_request"
    assert "custom-loop" not in draft_saved.text
    assert profile_live_decision("draft", "draft") == "draft adapter profiles are rejected for live run"
    assert "extend HARNESS_ORDER" in profile_live_decision("openbot", "registered")
    assert not (tmp_path / "harnesses" / "profiles" / "custom-loop.yaml").exists()

    impersonation = dict(_openbot_body())
    impersonation["draft_adapter_name"] = "openbot"
    impersonated = client.post("/v1/harness-configs/validate", json=impersonation)
    assert impersonated.status_code == 200
    assert impersonated.json()["ok"] is False

    listed = client.get("/v1/harness-configs")
    assert listed.status_code == 200
    catalog = listed.json()
    assert catalog["harness_order"] == ["openbot", "inspect_react"]
    assert "HARNESS_ORDER" in catalog["note"]
    assert "not a free-form upload" in catalog["note"]
    assert catalog["note"] == PROFILE_NOTE
    assert catalog["spend_execution"] == "disabled"
    assert catalog["executed"] is False
    ids = {item["profile_id"] for item in catalog["profiles"]}
    assert ids == {"openbot-benchmark", "inspect-react-pin"}
    exported = client.get("/v1/harness-configs/openbot-benchmark")
    assert exported.status_code == 200
    assert exported.json()["ref"]["config_hash"] == saved.json()["config_hash"]
    assert HARNESS_ORDER == ("openbot", "inspect_react")


def test_catalog_intake_stores_metadata_outside_the_pool_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _block_network(monkeypatch)
    client = _client(tmp_path, {"OHBE_ALLOW_LIVE_SPEND": "1"})
    body = {
        "model_id": "vllm/lab-instruct",
        "provider": "vllm",
        "name": "lab-instruct",
        "revision": "unpinned",
        "litellm_model": "hosted_vllm/lab-instruct",
        "modality": "code",
        "strengths": ["code", "reasoning"],
        "placement": "local",
    }
    checked = client.post("/v1/model-catalog/validate", json=body)
    assert checked.status_code == 200
    payload = checked.json()
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["spend"] == "none"
    assert payload["network"] == "not_contacted"
    assert payload["live_spend"] == "env_set"
    assert payload["spend_execution"] == "disabled"
    assert payload["destination"] == "models/catalog/vllm__lab-instruct.yaml"
    assert {item["id"] for item in payload["checks"] if item["passed"]} >= {
        "catalog_metadata",
        "catalog_placement",
        "pool_fail_closed",
        "pool_local_provider",
        "spend_not_executed",
    }

    saved = client.post("/v1/model-catalog", json=body)
    assert saved.status_code == 200
    assert saved.json()["spend_execution"] == "disabled"
    text = (tmp_path / "models" / "catalog" / "vllm__lab-instruct.yaml").read_text(encoding="utf-8")
    assert "kind: ohbe.model_catalog_entry" in text
    assert "modality: code" in text
    assert "placement: local" in text
    assert "live_run: rejected" in text
    assert "://" not in text
    assert not (tmp_path / "models" / "pools" / "pool-catalog-check.yaml").exists()

    gateway = dict(body)
    gateway["model_id"] = "ollama/lab"
    gateway["provider"] = "ollama"
    gateway["name"] = "lab"
    gateway["litellm_model"] = "ollama/lab"
    gateway["placement"] = "gateway"
    gateway_saved = client.post("/v1/model-catalog", json=gateway)
    assert gateway_saved.status_code == 200
    gateway_text = (tmp_path / "models" / "catalog" / "ollama__lab.yaml").read_text(encoding="utf-8")
    assert "placement: gateway" in gateway_text
    assert "://" not in gateway_text

    secret = dict(body)
    secret["name"] = "sk-catalog"
    hidden = client.post("/v1/model-catalog/validate", json=secret)
    assert hidden.json()["ok"] is False
    assert "sk-catalog" not in hidden.text
    cloud = dict(body)
    cloud["provider"] = "openai"
    cloud["model_id"] = "openai/gpt"
    cloud["litellm_model"] = "openai/gpt"
    rejected = client.post("/v1/model-catalog", json=cloud)
    assert rejected.status_code == 422
    assert rejected.json()["error"] == "invalid_request"
    leaked = client.post("/v1/model-catalog", json={**body, "api_key": "sk-test-secret", "live": True})
    assert leaked.status_code == 422
    assert "sk-test-secret" not in leaked.text

    listed = client.get("/v1/model-catalog")
    assert listed.status_code == 200
    assert listed.json()["spend_execution"] == "disabled"
    assert {item["model_id"] for item in listed.json()["entries"]} == {"vllm/lab-instruct", "ollama/lab"}
    detail = client.get("/v1/model-catalog/vllm__lab-instruct.yaml")
    assert detail.status_code == 200
    assert detail.json()["modality"] == "code"
    assert detail.json()["strengths"] == ["code", "reasoning"]
    assert detail.json()["executed"] is False


def test_harness_profile_rejects_secrets_and_live_flag(tmp_path: Path) -> None:
    client = _client(tmp_path)
    secret = _openbot_body()
    secret["name"] = "sk-profile"
    response = client.post("/v1/harness-configs/validate", json=secret)
    assert response.json()["ok"] is False
    assert "sk-profile" not in response.text
    leaked = client.post("/v1/harness-configs", json={**_openbot_body(), "api_key": "sk-test-secret", "live": True})
    assert leaked.status_code == 422
    assert "sk-test-secret" not in leaked.text
    assert not (tmp_path / "harnesses").exists()
