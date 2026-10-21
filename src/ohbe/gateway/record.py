"""Build an OHBE invocation and trace event from a gateway response."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ohbe.core.base import AwareDatetime
from ohbe.core.invocation import InvocationStatus, ModelInvocation
from ohbe.core.model import ModelDefinition
from ohbe.core.pricing import select_pricing_ref
from ohbe.core.trace import TraceEventType, TraceLog, TraceRecord
from ohbe.core.usage import UsageStatus
from ohbe.gateway.reconcile import Reconciliation, reconcile_response


class GatewayError(RuntimeError):
    """The gateway call could not be recorded as a metered success."""


class UsageNotAvailable(GatewayError):
    """The proxy response has no usable token counts.

    The invocation is already built. Its counts are null, and its cost is
    invalid. Callers can persist ``invocation`` and must not substitute zero.
    """

    def __init__(self, invocation: ModelInvocation, reconciliation: Reconciliation) -> None:
        self.invocation = invocation
        self.reconciliation = reconciliation
        super().__init__(
            f"token usage is {invocation.usage.status.value}; refusing to record a zero"
        )


class ModelNotInPool(GatewayError):
    """The requested model is outside the pool published to LiteLLM."""


class GatewayTransportError(GatewayError):
    """The proxy did not return a JSON body."""

    def __init__(
        self,
        message: str,
        *,
        invocation: ModelInvocation | None = None,
        reconciliation: Reconciliation | None = None,
    ) -> None:
        super().__init__(message)
        self.invocation = invocation
        self.reconciliation = reconciliation


def record_invocation(
    *,
    invocation_id: str,
    execution_id: str,
    seq: int,
    model: ModelDefinition,
    request_body: dict[str, Any],
    http_status: int,
    response_body: dict[str, Any] | None,
    response_headers: Mapping[str, str] | None = None,
    started_at: AwareDatetime | None = None,
    finished_at: AwareDatetime | None = None,
    transport_error: str | None = None,
) -> tuple[ModelInvocation, Reconciliation]:
    """Store the proxy body as an invocation. Spend never becomes a price."""

    reconciliation = reconcile_response(response_body, response_headers)
    failed = transport_error is not None or http_status >= 400
    error = transport_error or _error_message(response_body)
    if failed and error is None:
        error = f"gateway HTTP {http_status}"
    latency_ms = _latency_ms(started_at, finished_at)
    invocation = ModelInvocation.build(
        invocation_id=invocation_id,
        execution_id=execution_id,
        seq=seq,
        model=model,
        status=InvocationStatus.FAILED if failed else InvocationStatus.COMPLETED,
        request=request_body,
        response=response_body,
        usage=reconciliation.usage,
        pricing_ref=select_pricing_ref(model=model.pricing_ref),
        cost=reconciliation.cost,
        latency_ms=latency_ms,
        started_at=started_at,
        finished_at=finished_at,
        error=error,
    )
    return invocation, reconciliation


def append_invocation(
    log: TraceLog,
    invocation: ModelInvocation,
    reconciliation: Reconciliation,
    *,
    record_id: str,
    recorded_at: AwareDatetime,
    metadata: Mapping[str, str] | None = None,
) -> TraceRecord:
    """Append one ``invocation.completed`` event.

    Token fields are null when usage is missing or invalid. They are not zero.
    ``cost_amount`` stays null; ``spend_signal_amount`` is the proxy signal.
    """

    payload: dict[str, Any] = {
        "invocation_id": invocation.invocation_id,
        "invocation_hash": invocation.invocation_hash,
        "model_id": invocation.model.model_id,
        "status": invocation.status.value,
        "usage_status": invocation.usage.status.value,
        "prompt_tokens": invocation.usage.prompt_tokens,
        "completion_tokens": invocation.usage.completion_tokens,
        "total_tokens": invocation.usage.total_tokens,
        "cost_status": invocation.cost.status.value,
        "cost_amount": invocation.cost.amount,
        "spend_signal_status": reconciliation.spend_signal.status,
        "spend_signal_amount": reconciliation.spend_signal.amount,
    }
    if metadata is not None:
        payload["metadata"] = dict(metadata)
    if invocation.usage.status is not UsageStatus.PRESENT:
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if payload[name] is not None:
                raise GatewayError(f"refusing to trace a {invocation.usage.status.value} count")
    return log.append(
        execution_id=invocation.execution_id,
        event_type=TraceEventType.INVOCATION_COMPLETED,
        payload=payload,
        recorded_at=recorded_at,
        record_id=record_id,
    )


def _latency_ms(
    started_at: AwareDatetime | None,
    finished_at: AwareDatetime | None,
) -> int | None:
    if started_at is None or finished_at is None:
        return None
    millis = int((finished_at - started_at).total_seconds() * 1000)
    if millis < 0:
        return None
    return millis


def _error_message(body: Mapping[str, Any] | None) -> str | None:
    if body is None:
        return None
    error = body.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, Mapping):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None
