"""LiteLLM metadata headers and model-pool config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ohbe.environment.policy import controlled_track_policy
from ohbe.gateway.constants import LITELLM_IMAGE, LITELLM_VERSION
from ohbe.gateway.metadata import (
    GatewayCallMetadata,
    build_chat_body,
    decode_spend_logs_metadata,
    metadata_tags,
    request_headers,
    spend_logs_metadata,
    spend_logs_metadata_header,
)
from ohbe.gateway.config import (
    gateway_environment,
    proxy_config_document,
    render_proxy_config,
    virtual_key_request,
)
from tests.samples import sample_model, sample_pool

ROOT = Path(__file__).resolve().parents[1]


def _metadata() -> GatewayCallMetadata:
    return GatewayCallMetadata(
        benchmark_run_id="exp-1",
        execution_id="exec-1",
        task_id="task-001",
        harness_id="harness-echo",
        purpose="benchmark",
        invocation_id="inv-1",
        model_id="openai/gpt-test",
    )


def test_spend_metadata_round_trips_through_the_litellm_header() -> None:
    metadata = _metadata()
    headers = request_headers(metadata, api_key="sk-ohbe-test")
    decoded = decode_spend_logs_metadata(headers["x-litellm-spend-logs-metadata"])
    assert decoded == spend_logs_metadata(metadata)
    assert decoded["benchmark_run_id"] == "exp-1"
    assert decoded["execution_id"] == "exec-1"
    assert decoded["task_id"] == "task-001"
    assert decoded["harness_id"] == "harness-echo"
    assert decoded["purpose"] == "benchmark"
    assert headers["x-litellm-spend-logs-metadata"] == spend_logs_metadata_header(metadata)
    tags = headers["x-litellm-tags"].split(",")
    assert tags == metadata_tags(metadata)
    assert "benchmark_run_id:exp-1" in tags
    assert "purpose:benchmark" in tags
    body = build_chat_body("openai/gpt-test", [{"role": "user", "content": "hi"}], metadata)
    assert body["metadata"] == {"spend_logs_metadata": decoded}
    assert body["model"] == "openai/gpt-test"


def test_metadata_rejects_commas_that_would_split_tags() -> None:
    with pytest.raises(ValueError):
        GatewayCallMetadata(
            benchmark_run_id="exp,1",
            execution_id="exec-1",
            task_id="task-001",
            harness_id="harness-echo",
            purpose="benchmark",
        )


def test_proxy_config_lists_only_the_pool_and_no_literal_secret() -> None:
    pool = sample_pool(
        models=(
            sample_model(metadata={"litellm_model": "openai/gpt-test", "api_key_env": "OPENAI_API_KEY"}),
            sample_model(model_id="anthropic/claude-test", provider="anthropic", name="claude-test"),
        )
    )
    document = proxy_config_document(pool)
    names = [entry["model_name"] for entry in document["model_list"]]
    assert names == ["openai/gpt-test", "anthropic/claude-test"]
    assert document["model_list"][0]["litellm_params"]["api_key"] == "os.environ/OPENAI_API_KEY"
    assert "api_key" not in document["model_list"][1]["litellm_params"]
    rendered = render_proxy_config(pool)
    assert LITELLM_IMAGE in rendered
    assert pool.content_hash in rendered
    assert "sk-" not in rendered
    assert "enterprise" not in rendered
    with pytest.raises(ValueError):
        proxy_config_document(
            sample_pool(models=(sample_model(metadata={"api_key_env": "sk-secret"}),))
        )


def test_virtual_key_is_restricted_to_pool_model_ids() -> None:
    pool = sample_pool()
    body = virtual_key_request(pool, key_alias="ohbe-exp-1", metadata=_metadata())
    assert body["models"] == [model.model_id for model in pool.models]
    assert "*" not in body["models"]
    assert body["metadata"]["spend_logs_metadata"]["benchmark_run_id"] == "exp-1"
    assert json.dumps(body["metadata"]["tags"]).count("purpose:benchmark") == 1


def test_pinned_oss_image_and_sample_compose() -> None:
    assert LITELLM_VERSION == "1.104.2"
    assert LITELLM_IMAGE == "ghcr.io/berriai/litellm:v1.104.2"
    assert "enterprise" not in LITELLM_IMAGE
    config = (ROOT / "docker" / "litellm" / "config.sample.yaml").read_text(encoding="utf-8")
    compose = (ROOT / "docker" / "litellm" / "compose.yaml").read_text(encoding="utf-8")
    assert "model_list:" in config
    assert "os.environ/OPENAI_API_KEY" in config
    assert "litellm-enterprise" not in config
    assert "enterprise/" not in config
    assert "image: ghcr.io/berriai/litellm:v1.104.2" in compose
    assert "litellm-enterprise" not in compose
    assert "internal: true" in compose
    assert "ohbe-gateway" in compose
    caller = compose.split("model-caller:", 1)[1].split("\nnetworks:", 1)[0]
    assert "ohbe_egress" in caller
    assert "upstream" not in caller
    environment = gateway_environment(
        controlled_track_policy(gateway_host="ohbe-gateway", gateway_port=4000)
    )
    assert environment.image_ref == LITELLM_IMAGE
    assert environment.gateway_port == 4000
    assert environment.image_digest is None
