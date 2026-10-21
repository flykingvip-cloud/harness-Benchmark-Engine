"""Shared model-pool intake fields.

OHBE, OpenBot 平台端, and OpenBot 企业端 use these rules. The canonical
write-up is ``docs/model-pool-intake.md``. This module does not open a
socket, does not read an API key from the environment, and does not unlock
spend.
"""

from __future__ import annotations

import hashlib
import re

from ohbe.library import revision_admitted

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_LOCAL_PROVIDERS = frozenset({"vllm", "ollama"})


def credential_fingerprint(api_key: str) -> str:
    """Short fingerprint. The caller has already shape-checked the token."""

    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12]


def litellm_prefix(provider: str) -> str:
    if provider == "vllm":
        return "hosted_vllm/"
    if provider == "ollama":
        return "ollama/"
    return ""


def local_identity_issues(
    *,
    provider: str,
    name: str,
    revision: str,
    model_id: str,
    litellm_model: str,
) -> list[str]:
    """Required local triad. Pricing is addressed by ``model_id``."""

    issues: list[str] = []
    if provider not in _LOCAL_PROVIDERS:
        issues.append("provider")
    if not isinstance(name, str) or _NAME.fullmatch(name) is None or name != name.strip():
        issues.append("name")
    if not revision_admitted(revision):
        issues.append("revision")
    expected_id = f"{provider}/{name}" if provider in _LOCAL_PROVIDERS else ""
    if not expected_id or model_id != expected_id:
        issues.append("model_id")
    prefix = litellm_prefix(provider)
    expected_alias = f"{prefix}{name}" if prefix else ""
    if not expected_alias or litellm_model != expected_alias:
        issues.append("litellm_model")
    for value in (name, revision, model_id, litellm_model):
        if not isinstance(value, str):
            issues.append("secret")
            continue
        lowered = value.lower()
        if "sk-" in lowered or "api_key" in lowered:
            issues.append("secret")
        if "://" in value:
            issues.append("url")
        if "openai.com" in lowered or "anthropic.com" in lowered:
            issues.append("cloud_host")
    return _unique(issues)


def api_key_issues(api_key: str) -> list[str]:
    if (
        not isinstance(api_key, str)
        or len(api_key) < 8
        or len(api_key) > 256
        or any(character.isspace() for character in api_key)
        or "://" in api_key
    ):
        return ["api_key"]
    return []


def cloud_gateway_issues(
    *,
    provider: str,
    name: str,
    revision: str,
    model_id: str,
    litellm_model: str,
    api_key: str,
    gateway_url: str,
) -> list[str]:
    """添加云端 for a vLLM or Ollama alias reached through the operator gateway."""

    issues = local_identity_issues(
        provider=provider,
        name=name,
        revision=revision,
        model_id=model_id,
        litellm_model=litellm_model,
    )
    issues.extend(api_key_issues(api_key))
    if not isinstance(gateway_url, str) or not gateway_url.strip() or gateway_url != gateway_url.strip():
        issues.append("gateway_url")
    elif "://" not in gateway_url:
        issues.append("gateway_url")
    return _unique(issues)


def cloud_provider_issues(*, provider: str, model_id: str, litellm_model: str) -> list[str]:
    """Legacy cloud-provider intake. ``model_id`` is both the alias and the price key."""

    issues: list[str] = []
    if not isinstance(model_id, str) or not model_id.startswith(f"{provider}/") or model_id == f"{provider}/":
        issues.append("model_id")
    if not isinstance(litellm_model, str) or litellm_model != model_id:
        issues.append("litellm_model")
    if isinstance(litellm_model, str):
        lowered = litellm_model.lower()
        if "sk-" in lowered or "api_key" in lowered or "://" in litellm_model:
            issues.append("secret")
    return _unique(issues)


def _unique(issues: list[str]) -> list[str]:
    found: list[str] = []
    for item in issues:
        if item not in found:
            found.append(item)
    return found
