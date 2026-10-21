"""Black-box WorkBuddy adapter.

WorkBuddy 5.7.7 is a desktop app. The fair non-GUI path is the bundled
CodeBuddy Code CLI, one ``--print --output-format json`` turn, with
``models.json`` pointed only at the OHBE LiteLLM chat-completions URL.
Permission skip is not passed. WebFetch and WebSearch are disallowed.
``CODEBUDDY_BASE_URL`` is stripped. The turn is not handed to DeepSeek
Harness.

``--json``-style stdout is a supplementary transcript. Token counts stay
missing unless that object has a complete usage object. They are never
filled with zero. ``total_cost_usd`` is not an OHBE cost. The desktop
installer is not executed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.adapters.workbuddy.profile import (
    DISALLOWED_TOOLS,
    WORKBUDDY_BASE_URL_OVERRIDE,
    WORKBUDDY_CLI,
    WORKBUDDY_DESKTOP_INSTALL,
    WORKBUDDY_DSH_MODEL_SOURCE,
    WORKBUDDY_EXECUTION_PATH,
    WORKBUDDY_MODEL_ROUTE,
    WORKBUDDY_PERMISSIONS,
    WORKBUDDY_PLUGIN_BRIDGE,
    WORKBUDDY_PRODUCT_LAYER,
    WORKBUDDY_PROFILE,
    WorkBuddyProfileError,
    chat_completions_url,
    check_workbuddy_binding,
    render_mcp_json,
    render_models_json,
    render_settings_json,
    selected_model_id,
)
from ohbe.adapters.workbuddy.runner import (
    benchmark_child_env,
    headless_print_argv,
    isolated_workbuddy_state,
    run_command,
)
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.integrity import GENESIS_HASH
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog, TraceRecord
from ohbe.gateway.reconcile import reconcile_response


class WorkBuddyAdapter:
    """Map one bundled ``codebuddy --print`` run onto OHBE records."""

    def __init__(self, *, binary: str, api_key: str) -> None:
        if not binary:
            raise ValueError("binary must be the bundled codebuddy executable")
        self.binary = binary
        self.api_key = api_key

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        timeout = check_workbuddy_binding(
            request.config,
            pool=request.pool,
            environment=request.environment,
        )
        state_dir = isolated_workbuddy_state(request.state_dir)
        config_dir = state_dir / ".codebuddy"
        message = task_message(request.task)
        model_id = selected_model_id(request.task, request.pool)
        endpoint = chat_completions_url(request.environment)
        (config_dir / "models.json").write_text(
            render_models_json(pool=request.pool, environment=request.environment),
            encoding="utf-8",
        )
        settings_path = config_dir / "settings.json"
        settings_path.write_text(
            render_settings_json(model_id=model_id, pool=request.pool),
            encoding="utf-8",
        )
        mcp_path = config_dir / "mcp.json"
        mcp_path.write_text(render_mcp_json(), encoding="utf-8")
        env = benchmark_child_env(
            os_environ(),
            state_dir=state_dir,
            api_key=self.api_key,
            model_id=model_id,
            timeout_seconds=timeout,
        )
        command = headless_print_argv(
            self.binary,
            model_id=model_id,
            settings=settings_path,
            mcp_config=mcp_path,
            disallowed_tools=",".join(DISALLOWED_TOOLS),
        )
        try:
            completed = run_command(
                command,
                env=env,
                cwd=state_dir,
                timeout_seconds=timeout,
                stdin_text=message,
            )
        except subprocess.TimeoutExpired:
            completed = None
        if completed is None:
            status = ExecutionStatus.FAILED
            error = "codebuddy print timed out"
            call = _call(command, 124, "", error)
            parsed = None
        else:
            call = _call(command, completed.returncode, completed.stdout, completed.stderr)
            parsed = _parse_json_object(completed.stdout)
            status, error = interpret_print_result(
                completed.stdout,
                completed.stderr,
                completed.returncode,
                model_id=model_id,
                endpoint=endpoint,
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
        trace = _trace(request, execution, call, usage, answer, error, model_id, timeout, state_dir)
        return HarnessRun(execution=execution, trace=trace)


def task_message(task: Any) -> str:
    for key in ("prompt", "message"):
        value = task.input.get(key)
        if isinstance(value, str) and value:
            return value
    rendered = json.dumps(task.input, ensure_ascii=False, sort_keys=True)
    if not rendered:
        raise WorkBuddyProfileError("task input is empty")
    return rendered


def interpret_print_result(
    stdout: str,
    stderr: str,
    exit_code: int,
    *,
    model_id: str,
    endpoint: str,
) -> tuple[ExecutionStatus, str | None]:
    """Map ``--output-format json`` onto an execution status.

    Exit 0 with a non-empty ``result`` (or the older ``response`` field) is a
    completed turn. The objective judge scores the answer later. A non-zero
    exit, ``is_error``, or a subtype other than ``success`` is a failed
    execution. Plain text is not this contract. A provider, endpoint, or
    runtime other than the pinned LiteLLM route fails closed.
    """

    parsed = _parse_json_object(stdout)
    if exit_code != 0:
        detail = _error_message(parsed)
        if detail is None:
            detail = stderr.strip() or stdout.strip() or f"codebuddy print exited {exit_code}"
        timed_out = exit_code == 124 or (isinstance(parsed, dict) and parsed.get("subtype") == "timeout")
        if timed_out and "timeout" not in detail.lower() and "timed out" not in detail.lower():
            detail = f"codebuddy print timed out: {detail}"
        return ExecutionStatus.FAILED, detail
    if not isinstance(parsed, dict):
        return ExecutionStatus.FAILED, "codebuddy --output-format json did not return a JSON object"
    if parsed.get("is_error") is True:
        return ExecutionStatus.FAILED, _error_message(parsed) or "codebuddy reported failure"
    subtype = parsed.get("subtype")
    if subtype not in (None, "success"):
        return ExecutionStatus.FAILED, _error_message(parsed) or "codebuddy reported failure"
    route_error = _route_error(parsed, model_id, endpoint)
    if route_error is not None:
        return ExecutionStatus.FAILED, route_error
    if answer_text(parsed) is None:
        return ExecutionStatus.FAILED, "codebuddy print returned no answer"
    return ExecutionStatus.SUCCEEDED, None


def answer_text(payload: dict[str, Any] | None) -> str | None:
    """Prefer ``result``, then the older reference field ``response``."""

    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if isinstance(result, str) and result.strip():
        return result
    response = payload.get("response")
    if isinstance(response, str) and response.strip():
        return response
    return None


def usage_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Copy usage from the JSON object. Omitted usage stays missing, not zero.

    Chat-completions names are accepted as already translated. Anthropic-style
    ``input_tokens`` / ``output_tokens`` / ``total_tokens`` are copied onto
    those names. A pair without ``total_tokens`` is incomplete and stays
    invalid. ``total_cost_usd`` is ignored here.
    """

    body = _usage_body(payload)
    if body is None:
        reconciliation = reconcile_response(None)
        reason = "workbuddy transcript omitted usage"
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
    raw = _usage_object(payload)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"usage": raw}
    chat = ("prompt_tokens", "completion_tokens", "total_tokens")
    if all(name in raw for name in chat):
        return {"usage": {name: raw[name] for name in chat}}
    anthropic = ("input_tokens", "output_tokens", "total_tokens")
    if all(name in raw for name in anthropic):
        return {
            "usage": {
                "prompt_tokens": raw["input_tokens"],
                "completion_tokens": raw["output_tokens"],
                "total_tokens": raw["total_tokens"],
            }
        }
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


def _usage_object(payload: dict[str, Any] | None) -> Any:
    if not isinstance(payload, dict):
        return None
    if "usage" in payload and payload.get("usage") is not None:
        return payload.get("usage")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and metadata.get("usage") is not None:
        return metadata.get("usage")
    return None


def _route_error(parsed: dict[str, Any], model_id: str, endpoint: str) -> str | None:
    provider = parsed.get("provider")
    if provider is not None and provider != "litellm":
        return "codebuddy provider was not litellm"
    model = parsed.get("model")
    if model is not None and model != model_id:
        return "codebuddy model route was not the selected pool member"
    for key in ("url", "base_url", "baseUrl", "endpoint"):
        value = parsed.get(key)
        if isinstance(value, str) and value != endpoint:
            return "codebuddy endpoint was not the OHBE gateway"
    for key in ("runtime", "harness", "agentRuntime"):
        value = parsed.get(key)
        if isinstance(value, str) and value not in {WORKBUDDY_CLI, "workbuddy"}:
            return "codebuddy runtime was not the bundled print CLI"
        if isinstance(value, dict) and value.get("id") not in (None, WORKBUDDY_CLI, "workbuddy"):
            return "codebuddy runtime was not the bundled print CLI"
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
        "command": "print",
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
    timeout_seconds: int,
    state_dir: Path,
) -> ExecutionTrace:
    identity = {
        "base_url_override": WORKBUDDY_BASE_URL_OVERRIDE,
        "cli": WORKBUDDY_CLI,
        "config_hash": request.config.config_hash,
        "desktop_install": WORKBUDDY_DESKTOP_INSTALL,
        "dsh_model_source": WORKBUDDY_DSH_MODEL_SOURCE,
        "execution_path": WORKBUDDY_EXECUTION_PATH,
        "harness_version": request.config.version,
        "model_pool_hash": request.pool.content_hash,
        "model_pool_id": request.pool.pool_id,
        "model_route": WORKBUDDY_MODEL_ROUTE,
        "permissions": WORKBUDDY_PERMISSIONS,
        "plugin_bridge": WORKBUDDY_PLUGIN_BRIDGE,
        "product_layer": WORKBUDDY_PRODUCT_LAYER,
        "profile": WORKBUDDY_PROFILE,
    }
    log = TraceLog(state_dir / "trace.jsonl")
    started = log.append(
        execution_id=request.execution_id,
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={
            **identity,
            "cli_timeout_ms": timeout_seconds * 1000,
            "gateway_host": request.environment.gateway_host,
            "gateway_port": request.environment.gateway_port,
            "model_id": model_id,
            "prompt_transport": "stdin",
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
        event_type="workbuddy.transcript",
        payload={
            "answer": answer,
            "base_url_override": WORKBUDDY_BASE_URL_OVERRIDE,
            "calls": [call],
            "cli": WORKBUDDY_CLI,
            "desktop_install": WORKBUDDY_DESKTOP_INSTALL,
            "dsh_model_source": WORKBUDDY_DSH_MODEL_SOURCE,
            "execution_path": WORKBUDDY_EXECUTION_PATH,
            "model_route": WORKBUDDY_MODEL_ROUTE,
            "permissions": WORKBUDDY_PERMISSIONS,
            "plugin_bridge": WORKBUDDY_PLUGIN_BRIDGE,
            "product_layer": WORKBUDDY_PRODUCT_LAYER,
            "source_of_truth": "ohbe.trace",
            "supplementary": True,
            "tool_surface": (
                "headless print; WebFetch and WebSearch disallowed; "
                "permission skip not passed; not the WorkBuddy GUI, "
                "not a DeepSeek Harness model source, and not OpenBot or Inspect react"
            ),
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
