"""One model call recorded inside an execution."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import AwareDatetime, NonNegativeInt, OHBEModel, build_sealed
from ohbe.core.integrity import require_sha256, seal_or_verify, sha256_canonical
from ohbe.core.model import ModelDefinition
from ohbe.core.pricing import CostAssessment, CostStatus, PricingRef
from ohbe.core.usage import TokenUsage, UsageStatus


class InvocationStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _missing_usage() -> TokenUsage:
    return TokenUsage.missing()


def _not_computed_cost() -> CostAssessment:
    return CostAssessment.not_computed()


class ModelInvocation(OHBEModel):
    """A single model call.

    `usage` defaults to missing, and `cost` defaults to not computed. Neither
    default is a numeric zero. When `request` or `response` bodies are stored
    on the document, their hashes must match the canonical JSON of those bodies.
    Bodies may also live outside the document; in that case only the hash is set.
    """

    invocation_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    seq: NonNegativeInt
    model: ModelDefinition
    status: InvocationStatus
    request_hash: str
    response_hash: str | None = None
    request: dict[str, Any] | None = None
    response: dict[str, Any] | None = None
    usage: TokenUsage = Field(default_factory=_missing_usage)
    pricing_ref: PricingRef | None = None
    cost: CostAssessment = Field(default_factory=_not_computed_cost)
    latency_ms: NonNegativeInt | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    error: str | None = None
    invocation_hash: str

    @field_validator("usage", mode="before")
    @classmethod
    def _null_usage_is_missing(cls, value: Any) -> Any:
        if value is None:
            return TokenUsage.missing()
        return value

    @field_validator("cost", mode="before")
    @classmethod
    def _null_cost_is_not_computed(cls, value: Any) -> Any:
        if value is None:
            return CostAssessment.not_computed()
        return value

    @field_validator("request_hash")
    @classmethod
    def _request_hash(cls, value: str) -> str:
        return require_sha256(value, field="request_hash")

    @field_validator("response_hash")
    @classmethod
    def _response_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_sha256(value, field="response_hash")

    @field_validator("invocation_hash")
    @classmethod
    def _invocation_hash(cls, value: str) -> str:
        return require_sha256(value, field="invocation_hash")

    @model_validator(mode="after")
    def _invariants(self, info: ValidationInfo) -> ModelInvocation:
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at is earlier than started_at")
        dumped = self.model_dump(mode="json")
        if self.request is not None:
            expected_request = sha256_canonical(dumped["request"])
            if self.request_hash != expected_request:
                raise ValueError("request_hash does not match the canonical request")
        if self.response is not None:
            if self.response_hash is None:
                raise ValueError("response_hash is required when response is recorded")
            expected_response = sha256_canonical(dumped["response"])
            if self.response_hash != expected_response:
                raise ValueError("response_hash does not match the canonical response")
        if self.usage.status is not UsageStatus.PRESENT and (
            self.cost.status is CostStatus.COMPUTED or self.cost.amount is not None
        ):
            raise ValueError(
                "cost cannot be computed when token usage is missing or invalid"
            )
        seal_or_verify(self, info, "invocation_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> ModelInvocation:
        if data.get("request") is not None and "request_hash" not in data:
            data["request_hash"] = sha256_canonical(data["request"])
        if data.get("response") is not None and "response_hash" not in data:
            data["response_hash"] = sha256_canonical(data["response"])
        if "request_hash" not in data:
            raise TypeError("request_hash is required when request is omitted")
        return build_sealed(cls, "invocation_hash", data)
