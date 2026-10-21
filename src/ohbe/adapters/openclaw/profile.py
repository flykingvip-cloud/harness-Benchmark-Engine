"""LiteLLM-only profile for one OpenClaw embedded agent turn.

OpenClaw is an orchestration and channel product. Plugins can replace the
turn executor with DeepSeek Harness, Codex, Claude CLI, or another runtime.
That path is a bridge, not a peer of OpenBot or Inspect ``react()``.

This profile refuses the bridge. It pins npm ``openclaw@2026.10.1`` (git tag
``v2026.10.1``, the catalog tarball) and writes one config for
``openclaw agent exec``:

- provider ``litellm`` only, ``api: openai-completions``
- ``baseUrl`` is ``http://<gateway-host>:<gateway-port>/v1``
- ``apiKey`` is the ``${LITELLM_API_KEY}`` env reference, never a literal
- ``agentRuntime.id`` is the built-in runtime ``openclaw``
- plugins are disabled and the allow list is empty
- channels are empty and delivery is off
- the OpenClaw Gateway section is absent

``latest`` is not a harness version. The rounds whitelist ``0.1.0`` is a
different gate and is not this pin.
"""

from __future__ import annotations

import json
from typing import Any

from ohbe.adapters.openbot.profile import litellm_base_url
from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelPool
from ohbe.core.task import Task
from ohbe.environment.policy import is_denied_provider_host, normalize_host

OPENCLAW_PACKAGE = "openclaw"
OPENCLAW_VERSION = "2026.10.1"
OPENCLAW_GIT_TAG = "v2026.10.1"
OPENCLAW_NPM_SPEC = f"{OPENCLAW_PACKAGE}@{OPENCLAW_VERSION}"
OPENCLAW_PROFILE = "agent-exec"
OPENCLAW_RUNTIME = "openclaw"
OPENCLAW_EXECUTION_PATH = "embedded-agent"
OPENCLAW_PRODUCT_LAYER = "orchestration-channel"
OPENCLAW_PLUGIN_BRIDGE = "refused"
LITELLM_API_KEY_ENV = "LITELLM_API_KEY"
API_KEY_REFERENCE = "${LITELLM_API_KEY}"
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
_TOP_LEVEL = frozenset({"agents", "channels", "messages", "models", "plugins"})


class OpenClawProfileError(ValueError):
    """The embedded-agent profile would bypass LiteLLM or bridge to another runtime."""


def normalize_openclaw_version(version: str) -> str:
    """Accept only the pinned npm release. ``latest`` is rejected."""

    if not isinstance(version, str) or version.strip().lower() in _FLOATING or version != OPENCLAW_VERSION:
        raise OpenClawProfileError(f"OpenClaw version must be {OPENCLAW_VERSION} ({OPENCLAW_GIT_TAG})")
    return version


def openclaw_base_url(environment: ExecutionEnvironment) -> str:
    """OpenAI-compatible gateway origin plus ``/v1``.

    ``api: openai-completions`` posts to ``{baseUrl}/chat/completions``.
    The OpenClaw Gateway is not this URL.
    """

    return litellm_base_url(environment) + "/v1"


def litellm_model_ref(model_id: str) -> str:
    """Pool member addressed as ``litellm/<model_id>``. The first slash is the provider."""

    if not isinstance(model_id, str) or not model_id or model_id.startswith("litellm/"):
        raise OpenClawProfileError("model id must be a pool member, not a litellm/ ref")
    return f"litellm/{model_id}"


def openclaw_harness_config(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int = 600,
    version: str = OPENCLAW_VERSION,
) -> HarnessConfig:
    """Harness document whose version is the pinned OpenClaw npm release.

    ``plugin_bridge`` is ``refused``: this document is the embedded agent,
    not a DeepSeek Harness, Codex, or Claude CLI bridge. It does not put
    OpenClaw in ``HARNESS_ORDER``.
    """

    normalize_openclaw_version(version)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise OpenClawProfileError("timeout_seconds must be a positive integer")
    if not pool.models:
        raise OpenClawProfileError("model pool is empty")
    _require_gateway(environment)
    return HarnessConfig.build(
        harness_id="openclaw",
        name="openclaw",
        version=version,
        parameters={
            "execution_path": OPENCLAW_EXECUTION_PATH,
            "gateway_host": environment.gateway_host,
            "gateway_port": environment.gateway_port,
            "git_tag": OPENCLAW_GIT_TAG,
            "models": [model.model_id for model in pool.models],
            "package": OPENCLAW_PACKAGE,
            "plugin_bridge": OPENCLAW_PLUGIN_BRIDGE,
            "product_layer": OPENCLAW_PRODUCT_LAYER,
            "profile": OPENCLAW_PROFILE,
            "provider": "litellm",
            "runtime": OPENCLAW_RUNTIME,
            "timeout_seconds": timeout_seconds,
        },
    )


def selected_model_id(task: Task, pool: ModelPool) -> str:
    """Pool member this run will pin. A stranger id is not launched."""

    allowed = [model.model_id for model in pool.models]
    if not allowed:
        raise OpenClawProfileError("model pool is empty")
    requested = task.input.get("model_id")
    if isinstance(requested, str) and requested:
        if requested not in allowed:
            raise OpenClawProfileError(f"{requested} is not in the model pool allowlist")
        return requested
    return allowed[0]


def exec_config_document(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    model_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """JSON config for one ``agent exec --config`` run.

    ``models.mode`` is ``replace``, so ambient providers are not merged in.
    ``plugins.enabled`` is false and ``plugins.allow`` is empty, so a
    DeepSeek Harness or other plugin cannot take the turn. ``agentRuntime.id``
    is ``openclaw`` on the provider, on each model, and on the selected ref.
    The legacy alias ``pi`` is not accepted.
    """

    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise OpenClawProfileError("timeout_seconds must be a positive integer")
    if model_id not in {model.model_id for model in pool.models}:
        raise OpenClawProfileError(f"{model_id} is not in the model pool allowlist")
    _require_gateway(environment)
    ref = litellm_model_ref(model_id)
    runtime = {"id": OPENCLAW_RUNTIME}
    document: dict[str, Any] = {
        "agents": {
            "defaults": {
                "model": {"fallbacks": [], "primary": ref},
                "models": {ref: {"agentRuntime": runtime}},
                "sandbox": {"mode": "off"},
                "timeoutSeconds": timeout_seconds,
            }
        },
        "channels": {},
        "messages": {"deliver": False},
        "models": {
            "mode": "replace",
            "providers": {
                "litellm": {
                    "agentRuntime": runtime,
                    "api": "openai-completions",
                    "apiKey": API_KEY_REFERENCE,
                    "baseUrl": openclaw_base_url(environment),
                    "models": [
                        {
                            "agentRuntime": {"id": OPENCLAW_RUNTIME},
                            "id": model.model_id,
                            "name": model.name,
                        }
                        for model in pool.models
                    ],
                }
            },
        },
        "plugins": {"allow": [], "enabled": False},
    }
    validate_exec_config(
        document,
        pool=pool,
        environment=environment,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
    )
    return document


def render_exec_config(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    model_id: str,
    timeout_seconds: int,
) -> str:
    document = exec_config_document(
        pool=pool,
        environment=environment,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
    )
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    loaded = json.loads(rendered)
    validate_exec_config(
        loaded,
        pool=pool,
        environment=environment,
        model_id=model_id,
        timeout_seconds=timeout_seconds,
    )
    return rendered


def validate_exec_config(
    document: Any,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    model_id: str,
    timeout_seconds: int,
) -> None:
    """Raise when the file can reach another provider or another runtime."""

    if not isinstance(document, dict) or set(document) != _TOP_LEVEL:
        raise OpenClawProfileError("OpenClaw config must be the embedded LiteLLM document")
    if "gateway" in document:
        raise OpenClawProfileError("OpenClaw config must not start the OpenClaw Gateway")
    _require_agents(document.get("agents"), model_id=model_id, timeout_seconds=timeout_seconds)
    if document.get("channels") != {}:
        raise OpenClawProfileError("OpenClaw channels must be empty")
    if document.get("messages") != {"deliver": False}:
        raise OpenClawProfileError("OpenClaw delivery must be off")
    _require_providers(document.get("models"), pool=pool, environment=environment)
    plugins = document.get("plugins")
    if plugins != {"allow": [], "enabled": False}:
        raise OpenClawProfileError("OpenClaw plugins must be disabled")
    blob = json.dumps(document).lower()
    for banned in _FORBIDDEN_TEXT:
        if banned in blob:
            raise OpenClawProfileError(f"OpenClaw config contains {banned}")


def check_openclaw_binding(
    config: HarnessConfig,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> int:
    """Reject a harness config that is not this embedded LiteLLM profile."""

    normalize_openclaw_version(config.version)
    if config.harness_id != "openclaw" or config.name != "openclaw":
        raise OpenClawProfileError("harness_id must be openclaw")
    parameters = config.parameters
    expected = {
        "execution_path": OPENCLAW_EXECUTION_PATH,
        "git_tag": OPENCLAW_GIT_TAG,
        "package": OPENCLAW_PACKAGE,
        "plugin_bridge": OPENCLAW_PLUGIN_BRIDGE,
        "product_layer": OPENCLAW_PRODUCT_LAYER,
        "profile": OPENCLAW_PROFILE,
        "provider": "litellm",
        "runtime": OPENCLAW_RUNTIME,
    }
    for key, value in expected.items():
        if parameters.get(key) != value:
            raise OpenClawProfileError(f"harness config {key} must be {value}")
    if parameters.get("gateway_host") != environment.gateway_host:
        raise OpenClawProfileError("harness gateway host does not match the egress record")
    if parameters.get("gateway_port") != environment.gateway_port:
        raise OpenClawProfileError("harness gateway port does not match the egress record")
    if list(parameters.get("models") or []) != [model.model_id for model in pool.models]:
        raise OpenClawProfileError("harness model list does not match the pool")
    _require_gateway(environment)
    timeout = parameters.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise OpenClawProfileError("timeout_seconds must be a positive integer")
    return timeout


def _require_gateway(environment: ExecutionEnvironment) -> None:
    host = normalize_host(environment.gateway_host)
    if is_denied_provider_host(host):
        raise OpenClawProfileError("OpenClaw cannot target a model provider")


def _require_agents(agents: Any, *, model_id: str, timeout_seconds: int) -> None:
    if not isinstance(agents, dict) or set(agents) != {"defaults"}:
        raise OpenClawProfileError("OpenClaw agents must set defaults only")
    defaults = agents["defaults"]
    if not isinstance(defaults, dict):
        raise OpenClawProfileError("OpenClaw agent defaults must be a mapping")
    ref = litellm_model_ref(model_id)
    if defaults.get("model") != {"fallbacks": [], "primary": ref}:
        raise OpenClawProfileError("OpenClaw primary model must be the selected litellm ref")
    if defaults.get("models") != {ref: {"agentRuntime": {"id": OPENCLAW_RUNTIME}}}:
        raise OpenClawProfileError("OpenClaw model policy must pin the built-in runtime")
    if defaults.get("sandbox") != {"mode": "off"}:
        raise OpenClawProfileError("OpenClaw sandbox must be off")
    if defaults.get("timeoutSeconds") != timeout_seconds:
        raise OpenClawProfileError("OpenClaw timeoutSeconds must match the harness timeout")


def _require_providers(models: Any, *, pool: ModelPool, environment: ExecutionEnvironment) -> None:
    if not isinstance(models, dict) or models.get("mode") != "replace":
        raise OpenClawProfileError("OpenClaw models.mode must be replace")
    providers = models.get("providers")
    if not isinstance(providers, dict) or set(providers) != {"litellm"}:
        raise OpenClawProfileError("OpenClaw config must list only the litellm provider")
    litellm = providers["litellm"]
    if not isinstance(litellm, dict):
        raise OpenClawProfileError("litellm provider must be a mapping")
    if set(litellm) != {"agentRuntime", "api", "apiKey", "baseUrl", "models"}:
        raise OpenClawProfileError("litellm provider fields must be the embedded gateway route")
    if litellm.get("agentRuntime") != {"id": OPENCLAW_RUNTIME}:
        raise OpenClawProfileError("litellm runtime must be the built-in openclaw runtime")
    if litellm.get("api") != "openai-completions":
        raise OpenClawProfileError("litellm api must be openai-completions")
    if litellm.get("apiKey") != API_KEY_REFERENCE or "api_key" in litellm:
        raise OpenClawProfileError("litellm credentials must be the LITELLM_API_KEY env reference")
    if litellm.get("baseUrl") != openclaw_base_url(environment):
        raise OpenClawProfileError("litellm baseUrl must be the OHBE gateway")
    expected = [
        {"agentRuntime": {"id": OPENCLAW_RUNTIME}, "id": model.model_id, "name": model.name}
        for model in pool.models
    ]
    if litellm.get("models") != expected:
        raise OpenClawProfileError("litellm models must be the pool allowlist")
