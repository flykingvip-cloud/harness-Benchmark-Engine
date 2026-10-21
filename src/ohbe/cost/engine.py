"""Offline cost from a trace and a pinned pricing table.

Token counts come from `invocation.completed` events. `cost_amount` on those
events is ignored, including an explicit zero. Missing or invalid usage makes
both cost fields invalid with a null amount. A present zero stays a computed
zero.

`provider_cost` is the provider token invoice. For a `tokens` meter it is the
list price. For `normalized_gpu_seconds` it is the computed zero `0`: the
weights are local and there is no provider token invoice. That zero is written
only when the GPU-second price is also computed. It is not a fill-in for
missing usage and it is not the proxy response cost.

`normalized_cost` is the comparable figure, and it is `cost` / the execution
total. A token meter copies the list price. A GPU-second meter is
`normalized_gpu_seconds * (gpu_cost_per_hour / 3600)`. The seconds come from
the event. They are not inferred from tokens, latency, or a response cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ohbe.core.pricing import CostAssessment, PricingRef
from ohbe.core.trace import ExecutionTrace, TraceEventType
from ohbe.core.usage import TokenUsage, UsageStatus
from ohbe.cost.decimal import format_amount, parse_decimal
from ohbe.cost.table import (
    GpuSecondModelPrice,
    PricingError,
    PricingTable,
    TokenModelPrice,
)

_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class InvocationCost:
    """Price of one recorded model call. Amounts are null unless computed."""

    record_id: str
    model_id: str | None
    usage: TokenUsage
    provider_cost: CostAssessment
    normalized_cost: CostAssessment
    cost: CostAssessment


@dataclass(frozen=True)
class ExecutionCost:
    """Total model cost for one trace. Partial sums are not filled in.

    `total` is `normalized_cost`. `provider_cost` is summed on its own.
    """

    execution_id: str
    trace_id: str
    head_hash: str
    invocations: tuple[InvocationCost, ...]
    provider_cost: CostAssessment
    normalized_cost: CostAssessment
    total: CostAssessment


def price_trace(
    trace: ExecutionTrace,
    *,
    table: PricingTable,
    pricing_ref: PricingRef,
) -> ExecutionCost:
    """Recompute invocation costs and the execution total from `trace`."""

    _require_pin(table, pricing_ref)
    default_model = _started_model_id(trace)
    invocations = tuple(
        _price_invocation(record.record_id, record.payload, default_model, table, pricing_ref)
        for record in trace.records
        if record.event_type == TraceEventType.INVOCATION_COMPLETED
    )
    provider, normalized = _totals(invocations, pricing_ref)
    return ExecutionCost(
        execution_id=trace.execution_id,
        trace_id=trace.trace_id,
        head_hash=trace.head_hash,
        invocations=invocations,
        provider_cost=provider,
        normalized_cost=normalized,
        total=normalized,
    )


def _require_pin(table: PricingTable, pricing_ref: PricingRef) -> None:
    if pricing_ref.pricing_id != table.pricing_id or pricing_ref.version != table.version:
        raise PricingError("pricing ref id or version does not match the table")
    if pricing_ref.content_hash != table.content_hash:
        raise PricingError("pricing ref content_hash does not match the table bytes")


def _started_model_id(trace: ExecutionTrace) -> str | None:
    for record in trace.records:
        if record.event_type != TraceEventType.EXECUTION_STARTED:
            continue
        model_id = record.payload.get("model_id")
        if isinstance(model_id, str) and model_id:
            return model_id
    return None


def _price_invocation(
    record_id: str,
    payload: dict[str, object],
    default_model: str | None,
    table: PricingTable,
    pricing_ref: PricingRef,
) -> InvocationCost:
    model_id = payload.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        model_id = default_model
    named = model_id if isinstance(model_id, str) else None
    usage = usage_from_payload(payload)
    if usage.status is not UsageStatus.PRESENT:
        reason = usage.reason or f"usage {usage.status.value}"
        return _invalid(record_id, named, usage, reason)
    if not isinstance(model_id, str):
        return _invalid(record_id, None, usage, "invocation has no model_id")
    if usage.prompt_tokens is None or usage.completion_tokens is None:
        return _invalid(record_id, model_id, usage, "present usage omitted a token count")
    price = table.price_for(model_id)
    if price is None:
        return _invalid(record_id, model_id, usage, f"{model_id} is not in the pricing table")
    if isinstance(price, TokenModelPrice):
        prompt = Decimal(usage.prompt_tokens) * price.prompt_per_million
        completion = Decimal(usage.completion_tokens) * price.completion_per_million
        amount = _computed((prompt + completion) / _MILLION, table, pricing_ref)
        return InvocationCost(
            record_id=record_id,
            model_id=model_id,
            usage=usage,
            provider_cost=amount,
            normalized_cost=amount,
            cost=amount,
        )
    if isinstance(price, GpuSecondModelPrice):
        seconds, reason = _gpu_seconds(payload)
        if seconds is None:
            return _invalid(record_id, model_id, usage, reason or "normalized_gpu_seconds was not reported")
        rate = table.gpu_for(price.gpu_type)
        if rate is None:
            return _invalid(record_id, model_id, usage, f"{price.gpu_type} is not in the pricing table")
        normalized = _computed(seconds * rate.usd_per_second, table, pricing_ref)
        provider = _computed(Decimal(0), table, pricing_ref)
        return InvocationCost(
            record_id=record_id,
            model_id=model_id,
            usage=usage,
            provider_cost=provider,
            normalized_cost=normalized,
            cost=normalized,
        )
    return _invalid(record_id, model_id, usage, "pricing meter is not supported")


def _gpu_seconds(payload: dict[str, object]) -> tuple[Decimal | None, str | None]:
    """Read the meter's quantity. Absent is missing, not zero.

    A JSON float is rejected. Whole seconds may be an int. Fractional seconds
    must be a decimal string.
    """

    if "normalized_gpu_seconds" not in payload or payload.get("normalized_gpu_seconds") is None:
        return None, "normalized_gpu_seconds was not reported"
    value = payload.get("normalized_gpu_seconds")
    if isinstance(value, bool) or isinstance(value, float):
        return None, "normalized_gpu_seconds must be a non-negative decimal string"
    if isinstance(value, int):
        if value < 0:
            return None, "normalized_gpu_seconds must be a non-negative decimal string"
        return Decimal(value), None
    if isinstance(value, str):
        try:
            number = parse_decimal(value, field="normalized_gpu_seconds")
        except ValueError:
            return None, "normalized_gpu_seconds must be a non-negative decimal string"
        if number < 0:
            return None, "normalized_gpu_seconds must be a non-negative decimal string"
        return number, None
    return None, "normalized_gpu_seconds must be a non-negative decimal string"


def _computed(amount: Decimal, table: PricingTable, pricing_ref: PricingRef) -> CostAssessment:
    return CostAssessment.computed(
        amount=format_amount(amount),
        currency=table.currency,
        pricing_ref=pricing_ref,
    )


def _invalid(record_id: str, model_id: str | None, usage: TokenUsage, reason: str) -> InvocationCost:
    assessment = CostAssessment.invalid(reason=reason)
    return InvocationCost(
        record_id=record_id,
        model_id=model_id,
        usage=usage,
        provider_cost=assessment,
        normalized_cost=assessment,
        cost=assessment,
    )


def usage_from_payload(payload: dict[str, object]) -> TokenUsage:
    """Read token counts. Absent counts stay missing, not zero.

    A `cost_amount` field is not consulted. `normalized_gpu_seconds` is not a
    token count and is read only by the GPU-second meter.
    """

    status = payload.get("usage_status")
    reason = payload.get("reason") if isinstance(payload.get("reason"), str) else None
    counts = (
        payload.get("prompt_tokens"),
        payload.get("completion_tokens"),
        payload.get("total_tokens"),
    )
    if status == "missing" or (status is None and all(count is None for count in counts)):
        if any(count is not None for count in counts):
            return TokenUsage.invalid(reason="missing usage carried token counts")
        return TokenUsage.missing(reason=reason or "trace omitted usage")
    if status == "invalid":
        return TokenUsage.invalid(reason=reason or "trace marked usage invalid")
    if status not in {None, "present"}:
        return TokenUsage.invalid(reason="usage_status is not present, missing, or invalid")
    if any(count is None for count in counts):
        return TokenUsage.invalid(reason="present usage omitted a token count")
    try:
        usage = TokenUsage.present(
            prompt_tokens=counts[0],  # type: ignore[arg-type]
            completion_tokens=counts[1],  # type: ignore[arg-type]
            total_tokens=counts[2],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        return TokenUsage.invalid(reason=str(exc))
    prompt = usage.prompt_tokens or 0
    completion = usage.completion_tokens or 0
    total = usage.total_tokens or 0
    if total != prompt + completion:
        return TokenUsage.invalid(reason="total_tokens is not prompt plus completion")
    return usage


def _totals(
    invocations: tuple[InvocationCost, ...],
    pricing_ref: PricingRef,
) -> tuple[CostAssessment, CostAssessment]:
    if not invocations:
        empty = CostAssessment.invalid(reason="trace has no model invocation")
        return empty, empty
    return (
        _sum(tuple(item.provider_cost for item in invocations), pricing_ref),
        _sum(tuple(item.normalized_cost for item in invocations), pricing_ref),
    )


def _sum(costs: tuple[CostAssessment, ...], pricing_ref: PricingRef) -> CostAssessment:
    if any(item.status.value != "computed" or item.amount is None for item in costs):
        return CostAssessment.invalid(reason="an invocation cost is missing or invalid")
    amount = sum((Decimal(item.amount) for item in costs if item.amount is not None), Decimal(0))
    currency = costs[0].currency
    if currency is None or any(item.currency != currency for item in costs):
        return CostAssessment.invalid(reason="invocation currencies differ")
    return CostAssessment.computed(
        amount=format_amount(amount),
        currency=currency,
        pricing_ref=pricing_ref,
    )
