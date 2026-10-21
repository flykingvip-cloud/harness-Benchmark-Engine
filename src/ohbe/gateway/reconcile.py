"""Turn a LiteLLM-shaped response into OHBE usage and a spend signal.

``x-litellm-response-cost`` is the proxy's USD signal. It is not an OHBE
cost. The offline calculator (a later step) is the only writer of
``CostAssessment.computed``. Missing or unusable token counts stay
``missing`` or ``invalid`` and never become zeros.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ohbe.core.pricing import CostAssessment
from ohbe.core.usage import TokenUsage, UsageStatus
from ohbe.gateway.constants import RESPONSE_COST_HEADER

_AMOUNT = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")
SpendStatus = Literal["present", "absent", "invalid"]


@dataclass(frozen=True)
class SpendSignal:
    """Proxy-reported response cost. Absent is not zero."""

    status: SpendStatus
    amount: str | None = None
    currency: str | None = None
    source: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Reconciliation:
    """Usage OHBE will store, plus the proxy spend signal beside it."""

    usage: TokenUsage
    cost: CostAssessment
    spend_signal: SpendSignal


def reconcile_response(
    body: Mapping[str, object] | None,
    headers: Mapping[str, str] | None = None,
) -> Reconciliation:
    """Read usage and response cost. Do not invent either."""

    usage = token_usage_from_body(body)
    signal = spend_signal_from_response(body, headers)
    if usage.status is UsageStatus.PRESENT:
        cost = CostAssessment.not_computed()
    else:
        detail = usage.reason or f"usage {usage.status.value}"
        cost = CostAssessment.invalid(
            reason=f"{detail}; LiteLLM spend is not an OHBE cost",
        )
    return Reconciliation(usage=usage, cost=cost, spend_signal=signal)


def token_usage_from_body(body: Mapping[str, object] | None) -> TokenUsage:
    """Map a chat-completions ``usage`` object onto ``TokenUsage``."""

    if body is None or "usage" not in body or body.get("usage") is None:
        return TokenUsage.missing(reason="litellm response omitted usage")
    raw = body.get("usage")
    if not isinstance(raw, Mapping):
        return TokenUsage.invalid(reason="litellm usage is not an object")
    counts: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if name not in raw or raw.get(name) is None:
            return TokenUsage.invalid(reason=f"litellm usage omitted {name}")
        value = raw.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return TokenUsage.invalid(reason=f"litellm {name} is not an integer")
        if value < 0:
            return TokenUsage.invalid(reason=f"litellm {name} is negative")
        counts[name] = value
    prompt = counts["prompt_tokens"]
    completion = counts["completion_tokens"]
    total = counts["total_tokens"]
    if total != prompt + completion:
        return TokenUsage.invalid(
            reason="litellm total_tokens does not equal prompt_tokens + completion_tokens",
        )
    return TokenUsage.present(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
    )


def spend_signal_from_response(
    body: Mapping[str, object] | None,
    headers: Mapping[str, str] | None = None,
) -> SpendSignal:
    """Read the first cost the proxy actually sent."""

    source = _cost_source(body, headers)
    if source is None:
        return SpendSignal(status="absent", reason="litellm response omitted response cost")
    name, raw = source
    amount = _decimal_amount(raw)
    if amount is None:
        return SpendSignal(
            status="invalid",
            source=name,
            reason="litellm response cost is not a non-negative decimal",
        )
    return SpendSignal(status="present", amount=amount, currency="USD", source=name)


def _cost_source(
    body: Mapping[str, object] | None,
    headers: Mapping[str, str] | None,
) -> tuple[str, object] | None:
    if headers is not None:
        for key, value in headers.items():
            if key.lower() == RESPONSE_COST_HEADER:
                return (f"header:{RESPONSE_COST_HEADER}", value)
    if body is not None and "response_cost" in body:
        return ("body.response_cost", body.get("response_cost"))
    hidden = body.get("_hidden_params") if body is not None else None
    if isinstance(hidden, Mapping) and "response_cost" in hidden:
        return ("body._hidden_params.response_cost", hidden.get("response_cost"))
    return None


def _decimal_amount(value: object) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, Decimal):
        text = format(value, "f")
    elif isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        text = format(Decimal(str(value)), "f")
    else:
        return None
    if _AMOUNT.fullmatch(text) is None:
        return None
    return text
