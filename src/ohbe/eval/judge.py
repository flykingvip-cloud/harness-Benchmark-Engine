"""Classify one execution from its task and trace.

The objective is recomputed from the recorded answer. An explicit infra
category on `execution.finished` (timeout, budget, harness error, egress,
cancellation, invalid usage, or infra) keeps the run out of the task-failure
count. A harness `failed` status with no answer is a harness error, not an
objective miss.
"""

from __future__ import annotations

from dataclasses import dataclass

from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.task import Task
from ohbe.core.trace import ExecutionTrace, TraceEventType
from ohbe.eval.failure import INFRA_INVALID, FailureCategory, parse_failure_category
from ohbe.eval.objective import ObjectiveError, extract_answer, score_task


@dataclass(frozen=True)
class ExecutionJudgement:
    """Observed outcome of one execution. Invalid runs are not task failures."""

    execution_id: str
    task_id: str
    task_content_hash: str
    harness_id: str
    model_pool_id: str
    model_pool_hash: str
    seed: int
    validity: str
    outcome: str
    failure_category: str | None
    usage_category: str | None
    scorer: str | None
    target: str | None
    answer: str | None
    passed: bool | None
    latency_ms: int | None
    schema_version: str
    ohbe_version: str
    reason: str | None


def latency_ms(execution: Execution) -> int | None:
    """Elapsed milliseconds from the execution record. Missing clocks stay null."""

    if execution.started_at is None or execution.finished_at is None:
        return None
    delta = execution.finished_at - execution.started_at
    millis = int(delta.total_seconds() * 1000)
    if millis < 0:
        return None
    return millis


def judge_execution(*, execution: Execution, trace: ExecutionTrace, task: Task) -> ExecutionJudgement:
    """Recompute the objective and separate infra failures from task failures."""

    base = _blank(execution)
    if trace.execution_id != execution.execution_id:
        return _invalid(base, FailureCategory.INFRA_INVALID, "trace execution_id does not match the execution")
    if execution.task_id != task.task_id or execution.task_content_hash != task.content_hash:
        return _invalid(base, FailureCategory.INFRA_INVALID, "execution is not bound to this task")
    if execution.status in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING}:
        return _invalid(base, FailureCategory.INFRA_INVALID, "execution did not finish")
    finished = _last(trace, TraceEventType.EXECUTION_FINISHED)
    if finished is None:
        return _invalid(base, FailureCategory.INFRA_INVALID, "trace has no execution.finished event")
    error = finished.payload.get("error")
    error_text = error if isinstance(error, str) else None
    named = finished.payload.get("failure_category")
    if named is not None and parse_failure_category(named) is None:
        return _invalid(base, FailureCategory.INFRA_INVALID, "unknown failure_category on the trace")
    category = parse_failure_category(named)
    answer = extract_answer(trace)
    if category is None:
        category = _infer_category(execution, error_text, answer)
    if category in INFRA_INVALID:
        return _invalid(base, category, error_text or category.value, answer=answer)
    try:
        objective = score_task(task, answer)
    except ObjectiveError as exc:
        return _invalid(base, FailureCategory.INFRA_INVALID, str(exc), answer=answer)
    if objective.passed:
        return _replace(
            base,
            validity="valid",
            outcome="passed",
            failure_category=None,
            scorer=objective.scorer,
            target=objective.target,
            answer=answer,
            passed=True,
            reason=None,
        )
    return _replace(
        base,
        validity="valid",
        outcome="failed",
        failure_category=FailureCategory.EVAL_FAIL.value,
        scorer=objective.scorer,
        target=objective.target,
        answer=answer,
        passed=False,
        reason=error_text or "objective scorer did not pass",
    )


def _infer_category(
    execution: Execution,
    error: str | None,
    answer: str | None,
) -> FailureCategory | None:
    if execution.status is ExecutionStatus.CANCELLED:
        return FailureCategory.CANCELLED
    text = (error or "").lower()
    if "egress" in text:
        return FailureCategory.EGRESS_VIOLATION
    if "timeout" in text or "timed out" in text:
        return FailureCategory.TIMEOUT
    if "budget" in text:
        return FailureCategory.BUDGET
    if execution.status is ExecutionStatus.FAILED and answer is None:
        return FailureCategory.HARNESS_ERROR
    return None


def _last(trace: ExecutionTrace, event_type: str):
    found = None
    for record in trace.records:
        if record.event_type == event_type:
            found = record
    return found


def _blank(execution: Execution) -> ExecutionJudgement:
    return ExecutionJudgement(
        execution_id=execution.execution_id,
        task_id=execution.task_id,
        task_content_hash=execution.task_content_hash,
        harness_id=execution.harness.harness_id,
        model_pool_id=execution.model_pool_id,
        model_pool_hash=execution.model_pool_hash,
        seed=execution.seed,
        validity="invalid",
        outcome="not_scored",
        failure_category=FailureCategory.INFRA_INVALID.value,
        usage_category=None,
        scorer=None,
        target=None,
        answer=None,
        passed=None,
        latency_ms=latency_ms(execution),
        schema_version=execution.schema_version or SCHEMA_VERSION,
        ohbe_version=execution.fingerprint.ohbe_version,
        reason=None,
    )


def _invalid(
    base: ExecutionJudgement,
    category: FailureCategory,
    reason: str,
    *,
    answer: str | None = None,
) -> ExecutionJudgement:
    return _replace(
        base,
        validity="invalid",
        outcome="not_scored",
        failure_category=category.value,
        answer=answer,
        passed=None,
        reason=reason,
    )


def _replace(base: ExecutionJudgement, **changes: object) -> ExecutionJudgement:
    data = dict(base.__dict__)
    data.update(changes)
    return ExecutionJudgement(**data)
