"""OHBE call metadata as LiteLLM spend tags and headers.

LiteLLM OSS 1.104.2 copies the JSON object in
``x-litellm-spend-logs-metadata`` onto spend logs, and splits
``x-litellm-tags`` on commas. The same object is repeated in the JSON body
under ``metadata.spend_logs_metadata``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from ohbe.gateway.constants import SPEND_LOGS_METADATA_HEADER, TAGS_HEADER

_TOKEN = re.compile(r"^[A-Za-z0-9_.:/=+-]+$")
_FIELDS = (
    "benchmark_run_id",
    "execution_id",
    "task_id",
    "harness_id",
    "purpose",
    "invocation_id",
    "model_id",
)
_REQUIRED = _FIELDS[:5]


def _check_token(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError(
            f"{field} must be a non-empty ASCII token without commas or whitespace"
        )


@dataclass(frozen=True)
class GatewayCallMetadata:
    """Identifiers attached to one model call.

    ``benchmark_run_id`` is the OHBE experiment id. ``purpose`` distinguishes
    benchmark traffic from any later non-benchmark call that shares a key.
    """

    benchmark_run_id: str
    execution_id: str
    task_id: str
    harness_id: str
    purpose: str
    invocation_id: str | None = None
    model_id: str | None = None

    def __post_init__(self) -> None:
        for name in _REQUIRED:
            _check_token(getattr(self, name), field=name)
        for name in ("invocation_id", "model_id"):
            value = getattr(self, name)
            if value is not None:
                _check_token(value, field=name)


def spend_logs_metadata(metadata: GatewayCallMetadata) -> dict[str, str]:
    """Return the object LiteLLM stores on the spend log."""

    logged = {
        "benchmark_run_id": metadata.benchmark_run_id,
        "execution_id": metadata.execution_id,
        "task_id": metadata.task_id,
        "harness_id": metadata.harness_id,
        "purpose": metadata.purpose,
    }
    if metadata.invocation_id is not None:
        logged["invocation_id"] = metadata.invocation_id
    if metadata.model_id is not None:
        logged["model_id"] = metadata.model_id
    return logged


def metadata_tags(metadata: GatewayCallMetadata) -> list[str]:
    """Stable ``key:value`` tags. Values cannot contain commas."""

    logged = spend_logs_metadata(metadata)
    return [f"{key}:{logged[key]}" for key in _FIELDS if key in logged]


def spend_logs_metadata_header(metadata: GatewayCallMetadata) -> str:
    """Canonical JSON for ``x-litellm-spend-logs-metadata``."""

    return json.dumps(
        spend_logs_metadata(metadata),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def request_headers(metadata: GatewayCallMetadata, *, api_key: str) -> dict[str, str]:
    """Headers for one proxy chat-completions call."""

    if not isinstance(api_key, str) or not api_key or any(ch.isspace() for ch in api_key):
        raise ValueError("api_key must be a non-empty token")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        SPEND_LOGS_METADATA_HEADER: spend_logs_metadata_header(metadata),
        TAGS_HEADER: ",".join(metadata_tags(metadata)),
    }


def decode_spend_logs_metadata(header: str) -> dict[str, str]:
    """Parse a spend-logs metadata header back into string fields."""

    try:
        parsed = json.loads(header)
    except json.JSONDecodeError as exc:
        raise ValueError("spend logs metadata header is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("spend logs metadata header must be a JSON object")
    decoded: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("spend logs metadata values must be strings")
        decoded[key] = value
    return decoded


def build_chat_body(
    model_name: str,
    messages: list[Mapping[str, str]],
    metadata: GatewayCallMetadata,
) -> dict[str, object]:
    """OpenAI-compatible body whose metadata matches the spend header."""

    if not isinstance(model_name, str) or not model_name:
        raise ValueError("model_name must be a non-empty string")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("each message must be an object")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("each message needs string role and content")
        normalized.append({"role": role, "content": content})
    return {
        "messages": normalized,
        "metadata": {"spend_logs_metadata": spend_logs_metadata(metadata)},
        "model": model_name,
    }
