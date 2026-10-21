"""Black-box DeepSeek Harness adapter.

The CLI is invoked with an isolated ``DSH_HOME`` and a ``--patch`` overlay
that names only the OHBE LiteLLM gateway. Headless stdout is stored as a
supplementary transcript. Token counts stay missing unless that stdout is a
JSON object with a usage object; they are never filled with zero.

Assumptions for ``dsh`` 0.2.1-alpha.2 (``dsh-v0.2.1-alpha.2``): ``--profile
headless`` plus a positional task prints the final assistant text and exits
0 when the turn completes. ``--patch`` replaces ``llm-pi-ai`` providers and
``agent-default-model``. The headless profile still mounts its own tools
(shell, filesystem, and a DeepSeek web search plugin). Those tools are not
the OpenBot or Inspect ``react()`` tool surface. Official provider
credentials are stripped, so that search plugin cannot authenticate.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.adapters.deepseek_harness.profile import (
    DeepSeekHarnessProfileError,
    check_dsh_binding,
    render_litellm_patch,
    selected_model_id,
)
from ohbe.adapters.deepseek_harness.runner import (
    benchmark_child_env,
    headless_argv,
    isolated_dsh_home,
    run_command,
)
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.integrity import GENESIS_HASH
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog, TraceRecord
from ohbe.gateway.reconcile import reconcile_response


class DeepSeekHarnessAdapter:
    """Map one ``dsh --profile headless`` run onto OHBE records."""

    def __init__(self, *, binary: str, api_key: str) -> None:
        if not binary:
            raise ValueError("binary must be the dsh executable")
        self.binary = binary
        self.api_key = api_key

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        timeout = check_dsh_binding(
            request.config,
            pool=request.pool,
            environment=request.environment,
        )
        state_dir = isolated_dsh_home(request.state_dir)
        message = task_message(request.task)
        model_id = selected_model_id(request.task, request.pool)
        patch_path = state_dir / "ohbe-litellm.patch.yml"
        patch_path.write_text(
            render_litellm_patch(
                pool=request.pool,
                environment=request.environment,
                model_id=model_id,
            ),
            encoding="utf-8",
        )
        env = benchmark_child_env(os_environ(), state_dir=state_dir, api_key=self.api_key)
        command = headless_argv(self.binary, message, patch=patch_path)
        try:
            completed = run_command(command, env=env, cwd=state_dir, timeout_seconds=timeout)
        except subprocess.TimeoutExpired:
            completed = None
        if completed is None:
            status = ExecutionStatus.FAILED
            error = "dsh headless timed out"
            call = _call(command, 124, "", error)
            parsed = None
        else:
            call = _call(command, completed.returncode, completed.stdout, completed.stderr)
            parsed = _parse_json_object(completed.stdout)
            status, error = interpret_headless_result(
                completed.stdout,
                completed.stderr,
                completed.returncode,
            )
        usage = usage_fields(parsed)
        answer = answer_text(completed.stdout) if completed is not None else None
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
        trace = _trace(request, execution, call, usage, answer, error, model_id)
        return HarnessRun(execution=execution, trace=trace)


def task_message(task: Any) -> str:
    for key in ("prompt", "message"):
        value = task.input.get(key)
        if isinstance(value, str) and value:
            return value
    rendered = json.dumps(task.input, ensure_ascii=False, sort_keys=True)
    if not rendered:
        raise DeepSeekHarnessProfileError("task input is empty")
    return rendered


def interpret_headless_result(stdout: str, stderr: str, exit_code: int) -> tuple[ExecutionStatus, str | None]:
    """Map the headless exit onto an execution status.

    Exit 0 is a completed turn. The objective judge scores the answer later.
    An empty completion fails closed. Non-JSON stdout is the normal contract
    for this CLI and is not a failure by itself.
    """

    if exit_code != 0:
        parsed = _parse_json_object(stdout)
        detail = None
        if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
            detail = parsed["error"]
        if detail is None:
            detail = stderr.strip() or stdout.strip() or f"dsh headless exited {exit_code}"
        return ExecutionStatus.FAILED, detail
    if not stdout.strip():
        return ExecutionStatus.FAILED, "dsh headless returned no answer"
    return ExecutionStatus.SUCCEEDED, None


def answer_text(stdout: str) -> str | None:
    """Prefer a JSON ``text`` or ``answer`` field, otherwise the raw completion."""

    stripped = stdout.strip()
    if not stripped:
        return None
    parsed = _parse_json_object(stripped)
    if isinstance(parsed, dict):
        for key in ("text", "answer"):
            value = parsed.get(key)
            if isinstance(value, str) and value:
                return value
    return stripped


def usage_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Copy usage from JSON stdout. Omitted usage stays missing, not zero."""

    if payload is None or "usage" not in payload:
        reconciliation = reconcile_response(None)
        reason = "dsh transcript omitted usage"
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


def _call(argv: list[str], exit_code: int, stdout: str, stderr: str) -> dict[str, Any]:
    return {
        "argv": argv[1:],
        "command": "headless",
        "exit_code": exit_code,
        "stderr": stderr,
        "stdout": stdout,
    }


def _trace(
    request: HarnessRunRequest,
    execution: Execution,
    call: dict[str, Any],
    usage: dict[str, Any],
    answer: str | None,
    error: str | None,
    model_id: str,
) -> ExecutionTrace:
    identity = {
        "config_hash": request.config.config_hash,
        "git_tag": request.config.parameters.get("git_tag"),
        "harness_version": request.config.version,
        "model_pool_hash": request.pool.content_hash,
        "model_pool_id": request.pool.pool_id,
        "package": request.config.parameters.get("package"),
    }
    log = TraceLog(request.state_dir / "trace.jsonl")
    started = log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={
            **identity,
            "gateway_host": request.environment.gateway_host,
            "gateway_port": request.environment.gateway_port,
            "model_id": model_id,
            "profile": "headless",
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
        event_type="dsh.transcript",
        payload={
            "answer": answer,
            "calls": [call],
            "source_of_truth": "ohbe.trace",
            "supplementary": True,
            "tool_surface": "headless plugins differ from openbot and inspect react",
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
