"""OpenClaw adapter against a fake CLI. CI does not spend."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ohbe.adapters.fairness import FairnessRecord, assert_same_task_and_pool, fairness_record
from ohbe.adapters.openclaw.adapter import OpenClawAdapter
from ohbe.adapters.openclaw.profile import (
    OPENCLAW_PACKAGE,
    OPENCLAW_VERSION,
    OpenClawProfileError,
    normalize_openclaw_version,
    render_exec_config,
    validate_exec_config,
)
from ohbe.adapters.openclaw.runner import which_openclaw
from ohbe.adapters.pilot import pilot_openclaw_request, pilot_requests
from ohbe.core.execution import ExecutionEnvironment, ExecutionStatus
from ohbe.core.trace import TraceEventType
from ohbe.eval.objective import extract_answer
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import OPENCLAW_INSTALL_URL, OPENCLAW_NPM_VERSION
from tests.samples import NOW, sample_environment, sample_fingerprint, sample_pool

_FAKE = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

home = Path(os.environ["OPENCLAW_STATE_DIR"])
argv = sys.argv[1:]
message = ""
if "--message-file" in argv:
    message = Path(argv[argv.index("--message-file") + 1]).read_text(encoding="utf-8")
model_ref = argv[argv.index("--model") + 1] if "--model" in argv else ""
model_id = model_ref[len("litellm/"):] if model_ref.startswith("litellm/") else model_ref
record = {
    "argv": argv,
    "litellm": os.environ.get("LITELLM_API_KEY"),
    "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
    "openai": os.environ.get("OPENAI_API_KEY"),
    "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
    "proxy": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
    "home": os.environ.get("HOME"),
    "state": os.environ.get("OPENCLAW_STATE_DIR"),
    "runtime_env": os.environ.get("OPENCLAW_AGENT_RUNTIME"),
    "message": message,
}
(home / "calls.json").write_text(json.dumps(record), encoding="utf-8")
provider = "litellm"
if message.startswith("bypass"):
    provider = "openai"
envelope = {
    "ok": True,
    "status": "ok",
    "final": "pong",
    "provider": provider,
    "model": model_id,
}
if message.startswith("fail"):
    print(json.dumps({"ok": False, "status": "error", "error": {"message": "task failed", "kind": "model"}}))
    raise SystemExit(1)
if message.startswith("timeout"):
    print(json.dumps({"ok": False, "status": "timeout", "error": {"message": "deadline", "kind": "timeout"}}))
    raise SystemExit(2)
if message.startswith("plain"):
    print("pong")
    raise SystemExit(0)
if message.startswith("json"):
    envelope["usage"] = {"input": 2, "output": 1, "total": 3}
    envelope["costUsd"] = 0.01
    print(json.dumps(envelope))
    raise SystemExit(0)
if message.startswith("zero"):
    envelope["usage"] = {"input": 0, "output": 0, "total": 0}
    print(json.dumps(envelope))
    raise SystemExit(0)
if message.startswith("bridge"):
    envelope["runtime"] = "dsh"
    print(json.dumps(envelope))
    raise SystemExit(0)
print(json.dumps(envelope))
"""


def _fake(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FAKE, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _request(state: Path, *, prompt: str, model_id: str | None = None):
    from ohbe.adapters.base import HarnessRunRequest
    from ohbe.adapters.openclaw.profile import openclaw_harness_config
    from tests.samples import sample_task

    environment = sample_environment()
    pool = sample_pool()
    config = openclaw_harness_config(pool=pool, environment=environment, timeout_seconds=30)
    task_input: dict[str, str] = {"prompt": prompt}
    if model_id is not None:
        task_input["model_id"] = model_id
    return HarnessRunRequest(
        task=sample_task(input=task_input),
        pool=pool,
        config=config,
        environment=environment,
        experiment_id="exp-openclaw",
        execution_id="exec-openclaw",
        trace_id="trace-openclaw",
        seed=7,
        fingerprint=sample_fingerprint(),
        state_dir=state,
        started_at=NOW,
        finished_at=NOW,
    )


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    prompt: str,
    model_id: str | None = None,
    name: str = "state",
):
    binary = _fake(tmp_path / "openclaw")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct-provider")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-provider")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-provider")
    monkeypatch.setenv("OPENCLAW_AGENT_RUNTIME", "dsh")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    request = _request(tmp_path / name, prompt=prompt, model_id=model_id)
    run = OpenClawAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    return request, run


def test_pin_rejects_latest_and_matches_the_catalog() -> None:
    assert OPENCLAW_NPM_VERSION == OPENCLAW_VERSION == "2026.10.1"
    assert OPENCLAW_INSTALL_URL == "https://registry.npmjs.org/openclaw/-/openclaw-2026.10.1.tgz"
    assert "latest" not in OPENCLAW_INSTALL_URL
    assert HARNESS_ORDER == ("openbot", "inspect_react")
    with pytest.raises(OpenClawProfileError):
        normalize_openclaw_version("latest")
    with pytest.raises(OpenClawProfileError):
        normalize_openclaw_version("main")


def test_embedded_exec_keeps_missing_usage_and_strips_provider_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, run = _run(tmp_path, monkeypatch, prompt="Reply pong")
    assert run.execution.status is ExecutionStatus.SUCCEEDED
    assert run.execution.oracle is None
    assert run.execution.routing_regret is None
    assert extract_answer(run.trace) == "pong"
    usage = next(record for record in run.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert usage.payload["usage_status"] == "missing"
    assert usage.payload["prompt_tokens"] is None
    assert usage.payload["cost_amount"] is None
    transcript = next(record for record in run.trace.records if record.event_type == "openclaw.transcript")
    assert transcript.payload["product_layer"] == "orchestration-channel"
    assert transcript.payload["execution_path"] == "embedded-agent"
    assert transcript.payload["runtime"] == "openclaw"
    assert transcript.payload["plugin_bridge"] == "refused"
    calls = json.loads((tmp_path / "state" / "calls.json").read_text(encoding="utf-8"))
    assert calls["argv"][:3] == ["agent", "exec", "--json"]
    assert "--config" in calls["argv"]
    assert "--auth-env-only" not in calls["argv"]
    assert "--isolated" not in calls["argv"]
    assert "gateway" not in calls["argv"]
    assert calls["message"] == "Reply pong"
    assert calls["litellm"] == "litellm-test-key"
    assert calls["anthropic"] is None
    assert calls["openai"] is None
    assert calls["deepseek"] is None
    assert calls["proxy"] is None
    assert calls["runtime_env"] is None
    assert calls["home"] == str(tmp_path / "state")
    config = json.loads((tmp_path / "state" / "ohbe-openclaw.json").read_text(encoding="utf-8"))
    assert set(config["models"]["providers"]) == {"litellm"}
    assert config["models"]["providers"]["litellm"]["baseUrl"] == "http://ohbe-gateway:8080/v1"
    assert config["models"]["providers"]["litellm"]["apiKey"] == "${LITELLM_API_KEY}"
    assert config["models"]["providers"]["litellm"]["agentRuntime"] == {"id": "openclaw"}
    assert config["plugins"] == {"allow": [], "enabled": False}
    assert config["channels"] == {}
    assert "gateway" not in config
    rendered = (tmp_path / "state" / "ohbe-openclaw.json").read_text(encoding="utf-8")
    assert "api.deepseek.com" not in rendered
    assert "api.openai.com" not in rendered
    assert "sk-" not in rendered.lower()
    blob = (tmp_path / "state" / "trace.jsonl").read_text(encoding="utf-8")
    assert "litellm-test-key" not in blob
    assert "sk-direct-provider" not in blob
    assert "sk-anthropic-provider" not in blob
    assert "sk-deepseek-provider" not in blob
    assert request.config.parameters["package"] == OPENCLAW_PACKAGE
    assert request.config.parameters["plugin_bridge"] == "refused"


def test_json_usage_is_kept_and_cost_usd_is_not_an_ohbe_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, present = _run(tmp_path, monkeypatch, prompt="json please", name="present")
    usage = next(record for record in present.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert usage.payload["usage_status"] == "present"
    assert usage.payload["prompt_tokens"] == 2
    assert usage.payload["completion_tokens"] == 1
    assert usage.payload["total_tokens"] == 3
    assert usage.payload["cost_status"] == "not_computed"
    assert usage.payload["cost_amount"] is None
    assert extract_answer(present.trace) == "pong"
    _, zeros = _run(tmp_path, monkeypatch, prompt="zero please", name="zeros")
    zero_usage = next(record for record in zeros.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert zero_usage.payload["usage_status"] == "present"
    assert zero_usage.payload["prompt_tokens"] == 0


def test_failed_exit_bridge_and_unknown_model_do_not_score(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, failed = _run(tmp_path, monkeypatch, prompt="fail this")
    assert failed.execution.status is ExecutionStatus.FAILED
    assert extract_answer(failed.trace) is None
    finished = next(record for record in failed.trace.records if record.event_type == TraceEventType.EXECUTION_FINISHED)
    assert finished.payload["error"] == "task failed"
    _, timed = _run(tmp_path, monkeypatch, prompt="timeout this", name="timed")
    timed_finished = next(
        record for record in timed.trace.records if record.event_type == TraceEventType.EXECUTION_FINISHED
    )
    assert "timed out" in timed_finished.payload["error"]
    _, plain = _run(tmp_path, monkeypatch, prompt="plain text", name="plain")
    assert plain.execution.status is ExecutionStatus.FAILED
    _, bypass = _run(tmp_path, monkeypatch, prompt="bypass provider", name="bypass")
    assert bypass.execution.status is ExecutionStatus.FAILED
    assert extract_answer(bypass.trace) is None
    _, bridge = _run(tmp_path, monkeypatch, prompt="bridge to dsh", name="bridge")
    assert bridge.execution.status is ExecutionStatus.FAILED
    bridge_finished = next(
        record for record in bridge.trace.records if record.event_type == TraceEventType.EXECUTION_FINISHED
    )
    assert bridge_finished.payload["error"] == "openclaw runtime was not the built-in embedded agent"
    with pytest.raises(OpenClawProfileError):
        _run(tmp_path, monkeypatch, prompt="Reply pong", model_id="not-in-pool", name="stranger")


def test_fairness_shares_task_pool_and_gateway(tmp_path: Path) -> None:
    binary = _fake(tmp_path / "bin" / "openclaw")
    environment = sample_environment()
    pool = sample_pool()
    openbot, _inspect = pilot_requests(
        environment=environment,
        pool=pool,
        fingerprint=sample_fingerprint(),
        state_root=tmp_path / "pilot",
        started_at=NOW,
        finished_at=NOW,
    )
    request = pilot_openclaw_request(openbot, state_dir=tmp_path / "openclaw")
    assert request.task is openbot.task
    assert request.pool is openbot.pool
    assert request.environment is openbot.environment
    assert request.config.config_hash != openbot.config.config_hash
    run = OpenClawAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    record = fairness_record(run, task=request.task)
    assert record.task_id == openbot.task.task_id
    assert record.model_pool_hash == openbot.pool.content_hash
    assert record.model_id == pool.models[0].model_id
    assert record.gateway_host == environment.gateway_host
    assert record.gateway_port == environment.gateway_port
    assert record.harness_version == OPENCLAW_VERSION
    assert run.execution.oracle is None
    assert "openclaw" not in HARNESS_ORDER
    partner = FairnessRecord(
        harness_id=openbot.config.harness_id,
        harness_version=openbot.config.version,
        config_hash=openbot.config.config_hash,
        task_id=openbot.task.task_id,
        task_content_hash=openbot.task.content_hash,
        task_input=dict(openbot.task.input),
        model_pool_id=openbot.pool.pool_id,
        model_pool_hash=openbot.pool.content_hash,
        model_id=openbot.pool.models[0].model_id,
        gateway_host=environment.gateway_host,
        gateway_port=environment.gateway_port,
        egress_policy_version=environment.egress_policy_version,
        status=record.status,
    )
    assert_same_task_and_pool(record, partner)


def test_config_rejects_a_provider_bypass_and_a_plugin_runtime() -> None:
    environment = sample_environment()
    pool = sample_pool()
    model_id = pool.models[0].model_id
    text = render_exec_config(pool=pool, environment=environment, model_id=model_id, timeout_seconds=30)
    document = json.loads(text)
    document["models"]["providers"]["litellm"]["agentRuntime"] = {"id": "dsh"}
    with pytest.raises(OpenClawProfileError):
        validate_exec_config(
            document,
            pool=pool,
            environment=environment,
            model_id=model_id,
            timeout_seconds=30,
        )
    document = json.loads(text)
    document["models"]["providers"]["openai"] = {"api": "openai-completions"}
    with pytest.raises(OpenClawProfileError):
        validate_exec_config(
            document,
            pool=pool,
            environment=environment,
            model_id=model_id,
            timeout_seconds=30,
        )
    denied = sample_environment().model_copy(update={"gateway_host": "api.openai.com"})
    assert isinstance(denied, ExecutionEnvironment)
    with pytest.raises(OpenClawProfileError):
        render_exec_config(pool=pool, environment=denied, model_id=model_id, timeout_seconds=30)


@pytest.mark.skipif(not os.environ.get("OPENCLAW_BIN"), reason="OPENCLAW_BIN is not set")
def test_real_openclaw_version_flag_does_not_run_a_task() -> None:
    binary = which_openclaw()
    assert binary is not None
    completed = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.args[1:] == ["--version"]
