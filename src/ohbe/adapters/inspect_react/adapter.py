"""Reference harness: Inspect AI `react()` through the LiteLLM proxy.

Importing this module does not import Inspect. The live eval path imports
`inspect_ai` only when a caller does not supply a runner.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.adapters.inspect_react.plan import (
    INSPECT_AI_VERSION,
    InspectEvalOutcome,
    InspectEvalPlan,
    InspectProfileError,
    build_eval_plan,
    check_inspect_binding,
    public_plan,
)
from ohbe.adapters.openbot.runner import isolated_state_dir
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.integrity import GENESIS_HASH
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog, TraceRecord
from ohbe.gateway.reconcile import reconcile_response

EvalRunner = Callable[[InspectEvalPlan], InspectEvalOutcome]


class InspectReactAdapter:
    """Map one OHBE task onto an Inspect `react()` eval and an OHBE trace."""

    def __init__(self, *, api_key: str, runner: EvalRunner | None = None) -> None:
        self.api_key = api_key
        self.runner = runner if runner is not None else live_inspect_eval

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        check_inspect_binding(request.config, pool=request.pool, environment=request.environment)
        state_dir = isolated_state_dir(request.state_dir)
        plan = build_eval_plan(
            task=request.task,
            pool=request.pool,
            environment=request.environment,
            api_key=self.api_key,
        )
        if plan.task_input != request.task.input:
            raise InspectProfileError("eval plan changed the task input")
        try:
            outcome = self.runner(plan)
        except InspectProfileError:
            raise
        except Exception as exc:
            raise AdapterError(f"inspect eval failed to start: {exc}") from exc
        status = ExecutionStatus.SUCCEEDED if outcome.success else ExecutionStatus.FAILED
        error = None if outcome.success else (outcome.error or "inspect react eval did not pass the objective scorer")
        usage = _usage(outcome)
        execution = Execution.build(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            task_id=request.task.task_id,
            task_content_hash=request.task.content_hash,
            harness=request.config.to_ref(),
            model_pool_id=request.pool.pool_id,
            model_pool_hash=request.pool.content_hash,
            seed=request.seed,
            status=status,
            started_at=request.started_at,
            finished_at=request.finished_at,
            fingerprint=request.fingerprint,
            environment=request.environment,
        )
        execution.check_binding(task=request.task, harness=request.config, pool=request.pool)
        trace = _trace(request, execution, plan, outcome, usage, error, state_dir)
        return HarnessRun(execution=execution, trace=trace)


def live_inspect_eval(plan: InspectEvalPlan) -> InspectEvalOutcome:
    """Run `react()` with inspect-ai. Requires the `inspect` extra and a proxy."""

    import os

    try:
        from inspect_ai import Task
        from inspect_ai import eval as inspect_eval
        from inspect_ai.agent import react
        from inspect_ai.dataset import Sample
        from inspect_ai.scorer import includes, match
        from inspect_ai.tool import bash
    except ImportError as exc:
        raise AdapterError(
            f"inspect-ai=={INSPECT_AI_VERSION} is not installed; pip install -e '.[inspect]'"
        ) from exc
    scorer = includes() if plan.scorer == "includes" else match()
    task = Task(
        dataset=[Sample(input=plan.sample_input, target=plan.sample_target)],
        solver=react(tools=[bash()], model=plan.model),
        scorer=scorer,
        sandbox="docker",
    )
    previous = {key: os.environ.get(key) for key in plan.env}
    os.environ.update(plan.env)
    try:
        logs = inspect_eval(
            task,
            model=plan.model,
            model_args={"model_info": False},
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return _outcome_from_logs(logs)


def _outcome_from_logs(logs: Any) -> InspectEvalOutcome:
    if not logs:
        return InspectEvalOutcome(success=False, error="inspect eval returned no log")
    log = logs[0]
    samples = getattr(log, "samples", None) or []
    if not samples:
        return InspectEvalOutcome(success=False, error="inspect eval returned no sample")
    sample = samples[0]
    scores = getattr(sample, "scores", None) or {}
    value = None
    for score in scores.values():
        value = getattr(score, "value", score)
        break
    text = str(value) if value is not None else None
    success = text in {"C", "1", "1.0"}
    answer = getattr(sample, "output", None)
    answer_text = getattr(answer, "completion", None)
    if not isinstance(answer_text, str):
        answer_text = None
    return InspectEvalOutcome(
        success=success,
        score=text,
        answer=answer_text,
        usage=None,
        error=None if success else "objective scorer did not mark the sample correct",
    )


def _usage(outcome: InspectEvalOutcome) -> dict[str, Any]:
    body = {"usage": outcome.usage} if outcome.usage is not None else None
    if body is None:
        reconciliation = reconcile_response(None)
        reason = "inspect eval omitted usage"
    else:
        reconciliation = reconcile_response(body)
        reason = reconciliation.usage.reason
    return {
        "usage_status": reconciliation.usage.status.value,
        "prompt_tokens": reconciliation.usage.prompt_tokens,
        "completion_tokens": reconciliation.usage.completion_tokens,
        "total_tokens": reconciliation.usage.total_tokens,
        "cost_status": reconciliation.cost.status.value,
        "cost_amount": reconciliation.cost.amount,
        "reason": reason,
    }


def _trace(
    request: HarnessRunRequest,
    execution: Execution,
    plan: InspectEvalPlan,
    outcome: InspectEvalOutcome,
    usage: dict[str, Any],
    error: str | None,
    state_dir: Any,
) -> ExecutionTrace:
    identity = {
        "config_hash": request.config.config_hash,
        "harness_version": request.config.version,
        "model_pool_hash": request.pool.content_hash,
        "model_pool_id": request.pool.pool_id,
    }
    log = TraceLog(state_dir / "trace.jsonl")
    started = log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={
            **identity,
            "gateway_host": request.environment.gateway_host,
            "gateway_port": request.environment.gateway_port,
            "model_id": plan.model_id,
            "provider": "litellm-proxy",
            "sandbox": plan.sandbox,
            "solver": plan.solver,
            "task_id": request.task.task_id,
            "task_input": request.task.input,
        },
        recorded_at=request.started_at,
        record_id=f"{request.execution_id}-started",
    )
    log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.INVOCATION_COMPLETED,
        payload={**usage, "model_pool_id": request.pool.pool_id, "source": "ohbe"},
        recorded_at=request.finished_at,
        record_id=f"{request.execution_id}-usage",
    )
    log.append(
        execution_id=request.execution_id,
        event_type="inspect.transcript",
        payload={
            "answer": outcome.answer,
            "plan": public_plan(plan),
            "score": outcome.score,
            "source_of_truth": "ohbe.trace",
            "supplementary": True,
        },
        recorded_at=request.finished_at,
        record_id=f"{request.execution_id}-transcript",
    )
    finished = log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.EXECUTION_FINISHED,
        payload={**identity, "error": error, "status": execution.status.value},
        recorded_at=request.finished_at,
        record_id=f"{request.execution_id}-finished",
    )
    records = tuple(log.read())
    if records[0].prev_hash != GENESIS_HASH or finished.record_hash != records[-1].record_hash:
        raise AdapterError("trace head was not recorded")
    if not isinstance(started, TraceRecord):
        raise AdapterError("trace did not start")
    report = log.verify(expected_head=finished.record_hash)
    if not report.ok:
        raise AdapterError(report.errors[0] if report.errors else "trace failed verification")
    return ExecutionTrace.build(
        trace_id=request.trace_id,
        execution_id=request.execution_id,
        records=records,
    )
