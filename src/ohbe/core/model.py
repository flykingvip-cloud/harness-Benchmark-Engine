"""Model identity and the pool a harness may call."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import OHBEModel, build_sealed
from ohbe.core.integrity import require_sha256, seal_or_verify
from ohbe.core.pricing import PricingRef


class ModelDefinition(OHBEModel):
    """A model the benchmark can name.

    `pricing_ref` is optional. Absence means "no table recorded", not a free model.
    """

    model_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    name: str = Field(min_length=1)
    revision: str | None = None
    pricing_ref: PricingRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelPool(OHBEModel):
    """Ordered set of models.

    Order and membership are part of `content_hash`. Duplicate `model_id`
    values are rejected.
    """

    pool_id: str = Field(min_length=1)
    models: tuple[ModelDefinition, ...] = Field(min_length=1)
    content_hash: str

    @field_validator("content_hash")
    @classmethod
    def _content_hash(cls, value: str) -> str:
        return require_sha256(value, field="content_hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> ModelPool:
        ids = [model.model_id for model in self.models]
        if len(ids) != len(set(ids)):
            raise ValueError("model_pool contains duplicate model_id values")
        seal_or_verify(self, info, "content_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> ModelPool:
        return build_sealed(cls, "content_hash", data)
