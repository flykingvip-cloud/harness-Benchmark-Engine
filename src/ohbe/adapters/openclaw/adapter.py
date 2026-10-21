"""Black-box OpenClaw adapter.

OpenClaw is an orchestration and channel layer. A plugin can hand the turn
to DeepSeek Harness or another agent. This adapter does not do that. It runs
``openclaw agent exec`` with a config that pins the built-in runtime
``openclaw``, disables plugins, and names only the OHBE LiteLLM gateway.

The exec defaults still select the ``coding`` tool profile, leave the sandbox
off, and scope filesystem tools to ``--cwd``. That is not the OpenBot
benchmark profile and not Inspect ``react()`` with ``bash`` in Docker.
``--json`` stdout is a supplementary transcript. Token counts stay missing
unless that envelope has a usage object. OpenClaw's ``input``/``output``/
``total`` fields are copied onto the chat-completions names. They are never
filled with zero. ``costUsd`` is not an OHBE cost.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.adapters.openclaw.profile import (
    OPENCLAW_EXECUTION_PATH,
    OPENCLAW_PLUGIN_BRIDGE,
    OPENCLAW_PRODUCT_LAYER,
    OPENCLAW_RUNTIME,
    OpenClawProfileError,
    check_openclaw_binding,
    litellm_model_ref,
    render_exec_config,
    selected_model_id,
)
from ohbe.adapters.openclaw.runner import (
    agent_exec_argv,
    benchmark_child_env,
    isolated_openclaw_state,
    run_command,
)
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.integrity import GENESIS_HASH
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog, TraceRecord
from ohbe.gateway.reconcile import reconcile_response


class OpenClawAdapter:
    """Map one embedded ``openclaw agent exec`` run onto OHBE records."""

    def __init__(self, *, binary: str, api_key: str) -> None:
        if not binary:
            raise ValueError("binary must be the openclaw executable")
        self.binary = binary
        self.api_key = api_key

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        timeout = check_openclaw_binding(
            request.config,
            pool=request.pool,
            environment=request.environment,
        )
        state_dir = isolated_openclaw_state(request.state_dir)
        message = task_message(request.task)
        model_id = selected_model_id(request.task, request.pool)
        config_path = state_dir / "ohbe-openclaw.json"
        message_path = state_dir / "ohbe-task.txt"
        config_path.write_text(
            render_exec_config(
                pool=request.pool,
                environment=request.environment,
                model_id=model_id,
                timeout_seconds=timeout,
            ),
            encoding="utf-8",
        )
        message_path.write_text(message, encoding="utf-8")
        env = benchmark_child_env(os_environ(), state_dir=state_dir, api_key=self.api_key)
        command = agent_exec_argv(
            self.binary,
            config=config_path,
            state_dir=state_dir,
            message_file=message_path,
            model_ref=litellm_model_ref(model_id),
            timeout_seconds=timeout,
        )
        try:
            completed = run_command(command, env=env, cwd=state_dir, timeout_seconds=timeout)
        except subprocess.TimeoutExpired:
            completed = None
        if completed is None:
            status = ExecutionStatus.FAILED
            error = "openclaw agent exec timed out"
            call = _call(command, 124, "", error)
            parsed = None
        else:
            call = _call(command, completed.returncode, completed.stdout, completed.stderr)
            parsed = _parse_json_object(completed.stdout)
            status, error = interpret_exec_result(
                completed.stdout,
                completed.stderr,
                completed.returncode,
                model_id=model_id,
            )
        usage = usage_fields(parsed)
        answer = answer_text(parsed) if status is ExecutionStatus.SUCCEEDED else None
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
        raise OpenClawProfileError("task input is empty")
    return rendered


def interpret_exec_result(
    stdout: str,
    stderr: str,
    exit_code: int,
    *,
    model_id: str,
) -> tuple[ExecutionStatus, str | None]:
    """Map the ``--json`` envelope onto an execution status.

    Exit 0 with ``status: ok`` and a non-empty ``final`` is a completed turn.
    The objective judge scores the answer later. Exit 2, or ``status:
    timeout``, is a timeout. A provider or runtime other than the pinned
    LiteLLM embedded agent fails closed. Plain text is not this contract.
    """

    parsed = _parse_json_object(stdout)
    if exit_code != 0:
        detail = _error_message(parsed)
        if detail is None:
            detail = stderr.strip() or stdout.strip() or f"openclaw agent exec exited {exit_code}"
        timed_out = exit_code == 2 or (isinstance(parsed, dict) and parsed.get("status") == "timeout")
        if timed_out and "timeout" not in detail.lower() and "timed out" not in detail.lower():
            detail = f"openclaw agent exec timed out: {detail}"
        return ExecutionStatus.FAILED, detail
    if not isinstance(parsed, dict):
        return ExecutionStatus.FAILED, "openclaw --json did not return a JSON object"
    if parsed.get("ok") is not True or parsed.get("status") != "ok":
        return ExecutionStatus.FAILED, _error_message(parsed) or "openclaw reported failure"
    route_error = _route_error(parsed, model_id)
    if route_error is not None:
        return ExecutionStatus.FAILED, route_error
    if answer_text(parsed) is None:
        return ExecutionStatus.FAILED, "openclaw agent exec returned no answer"
    return ExecutionStatus.SUCCEEDED, None


def answer_text(payload: dict[str, Any] | None) -> str | None:
    """Prefer the envelope ``final`` text, then the first payload ``text``."""

    if not isinstance(payload, dict):
        return None
    final = payload.get("final")
    if isinstance(final, str) and final.strip():
        return final
    payloads = payload.get("payloads")
    if isinstance(payloads, list):
        for item in payloads:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return None


def usage_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Copy usage from the JSON envelope. Omitted usage stays missing, not zero.

    The stable envelope uses ``input``, ``output``, and ``total``. A
    chat-completions usage object is accepted as already translated.
    ``costUsd`` is ignored here.
    """

    body = _usage_body(payload)
    if body is None:
        reconciliation = reconcile_response(None)
        reason = "openclaw transcript omitted usage"
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


def os_environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def _usage_body(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None or "usage" not in payload or payload.get("usage") is None:
        return None
    raw = payload.get("usage")
    if not isinstance(raw, dict):
        return {"usage": raw}
    chat = ("prompt_tokens", "completion_tokens", "total_tokens")
    if all(name in raw for name in chat):
        return {"usage": {name: raw[name] for name in chat}}
    native = ("input", "output", "total")
    if all(name in raw for name in native):
        return {
            "usage": {
                "prompt_tokens": raw["input"],
                "completion_tokens": raw["output"],
                "total_tokens": raw["total"],
            }
        }
    return {"usage": raw}


def _route_error(parsed: dict[str, Any], model_id: str) -> str | None:
    provider = parsed.get("provider")
    if provider is not None and provider != "litellm":
        return "openclaw provider was not litellm"
    model = parsed.get("model")
    if model is not None and model not in {model_id, litellm_model_ref(model_id)}:
        return "openclaw model route was not the selected pool member"
    for key in ("runtime", "agentRuntime", "harness"):
        value = parsed.get(key)
        if isinstance(value, str) and value != OPENCLAW_RUNTIME:
            return "openclaw runtime was not the built-in embedded agent"
        if isinstance(value, dict) and value.get("id") not in (None, OPENCLAW_RUNTIME):
            return "openclaw runtime was not the built-in embedded agent"
    return None


def _error_message(parsed: dict[str, Any] | None) -> str | None:
    if not isinstance(parsed, dict):
        return None
    error = parsed.get("error")
    if isinstance(error, str) and error:
        return error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
    return None


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
        "command": "agent-exec",
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
        "execution_path": OPENCLAW_EXECUTION_PATH,
        "git_tag": request.config.parameters.get("git_tag"),
        "harness_version": request.config.version,
        "model_pool_hash": request.pool.content_hash,
        "model_pool_id": request.pool.pool_id,
        "package": request.config.parameters.get("package"),
        "plugin_bridge": OPENCLAW_PLUGIN_BRIDGE,
        "product_layer": OPENCLAW_PRODUCT_LAYER,
        "runtime": OPENCLAW_RUNTIME,
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
            "model_ref": litellm_model_ref(model_id),
            "profile": "agent-exec",
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
        event_type="openclaw.transcript",
        payload={
            "answer": answer,
            "calls": [call],
            "execution_path": OPENCLAW_EXECUTION_PATH,
            "plugin_bridge": OPENCLAW_PLUGIN_BRIDGE,
            "product_layer": OPENCLAW_PRODUCT_LAYER,
            "runtime": OPENCLAW_RUNTIME,
            "source_of_truth": "ohbe.trace",
            "supplementary": True,
            "tool_surface": "coding profile, sandbox off, cwd scoped; not openbot and not inspect react",
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
