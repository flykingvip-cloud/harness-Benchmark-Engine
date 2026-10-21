"""One harness execution of one task."""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import AwareDatetime, OHBEModel, StrictInt, build_sealed
from ohbe.core.extensions import OracleStub, RoutingRegretStub
from ohbe.core.fingerprint import RuntimeFingerprint
from ohbe.core.harness import HarnessConfig, HarnessRef
from ohbe.core.integrity import require_sha256, seal_or_verify
from ohbe.core.model import ModelPool
from ohbe.core.task import Task

_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionEnvironment(OHBEModel):
    """Isolation record for the process that calls models.

    `egress_policy_version` is the allowlist contract. `image_digest` is the
    Docker image id or repo digest the runner actually used. Core stores these
    fields and does not start containers.
    """

    egress_policy_version: str = Field(min_length=1)
    gateway_host: str = Field(min_length=1)
    gateway_port: Annotated[int, Field(strict=True, ge=1, le=65535)]
    image_ref: str | None = None
    image_digest: str | None = None

    @field_validator("gateway_host")
    @classmethod
    def _gateway_host(cls, value: str) -> str:
        if "://" in value or "/" in value or any(ch.isspace() for ch in value):
            raise ValueError("gateway_host must be a hostname or IP, not a URL")
        return value

    @field_validator("image_ref")
    @classmethod
    def _image_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("image_ref must be a non-empty image reference")
        return value

    @field_validator("image_digest")
    @classmethod
    def _image_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _IMAGE_DIGEST.fullmatch(value) is None:
            raise ValueError("image_digest must be sha256:<64 lowercase hex>")
        return value


class Execution(OHBEModel):
    """Per-task run record.

    The record stores hashes of the task, harness config, and model pool so
    the binding can be checked when those documents are in hand. `seed` is the
    execution seed. `environment`, when set, records the egress policy and
    image digest used for the process that calls models. Oracle and routing
    regret are optional stubs.
    """

    execution_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_content_hash: str
    harness: HarnessRef
    model_pool_id: str = Field(min_length=1)
    model_pool_hash: str
    seed: StrictInt
    status: ExecutionStatus
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    fingerprint: RuntimeFingerprint
    environment: ExecutionEnvironment | None = None
    oracle: OracleStub | None = None
    routing_regret: RoutingRegretStub | None = None
    execution_hash: str

    @field_validator("task_content_hash", "model_pool_hash", "execution_hash")
    @classmethod
    def _hashes(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field=info.field_name or "hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> Execution:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at is earlier than started_at")
        seal_or_verify(self, info, "execution_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> Execution:
        return build_sealed(cls, "execution_hash", data)

    def check_binding(self, *, task: Task, harness: HarnessConfig, pool: ModelPool) -> None:
        """Raise if this execution does not name these exact documents."""

        if self.task_id != task.task_id or self.task_content_hash != task.content_hash:
            raise ValueError("execution is not bound to this task")
        if not self.harness.matches(harness):
            raise ValueError("execution is not bound to this harness")
        if self.model_pool_id != pool.pool_id or self.model_pool_hash != pool.content_hash:
            raise ValueError("execution is not bound to this model pool")
