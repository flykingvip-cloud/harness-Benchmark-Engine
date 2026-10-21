"""LiteLLM-only profile for one DeepSeek Harness headless run.

The pin is the npm release ``@deepseek-ai/dsh@0.2.1-alpha.2``, which is git
tag ``dsh-v0.2.1-alpha.2``. A floating token such as ``latest`` is not a
harness version. The written Cordis patch names provider ``litellm`` only.
The API key stays in ``LITELLM_API_KEY`` and is not written into the patch.
"""

from __future__ import annotations

from typing import Any

import yaml

from ohbe.adapters.openbot.profile import litellm_base_url
from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelPool
from ohbe.core.task import Task
from ohbe.environment.policy import is_denied_provider_host, normalize_host

DSH_PACKAGE = "@deepseek-ai/dsh"
DSH_VERSION = "0.2.1-alpha.2"
DSH_GIT_TAG = "dsh-v0.2.1-alpha.2"
DSH_NPM_SPEC = f"{DSH_PACKAGE}@{DSH_VERSION}"
LITELLM_API_KEY_ENV = "LITELLM_API_KEY"
_FLOATING = frozenset({"latest", "unpinned", "main", "master"})
_FORBIDDEN_TEXT = (
    "api.openai.com",
    "api.anthropic.com",
    "api.deepseek.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
    "huggingface.co",
    "sk-",
)


class DeepSeekHarnessProfileError(ValueError):
    """The headless profile would bypass the LiteLLM gateway."""


def normalize_dsh_version(version: str) -> str:
    """Accept only the pinned npm release. ``latest`` is rejected."""

    if not isinstance(version, str) or version.strip().lower() in _FLOATING or version != DSH_VERSION:
        raise DeepSeekHarnessProfileError(
            f"DeepSeek Harness version must be {DSH_VERSION} ({DSH_GIT_TAG})"
        )
    return version


def dsh_base_url(environment: ExecutionEnvironment) -> str:
    """OpenAI-compatible gateway origin. The client appends ``/chat/completions``."""

    return litellm_base_url(environment) + "/v1"


def dsh_harness_config(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int = 600,
    version: str = DSH_VERSION,
) -> HarnessConfig:
    """Harness document whose version is the pinned DSH npm release."""

    normalize_dsh_version(version)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise DeepSeekHarnessProfileError("timeout_seconds must be a positive integer")
    if not pool.models:
        raise DeepSeekHarnessProfileError("model pool is empty")
    _require_gateway(environment)
    return HarnessConfig.build(
        harness_id="deepseek-harness",
        name="deepseek-harness",
        version=version,
        parameters={
            "git_tag": DSH_GIT_TAG,
            "package": DSH_PACKAGE,
            "profile": "headless",
            "provider": "litellm",
            "gateway_host": environment.gateway_host,
            "gateway_port": environment.gateway_port,
            "models": [model.model_id for model in pool.models],
            "timeout_seconds": timeout_seconds,
        },
    )


def selected_model_id(task: Task, pool: ModelPool) -> str:
    """Pool member this run will pin. A stranger id is not launched."""

    allowed = [model.model_id for model in pool.models]
    if not allowed:
        raise DeepSeekHarnessProfileError("model pool is empty")
    requested = task.input.get("model_id")
    if isinstance(requested, str) and requested:
        if requested not in allowed:
            raise DeepSeekHarnessProfileError(f"{requested} is not in the model pool allowlist")
        return requested
    return allowed[0]


def litellm_patch_document(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    model_id: str,
) -> list[dict[str, Any]]:
    """Cordis ``--patch`` overlay: one LiteLLM route and that default model.

    ``dsh`` 0.2.1 applies ``--patch`` after the headless profile layer. A
    config override replaces the entry, so this list is the whole provider
    map the run is allowed to see. Official DeepSeek, OpenAI, and Anthropic
    routes are absent.
    """

    if model_id not in {model.model_id for model in pool.models}:
        raise DeepSeekHarnessProfileError(f"{model_id} is not in the model pool allowlist")
    document: list[dict[str, Any]] = [
        {
            "id": "llm-pi-ai",
            "config": {
                "providers": {
                    "litellm": {
                        "api": "openai-completions",
                        "apiKeyEnv": LITELLM_API_KEY_ENV,
                        "baseURL": dsh_base_url(environment),
                        "compat": {
                            "maxTokensField": "max_tokens",
                            "supportsDeveloperRole": False,
                        },
                        "models": [{"id": model.model_id} for model in pool.models],
                    }
                }
            },
        },
        {
            "id": "agent-default-model",
            "config": {
                "model": model_id,
                "provider": "litellm",
            },
        },
    ]
    validate_litellm_patch(document, pool=pool, environment=environment, model_id=model_id)
    return document


def render_litellm_patch(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    model_id: str,
) -> str:
    document = litellm_patch_document(pool=pool, environment=environment, model_id=model_id)
    rendered = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    if not rendered.endswith("\n"):
        rendered += "\n"
    loaded = yaml.safe_load(rendered)
    validate_litellm_patch(loaded, pool=pool, environment=environment, model_id=model_id)
    return rendered


def validate_litellm_patch(
    document: Any,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    model_id: str,
) -> None:
    """Raise when the overlay can reach anything but the gateway allowlist."""

    if not isinstance(document, list) or len(document) != 2:
        raise DeepSeekHarnessProfileError("DSH patch must be the LiteLLM overlay")
    providers_entry, default_entry = document
    if not isinstance(providers_entry, dict) or not isinstance(default_entry, dict):
        raise DeepSeekHarnessProfileError("DSH patch entries must be mappings")
    if providers_entry.get("id") != "llm-pi-ai" or default_entry.get("id") != "agent-default-model":
        raise DeepSeekHarnessProfileError("DSH patch must set llm-pi-ai and agent-default-model")
    providers = providers_entry.get("config", {}).get("providers", {})
    if not isinstance(providers, dict) or set(providers) != {"litellm"}:
        raise DeepSeekHarnessProfileError("DSH patch must list only the litellm provider")
    litellm = providers["litellm"]
    if not isinstance(litellm, dict):
        raise DeepSeekHarnessProfileError("litellm provider must be a mapping")
    if litellm.get("api") != "openai-completions":
        raise DeepSeekHarnessProfileError("litellm api must be openai-completions")
    if litellm.get("apiKeyEnv") != LITELLM_API_KEY_ENV or "apiKey" in litellm:
        raise DeepSeekHarnessProfileError("litellm credentials must be the LITELLM_API_KEY env reference")
    if litellm.get("baseURL") != dsh_base_url(environment):
        raise DeepSeekHarnessProfileError("litellm baseURL must be the OHBE gateway")
    models = litellm.get("models")
    expected = [{"id": model.model_id} for model in pool.models]
    if models != expected:
        raise DeepSeekHarnessProfileError("litellm models must be the pool allowlist")
    default = default_entry.get("config", {})
    if default.get("provider") != "litellm" or default.get("model") != model_id:
        raise DeepSeekHarnessProfileError("agent-default-model must be the selected pool member")
    blob = yaml.safe_dump(document).lower()
    for banned in _FORBIDDEN_TEXT:
        if banned in blob:
            raise DeepSeekHarnessProfileError(f"DSH patch contains {banned}")


def check_dsh_binding(
    config: HarnessConfig,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> int:
    """Reject a harness config that does not describe this gateway and pool."""

    normalize_dsh_version(config.version)
    if config.harness_id != "deepseek-harness":
        raise DeepSeekHarnessProfileError("harness_id must be deepseek-harness")
    parameters = config.parameters
    if parameters.get("provider") != "litellm" or parameters.get("profile") != "headless":
        raise DeepSeekHarnessProfileError("harness config must be the LiteLLM headless profile")
    if parameters.get("package") != DSH_PACKAGE or parameters.get("git_tag") != DSH_GIT_TAG:
        raise DeepSeekHarnessProfileError("harness config must record the pinned DSH package and git tag")
    if parameters.get("gateway_host") != environment.gateway_host:
        raise DeepSeekHarnessProfileError("harness gateway host does not match the egress record")
    if parameters.get("gateway_port") != environment.gateway_port:
        raise DeepSeekHarnessProfileError("harness gateway port does not match the egress record")
    if list(parameters.get("models") or []) != [model.model_id for model in pool.models]:
        raise DeepSeekHarnessProfileError("harness model list does not match the pool")
    _require_gateway(environment)
    timeout = parameters.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise DeepSeekHarnessProfileError("timeout_seconds must be a positive integer")
    return timeout


def _require_gateway(environment: ExecutionEnvironment) -> None:
    host = normalize_host(environment.gateway_host)
    if is_denied_provider_host(host):
        raise DeepSeekHarnessProfileError("DeepSeek Harness cannot target a model provider")
