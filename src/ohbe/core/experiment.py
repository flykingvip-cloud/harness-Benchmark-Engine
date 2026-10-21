"""Experiment manifest: the run-level reproducibility record."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import AwareDatetime, OHBEModel, StrictInt, build_sealed
from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.extensions import OracleStub, RoutingRegretStub
from ohbe.core.fingerprint import RuntimeFingerprint
from ohbe.core.harness import HarnessRef
from ohbe.core.integrity import require_sha256, seal_or_verify
from ohbe.core.model import ModelPool
from ohbe.core.pricing import PricingRef
from ohbe.core.task import Task, hash_task_set


class Experiment(OHBEModel):
    """Run-level manifest shared by the executions in one experiment.

    Records schema and package versions (via `fingerprint`), the seed, the
    harness ref, the model pool, the hash of the ordered task set, and, when
    set, the egress policy and image digest in `environment`. v0.1 stores the
    manifest and does not launch the run.
    """

    experiment_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    created_at: AwareDatetime
    seed: StrictInt
    task_set_hash: str
    harness: HarnessRef
    model_pool: ModelPool
    pricing_ref: PricingRef | None = None
    fingerprint: RuntimeFingerprint
    environment: ExecutionEnvironment | None = None
    oracle: OracleStub | None = None
    routing_regret: RoutingRegretStub | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    experiment_hash: str

    @field_validator("task_set_hash", "experiment_hash")
    @classmethod
    def _hashes(cls, value: str, info: ValidationInfo) -> str:
        return require_sha256(value, field=info.field_name or "hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> Experiment:
        seal_or_verify(self, info, "experiment_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> Experiment:
        return build_sealed(cls, "experiment_hash", data)

    def check_task_set(self, tasks: Sequence[Task]) -> None:
        """Raise if `task_set_hash` does not match `tasks` in this order."""

        if self.task_set_hash != hash_task_set(tasks):
            raise ValueError("task_set_hash does not match the given tasks")
