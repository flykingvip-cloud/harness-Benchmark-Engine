"""Canonical JSON hashes and the append-only trace chain.

The hash chain detects a changed, reordered, or partially rewritten record.
It is not a signature: a caller who rewrites a suffix and every following
digest can produce another internally valid chain. Pin the head hash once a
run is sealed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ValidationInfo

GENESIS_HASH = "0" * 64
# Input sentinel only. build_sealed replaces it before a model is returned.
HASH_PLACEHOLDER = GENESIS_HASH

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class TraceIntegrityError(ValueError):
    """A JSONL trace failed an integrity check."""

    def __init__(self, message: str, *, index: int | None = None) -> None:
        self.index = index
        if index is None:
            super().__init__(message)
        else:
            super().__init__(f"{message} (index {index})")


def canonical_json(data: Mapping[str, Any]) -> str:
    """Serialize a JSON object with sorted keys and no insignificant whitespace."""

    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_canonical(data: Mapping[str, Any]) -> str:
    """Return the lowercase sha256 hex digest of `canonical_json(data)`."""

    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def require_sha256(value: str, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase sha256 hex digest")
    return value


def canonical_model_hash(model: BaseModel, *, exclude: Collection[str] = ()) -> str:
    """Hash the JSON form of a model, excluding derived hash fields."""

    payload = model.model_dump(mode="json", exclude=set(exclude))
    return sha256_canonical(payload)


def seal_or_verify(model: BaseModel, info: ValidationInfo, field: str) -> None:
    """Fill `field` while sealing, or reject a document whose digest does not match.

    Sealing is enabled only for the model named by ``context["ohbe_seal"]``.
    Nested models keep verifying their own digests, so a parent seal cannot
    recompute a child hash that was not supplied.
    """

    expected = canonical_model_hash(model, exclude={field})
    sealing = bool(info.context and info.context.get("ohbe_seal") == type(model).__name__)
    if sealing:
        object.__setattr__(model, field, expected)
        confirmed = canonical_model_hash(model, exclude={field})
        if confirmed != expected:
            raise RuntimeError(f"{field} changed while sealing")
        return
    if getattr(model, field) != expected:
        raise ValueError(f"{field} does not match canonical content")


class _ChainedRecord(Protocol):
    seq: int
    prev_hash: str
    record_hash: str


def verify_chain(records: Sequence[_ChainedRecord]) -> str:
    """Check a hash chain and return its head digest.

    An empty chain's head is `GENESIS_HASH`. Sequence numbers must be
    ``0..n-1``. Each `prev_hash` must equal the previous record's
    `record_hash`.
    """

    previous = GENESIS_HASH
    for index, record in enumerate(records):
        if record.seq != index:
            raise TraceIntegrityError(
                f"record seq {record.seq} is not contiguous",
                index=index,
            )
        if record.prev_hash != previous:
            raise TraceIntegrityError(
                "prev_hash does not continue the chain",
                index=index,
            )
        expected = canonical_model_hash(record, exclude={"record_hash"})  # type: ignore[arg-type]
        if record.record_hash != expected:
            raise TraceIntegrityError("record_hash mismatch", index=index)
        previous = record.record_hash
    return previous
