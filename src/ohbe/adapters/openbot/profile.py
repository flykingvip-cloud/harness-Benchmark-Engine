"""Benchmark profile for OpenBot.

The profile allows only the LiteLLM proxy on the OHBE gateway. Tool-sandbox
`docker.network: none` does not cover the OpenBot process that calls models,
so this file does not use that setting as the egress control.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelPool
from ohbe.environment.policy import is_denied_provider_host, normalize_host

OPENBOT_COMMIT_UNAVAILABLE = "openbot-commit-unavailable"
OPENBOT_IMAGE_UNAVAILABLE = "openbot-image-unavailable"
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_FORBIDDEN_TEXT = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
    "huggingface.co",
    "openai_api_key",
    "anthropic_api_key",
    "huggingface_api_key",
    "hf_token",
    "sk-",
)
BASE_URL_ENV = "LITELLM_BASE_URL"


class OpenBotProfileError(ValueError):
    """The benchmark profile would bypass the LiteLLM gateway."""


def normalize_openbot_commit(commit: str) -> str:
    """Accept a full git SHA, or the documented CI placeholder."""

    if commit == OPENBOT_COMMIT_UNAVAILABLE:
        return commit
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise OpenBotProfileError(
            "OpenBot version must be a 40-character git SHA or "
            f"{OPENBOT_COMMIT_UNAVAILABLE}"
        )
    return commit


def litellm_model_ref(model_id: str) -> str:
    return f"litellm/{model_id}"


def litellm_base_url(environment: ExecutionEnvironment, *, scheme: str = "http") -> str:
    """Proxy origin from the egress record. Not a provider URL."""

    if scheme not in {"http", "https"}:
        raise OpenBotProfileError("scheme must be http or https")
    host = normalize_host(environment.gateway_host)
    if is_denied_provider_host(host):
        raise OpenBotProfileError("benchmark profile cannot target a model provider")
    literal = host
    if ":" in literal and not literal.startswith("["):
        literal = f"[{literal}]"
    return f"{scheme}://{literal}:{environment.gateway_port}"


def openbot_harness_config(
    *,
    commit: str,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int = 600,
    base_url_env: str | None = None,
) -> HarnessConfig:
    """Harness document whose version is the OpenBot git SHA.

    `base_url_env` names the variable the live runner already resolved into
    `environment`. The written profile stores that concrete gateway origin,
    not a cloud provider key.
    """

    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise OpenBotProfileError("timeout_seconds must be a positive integer")
    if base_url_env is not None and base_url_env != BASE_URL_ENV:
        raise OpenBotProfileError(f"base_url_env must be {BASE_URL_ENV}")
    parameters: dict[str, Any] = {
        "profile": "benchmark",
        "provider": "litellm",
        "gateway_host": environment.gateway_host,
        "gateway_port": environment.gateway_port,
        "models": [model.model_id for model in pool.models],
        "deliver": False,
        "elevated": False,
        "channels": [],
        "local": True,
        "timeout_seconds": timeout_seconds,
    }
    if base_url_env is not None:
        parameters["base_url_env"] = base_url_env
    return HarnessConfig.build(
        harness_id="openbot",
        name="openbot-benchmark",
        version=normalize_openbot_commit(commit),
        parameters=parameters,
    )


def benchmark_profile(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int,
    scheme: str = "http",
) -> dict[str, Any]:
    """OpenBot config: LiteLLM only, pool allowlist, delivery and channels off."""

    if not pool.models:
        raise OpenBotProfileError("model pool is empty")
    refs = [litellm_model_ref(model.model_id) for model in pool.models]
    document: dict[str, Any] = {
        "agents": {
            "defaults": {
                "elevatedDefault": "off",
                "model": {"fallbacks": [], "primary": refs[0]},
                "models": {ref: {} for ref in refs},
                "sandbox": {"mode": "off"},
                "timeoutSeconds": timeout_seconds,
            }
        },
        "auth": {
            "profiles": {
                "litellm:default": {
                    "apiKey": "${LITELLM_API_KEY}",
                    "mode": "api_key",
                    "provider": "litellm",
                }
            }
        },
        "channels": {},
        "messages": {"deliver": False},
        "models": {
            "mode": "replace",
            "providers": {
                "litellm": {
                    "api": "openai-completions",
                    "apiKey": "${LITELLM_API_KEY}",
                    "baseUrl": litellm_base_url(environment, scheme=scheme),
                    "models": [
                        {"id": model.model_id, "name": model.name} for model in pool.models
                    ],
                }
            },
        },
        "tools": {"elevated": {"enabled": False}},
    }
    validate_benchmark_profile(document, pool=pool, environment=environment)
    return document


def render_benchmark_profile(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int,
    scheme: str = "http",
) -> str:
    document = benchmark_profile(
        pool=pool,
        environment=environment,
        timeout_seconds=timeout_seconds,
        scheme=scheme,
    )
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def validate_benchmark_profile(
    document: dict[str, Any],
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> None:
    """Raise when the document can reach anything but the gateway allowlist."""

    providers = document.get("models", {}).get("providers", {})
    if set(providers) != {"litellm"}:
        raise OpenBotProfileError("benchmark profile must list only the litellm provider")
    litellm = providers["litellm"]
    expected_url = litellm_base_url(environment)
    if litellm.get("baseUrl") != expected_url:
        raise OpenBotProfileError("litellm baseUrl must be the OHBE gateway")
    if litellm.get("apiKey") != "${LITELLM_API_KEY}":
        raise OpenBotProfileError("litellm apiKey must be the LITELLM_API_KEY reference")
    refs = [litellm_model_ref(model.model_id) for model in pool.models]
    allow = document.get("agents", {}).get("defaults", {}).get("models", {})
    if list(allow) != refs:
        raise OpenBotProfileError("agents.defaults.models must be the pool allowlist")
    primary = document.get("agents", {}).get("defaults", {}).get("model", {}).get("primary")
    if primary != refs[0]:
        raise OpenBotProfileError("primary model must be the first allowlisted model")
    if document.get("messages", {}).get("deliver") is not False:
        raise OpenBotProfileError("benchmark profile must disable deliver")
    if document.get("channels") != {}:
        raise OpenBotProfileError("benchmark profile must not configure channels")
    if document.get("tools", {}).get("elevated", {}).get("enabled") is not False:
        raise OpenBotProfileError("benchmark profile must disable elevated tools")
    blob = json.dumps(document, ensure_ascii=False).lower()
    for banned in _FORBIDDEN_TEXT:
        if banned in blob:
            raise OpenBotProfileError(f"benchmark profile contains {banned}")


def check_harness_binding(
    config: HarnessConfig,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> int:
    """Reject a harness config that does not describe this gateway and pool."""

    normalize_openbot_commit(config.version)
    if config.harness_id != "openbot":
        raise OpenBotProfileError("harness_id must be openbot")
    parameters = config.parameters
    if parameters.get("provider") != "litellm" or parameters.get("deliver") is not False:
        raise OpenBotProfileError("harness config must be the LiteLLM benchmark profile")
    if parameters.get("elevated") is not False or parameters.get("channels") not in ([], ()):
        raise OpenBotProfileError("harness config must disable elevated tools and channels")
    if parameters.get("base_url_env") not in {None, BASE_URL_ENV}:
        raise OpenBotProfileError(f"base_url_env must be {BASE_URL_ENV}")
    if parameters.get("gateway_host") != environment.gateway_host:
        raise OpenBotProfileError("harness gateway host does not match the egress record")
    if parameters.get("gateway_port") != environment.gateway_port:
        raise OpenBotProfileError("harness gateway port does not match the egress record")
    if list(parameters.get("models") or []) != [model.model_id for model in pool.models]:
        raise OpenBotProfileError("harness model list does not match the pool")
    timeout = parameters.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise OpenBotProfileError("timeout_seconds must be a positive integer")
    return timeout
