"""Benchmark task documents."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import SCHEMA_VERSION, OHBEModel, build_sealed
from ohbe.core.integrity import require_sha256, seal_or_verify, sha256_canonical


class Task(OHBEModel):
    """One benchmark task.

    `content_hash` covers every other field, including `schema_version`.
    Build tasks with `Task.build` so the digest is derived rather than chosen.
    """

    task_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        return require_sha256(value, field="content_hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> Task:
        seal_or_verify(self, info, "content_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> Task:
        return build_sealed(cls, "content_hash", data)


def hash_task_set(tasks: Sequence[Task]) -> str:
    """Hash task content digests in the given order.

    Order is part of the identity. An empty sequence has a defined digest.
    """

    return sha256_canonical(
        {
            "schema_version": SCHEMA_VERSION,
            "task_content_hashes": [task.content_hash for task in tasks],
        }
    )
