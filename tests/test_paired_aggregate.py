"""Paired metrics from immutable executions and traces."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ohbe.adapters.pilot import pilot_task
from ohbe.aggregate import (
    OBSERVED_NOTE,
    AggregationError,
    PairedProtocol,
    aggregate_paired,
    dump_json,
    dump_jsonl,
    execution_documents,
    report_document,
)
from ohbe.aggregate.uncertainty import mean_interval95, wilson95
from ohbe.core.execution import ExecutionStatus
from ohbe.core.harness import HarnessConfig
from ohbe.core.integrity import canonical_json
from ohbe.core.pricing import PricingRef
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog
from ohbe.cost.table import load_pricing_table
from ohbe.eval.failure import FailureCategory
from tests.samples import NOW, sample_execution, sample_pool

TABLE = Path(__file__).resolve().parents[1] / "pricing" / "ohbe-offline-2026-10-09.yaml"


def _harness(harness_id: str):
    return HarnessConfig.build(
        harness_id=harness_id,
        name=harness_id,
        version="1",
        parameters={},
    ).to_ref()


def _trace(tmp_path: Path, execution_id: str, events: list[tuple[str, dict]]) -> ExecutionTrace:
    log = TraceLog(tmp_path / f"{execution_id}.jsonl")
    for index, (event_type, payload) in enumerate(events):
        log.append(
            execution_id=execution_id,
            event_type=event_type,
            payload=payload,
            recorded_at=NOW,
            record_id=f"{execution_id}-{index}",
        )
    records = tuple(log.read())
    return ExecutionTrace.build(
        trace_id=f"trace-{execution_id}",
        execution_id=execution_id,
        records=records,
    )


def _run(
    tmp_path: Path,
    *,
    execution_id: str,
    harness_id: str,
    task,
    pool,
    answer: str | None,
    usage: dict | None,
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    latency_ms: int = 10,
    seed: int = 1,
    error: str | None = None,
    category: str | None = None,
):
    execution = sample_execution(
        execution_id=execution_id,
        experiment_id="exp-paired",
        task_id=task.task_id,
        task_content_hash=task.content_hash,
        harness=_harness(harness_id),
        model_pool_id=pool.pool_id,
        model_pool_hash=pool.content_hash,
        seed=seed,
        status=status,
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=latency_ms),
    )
    events: list[tuple[str, dict]] = [
        (
            TraceEventType.EXECUTION_STARTED,
            {"model_id": pool.models[0].model_id, "task_id": task.task_id, "task_input": task.input},
        )
    ]
    if usage is not None:
        events.append((TraceEventType.INVOCATION_COMPLETED, usage))
    if answer is not None:
        events.append(("evaluation.answer", {"answer": answer}))
    finished: dict = {"error": error, "status": status.value}
    if category is not None:
        finished["failure_category"] = category
    events.append((TraceEventType.EXECUTION_FINISHED, finished))
    return execution, _trace(tmp_path, execution_id, events)


def _present(prompt: int = 1000, completion: int = 500) -> dict:
    return {
        "completion_tokens": completion,
        "prompt_tokens": prompt,
        "total_tokens": prompt + completion,
        "usage_status": "present",
    }


def _protocol(**overrides) -> PairedProtocol:
    data = {"repetitions": 2, "timeout_seconds": 30}
    data.update(overrides)
    return PairedProtocol(**data)


def _pricing():
    table = load_pricing_table(TABLE)
    ref = PricingRef(
        pricing_id=table.pricing_id,
        version=table.version,
        uri=TABLE.as_uri(),
        content_hash=table.content_hash,
    )
    return table, ref


def test_wilson_and_mean_intervals_are_stable() -> None:
    assert wilson95(1, 2) == ("0.094529", "0.905471")
    assert wilson95(2, 2) == ("0.342372", "1.000000")
    assert mean_interval95([10, 30]) == ("20.000000", "0.400000", "39.600000")
    assert wilson95(0, 0) is None


def test_paired_metrics_exclude_invalid_runs_and_missing_usage(tmp_path: Path) -> None:
    task = pilot_task()
    pool = sample_pool()
    table, ref = _pricing()
    openbot_a = _run(
        tmp_path,
        execution_id="ob-1",
        harness_id="openbot",
        task=task,
        pool=pool,
        answer="pong",
        usage=_present(),
        latency_ms=10,
        seed=1,
    )
    openbot_b = _run(
        tmp_path,
        execution_id="ob-2",
        harness_id="openbot",
        task=task,
        pool=pool,
        answer="pong",
        usage=_present(),
        latency_ms=30,
        seed=2,
    )
    timed_out = _run(
        tmp_path,
        execution_id="ob-slow",
        harness_id="openbot",
        task=task,
        pool=pool,
        answer="pong",
        usage=_present(),
        latency_ms=5000,
        seed=3,
        status=ExecutionStatus.FAILED,
    )
    inspect_pass = _run(
        tmp_path,
        execution_id="in-1",
        harness_id="inspect-react",
        task=task,
        pool=pool,
        answer="pong",
        usage=_present(),
        latency_ms=20,
        seed=1,
    )
    inspect_fail = _run(
        tmp_path,
        execution_id="in-2",
        harness_id="inspect-react",
        task=task,
        pool=pool,
        answer="ping",
        usage=_present(),
        latency_ms=20,
        seed=2,
        status=ExecutionStatus.FAILED,
        error="objective missed",
    )
    missing = _run(
        tmp_path,
        execution_id="in-gap",
        harness_id="inspect-react",
        task=task,
        pool=pool,
        answer="pong",
        usage={"usage_status": "missing", "prompt_tokens": None, "cost_amount": "0"},
        seed=4,
    )
    report, rows = aggregate_paired(
        [openbot_a, openbot_b, timed_out, inspect_pass, inspect_fail, missing],
        task=task,
        pool=pool,
        table=table,
        pricing_ref=ref,
        protocol=_protocol(repetitions=3, timeout_seconds=1),
    )
    by_id = {item.harness_id: item for item in report.harnesses}
    openbot = by_id["openbot"]
    assert openbot.valid_count == 2
    assert openbot.invalid_count == 1
    assert openbot.success_count == 2
    assert openbot.task_failure_count == 0
    assert openbot.success_rate == "1.000000"
    assert openbot.invalid_by_category == {FailureCategory.TIMEOUT.value: 1}
    assert openbot.total_model_cost == "0.015"
    assert openbot.cost_per_success == "0.0075"
    assert openbot.prompt_tokens == 2000
    assert openbot.completion_tokens == 1000
    assert openbot.total_tokens == 3000
    assert openbot.latency_ms_mean == "20.000000"
    assert openbot.latency_ms_low == "0.400000"
    assert openbot.latency_ms_high == "39.600000"
    assert openbot.seeds == (1, 2, 3)
    assert openbot.complete is True
    inspect = by_id["inspect-react"]
    assert inspect.valid_count == 3
    assert inspect.success_count == 2
    assert inspect.task_failure_count == 1
    assert inspect.success_rate == "0.666667"
    assert inspect.cost_status == "invalid"
    assert inspect.total_model_cost is None
    assert inspect.cost_per_success is None
    assert inspect.prompt_tokens is None
    assert inspect.usage_status == "missing"
    assert inspect.complete is True
    assert report.note == OBSERVED_NOTE
    document = report_document(report)
    assert "routing_regret" not in document
    assert "oracle" not in document
    assert all(row.execution.oracle is None and row.execution.routing_regret is None for row in rows)
    again, _ = aggregate_paired(
        [openbot_a, openbot_b, timed_out, inspect_pass, inspect_fail, missing],
        task=task,
        pool=pool,
        table=table,
        pricing_ref=ref,
        protocol=_protocol(repetitions=3, timeout_seconds=1),
    )
    assert canonical_json(report_document(report)) == canonical_json(report_document(again))
    dump_json(tmp_path / "report.json", report_document(report))
    dump_jsonl(tmp_path / "rows.jsonl", execution_documents(rows))
    text = (tmp_path / "rows.jsonl").read_text(encoding="utf-8")
    assert '"cost_amount":null' in text or '"cost_amount":"0.0075"' in text
    assert "routing" not in text


def test_token_budget_and_missing_usage_are_not_task_failures(tmp_path: Path) -> None:
    task = pilot_task()
    pool = sample_pool()
    table, ref = _pricing()
    over = _run(
        tmp_path,
        execution_id="over",
        harness_id="openbot",
        task=task,
        pool=pool,
        answer="pong",
        usage=_present(),
    )
    report, rows = aggregate_paired(
        [over],
        task=task,
        pool=pool,
        table=table,
        pricing_ref=ref,
        protocol=_protocol(repetitions=1, max_total_tokens=10),
    )
    assert rows[0].judgement.validity == "invalid"
    assert rows[0].judgement.outcome == "not_scored"
    assert rows[0].judgement.failure_category == FailureCategory.BUDGET.value
    assert report.harnesses[0].success_count == 0
    assert report.harnesses[0].task_failure_count == 0
    assert report.harnesses[0].total_model_cost is None
    assert report.harnesses[0].success_rate is None

    gap = _run(
        tmp_path,
        execution_id="gap",
        harness_id="openbot",
        task=task,
        pool=pool,
        answer="pong",
        usage={"usage_status": "missing", "cost_amount": "0"},
    )
    priced, scored = aggregate_paired(
        [gap],
        task=task,
        pool=pool,
        table=table,
        pricing_ref=ref,
        protocol=_protocol(repetitions=1, max_total_tokens=100),
    )
    assert scored[0].judgement.failure_category == FailureCategory.INVALID_USAGE.value
    assert scored[0].judgement.validity == "invalid"
    assert priced.harnesses[0].task_failure_count == 0
    assert priced.harnesses[0].total_model_cost is None
    assert scored[0].cost.total.amount is None


def test_oracle_runs_are_rejected(tmp_path: Path) -> None:
    task = pilot_task()
    pool = sample_pool()
    table, ref = _pricing()
    execution, trace = _run(
        tmp_path,
        execution_id="oracle",
        harness_id="openbot",
        task=task,
        pool=pool,
        answer="pong",
        usage=_present(),
    )
    payload = execution.model_dump(mode="python")
    payload.pop("execution_hash")
    payload["oracle"] = {"ref": "dataset://oracle/echo"}
    from ohbe.core.execution import Execution

    forged = Execution.build(**payload)
    with pytest.raises(AggregationError, match="oracle"):
        aggregate_paired(
            [(forged, trace)],
            task=task,
            pool=pool,
            table=table,
            pricing_ref=ref,
            protocol=_protocol(repetitions=1),
        )
