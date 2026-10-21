"""LiteLLM proxy config and virtual-key bodies for one OHBE model pool.

``model_list`` is the server allowlist: the proxy only routes those
``model_name`` values. A virtual key repeats the same ids in ``models`` and
must not use ``*``. API keys are ``os.environ/NAME`` references, never literals.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.model import ModelDefinition, ModelPool
from ohbe.environment.policy import NetworkEgressPolicy
from ohbe.gateway.constants import LITELLM_IMAGE
from ohbe.gateway.metadata import GatewayCallMetadata, metadata_tags, spend_logs_metadata

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def upstream_model(model: ModelDefinition) -> str:
    """Provider model id stored in ``litellm_params.model``."""

    raw = model.metadata.get("litellm_model")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if "/" in model.model_id:
        return model.model_id
    return f"{model.provider}/{model.name}"


def api_key_env(model: ModelDefinition) -> str | None:
    """Environment variable name for this model's upstream key, if recorded."""

    raw = model.metadata.get("api_key_env")
    if raw is None:
        return None
    if not isinstance(raw, str) or _ENV_NAME.fullmatch(raw) is None:
        raise ValueError("api_key_env must be an uppercase environment variable name")
    return raw


def proxy_config_document(pool: ModelPool) -> dict[str, Any]:
    """JSON-shaped proxy config. Only pool members appear in ``model_list``."""

    model_list: list[dict[str, Any]] = []
    for model in pool.models:
        params: dict[str, str] = {"model": upstream_model(model)}
        env_name = api_key_env(model)
        if env_name is not None:
            params["api_key"] = f"os.environ/{env_name}"
        model_list.append({"model_name": model.model_id, "litellm_params": params})
    return {
        "model_list": model_list,
        "litellm_settings": {"drop_params": True},
        "general_settings": {"master_key": "os.environ/LITELLM_MASTER_KEY"},
    }


def render_proxy_config(pool: ModelPool) -> str:
    """YAML document a LiteLLM OSS proxy can load."""

    document = proxy_config_document(pool)
    lines = [
        f"# OHBE model pool {pool.pool_id}",
        f"# pool_hash: {pool.content_hash}",
        f"# image: {LITELLM_IMAGE}",
        "model_list:",
    ]
    for entry in document["model_list"]:
        lines.append(f"  - model_name: {_yaml_scalar(entry['model_name'])}")
        lines.append("    litellm_params:")
        params = entry["litellm_params"]
        lines.append(f"      model: {_yaml_scalar(params['model'])}")
        if "api_key" in params:
            lines.append(f"      api_key: {params['api_key']}")
    lines.extend(
        [
            "litellm_settings:",
            "  drop_params: true",
            "general_settings:",
            "  master_key: os.environ/LITELLM_MASTER_KEY",
            "",
        ]
    )
    return "\n".join(lines)


def virtual_key_request(
    pool: ModelPool,
    *,
    key_alias: str,
    metadata: GatewayCallMetadata,
) -> dict[str, Any]:
    """Body for ``POST /key/generate`` restricted to this pool."""

    if not isinstance(key_alias, str) or not key_alias or any(ch.isspace() for ch in key_alias):
        raise ValueError("key_alias must be a non-empty token")
    models = [model.model_id for model in pool.models]
    if "*" in models:
        raise ValueError("model pool must not contain a wildcard model id")
    return {
        "key_alias": key_alias,
        "models": models,
        "metadata": {
            "spend_logs_metadata": spend_logs_metadata(metadata),
            "tags": metadata_tags(metadata),
        },
    }


def gateway_environment(
    policy: NetworkEgressPolicy,
    *,
    image_digest: str | None = None,
) -> ExecutionEnvironment:
    """Environment record for a caller that may reach only this LiteLLM proxy."""

    return ExecutionEnvironment(
        egress_policy_version=policy.version,
        gateway_host=policy.gateway_host,
        gateway_port=policy.gateway_port,
        image_ref=LITELLM_IMAGE,
        image_digest=image_digest,
    )


def _yaml_scalar(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./:@+-]+", value):
        return value
    return json.dumps(value, ensure_ascii=False)
