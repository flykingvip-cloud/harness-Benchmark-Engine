"""GPU-farm pool template: ModelPool YAML, no secrets, fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from ohbe.pools.template import PoolTemplateError, load_pool_template

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "models" / "pools" / "pool-gpu-farm.example.yaml"


def test_example_pool_builds_a_sealed_model_pool() -> None:
    first = load_pool_template(POOL)
    second = load_pool_template(POOL)
    assert first.pool.pool_id == "pool-gpu-farm"
    assert first.pool.content_hash == second.pool.content_hash
    assert len(first.pool.content_hash) == 64
    assert first.gateway.base_url_env == "LITELLM_BASE_URL"
    assert first.gateway.api_key_env == "LITELLM_API_KEY"
    assert first.constraints.fail_closed is True
    assert "api.openai.com" in first.constraints.forbid_hosts
    assert "api.anthropic.com" in first.constraints.forbid_hosts
    assert [model.model_id for model in first.pool.models] == [
        "vllm/REPLACE_ME-instruct",
        "ollama/REPLACE_ME",
    ]
    assert all("REPLACE_ME" in model.model_id for model in first.pool.models)
    assert first.pool.models[0].metadata["litellm_model"] == "hosted_vllm/REPLACE_ME-instruct"
    text = POOL.read_text(encoding="utf-8")
    assert "sk-" not in text.lower()
    assert "://" not in text


def test_pool_template_rejects_cloud_providers_and_secrets(tmp_path: Path) -> None:
    original = POOL.read_text(encoding="utf-8")

    def _load(text: str) -> None:
        path = tmp_path / "pool.yaml"
        path.write_text(text, encoding="utf-8")
        load_pool_template(path)

    with pytest.raises(PoolTemplateError, match="vllm and ollama"):
        _load(original.replace("provider: vllm", "provider: openai", 1))
    with pytest.raises(PoolTemplateError, match="cloud provider API key"):
        _load(
            original.replace(
                "      litellm_model: hosted_vllm/REPLACE_ME-instruct\n",
                "      litellm_model: hosted_vllm/REPLACE_ME-instruct\n      api_key_env: OPENAI_API_KEY\n",
                1,
            )
        )
    with pytest.raises(PoolTemplateError, match="secret"):
        _load(original.replace("revision: unpinned", "revision: sk-live-secret", 1))
    with pytest.raises(PoolTemplateError, match="URL"):
        _load(original.replace("pool_id: pool-gpu-farm", "pool_id: http://10.0.0.1:4000", 1))
    with pytest.raises(PoolTemplateError, match="OpenAI or Anthropic"):
        _load(original.replace("vllm/REPLACE_ME-instruct", "vllm/api.openai.com", 1))
    with pytest.raises(PoolTemplateError, match="fail-closed"):
        _load(original.replace("fail_closed: true", "fail_closed: false", 1))
    with pytest.raises(PoolTemplateError, match="api.anthropic.com"):
        _load(original.replace("    - api.anthropic.com\n", ""))
    with pytest.raises(PoolTemplateError, match="duplicate model_id"):
        _load(
            original.replace(
                "  - model_id: ollama/REPLACE_ME\n"
                "    provider: ollama\n"
                "    name: REPLACE_ME\n"
                "    revision: unpinned\n"
                "    metadata:\n"
                "      litellm_model: ollama/REPLACE_ME\n",
                "  - model_id: vllm/REPLACE_ME-instruct\n"
                "    provider: vllm\n"
                "    name: REPLACE_ME-instruct\n"
                "    revision: unpinned\n"
                "    metadata:\n"
                "      litellm_model: hosted_vllm/REPLACE_ME-instruct\n",
                1,
            )
        )
    with pytest.raises(PoolTemplateError, match="LITELLM_BASE_URL"):
        _load(original.replace("base_url_env: LITELLM_BASE_URL", "base_url_env: OPENAI_BASE_URL", 1))
