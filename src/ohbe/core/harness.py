"""Harness configuration and the reference stored on a run."""

from __future__ import annotations

from typing import Any

from pydantic import Field, ValidationInfo, field_validator, model_validator

from ohbe.core.base import OHBEModel, build_sealed
from ohbe.core.integrity import require_sha256, seal_or_verify


class HarnessConfig(OHBEModel):
    """Full harness settings.

    `config_hash` covers the id, name, version, parameters, and schema version.
    """

    harness_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    config_hash: str

    @field_validator("config_hash")
    @classmethod
    def _config_hash(cls, value: str) -> str:
        return require_sha256(value, field="config_hash")

    @model_validator(mode="after")
    def _seal(self, info: ValidationInfo) -> HarnessConfig:
        seal_or_verify(self, info, "config_hash")
        return self

    @classmethod
    def build(cls, **data: Any) -> HarnessConfig:
        return build_sealed(cls, "config_hash", data)

    def to_ref(self) -> HarnessRef:
        return HarnessRef(
            harness_id=self.harness_id,
            name=self.name,
            version=self.version,
            config_hash=self.config_hash,
        )


class HarnessRef(OHBEModel):
    """Identity of a harness config without copying its parameters.

    The digest is checked when the full `HarnessConfig` is available.
    """

    harness_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    config_hash: str

    @field_validator("config_hash")
    @classmethod
    def _config_hash(cls, value: str) -> str:
        return require_sha256(value, field="config_hash")

    def matches(self, config: HarnessConfig) -> bool:
        return (
            self.harness_id == config.harness_id
            and self.name == config.name
            and self.version == config.version
            and self.config_hash == config.config_hash
        )
