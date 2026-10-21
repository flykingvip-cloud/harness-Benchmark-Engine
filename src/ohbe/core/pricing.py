"""Versioned pricing references.

OHBE core stores which price table a later offline calculator should use.
It does not look up rates or multiply them by token counts.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from ohbe.core.base import OHBEModel
from ohbe.core.integrity import require_sha256

_AMOUNT_RE = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class PricingRef(OHBEModel):
    """Pointer to an external pricing table.

    `content_hash`, when set, is the digest of that table's bytes. It is not
    a hash of this object, and core does not fetch `uri`.
    """

    pricing_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    uri: str | None = None
    content_hash: str | None = None

    @field_validator("content_hash")
    @classmethod
    def _external_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return require_sha256(value, field="content_hash")


class CostStatus(str, Enum):
    """Whether a numeric cost exists.

    `not_computed` means the offline calculator has not run.
    `invalid` means a cost must not be invented from the usage on hand.
    `computed` is reserved for a later calculator and requires an explicit amount.
    """

    NOT_COMPUTED = "not_computed"
    INVALID = "invalid"
    COMPUTED = "computed"


def _require_amount(value: str) -> str:
    if not isinstance(value, str) or _AMOUNT_RE.fullmatch(value) is None:
        raise ValueError("amount must be a base-10 decimal string")
    number = Decimal(value)
    if number == 0 and value.startswith("-"):
        raise ValueError("negative zero amount is not allowed")
    return value


class CostAssessment(OHBEModel):
    """A cost figure that is never silently zero.

    Omitted and not-yet-calculated costs keep `amount` null. A zero price is
    stored only when status is `computed` and the amount was supplied.
    """

    status: CostStatus = CostStatus.NOT_COMPUTED
    amount: str | None = None
    currency: str | None = None
    pricing_ref: PricingRef | None = None
    reason: str | None = None

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_is_string(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        raise ValueError("amount must be a decimal string or null")

    @model_validator(mode="after")
    def _amount_follows_status(self) -> CostAssessment:
        if self.status is CostStatus.COMPUTED:
            if self.amount is None or self.currency is None or self.pricing_ref is None:
                raise ValueError("computed cost requires amount, currency, and pricing_ref")
            if self.reason is not None:
                raise ValueError("computed cost does not take a reason")
            _require_amount(self.amount)
            if _CURRENCY_RE.fullmatch(self.currency) is None:
                raise ValueError("currency must be a 3-letter uppercase ISO code")
            return self
        if self.amount is not None or self.currency is not None or self.pricing_ref is not None:
            raise ValueError("only a computed cost may carry amount, currency, or pricing_ref")
        if self.status is CostStatus.NOT_COMPUTED and self.reason is not None:
            raise ValueError("not_computed cost does not take a reason; mark it invalid")
        return self

    @classmethod
    def not_computed(cls) -> CostAssessment:
        return cls(status=CostStatus.NOT_COMPUTED)

    @classmethod
    def invalid(cls, *, reason: str | None = None) -> CostAssessment:
        return cls(status=CostStatus.INVALID, reason=reason)

    @classmethod
    def computed(
        cls,
        *,
        amount: str,
        currency: str,
        pricing_ref: PricingRef,
    ) -> CostAssessment:
        return cls(
            status=CostStatus.COMPUTED,
            amount=amount,
            currency=currency,
            pricing_ref=pricing_ref,
        )


def select_pricing_ref(
    *,
    invocation: PricingRef | None = None,
    model: PricingRef | None = None,
    experiment: PricingRef | None = None,
) -> PricingRef | None:
    """Return the most specific pricing reference, or None.

    An invocation reference overrides the model, which overrides the
    experiment manifest. This does not compute a price. If every input is
    missing, the result is None rather than a zero cost.
    """

    if invocation is not None:
        return invocation
    if model is not None:
        return model
    return experiment
