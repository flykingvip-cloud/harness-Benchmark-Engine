"""Black-box OpenBot adapter.

The CLI is invoked with an isolated `OPENBOT_STATE_DIR` and a profile that
names only the OHBE LiteLLM gateway. OpenBot stdout is stored as a
supplementary transcript. Token counts stay missing unless that JSON actually
contains a usage object; they are never filled with zero.
"""

from __future__ import annotations

import json
from typing import Any

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.adapters.openbot.profile import (
    OpenBotProfileError,
    benchmark_profile,
    check_harness_binding,
    render_benchmark_profile,
)
from ohbe.adapters.openbot.runner import (
    agent_argv,
    benchmark_child_env,
    isolated_state_dir,
    onboard_argv,
    run_command,
)
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.integrity import GENESIS_HASH
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog, TraceRecord
from ohbe.gateway.reconcile import reconcile_response


class OpenBotAdapter:
    """Configure the benchmark profile and map one CLI run onto OHBE records."""

    def __init__(self, *, binary: str, api_key: str) -> None:
        if not binary:
            raise ValueError("binary must be the openbot executable")
        self.binary = binary
        self.api_key = api_key

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        timeout = check_harness_binding(
            request.config,
            pool=request.pool,
            environment=request.environment,
        )
        state_dir = isolated_state_dir(request.state_dir)
        message = task_message(request.task)
        requested = request.task.input.get("model_id")
        if isinstance(requested, str) and requested not in {model.model_id for model in request.pool.models}:
            raise OpenBotProfileError(f"{requested} is not in the model pool allowlist")
        env = benchmark_child_env(os_environ(), state_dir=state_dir, api_key=self.api_key)
        calls: list[dict[str, Any]] = []
        onboard = onboard_argv(self.binary)
        onboard_result = run_command(onboard, env=env, cwd=state_dir, timeout_seconds=timeout)
        calls.append(_call("onboard", onboard, onboard_result.returncode, onboard_result.stdout, onboard_result.stderr))
        profile_error = None
        agent_result = None
        if onboard_result.returncode != 0:
            status = ExecutionStatus.FAILED
            error = "openbot onboard failed"
        else:
            try:
                profile = benchmark_profile(
                    pool=request.pool,
                    environment=request.environment,
                    timeout_seconds=timeout,
                )
                (state_dir / "openbot.json").write_text(
                    render_benchmark_profile(
                        pool=request.pool,
                        environment=request.environment,
                        timeout_seconds=timeout,
                    ),
                    encoding="utf-8",
                )
            except OpenBotProfileError as exc:
                profile = None
                profile_error = str(exc)
            if profile_error is not None or profile is None:
                status = ExecutionStatus.FAILED
                error = profile_error
            else:
                command = agent_argv(
                    self.binary,
                    message,
                    timeout_seconds=timeout,
                    local=request.config.parameters.get("local", True) is not False,
                )
                agent_result = run_command(command, env=env, cwd=state_dir, timeout_seconds=timeout)
                calls.append(
                    _call("agent", command, agent_result.returncode, agent_result.stdout, agent_result.stderr)
                )
                status, error = interpret_agent_result(
                    agent_result.stdout,
                    agent_result.stderr,
                    agent_result.returncode,
                )
        parsed = _parse_json_object(agent_result.stdout) if agent_result is not None else None
        usage = usage_fields(parsed)
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
        trace = _trace(request, execution, calls, usage, error)
        return HarnessRun(execution=execution, trace=trace)


def task_message(task: Any) -> str:
    for key in ("prompt", "message"):
        value = task.input.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(task.input, ensure_ascii=False, sort_keys=True)


def interpret_agent_result(stdout: str, stderr: str, exit_code: int) -> tuple[ExecutionStatus, str | None]:
    """Map the CLI result onto an execution status. Non-JSON success fails closed."""

    if exit_code != 0:
        parsed = _parse_json_object(stdout)
        detail = None
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            detail = parsed["error"]
        if detail is None:
            detail = stderr.strip() or f"openbot agent exited {exit_code}"
        return ExecutionStatus.FAILED, detail
    parsed = _parse_json_object(stdout)
    if not isinstance(parsed, dict):
        return ExecutionStatus.FAILED, "openbot --json did not return a JSON object"
    if parsed.get("ok") is not True:
        error = parsed.get("error")
        return ExecutionStatus.FAILED, error if isinstance(error, str) and error else "openbot reported failure"
    return ExecutionStatus.SUCCEEDED, None


def usage_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Copy usage from the CLI JSON. Omitted usage stays missing, not zero."""

    if payload is None or "usage" not in payload:
        reconciliation = reconcile_response(None)
        reason = "openbot transcript omitted usage"
    else:
        reconciliation = reconcile_response(payload)
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


def os_environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def _parse_json_object(stdout: str) -> dict[str, Any] | None:
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _selected_model_id(request: HarnessRunRequest) -> str:
    requested = request.task.input.get("model_id")
    if isinstance(requested, str) and requested:
        return requested
    return request.pool.models[0].model_id


def _call(name: str, argv: list[str], exit_code: int, stdout: str, stderr: str) -> dict[str, Any]:
    return {
        "argv": argv[1:],
        "command": name,
        "exit_code": exit_code,
        "stderr": stderr,
        "stdout": stdout,
    }


def _trace(
    request: HarnessRunRequest,
    execution: Execution,
    calls: list[dict[str, Any]],
    usage: dict[str, Any],
    error: str | None,
) -> ExecutionTrace:
    identity = {
        "config_hash": request.config.config_hash,
        "harness_version": request.config.version,
        "model_pool_hash": request.pool.content_hash,
        "model_pool_id": request.pool.pool_id,
    }
    log = TraceLog(request.state_dir / "trace.jsonl")
    started = log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={
            **identity,
            "deliver": False,
            "gateway_host": request.environment.gateway_host,
            "gateway_port": request.environment.gateway_port,
            "model_id": _selected_model_id(request),
            "provider": "litellm",
            "task_id": request.task.task_id,
            "task_input": request.task.input,
        },
        recorded_at=request.started_at,
        record_id=f"{request.execution_id}-started",
    )
    log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.INVOCATION_COMPLETED,
        payload={
            **usage,
            "model_pool_id": request.pool.pool_id,
            "source": "ohbe",
        },
        recorded_at=request.finished_at,
        record_id=f"{request.execution_id}-usage",
    )
    log.append(
        execution_id=request.execution_id,
        event_type="openbot.transcript",
        payload={
            "calls": calls,
            "source_of_truth": "ohbe.trace",
            "supplementary": True,
        },
        recorded_at=request.finished_at,
        record_id=f"{request.execution_id}-transcript",
    )
    finished = log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.EXECUTION_FINISHED,
        payload={
            **identity,
            "error": error,
            "status": execution.status.value,
        },
        recorded_at=request.finished_at,
        record_id=f"{request.execution_id}-finished",
    )
    records = tuple(log.read())
    if records[0].prev_hash != GENESIS_HASH:
        raise AdapterError("trace did not start at the genesis hash")
    if not isinstance(started, TraceRecord) or finished.record_hash != records[-1].record_hash:
        raise AdapterError("trace head was not recorded")
    report = log.verify(expected_head=finished.record_hash)
    if not report.ok:
        raise AdapterError(report.errors[0] if report.errors else "trace failed verification")
    return ExecutionTrace.build(
        trace_id=request.trace_id,
        execution_id=request.execution_id,
        records=records,
    )
