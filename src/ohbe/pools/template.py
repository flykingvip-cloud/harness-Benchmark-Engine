"""Load a ModelPool template from YAML.

The file names gateway settings by environment variable. It does not contain
a URL, an API key, or observed spend. ``content_hash`` is derived when the
document is built into a ``ModelPool``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.model import ModelDefinition, ModelPool
from ohbe.environment.policy import is_denied_provider_host, normalize_host

POOL_KIND = "ohbe.model_pool"
BASE_URL_ENV = "LITELLM_BASE_URL"
API_KEY_ENV = "LITELLM_API_KEY"
REQUIRED_FORBID_HOSTS = frozenset({"api.openai.com", "api.anthropic.com"})
_LOCAL_PROVIDERS = frozenset({"ollama", "vllm"})
_LOCAL_UPSTREAM = {"ollama": "ollama/", "vllm": "hosted_vllm/"}
_ROOT_KEYS = frozenset(
    {"constraints", "gateway", "kind", "models", "pool_id", "schema_version"}
)
_GATEWAY_KEYS = frozenset({"api_key_env", "base_url_env"})
_CONSTRAINT_KEYS = frozenset({"fail_closed", "forbid_hosts"})
_MODEL_KEYS = frozenset({"metadata", "model_id", "name", "provider", "revision"})
_METADATA_KEYS = frozenset({"litellm_model"})


class PoolTemplateError(ValueError):
    """The pool template cannot be loaded."""


@dataclass(frozen=True)
class GatewayBinding:
    """Environment variable names. The URL and key stay outside the file."""

    base_url_env: str
    api_key_env: str


@dataclass(frozen=True)
class PoolConstraints:
    """Fail closed when a gateway host is a cloud model provider."""

    fail_closed: bool
    forbid_hosts: tuple[str, ...]


@dataclass(frozen=True)
class PoolTemplate:
    """A checked template and the ``ModelPool`` built from its models."""

    pool: ModelPool
    gateway: GatewayBinding
    constraints: PoolConstraints
    source: str


def repository_root() -> Path:
    """Checkout that contains this package and the experiment files."""

    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / "experiments").is_dir():
            return parent
    raise PoolTemplateError("OHBE checkout root was not found")


def load_pool_template(path: str | Path) -> PoolTemplate:
    """Read one pool YAML file and build a sealed ``ModelPool``."""

    file = Path(path)
    try:
        raw = file.read_bytes()
    except OSError as exc:
        raise PoolTemplateError(f"could not read model pool: {exc}") from exc
    return parse_pool_template(decode_pool_text(raw), str(file))


def decode_pool_text(raw: bytes) -> str:
    """Reject a BOM, CR bytes, and text that is not UTF-8."""

    if raw.startswith(b"\xef\xbb\xbf"):
        raise PoolTemplateError("model pool file must not start with a BOM")
    if b"\r" in raw:
        raise PoolTemplateError("model pool file must use LF newlines")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PoolTemplateError("model pool file is not valid UTF-8") from exc


def parse_pool_template(text: str, source: str) -> PoolTemplate:
    """Validate pool YAML text. ``source`` is recorded; it is not fetched."""

    if text.startswith("\ufeff"):
        raise PoolTemplateError("model pool file must not start with a BOM")
    if "\r" in text:
        raise PoolTemplateError("model pool file must use LF newlines")
    if "sk-" in text.lower():
        raise PoolTemplateError("model pool file must not contain a secret")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PoolTemplateError(f"model pool file is not YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PoolTemplateError("model pool file must be a YAML mapping")
    _exact_keys(loaded, _ROOT_KEYS, "model pool")
    if loaded.get("schema_version") != SCHEMA_VERSION:
        raise PoolTemplateError(f"schema_version must be {SCHEMA_VERSION}")
    if loaded.get("kind") != POOL_KIND:
        raise PoolTemplateError(f"kind must be {POOL_KIND}")
    _reject_embedded_targets(loaded, "model pool")
    pool_id = _text(loaded.get("pool_id"), "pool_id")
    gateway = _gateway(loaded.get("gateway"))
    constraints = _constraints(loaded.get("constraints"))
    definitions = model_definitions(loaded.get("models"))
    pool = ModelPool.build(pool_id=pool_id, models=definitions)
    return PoolTemplate(pool=pool, gateway=gateway, constraints=constraints, source=source)


def model_definitions(value: object) -> tuple[ModelDefinition, ...]:
    """Parse the model list shared by a pool file and an inline manifest."""

    if not isinstance(value, list) or not value:
        raise PoolTemplateError("model_pool.models must be a non-empty list")
    models: list[ModelDefinition] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise PoolTemplateError(f"model_pool.models[{index}] must be a mapping")
        label = f"model_pool.models[{index}]"
        _exact_keys(item, _MODEL_KEYS, label)
        model_id = _text(item.get("model_id"), "model_id")
        if model_id in seen:
            raise PoolTemplateError(f"duplicate model_id {model_id}")
        seen.add(model_id)
        provider = _text(item.get("provider"), "provider").lower()
        if provider not in _LOCAL_PROVIDERS:
            raise PoolTemplateError("live model pool only allows vllm and ollama backends")
        if not model_id.startswith(f"{provider}/") or model_id == f"{provider}/":
            raise PoolTemplateError(f"model_id must look like {provider}/<name>")
        if is_denied_provider_host(model_id) or "openai.com" in model_id.lower() or "anthropic.com" in model_id.lower():
            raise PoolTemplateError("model_id must not name an OpenAI or Anthropic host")
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            raise PoolTemplateError("model metadata must name the LiteLLM upstream id")
        if "api_key_env" in metadata or "api_key" in metadata:
            raise PoolTemplateError("live model pool does not accept a cloud provider API key")
        _exact_keys(metadata, _METADATA_KEYS, "model.metadata")
        litellm_model = _text(metadata.get("litellm_model"), "litellm_model")
        prefix = _LOCAL_UPSTREAM[provider]
        if not litellm_model.startswith(prefix):
            raise PoolTemplateError(f"litellm_model for {provider} must start with {prefix}")
        if "openai.com" in litellm_model.lower() or "anthropic.com" in litellm_model.lower():
            raise PoolTemplateError("litellm_model must not name an OpenAI or Anthropic host")
        models.append(
            ModelDefinition(
                model_id=model_id,
                provider=provider,
                name=_text(item.get("name"), "name"),
                revision=_text(item.get("revision"), "revision"),
                metadata={"litellm_model": litellm_model},
            )
        )
    return tuple(models)


def _gateway(value: object) -> GatewayBinding:
    if not isinstance(value, dict):
        raise PoolTemplateError("gateway must be a mapping")
    _exact_keys(value, _GATEWAY_KEYS, "gateway")
    base_url_env = _text(value.get("base_url_env"), "gateway.base_url_env")
    api_key_env = _text(value.get("api_key_env"), "gateway.api_key_env")
    if base_url_env != BASE_URL_ENV:
        raise PoolTemplateError(f"gateway.base_url_env must be {BASE_URL_ENV}")
    if api_key_env != API_KEY_ENV:
        raise PoolTemplateError(f"gateway.api_key_env must be {API_KEY_ENV}")
    return GatewayBinding(base_url_env=base_url_env, api_key_env=api_key_env)


def _constraints(value: object) -> PoolConstraints:
    if not isinstance(value, dict):
        raise PoolTemplateError("constraints must be a mapping")
    _exact_keys(value, _CONSTRAINT_KEYS, "constraints")
    if value.get("fail_closed") is not True:
        raise PoolTemplateError("pool constraints must be fail-closed")
    raw_hosts = value.get("forbid_hosts")
    if not isinstance(raw_hosts, list) or not raw_hosts:
        raise PoolTemplateError("constraints.forbid_hosts must be a non-empty list")
    hosts: list[str] = []
    for index, item in enumerate(raw_hosts):
        host = normalize_host(_text(item, f"constraints.forbid_hosts[{index}]"))
        if not host or "://" in host or "/" in host:
            raise PoolTemplateError("constraints.forbid_hosts entries must be hostnames")
        hosts.append(host)
    if len(hosts) != len(set(hosts)):
        raise PoolTemplateError("constraints.forbid_hosts contains a duplicate")
    if not REQUIRED_FORBID_HOSTS <= set(hosts):
        raise PoolTemplateError("pool constraints must forbid api.openai.com and api.anthropic.com")
    return PoolConstraints(fail_closed=True, forbid_hosts=tuple(hosts))


def _reject_embedded_targets(value: object, path: str, *, in_forbid: bool = False) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_embedded_targets(item, f"{path}.{key}", in_forbid=in_forbid or key == "forbid_hosts")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_embedded_targets(item, f"{path}[{index}]", in_forbid=in_forbid)
        return
    if not isinstance(value, str):
        return
    if "://" in value:
        raise PoolTemplateError(f"{path} must not contain a URL")
    if in_forbid:
        return
    lowered = value.lower()
    if "openai.com" in lowered or "anthropic.com" in lowered:
        raise PoolTemplateError(f"{path} must not name an OpenAI or Anthropic host")


def _exact_keys(value: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise PoolTemplateError(f"{label} has unknown fields: {', '.join(sorted(extra))}")


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PoolTemplateError(f"{field} must be a non-empty string")
    return value
