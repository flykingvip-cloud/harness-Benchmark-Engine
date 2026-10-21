"""WorkBuddy adapter against a fake CLI. CI does not spend."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ohbe.adapters.fairness import FairnessRecord, assert_same_task_and_pool, fairness_record
from ohbe.adapters.pilot import pilot_requests, pilot_workbuddy_request
from ohbe.adapters.workbuddy.adapter import WorkBuddyAdapter
from ohbe.adapters.workbuddy.profile import (
    WORKBUDDY_VERSION,
    WorkBuddyProfileError,
    normalize_workbuddy_version,
    render_models_json,
    validate_models_document,
)
from ohbe.adapters.workbuddy.runner import which_workbuddy
from ohbe.core.execution import ExecutionEnvironment, ExecutionStatus
from ohbe.core.trace import TraceEventType
from ohbe.eval.objective import extract_answer
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import WORKBUDDY_INSTALL_URLS, WORKBUDDY_VERSION as CATALOG_VERSION
from tests.samples import NOW, sample_environment, sample_fingerprint, sample_pool

_FAKE = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

home = Path(os.environ["HOME"])
config = Path(os.environ["CODEBUDDY_CONFIG_DIR"])
stdin = sys.stdin.read()
argv = sys.argv[1:]
record = {
    "argv": argv,
    "stdin": stdin,
    "litellm": os.environ.get("LITELLM_API_KEY"),
    "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
    "openai": os.environ.get("OPENAI_API_KEY"),
    "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
    "base_url": os.environ.get("CODEBUDDY_BASE_URL"),
    "api_key": os.environ.get("CODEBUDDY_API_KEY"),
    "auth": os.environ.get("CODEBUDDY_AUTH_TOKEN"),
    "proxy": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
    "home": os.environ.get("HOME"),
    "config_dir": os.environ.get("CODEBUDDY_CONFIG_DIR"),
    "model_env": os.environ.get("CODEBUDDY_MODEL"),
    "sandbox": os.environ.get("CODEBUDDY_IS_SANDBOX"),
    "marketplace": os.environ.get("CODEBUDDY_SKIP_BUILTIN_MARKETPLACE"),
    "retries": os.environ.get("CODEBUDDY_MAX_RETRIES"),
}
(home / "calls.json").write_text(json.dumps(record), encoding="utf-8")
model = argv[argv.index("--model") + 1] if "--model" in argv else ""
models = json.loads((config / "models.json").read_text(encoding="utf-8"))
url = models["models"][0]["url"]
provider = "litellm"
if stdin.startswith("bypass"):
    provider = "openai"
    url = "https://api.openai.com/v1/chat/completions"
envelope = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "pong",
    "provider": provider,
    "model": model,
    "url": url,
}
if stdin.startswith("fail"):
    print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "error": "task failed"}))
    raise SystemExit(1)
if stdin.startswith("timeout"):
    print(json.dumps({"type": "result", "subtype": "timeout", "is_error": True, "error": "deadline"}))
    raise SystemExit(2)
if stdin.startswith("plain"):
    print("pong")
    raise SystemExit(0)
if stdin.startswith("legacy"):
    print(json.dumps({"response": "pong", "metadata": {}}))
    raise SystemExit(0)
if stdin.startswith("json"):
    envelope["usage"] = {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
    envelope["total_cost_usd"] = 0.01
    print(json.dumps(envelope))
    raise SystemExit(0)
if stdin.startswith("zero"):
    envelope["usage"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    print(json.dumps(envelope))
    raise SystemExit(0)
if stdin.startswith("partial"):
    envelope["usage"] = {"input_tokens": 2, "output_tokens": 1}
    print(json.dumps(envelope))
    raise SystemExit(0)
if stdin.startswith("bridge"):
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
    from ohbe.adapters.workbuddy.profile import workbuddy_harness_config
    from tests.samples import sample_task

    environment = sample_environment()
    pool = sample_pool()
    config = workbuddy_harness_config(pool=pool, environment=environment, timeout_seconds=30)
    task_input: dict[str, str] = {"prompt": prompt}
    if model_id is not None:
        task_input["model_id"] = model_id
    return HarnessRunRequest(
        task=sample_task(input=task_input),
        pool=pool,
        config=config,
        environment=environment,
        experiment_id="exp-workbuddy",
        execution_id="exec-workbuddy",
        trace_id="trace-workbuddy",
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
    binary = _fake(tmp_path / "codebuddy")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct-provider")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-provider")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-provider")
    monkeypatch.setenv("CODEBUDDY_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("CODEBUDDY_API_KEY", "sk-codebuddy-vendor")
    monkeypatch.setenv("CODEBUDDY_AUTH_TOKEN", "platform-token")
    monkeypatch.setenv("CODEBUDDY_IS_SANDBOX", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    request = _request(tmp_path / name, prompt=prompt, model_id=model_id)
    run = WorkBuddyAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    return request, run


def test_pin_rejects_latest_and_matches_the_catalog() -> None:
    assert CATALOG_VERSION == WORKBUDDY_VERSION == "5.7.7"
    assert len(WORKBUDDY_INSTALL_URLS) == 3
    assert all(url.endswith((".dmg", ".exe")) for url in WORKBUDDY_INSTALL_URLS)
    assert "latest" not in "".join(WORKBUDDY_INSTALL_URLS)
    assert HARNESS_ORDER == ("openbot", "inspect_react")
    assert "workbuddy" not in HARNESS_ORDER
    with pytest.raises(WorkBuddyProfileError):
        normalize_workbuddy_version("latest")
    with pytest.raises(WorkBuddyProfileError):
        normalize_workbuddy_version("main")


def test_print_keeps_missing_usage_and_strips_provider_keys(
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
    transcript = next(record for record in run.trace.records if record.event_type == "workbuddy.transcript")
    assert transcript.payload["product_layer"] == "desktop-app"
    assert transcript.payload["execution_path"] == "bundled-codebuddy-cli"
    assert transcript.payload["desktop_install"] == "unsupported"
    assert transcript.payload["model_route"] == "openai-chat-completions"
    assert transcript.payload["base_url_override"] == "refused"
    assert transcript.payload["dsh_model_source"] == "refused"
    assert transcript.payload["permissions"] == "not-skipped"
    assert transcript.payload["cli"] == "codebuddy"
    calls = json.loads((tmp_path / "state" / "calls.json").read_text(encoding="utf-8"))
    assert calls["argv"][:2] == ["--print", "--output-format"]
    assert calls["stdin"] == "Reply pong"
    assert "Reply pong" not in calls["argv"]
    assert "-y" not in calls["argv"]
    assert "--dangerously-skip-permissions" not in calls["argv"]
    assert "--serve" not in calls["argv"]
    assert "--fallback-model" not in calls["argv"]
    assert calls["litellm"] == "litellm-test-key"
    assert calls["anthropic"] is None
    assert calls["openai"] is None
    assert calls["deepseek"] is None
    assert calls["base_url"] is None
    assert calls["api_key"] is None
    assert calls["auth"] is None
    assert calls["proxy"] is None
    assert calls["sandbox"] is None
    assert calls["marketplace"] == "1"
    assert calls["retries"] == "0"
    assert calls["model_env"] == "openai/gpt-test"
    assert calls["home"] == str(tmp_path / "state")
    assert calls["config_dir"] == str(tmp_path / "state" / ".codebuddy")
    models = json.loads((tmp_path / "state" / ".codebuddy" / "models.json").read_text(encoding="utf-8"))
    assert models["availableModels"] == ["openai/gpt-test", "openai/gpt-test-mini"]
    assert {row["vendor"] for row in models["models"]} == {"litellm"}
    assert {row["url"] for row in models["models"]} == {"http://ohbe-gateway:8080/v1/chat/completions"}
    assert {row["apiKey"] for row in models["models"]} == {"${LITELLM_API_KEY}"}
    assert {row["supportsToolCall"] for row in models["models"]} == {False}
    settings = json.loads((tmp_path / "state" / ".codebuddy" / "settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "openai/gpt-test"
    assert settings["permissions"]["deny"] == ["WebFetch", "WebSearch"]
    assert "env" not in settings
    mcp = json.loads((tmp_path / "state" / ".codebuddy" / "mcp.json").read_text(encoding="utf-8"))
    assert mcp == {"mcpServers": {}}
    rendered = (tmp_path / "state" / ".codebuddy" / "models.json").read_text(encoding="utf-8")
    assert "api.deepseek.com" not in rendered
    assert "api.openai.com" not in rendered
    assert "sk-" not in rendered.lower()
    blob = (tmp_path / "state" / "trace.jsonl").read_text(encoding="utf-8")
    assert "litellm-test-key" not in blob
    assert "sk-direct-provider" not in blob
    assert "sk-codebuddy-vendor" not in blob
    assert "platform-token" not in blob
    assert request.config.parameters["desktop_install"] == "unsupported"
    assert request.config.parameters["dsh_model_source"] == "refused"
    assert request.config.version == "5.7.7"


def test_json_usage_is_kept_and_total_cost_usd_is_not_an_ohbe_cost(
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
    _, partial = _run(tmp_path, monkeypatch, prompt="partial usage", name="partial")
    partial_usage = next(
        record for record in partial.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED
    )
    assert partial_usage.payload["usage_status"] == "invalid"
    assert partial_usage.payload["prompt_tokens"] is None
    _, legacy = _run(tmp_path, monkeypatch, prompt="legacy response", name="legacy")
    assert legacy.execution.status is ExecutionStatus.SUCCEEDED
    assert extract_answer(legacy.trace) == "pong"


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
    assert bridge_finished.payload["error"] == "codebuddy runtime was not the bundled print CLI"
    with pytest.raises(WorkBuddyProfileError):
        _run(tmp_path, monkeypatch, prompt="Reply pong", model_id="not-in-pool", name="stranger")


def test_a_leading_dash_stays_on_stdin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, run = _run(tmp_path, monkeypatch, prompt="--print", name="dash")
    assert run.execution.status is ExecutionStatus.SUCCEEDED
    calls = json.loads((tmp_path / "dash" / "calls.json").read_text(encoding="utf-8"))
    assert calls["stdin"] == "--print"
    assert calls["argv"].count("--print") == 1


def test_fairness_shares_task_pool_and_gateway(tmp_path: Path) -> None:
    binary = _fake(tmp_path / "bin" / "codebuddy")
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
    request = pilot_workbuddy_request(openbot, state_dir=tmp_path / "workbuddy")
    assert request.task is openbot.task
    assert request.pool is openbot.pool
    assert request.environment is openbot.environment
    assert request.config.config_hash != openbot.config.config_hash
    run = WorkBuddyAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    record = fairness_record(run, task=request.task)
    assert record.task_id == openbot.task.task_id
    assert record.model_pool_hash == openbot.pool.content_hash
    assert record.model_id == pool.models[0].model_id
    assert record.gateway_host == environment.gateway_host
    assert record.gateway_port == environment.gateway_port
    assert record.harness_version == WORKBUDDY_VERSION
    assert run.execution.oracle is None
    assert "workbuddy" not in HARNESS_ORDER
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


def test_models_json_rejects_a_provider_bypass() -> None:
    environment = sample_environment()
    pool = sample_pool()
    text = render_models_json(pool=pool, environment=environment)
    document = json.loads(text)
    document["models"][0]["url"] = "https://api.openai.com/v1/chat/completions"
    with pytest.raises(WorkBuddyProfileError):
        validate_models_document(document, pool=pool, environment=environment)
    document = json.loads(text)
    document["models"][0]["apiKey"] = "sk-literal"
    with pytest.raises(WorkBuddyProfileError):
        validate_models_document(document, pool=pool, environment=environment)
    document = json.loads(text)
    document["models"][0]["supportsToolCall"] = True
    with pytest.raises(WorkBuddyProfileError):
        validate_models_document(document, pool=pool, environment=environment)
    denied = sample_environment().model_copy(update={"gateway_host": "api.openai.com"})
    assert isinstance(denied, ExecutionEnvironment)
    with pytest.raises(WorkBuddyProfileError):
        render_models_json(pool=pool, environment=denied)


@pytest.mark.skipif(not os.environ.get("WORKBUDDY_BIN"), reason="WORKBUDDY_BIN is not set")
def test_real_workbuddy_version_flag_does_not_run_a_task() -> None:
    binary = which_workbuddy()
    assert binary is not None
    completed = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.args[1:] == ["--version"]
