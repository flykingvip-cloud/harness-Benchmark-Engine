"""Token usage that distinguishes missing data from a real zero."""

from __future__ import annotations

from enum import Enum

from pydantic import model_validator

from ohbe.core.base import NonNegativeInt, OHBEModel


class UsageStatus(str, Enum):
    """Whether token counts are usable.

    `missing` means the source did not report usage.
    `invalid` means a usage object was present but cannot be trusted.
    `present` means every count was supplied, including an explicit zero.
    """

    PRESENT = "present"
    MISSING = "missing"
    INVALID = "invalid"


class TokenUsage(OHBEModel):
    """Provider token counts.

    Defaults are null. Constructing a usage value without counts yields
    `missing` only through `TokenUsage.missing` or `ModelInvocation`'s
    default. Counts of zero are stored only when status is `present` and the
    caller passed those zeros in.
    """

    status: UsageStatus
    prompt_tokens: NonNegativeInt | None = None
    completion_tokens: NonNegativeInt | None = None
    total_tokens: NonNegativeInt | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _counts_follow_status(self) -> TokenUsage:
        counts = (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        if self.status is UsageStatus.PRESENT:
            if any(count is None for count in counts):
                raise ValueError(
                    "present usage requires prompt_tokens, completion_tokens, and total_tokens"
                )
            if self.reason is not None:
                raise ValueError("present usage does not take a reason")
            return self
        if any(count is not None for count in counts):
            raise ValueError(f"{self.status.value} usage must not carry token counts")
        return self

    @classmethod
    def missing(cls, *, reason: str | None = None) -> TokenUsage:
        return cls(status=UsageStatus.MISSING, reason=reason)

    @classmethod
    def invalid(cls, *, reason: str | None = None) -> TokenUsage:
        return cls(status=UsageStatus.INVALID, reason=reason)

    @classmethod
    def present(
        cls,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
    ) -> TokenUsage:
        return cls(
            status=UsageStatus.PRESENT,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
