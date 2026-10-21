"""Shared OHBE schema base.

Documents are frozen Pydantic models. Nested dict payloads are ordinary Python
objects; treat them as immutable after validation so stored hashes stay true.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer

SCHEMA_VERSION = "0.1"

StrictInt = Annotated[int, Field(strict=True)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


def _parse_aware_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("datetime must be an ISO-8601 timestamp") from exc
    else:
        raise ValueError("datetime must be an ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError("datetime must be timezone-aware")
    return parsed.astimezone(UTC)


def _serialize_aware_datetime(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}Z"


AwareDatetime = Annotated[
    datetime,
    BeforeValidator(_parse_aware_datetime),
    PlainSerializer(_serialize_aware_datetime, when_used="json"),
]


class OHBEModel(BaseModel):
    """Base for every versioned OHBE document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["0.1"] = Field(
        default=SCHEMA_VERSION,
        description="OHBE core schema contract. Documents from other contracts are rejected.",
    )


def build_sealed[ModelT: OHBEModel](
    cls: type[ModelT],
    hash_field: str,
    data: Mapping[str, Any],
) -> ModelT:
    """Validate `data` and fill `hash_field` from the canonical document."""

    from ohbe.core.integrity import HASH_PLACEHOLDER

    if hash_field in data:
        raise TypeError(f"{hash_field} is derived; omit it")
    payload = dict(data)
    payload[hash_field] = HASH_PLACEHOLDER
    return cls.model_validate(payload, context={"ohbe_seal": cls.__name__})
