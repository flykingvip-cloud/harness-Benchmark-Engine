"""LiteLLM-only profile for one WorkBuddy bundled-CLI print turn.

WorkBuddy 5.7.7 publishes desktop installers only: macOS arm64, macOS x64,
and Windows x64. There is no Linux artifact. A click downloads the package
for this OS and does not launch it. Linux returns ``os_unsupported``.
``desktop-install`` stays ``unsupported``.

The non-GUI path is the CodeBuddy Code CLI bundled inside that app
(``codebuddy`` / ``cbc``). This profile runs one ``--print`` turn. It is not
the WorkBuddy GUI, and it does not register WorkBuddy as a model source for
DeepSeek Harness.

The model file is OpenAI chat-completions JSON:

- ``url`` is ``http://<gateway-host>:<gateway-port>/v1/chat/completions``
- ``apiKey`` is ``${LITELLM_API_KEY}``, never a literal
- ``availableModels`` is the pool allowlist
- ``supportsToolCall`` is false

``CODEBUDDY_BASE_URL`` is the Anthropic-protocol override. Public docs use it
to point at a vendor. This profile refuses that variable. ``--model`` is the
pool member id, not a ``litellm/`` prefix.

The desktop pin ``5.7.7`` is not the bundled CLI's ``--version`` string. The
inner package version is not published as a separate pin and is not installed
from npm.
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
from ohbe.preinstall import WORKBUDDY_VERSION

WORKBUDDY_CLI = "codebuddy"
WORKBUDDY_PROFILE = "headless-print"
WORKBUDDY_PRODUCT_LAYER = "desktop-app"
WORKBUDDY_EXECUTION_PATH = "bundled-codebuddy-cli"
WORKBUDDY_MODEL_ROUTE = "openai-chat-completions"
WORKBUDDY_DESKTOP_INSTALL = "unsupported"
WORKBUDDY_BASE_URL_OVERRIDE = "refused"
WORKBUDDY_PLUGIN_BRIDGE = "refused"
WORKBUDDY_DSH_MODEL_SOURCE = "refused"
WORKBUDDY_PERMISSIONS = "not-skipped"
LITELLM_API_KEY_ENV = "LITELLM_API_KEY"
API_KEY_REFERENCE = "${LITELLM_API_KEY}"
DISALLOWED_TOOLS = ("WebFetch", "WebSearch")
_FLOATING = frozenset({"latest", "unpinned", "main", "master"})
_FORBIDDEN_TEXT = (
    "api.openai.com",
    "api.anthropic.com",
    "api.deepseek.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
    "huggingface.co",
    "codebuddy.ai",
    "workbuddy.ai",
    "copilot.tencent.com",
    "sk-",
)
_MODEL_KEYS = frozenset({"apiKey", "id", "name", "supportsToolCall", "url", "vendor"})
_SETTINGS_KEYS = frozenset({"model", "permissions", "variantModels"})


class WorkBuddyProfileError(ValueError):
    """The print profile would leave LiteLLM or drive the desktop GUI."""


def normalize_workbuddy_version(version: str) -> str:
    """Accept only the desktop pin. ``latest`` is rejected.

    This is the WorkBuddy app version from the catalog, not ``codebuddy
    --version``.
    """

    if not isinstance(version, str) or version.strip().lower() in _FLOATING or version != WORKBUDDY_VERSION:
        raise WorkBuddyProfileError(f"WorkBuddy version must be {WORKBUDDY_VERSION}")
    return version


def chat_completions_url(environment: ExecutionEnvironment) -> str:
    """Full OpenAI chat-completions path. CodeBuddy does not append it."""

    return litellm_base_url(environment) + "/v1/chat/completions"


def workbuddy_harness_config(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    timeout_seconds: int = 600,
    version: str = WORKBUDDY_VERSION,
) -> HarnessConfig:
    """Harness document whose version is the desktop pin ``5.7.7``.

    The run is the bundled CLI print path. It does not put WorkBuddy in
    ``HARNESS_ORDER`` and it does not unlock a live spend.
    """

    normalize_workbuddy_version(version)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise WorkBuddyProfileError("timeout_seconds must be a positive integer")
    if not pool.models:
        raise WorkBuddyProfileError("model pool is empty")
    _require_gateway(environment)
    return HarnessConfig.build(
        harness_id="workbuddy",
        name="workbuddy",
        version=version,
        parameters={
            "base_url_override": WORKBUDDY_BASE_URL_OVERRIDE,
            "cli": WORKBUDDY_CLI,
            "desktop_install": WORKBUDDY_DESKTOP_INSTALL,
            "dsh_model_source": WORKBUDDY_DSH_MODEL_SOURCE,
            "execution_path": WORKBUDDY_EXECUTION_PATH,
            "gateway_host": environment.gateway_host,
            "gateway_port": environment.gateway_port,
            "model_route": WORKBUDDY_MODEL_ROUTE,
            "models": [model.model_id for model in pool.models],
            "permissions": WORKBUDDY_PERMISSIONS,
            "plugin_bridge": WORKBUDDY_PLUGIN_BRIDGE,
            "product_layer": WORKBUDDY_PRODUCT_LAYER,
            "profile": WORKBUDDY_PROFILE,
            "provider": "litellm",
            "timeout_seconds": timeout_seconds,
        },
    )


def selected_model_id(task: Task, pool: ModelPool) -> str:
    """Pool member this run will pin. A stranger id is not launched."""

    allowed = [model.model_id for model in pool.models]
    if not allowed:
        raise WorkBuddyProfileError("model pool is empty")
    requested = task.input.get("model_id")
    if isinstance(requested, str) and requested:
        if requested not in allowed:
            raise WorkBuddyProfileError(f"{requested} is not in the model pool allowlist")
        return requested
    return allowed[0]


def models_document(
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> dict[str, Any]:
    """``models.json`` with one OpenAI route per pool member.

    ``supportsToolCall`` is false. This print turn does not advertise tools.
    """

    if not pool.models:
        raise WorkBuddyProfileError("model pool is empty")
    _require_gateway(environment)
    url = chat_completions_url(environment)
    document: dict[str, Any] = {
        "availableModels": [model.model_id for model in pool.models],
        "models": [
            {
                "apiKey": API_KEY_REFERENCE,
                "id": model.model_id,
                "name": model.name,
                "supportsToolCall": False,
                "url": url,
                "vendor": "litellm",
            }
            for model in pool.models
        ],
    }
    validate_models_document(document, pool=pool, environment=environment)
    return document


def settings_document(*, model_id: str, pool: ModelPool) -> dict[str, Any]:
    """Settings that pin the selected model and deny network tools.

    There is no ``env`` block, so a base URL cannot be injected here.
    """

    if model_id not in {model.model_id for model in pool.models}:
        raise WorkBuddyProfileError(f"{model_id} is not in the model pool allowlist")
    document: dict[str, Any] = {
        "model": model_id,
        "permissions": {"deny": list(DISALLOWED_TOOLS)},
        "variantModels": {"lite": model_id, "reasoning": model_id},
    }
    validate_settings_document(document, model_id=model_id)
    return document


def mcp_document() -> dict[str, Any]:
    """Empty MCP map. ``--strict-mcp-config`` refuses every other server."""

    document: dict[str, Any] = {"mcpServers": {}}
    validate_mcp_document(document)
    return document


def render_models_json(*, pool: ModelPool, environment: ExecutionEnvironment) -> str:
    return _render_json(
        models_document(pool=pool, environment=environment),
        lambda loaded: validate_models_document(loaded, pool=pool, environment=environment),
    )


def render_settings_json(*, model_id: str, pool: ModelPool) -> str:
    return _render_json(
        settings_document(model_id=model_id, pool=pool),
        lambda loaded: validate_settings_document(loaded, model_id=model_id),
    )


def render_mcp_json() -> str:
    return _render_json(mcp_document(), validate_mcp_document)


def validate_models_document(
    document: Any,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> None:
    """Raise when the file can name another endpoint or a literal key."""

    if not isinstance(document, dict) or set(document) != {"availableModels", "models"}:
        raise WorkBuddyProfileError("WorkBuddy models.json must be the LiteLLM allowlist")
    expected_ids = [model.model_id for model in pool.models]
    if document.get("availableModels") != expected_ids:
        raise WorkBuddyProfileError("availableModels must be the pool allowlist")
    rows = document.get("models")
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        raise WorkBuddyProfileError("models must be the pool allowlist")
    url = chat_completions_url(environment)
    expected = [
        {
            "apiKey": API_KEY_REFERENCE,
            "id": model.model_id,
            "name": model.name,
            "supportsToolCall": False,
            "url": url,
            "vendor": "litellm",
        }
        for model in pool.models
    ]
    if rows != expected:
        raise WorkBuddyProfileError("each model must use the OHBE chat-completions URL")
    for row in rows:
        if not isinstance(row, dict) or set(row) != _MODEL_KEYS:
            raise WorkBuddyProfileError("model fields must be the LiteLLM chat route")
        if row.get("apiKey") != API_KEY_REFERENCE or "api_key" in row:
            raise WorkBuddyProfileError("model credentials must be the LITELLM_API_KEY env reference")
        if row.get("supportsToolCall") is not False:
            raise WorkBuddyProfileError("WorkBuddy print must not advertise tool calls")
    _reject_forbidden(json.dumps(document))


def validate_settings_document(document: Any, *, model_id: str) -> None:
    if not isinstance(document, dict) or set(document) != _SETTINGS_KEYS:
        raise WorkBuddyProfileError("WorkBuddy settings must pin the model and the tool deny list")
    if document.get("model") != model_id:
        raise WorkBuddyProfileError("settings model must be the selected pool member")
    permissions = document.get("permissions")
    if not isinstance(permissions, dict) or set(permissions) != {"deny"}:
        raise WorkBuddyProfileError("settings permissions must deny network tools")
    if permissions.get("deny") != list(DISALLOWED_TOOLS):
        raise WorkBuddyProfileError("settings must deny WebFetch and WebSearch")
    if document.get("variantModels") != {"lite": model_id, "reasoning": model_id}:
        raise WorkBuddyProfileError("lite and reasoning variants must stay on the selected model")
    if "env" in document:
        raise WorkBuddyProfileError("settings must not carry an env block")
    _reject_forbidden(json.dumps(document))


def validate_mcp_document(document: Any) -> None:
    if document != {"mcpServers": {}}:
        raise WorkBuddyProfileError("WorkBuddy MCP config must be empty")


def check_workbuddy_binding(
    config: HarnessConfig,
    *,
    pool: ModelPool,
    environment: ExecutionEnvironment,
) -> int:
    """Reject a harness config that is not this bundled-CLI print profile."""

    normalize_workbuddy_version(config.version)
    if config.harness_id != "workbuddy" or config.name != "workbuddy":
        raise WorkBuddyProfileError("harness_id must be workbuddy")
    parameters = config.parameters
    expected = {
        "base_url_override": WORKBUDDY_BASE_URL_OVERRIDE,
        "cli": WORKBUDDY_CLI,
        "desktop_install": WORKBUDDY_DESKTOP_INSTALL,
        "dsh_model_source": WORKBUDDY_DSH_MODEL_SOURCE,
        "execution_path": WORKBUDDY_EXECUTION_PATH,
        "model_route": WORKBUDDY_MODEL_ROUTE,
        "permissions": WORKBUDDY_PERMISSIONS,
        "plugin_bridge": WORKBUDDY_PLUGIN_BRIDGE,
        "product_layer": WORKBUDDY_PRODUCT_LAYER,
        "profile": WORKBUDDY_PROFILE,
        "provider": "litellm",
    }
    for key, value in expected.items():
        if parameters.get(key) != value:
            raise WorkBuddyProfileError(f"harness config {key} must be {value}")
    if parameters.get("gateway_host") != environment.gateway_host:
        raise WorkBuddyProfileError("harness gateway host does not match the egress record")
    if parameters.get("gateway_port") != environment.gateway_port:
        raise WorkBuddyProfileError("harness gateway port does not match the egress record")
    if list(parameters.get("models") or []) != [model.model_id for model in pool.models]:
        raise WorkBuddyProfileError("harness model list does not match the pool")
    _require_gateway(environment)
    timeout = parameters.get("timeout_seconds")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise WorkBuddyProfileError("timeout_seconds must be a positive integer")
    return timeout


def _require_gateway(environment: ExecutionEnvironment) -> None:
    host = normalize_host(environment.gateway_host)
    if is_denied_provider_host(host):
        raise WorkBuddyProfileError("WorkBuddy cannot target a model provider")


def _reject_forbidden(blob: str) -> None:
    lowered = blob.lower()
    for banned in _FORBIDDEN_TEXT:
        if banned in lowered:
            raise WorkBuddyProfileError(f"WorkBuddy config contains {banned}")


def _render_json(document: dict[str, Any], validate: Any) -> str:
    rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    validate(json.loads(rendered))
    return rendered
