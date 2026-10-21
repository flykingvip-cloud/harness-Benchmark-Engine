"""Usage capture fails closed. Proxy spend never becomes an OHBE price."""

from __future__ import annotations

from ohbe.core.pricing import CostStatus
from ohbe.core.trace import TraceLog
from ohbe.core.usage import UsageStatus
from ohbe.gateway.reconcile import reconcile_response
from ohbe.gateway.record import append_invocation, record_invocation
from tests.samples import NOW, sample_model

REQUEST = {"model": "openai/gpt-test", "messages": [{"role": "user", "content": "hi"}]}


def _record(body: dict | None, headers: dict[str, str] | None = None, *, http_status: int = 200):
    return record_invocation(
        invocation_id="inv-1",
        execution_id="exec-1",
        seq=0,
        model=sample_model(),
        request_body=REQUEST,
        http_status=http_status,
        response_body=body,
        response_headers=headers,
        started_at=NOW,
        finished_at=NOW,
    )


def test_present_usage_is_captured_and_response_cost_stays_a_signal() -> None:
    invocation, reconciliation = _record(
        {
            "id": "chatcmpl-1",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        },
        {"x-litellm-response-cost": "0.000214"},
    )
    assert invocation.status.value == "completed"
    assert invocation.usage.status is UsageStatus.PRESENT
    assert invocation.usage.prompt_tokens == 3
    assert invocation.usage.completion_tokens == 1
    assert invocation.usage.total_tokens == 4
    assert invocation.pricing_ref == sample_model().pricing_ref
    assert invocation.cost.status is CostStatus.NOT_COMPUTED
    assert invocation.cost.amount is None
    assert invocation.cost.pricing_ref is None
    assert reconciliation.spend_signal.status == "present"
    assert reconciliation.spend_signal.amount == "0.000214"
    assert reconciliation.spend_signal.currency == "USD"
    revived = type(invocation).model_validate_json(invocation.model_dump_json())
    assert revived.usage == invocation.usage
    assert revived.cost.amount is None


def test_explicit_zero_usage_is_preserved() -> None:
    invocation, reconciliation = _record(
        {"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
        {"x-litellm-response-cost": "0"},
    )
    assert invocation.usage.status is UsageStatus.PRESENT
    assert invocation.usage.prompt_tokens == 0
    assert invocation.cost.amount is None
    assert reconciliation.spend_signal.amount == "0"


def test_omitted_usage_is_missing_and_a_zero_spend_signal_is_not_copied() -> None:
    invocation, reconciliation = _record(
        {"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        {"x-litellm-response-cost": "0"},
    )
    assert invocation.usage.status is UsageStatus.MISSING
    assert invocation.usage.prompt_tokens is None
    assert invocation.usage.completion_tokens is None
    assert invocation.usage.total_tokens is None
    assert invocation.cost.status is CostStatus.INVALID
    assert invocation.cost.amount is None
    assert reconciliation.spend_signal.status == "present"
    assert reconciliation.spend_signal.amount == "0"
    dumped = invocation.model_dump(mode="json")
    assert dumped["usage"]["prompt_tokens"] is None
    assert dumped["cost"]["amount"] is None


def test_null_partial_and_inconsistent_usage_are_invalid_without_counts() -> None:
    cases = [
        {"usage": None},
        {"usage": {}},
        {"usage": {"prompt_tokens": 1}},
        {"usage": {"prompt_tokens": "3", "completion_tokens": 1, "total_tokens": 4}},
        {"usage": {"prompt_tokens": 1.0, "completion_tokens": 1, "total_tokens": 2}},
        {"usage": {"prompt_tokens": True, "completion_tokens": 0, "total_tokens": 1}},
        {"usage": {"prompt_tokens": -1, "completion_tokens": 1, "total_tokens": 0}},
        {"usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 5}},
        {"usage": []},
    ]
    for body in cases:
        invocation, _reconciliation = _record(body)
        assert invocation.usage.status in {UsageStatus.MISSING, UsageStatus.INVALID}
        assert invocation.usage.prompt_tokens is None
        assert invocation.usage.completion_tokens is None
        assert invocation.usage.total_tokens is None
        assert invocation.cost.status is CostStatus.INVALID
        assert invocation.cost.amount is None


def test_unusable_response_cost_does_not_affect_present_usage() -> None:
    invocation, reconciliation = _record(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}, "response_cost": "nope"},
        {"x-litellm-response-cost": "nope"},
    )
    assert invocation.usage.status is UsageStatus.PRESENT
    assert invocation.cost.status is CostStatus.NOT_COMPUTED
    assert invocation.cost.amount is None
    assert reconciliation.spend_signal.status == "invalid"
    assert reconciliation.spend_signal.amount is None


def test_absent_response_cost_is_not_zero() -> None:
    reconciliation = reconcile_response(
        {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        {},
    )
    assert reconciliation.spend_signal.status == "absent"
    assert reconciliation.spend_signal.amount is None
    assert reconciliation.cost.amount is None


def test_hidden_params_cost_is_a_signal_when_the_header_is_absent() -> None:
    reconciliation = reconcile_response(
        {
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            "_hidden_params": {"response_cost": 0.5},
        }
    )
    assert reconciliation.spend_signal.status == "present"
    assert reconciliation.spend_signal.amount == "0.5"
    assert reconciliation.cost.amount is None


def test_trace_records_null_counts_when_usage_is_missing(tmp_path) -> None:
    invocation, reconciliation = _record({"choices": []}, {"x-litellm-response-cost": "0"})
    log = TraceLog(tmp_path / "trace.jsonl")
    record = append_invocation(
        log,
        invocation,
        reconciliation,
        record_id="rec-0",
        recorded_at=NOW,
        metadata={"benchmark_run_id": "exp-1", "purpose": "benchmark"},
    )
    assert record.payload["usage_status"] == "missing"
    assert record.payload["prompt_tokens"] is None
    assert record.payload["completion_tokens"] is None
    assert record.payload["total_tokens"] is None
    assert record.payload["cost_amount"] is None
    assert record.payload["spend_signal_amount"] == "0"
    assert record.payload["metadata"]["benchmark_run_id"] == "exp-1"
    report = log.verify(expected_head=record.record_hash)
    assert report.ok
