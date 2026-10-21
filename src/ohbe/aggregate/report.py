"""Aggregate paired harness runs of one task.

Inputs are finished executions and their traces. The function does not launch
a harness. Invalid protocol runs stay out of the success rate and out of the
cost total. Missing usage is not added as zero. The report states what was
observed. It does not rank a router.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ohbe.core.execution import Execution
from ohbe.core.model import ModelPool
from ohbe.core.pricing import PricingRef
from ohbe.core.task import Task
from ohbe.core.trace import ExecutionTrace
from ohbe.core.usage import UsageStatus
from ohbe.aggregate.uncertainty import mean_interval95, wilson95
from ohbe.core.integrity import GENESIS_HASH, canonical_json
from ohbe.cost.decimal import format_amount, format_rate, parse_decimal
from ohbe.cost.engine import ExecutionCost, price_trace
from ohbe.cost.table import PricingTable
from ohbe.eval.failure import FailureCategory
from ohbe.eval.judge import ExecutionJudgement, judge_execution

OBSERVED_NOTE = "Observed results and uncertainty only. Routing optimality is not inferred."


class AggregationError(ValueError):
    """The runs are not one paired comparison."""


@dataclass(frozen=True)
class PairedProtocol:
    """Repetitions, timeout, and optional budgets for a paired comparison."""

    repetitions: int
    timeout_seconds: int
    max_total_tokens: int | None = None
    max_cost: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.repetitions, bool) or not isinstance(self.repetitions, int) or self.repetitions < 1:
            raise AggregationError("repetitions must be a positive integer")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
        ):
            raise AggregationError("timeout_seconds must be a positive integer")
        if self.max_total_tokens is not None and (
            isinstance(self.max_total_tokens, bool)
            or not isinstance(self.max_total_tokens, int)
            or self.max_total_tokens < 1
        ):
            raise AggregationError("max_total_tokens must be a positive integer")
        if self.max_cost is not None:
            try:
                amount = parse_decimal(self.max_cost, field="max_cost")
            except ValueError as exc:
                raise AggregationError(str(exc)) from exc
            if amount <= 0:
                raise AggregationError("max_cost must be positive")


@dataclass(frozen=True)
class HarnessMetrics:
    """Metrics for one harness on the shared task."""

    harness_id: str
    observed_count: int
    valid_count: int
    invalid_count: int
    success_count: int
    task_failure_count: int
    success_rate: str | None
    success_rate_low: str | None
    success_rate_high: str | None
    uncertainty: str
    total_model_cost: str | None
    cost_status: str
    cost_per_success: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    usage_status: str
    latency_ms_mean: str | None
    latency_ms_low: str | None
    latency_ms_high: str | None
    latency_missing_count: int
    invalid_by_category: dict[str, int]
    seeds: tuple[int, ...]
    ohbe_versions: tuple[str, ...]
    git_commits: tuple[str, ...]
    complete: bool


@dataclass(frozen=True)
class PairedReport:
    """Observed table for every harness that ran the same task and pool."""

    task_id: str
    task_content_hash: str
    model_pool_id: str
    model_pool_hash: str
    pricing_id: str
    pricing_version: str
    pricing_content_hash: str
    repetitions: int
    timeout_seconds: int
    max_total_tokens: int | None
    max_cost: str | None
    schema_version: str
    harnesses: tuple[HarnessMetrics, ...]
    note: str


@dataclass(frozen=True)
class ScoredRun:
    """One execution after judging and pricing. The trace is unchanged."""

    execution: Execution
    trace: ExecutionTrace
    judgement: ExecutionJudgement
    cost: ExecutionCost


def aggregate_paired(
    runs: Sequence[tuple[Execution, ExecutionTrace]],
    *,
    task: Task,
    pool: ModelPool,
    table: PricingTable,
    pricing_ref: PricingRef,
    protocol: PairedProtocol,
) -> tuple[PairedReport, tuple[ScoredRun, ...]]:
    """Judge, price, and aggregate. Returns the table and the per-run rows."""

    if not runs:
        raise AggregationError("paired aggregation requires at least one execution")
    scored: list[ScoredRun] = []
    schema_version: str | None = None
    for execution, trace in runs:
        _check_binding(execution, trace, task, pool)
        if schema_version is None:
            schema_version = execution.schema_version
        elif execution.schema_version != schema_version:
            raise AggregationError("schema_version differs across executions")
        cost = price_trace(trace, table=table, pricing_ref=pricing_ref)
        if cost.head_hash != trace.head_hash or cost.execution_id != execution.execution_id:
            raise AggregationError("cost record is not bound to this trace")
        judgement = apply_protocol(
            _with_usage(judge_execution(execution=execution, trace=trace, task=task), cost),
            cost,
            protocol=protocol,
        )
        scored.append(ScoredRun(execution=execution, trace=trace, judgement=judgement, cost=cost))
    grouped: OrderedDict[str, list[ScoredRun]] = OrderedDict()
    for item in scored:
        grouped.setdefault(item.judgement.harness_id, []).append(item)
    metrics = tuple(_metrics(harness_id, items, protocol) for harness_id, items in grouped.items())
    report = PairedReport(
        task_id=task.task_id,
        task_content_hash=task.content_hash,
        model_pool_id=pool.pool_id,
        model_pool_hash=pool.content_hash,
        pricing_id=table.pricing_id,
        pricing_version=table.version,
        pricing_content_hash=table.content_hash,
        repetitions=protocol.repetitions,
        timeout_seconds=protocol.timeout_seconds,
        max_total_tokens=protocol.max_total_tokens,
        max_cost=protocol.max_cost,
        schema_version=schema_version or "",
        harnesses=metrics,
        note=OBSERVED_NOTE,
    )
    return report, tuple(scored)


def apply_protocol(
    judgement: ExecutionJudgement,
    cost: ExecutionCost,
    *,
    protocol: PairedProtocol,
) -> ExecutionJudgement:
    """Drop a run from the task sample when it breaks timeout or budget."""

    if judgement.validity == "invalid":
        return judgement
    if judgement.latency_ms is None:
        return _invalidate(judgement, FailureCategory.INFRA_INVALID, "execution timestamps are required to certify the timeout")
    if judgement.latency_ms > protocol.timeout_seconds * 1000:
        return _invalidate(judgement, FailureCategory.TIMEOUT, "execution exceeded the timeout")
    if protocol.max_total_tokens is not None:
        if _usage_blocked(cost):
            return _invalidate(
                judgement,
                FailureCategory.INVALID_USAGE,
                "token budget cannot be certified without present usage",
            )
        if _token_total(cost) > protocol.max_total_tokens:
            return _invalidate(judgement, FailureCategory.BUDGET, "execution exceeded the token budget")
    if protocol.max_cost is not None:
        if cost.total.amount is None or cost.total.status.value != "computed":
            return _invalidate(
                judgement,
                FailureCategory.INVALID_USAGE,
                "cost budget cannot be certified without computed cost",
            )
        if Decimal(cost.total.amount) > parse_decimal(protocol.max_cost, field="max_cost"):
            return _invalidate(judgement, FailureCategory.BUDGET, "execution exceeded the cost budget")
    return judgement


def report_document(report: PairedReport) -> dict[str, Any]:
    """JSON object for a paired report. Tuples become lists."""

    return _jsonable(asdict(report))


def execution_documents(runs: Sequence[ScoredRun]) -> list[dict[str, Any]]:
    """One JSON object per execution, suitable for JSONL export."""

    rows: list[dict[str, Any]] = []
    for item in runs:
        prompt, completion, total = _row_tokens(item.cost)
        rows.append(
            {
                "answer": item.judgement.answer,
                "cost_amount": item.cost.total.amount,
                "cost_status": item.cost.total.status.value,
                "provider_cost": item.cost.provider_cost.amount,
                "provider_cost_status": item.cost.provider_cost.status.value,
                "normalized_cost": item.cost.normalized_cost.amount,
                "normalized_cost_status": item.cost.normalized_cost.status.value,
                "execution_id": item.judgement.execution_id,
                "failure_category": item.judgement.failure_category,
                "harness_id": item.judgement.harness_id,
                "latency_ms": item.judgement.latency_ms,
                "model_pool_id": item.judgement.model_pool_id,
                "outcome": item.judgement.outcome,
                "passed": item.judgement.passed,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "scorer": item.judgement.scorer,
                "seed": item.judgement.seed,
                "task_id": item.judgement.task_id,
                "trace_head": item.trace.head_hash,
                "usage_category": item.judgement.usage_category,
                "validity": item.judgement.validity,
            }
        )
    return rows


def dump_json(path: str | Path, document: Mapping[str, Any]) -> None:
    """Write one canonical JSON document. This is an export hook, not a report."""

    Path(path).write_text(canonical_json(document) + "\n", encoding="utf-8")


def dump_jsonl(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Write one canonical JSON object per line."""

    text = "".join(canonical_json(row) + "\n" for row in rows)
    Path(path).write_text(text, encoding="utf-8")


def _check_binding(execution: Execution, trace: ExecutionTrace, task: Task, pool: ModelPool) -> None:
    if execution.oracle is not None or execution.routing_regret is not None:
        raise AggregationError("paired aggregation does not score oracle or routing regret")
    if execution.task_id != task.task_id or execution.task_content_hash != task.content_hash:
        raise AggregationError("execution is not bound to this task")
    if execution.model_pool_id != pool.pool_id or execution.model_pool_hash != pool.content_hash:
        raise AggregationError("execution is not bound to this model pool")
    if trace.execution_id != execution.execution_id:
        raise AggregationError("trace is not bound to this execution")
    head = trace.records[-1].record_hash if trace.records else GENESIS_HASH
    if trace.head_hash != head:
        raise AggregationError("trace head does not match its records")


def _with_usage(judgement: ExecutionJudgement, cost: ExecutionCost) -> ExecutionJudgement:
    category = FailureCategory.INVALID_USAGE.value if _usage_blocked(cost) else None
    if judgement.usage_category == category:
        return judgement
    return _copy(judgement, usage_category=category)


def _usage_blocked(cost: ExecutionCost) -> bool:
    if not cost.invocations:
        return True
    return any(item.usage.status is not UsageStatus.PRESENT for item in cost.invocations)


def _token_total(cost: ExecutionCost) -> int:
    total = 0
    for item in cost.invocations:
        if item.usage.total_tokens is None:
            raise AggregationError("present usage is missing total_tokens")
        total += item.usage.total_tokens
    return total


def _invalidate(judgement: ExecutionJudgement, category: FailureCategory, reason: str) -> ExecutionJudgement:
    return _copy(
        judgement,
        validity="invalid",
        outcome="not_scored",
        failure_category=category.value,
        reason=reason,
    )


def _copy(judgement: ExecutionJudgement, **changes: object) -> ExecutionJudgement:
    data = dict(judgement.__dict__)
    data.update(changes)
    return ExecutionJudgement(**data)


def _metrics(harness_id: str, items: Sequence[ScoredRun], protocol: PairedProtocol) -> HarnessMetrics:
    valid = [item for item in items if item.judgement.validity == "valid"]
    invalid = [item for item in items if item.judgement.validity != "valid"]
    successes = [item for item in valid if item.judgement.outcome == "passed"]
    failures = [item for item in valid if item.judgement.outcome == "failed"]
    rate = None
    low = None
    high = None
    uncertainty = "none"
    if valid:
        rate = _rate(len(successes), len(valid))
        interval = wilson95(len(successes), len(valid))
        if interval is not None:
            low, high = interval
        uncertainty = "wilson95"
    cost_status, cost_amount = _cost_rollup(valid)
    per_success = None
    if cost_status == "computed" and cost_amount is not None and successes:
        per_success = format_amount(Decimal(cost_amount) / Decimal(len(successes)))
    prompt, completion, total, usage_status = _usage_rollup(valid)
    latencies = [item.judgement.latency_ms for item in valid if item.judgement.latency_ms is not None]
    latency_missing = sum(1 for item in valid if item.judgement.latency_ms is None)
    latency = mean_interval95([value for value in latencies if value is not None])
    counts: dict[str, int] = {}
    for item in invalid:
        name = item.judgement.failure_category or FailureCategory.INFRA_INVALID.value
        counts[name] = counts.get(name, 0) + 1
    commits = sorted({item.execution.fingerprint.git_commit for item in items if item.execution.fingerprint.git_commit})
    versions = sorted({item.execution.fingerprint.ohbe_version for item in items})
    return HarnessMetrics(
        harness_id=harness_id,
        observed_count=len(items),
        valid_count=len(valid),
        invalid_count=len(invalid),
        success_count=len(successes),
        task_failure_count=len(failures),
        success_rate=rate,
        success_rate_low=low,
        success_rate_high=high,
        uncertainty=uncertainty,
        total_model_cost=cost_amount,
        cost_status=cost_status,
        cost_per_success=per_success,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        usage_status=usage_status,
        latency_ms_mean=None if latency is None else latency[0],
        latency_ms_low=None if latency is None else latency[1],
        latency_ms_high=None if latency is None else latency[2],
        latency_missing_count=latency_missing,
        invalid_by_category=dict(sorted(counts.items())),
        seeds=tuple(item.execution.seed for item in items),
        ohbe_versions=tuple(versions),
        git_commits=tuple(commits),
        complete=len(items) == protocol.repetitions,
    )


def _rate(successes: int, total: int) -> str:
    return format_rate(Decimal(successes) / Decimal(total))


def _cost_rollup(valid: Sequence[ScoredRun]) -> tuple[str, str | None]:
    if not valid:
        return "invalid", None
    if any(item.cost.total.status.value != "computed" or item.cost.total.amount is None for item in valid):
        return "invalid", None
    amount = sum((Decimal(item.cost.total.amount) for item in valid), Decimal(0))
    return "computed", format_amount(amount)


def _usage_rollup(valid: Sequence[ScoredRun]) -> tuple[int | None, int | None, int | None, str]:
    if not valid:
        return None, None, None, "missing"
    statuses = [
        invocation.usage.status
        for item in valid
        for invocation in item.cost.invocations
    ]
    if any(status is UsageStatus.INVALID for status in statuses):
        return None, None, None, "invalid"
    if not statuses or any(status is not UsageStatus.PRESENT for status in statuses):
        return None, None, None, "missing"
    prompt = completion = total = 0
    for item in valid:
        for invocation in item.cost.invocations:
            if (
                invocation.usage.prompt_tokens is None
                or invocation.usage.completion_tokens is None
                or invocation.usage.total_tokens is None
            ):
                return None, None, None, "invalid"
            prompt += invocation.usage.prompt_tokens
            completion += invocation.usage.completion_tokens
            total += invocation.usage.total_tokens
    return prompt, completion, total, "present"


def _row_tokens(cost: ExecutionCost) -> tuple[int | None, int | None, int | None]:
    if _usage_blocked(cost):
        return None, None, None
    prompt = completion = total = 0
    for invocation in cost.invocations:
        if (
            invocation.usage.prompt_tokens is None
            or invocation.usage.completion_tokens is None
            or invocation.usage.total_tokens is None
        ):
            return None, None, None
        prompt += invocation.usage.prompt_tokens
        completion += invocation.usage.completion_tokens
        total += invocation.usage.total_tokens
    return prompt, completion, total


def _jsonable(value: object) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
