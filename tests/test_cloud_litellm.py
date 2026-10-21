"""Cloud LiteLLM origins fail closed before any socket is opened."""

from __future__ import annotations

import urllib.request

import pytest

from ohbe.environment.policy import (
    CloudLiteLLMDenied,
    EgressDenied,
    controlled_track_policy,
    is_cloud_litellm_host,
)
from ohbe.gateway.client import post_json


@pytest.mark.parametrize(
    "host",
    [
        "litellm.ai",
        "api.litellm.ai",
        "API.LITELLM.AI.",
        "org.proxy.litellm.ai",
        "litellm.com",
        "cloud.litellm.cloud",
        "api.berri.ai",
    ],
)
def test_known_cloud_hosts_are_classified(host: str) -> None:
    assert is_cloud_litellm_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "ohbe-gateway",
        "localhost",
        "127.0.0.1",
        "notlitellm.ai",
        "litellm.ai.example.com",
        "example.com",
    ],
)
def test_local_and_lookalike_hosts_are_not_cloud(host: str) -> None:
    assert not is_cloud_litellm_host(host)


def test_cloud_host_cannot_be_configured_as_the_gateway() -> None:
    with pytest.raises(CloudLiteLLMDenied, match="cloud LiteLLM endpoint is denied"):
        controlled_track_policy(gateway_host="api.litellm.ai", gateway_port=443)


def test_policy_denies_cloud_url_without_calling_a_transport() -> None:
    policy = controlled_track_policy(gateway_host="ohbe-gateway", gateway_port=4000)
    verdict = policy.evaluate("api.litellm.ai", 443)
    assert not verdict.allowed
    assert "cloud LiteLLM" in verdict.reason


def test_post_json_rejects_cloud_litellm_before_opening_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("opener")

    monkeypatch.setattr(urllib.request, "build_opener", boom)
    policy = controlled_track_policy(gateway_host="ohbe-gateway", gateway_port=4000)
    with pytest.raises(EgressDenied, match="cloud LiteLLM"):
        post_json(
            policy,
            "https://sk-secret:pw@api.litellm.ai/v1/chat/completions",
            {},
            {},
        )
