"""OpenBot adapter: one successful task and one failed task, gateway profile only."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ohbe.adapters.base import HarnessRunRequest
from ohbe.adapters.openbot.adapter import OpenBotAdapter, interpret_agent_result, usage_fields
from ohbe.adapters.openbot.profile import (
    OPENBOT_COMMIT_UNAVAILABLE,
    OPENBOT_IMAGE_UNAVAILABLE,
    OpenBotProfileError,
    benchmark_profile,
    normalize_openbot_commit,
    openbot_harness_config,
)
from ohbe.adapters.openbot.runner import docker_agent_argv
from ohbe.core.execution import ExecutionEnvironment, ExecutionStatus
from ohbe.core.trace import TraceEventType, TraceLog
from tests.samples import NOW, sample_environment, sample_fingerprint, sample_pool, sample_task

COMMIT = "0123456789abcdef0123456789abcdef01234567"
_FAKE = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state = Path(os.environ["OPENBOT_STATE_DIR"])
record = {
    "argv": sys.argv[1:],
    "litellm": "set" if os.environ.get("LITELLM_API_KEY") else "missing",
    "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
    "huggingface": os.environ.get("HUGGINGFACE_API_KEY") or os.environ.get("HF_TOKEN"),
    "openai": os.environ.get("OPENAI_API_KEY"),
    "proxy": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
}
with (state / "calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\\n")
argv = sys.argv[1:]
if argv[:1] == ["onboard"]:
    if "--non-interactive" not in argv or "litellm-api-key" not in argv:
        raise SystemExit(2)
    raise SystemExit(0)
if argv[:1] == ["agent"]:
    if "--deliver" in argv:
        raise SystemExit(3)
    config = json.loads((state / "openbot.json").read_text(encoding="utf-8"))
    providers = list(config["models"]["providers"])
    if providers != ["litellm"]:
        print(json.dumps({"ok": False, "error": "provider bypass"}))
        raise SystemExit(4)
    message = argv[argv.index("-m") + 1]
    if message.startswith("fail"):
        print(json.dumps({"ok": False, "error": "task failed"}))
        raise SystemExit(1)
    print(json.dumps({"ok": True, "text": "done"}))
    raise SystemExit(0)
raise SystemExit(2)
"""


def _fake(path: Path) -> Path:
    path.write_text(_FAKE, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _request(state: Path, *, prompt: str, commit: str = COMMIT) -> HarnessRunRequest:
    environment = sample_environment()
    pool = sample_pool()
    config = openbot_harness_config(commit=commit, pool=pool, environment=environment, timeout_seconds=30)
    task = sample_task(input={"prompt": prompt})
    return HarnessRunRequest(
        task=task,
        pool=pool,
        config=config,
        environment=environment,
        experiment_id="exp-1",
        execution_id="exec-openbot",
        trace_id="trace-openbot",
        seed=7,
        fingerprint=sample_fingerprint(),
        state_dir=state,
        started_at=NOW,
        finished_at=NOW,
    )


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, prompt: str, commit: str = COMMIT):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct-provider")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-provider")
    monkeypatch.setenv("HUGGINGFACE_API_KEY", "hf-provider")
    monkeypatch.setenv("HF_TOKEN", "hf-token-provider")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    binary = _fake(tmp_path / "openbot")
    request = _request(tmp_path / "state", prompt=prompt, commit=commit)
    result = OpenBotAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    calls = [
        json.loads(line)
        for line in (request.state_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return request, result, calls


def test_profile_is_litellm_only_and_pinned() -> None:
    environment = sample_environment()
    pool = sample_pool()
    document = benchmark_profile(pool=pool, environment=environment, timeout_seconds=30)
    rendered = json.dumps(document)
    assert list(document["models"]["providers"]) == ["litellm"]
    assert document["models"]["providers"]["litellm"]["baseUrl"] == "http://ohbe-gateway:8080"
    assert document["models"]["providers"]["litellm"]["apiKey"] == "${LITELLM_API_KEY}"
    assert list(document["agents"]["defaults"]["models"]) == [
        f"litellm/{model.model_id}" for model in pool.models
    ]
    assert document["messages"]["deliver"] is False
    assert document["channels"] == {}
    assert document["tools"]["elevated"]["enabled"] is False
    assert "sk-" not in rendered
    assert "api.openai.com" not in rendered
    assert normalize_openbot_commit(OPENBOT_COMMIT_UNAVAILABLE) == OPENBOT_COMMIT_UNAVAILABLE
    with pytest.raises(OpenBotProfileError):
        normalize_openbot_commit("latest")
    provider = ExecutionEnvironment(
        egress_policy_version="ohbe-egress-0.1",
        gateway_host="api.openai.com",
        gateway_port=443,
    )
    with pytest.raises(OpenBotProfileError):
        benchmark_profile(pool=pool, environment=provider, timeout_seconds=30)


def test_success_records_version_pool_and_missing_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request, result, calls = _run(tmp_path, monkeypatch, prompt="say hi")
    assert result.execution.status is ExecutionStatus.SUCCEEDED
    assert result.execution.harness.version == COMMIT
    assert result.execution.harness.config_hash == request.config.config_hash
    assert result.execution.model_pool_id == request.pool.pool_id
    assert result.execution.model_pool_hash == request.pool.content_hash
    result.execution.check_binding(task=request.task, harness=request.config, pool=request.pool)
    assert result.trace.head_hash == result.trace.records[-1].record_hash
    usage = next(record for record in result.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert usage.payload["usage_status"] == "missing"
    assert usage.payload["prompt_tokens"] is None
    assert usage.payload["completion_tokens"] is None
    assert usage.payload["total_tokens"] is None
    assert usage.payload["cost_amount"] is None
    started = result.trace.records[0]
    assert started.payload["harness_version"] == COMMIT
    assert started.payload["config_hash"] == request.config.config_hash
    assert started.payload["model_pool_id"] == request.pool.pool_id
    assert started.payload["deliver"] is False
    report = TraceLog(request.state_dir / "trace.jsonl").verify(expected_head=result.trace.head_hash)
    assert report.ok
    onboard, agent = calls
    assert onboard["argv"][:3] == ["onboard", "--non-interactive", "--auth-choice"]
    assert "litellm-api-key" in onboard["argv"]
    assert agent["argv"][0] == "agent"
    assert "--deliver" not in agent["argv"]
    assert "--local" in agent["argv"]
    assert "--json" in agent["argv"]
    assert agent["openai"] is None
    assert agent["anthropic"] is None
    assert agent["huggingface"] is None
    assert agent["proxy"] is None
    assert agent["litellm"] == "set"
    profile = json.loads((request.state_dir / "openbot.json").read_text(encoding="utf-8"))
    assert profile["models"]["providers"]["litellm"]["baseUrl"] == "http://ohbe-gateway:8080"
    blob = (request.state_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "litellm-test-key" not in blob
    assert "sk-direct-provider" not in blob


def test_failed_task_still_has_a_valid_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request, result, calls = _run(
        tmp_path,
        monkeypatch,
        prompt="fail the task",
        commit=OPENBOT_COMMIT_UNAVAILABLE,
    )
    assert result.execution.status is ExecutionStatus.FAILED
    assert result.execution.harness.version == OPENBOT_COMMIT_UNAVAILABLE
    assert result.execution.model_pool_id == request.pool.pool_id
    finished = result.trace.records[-1]
    assert finished.event_type == TraceEventType.EXECUTION_FINISHED
    assert finished.payload["status"] == "failed"
    assert finished.payload["error"] == "task failed"
    assert finished.payload["config_hash"] == request.config.config_hash
    usage = next(record for record in result.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert usage.payload["prompt_tokens"] is None
    assert usage.payload["cost_amount"] is None
    transcript = next(record for record in result.trace.records if record.event_type == "openbot.transcript")
    assert transcript.payload["supplementary"] is True
    assert transcript.payload["source_of_truth"] == "ohbe.trace"
    assert calls[-1]["argv"][0] == "agent"
    assert "--deliver" not in calls[-1]["argv"]
    report = TraceLog(request.state_dir / "trace.jsonl").verify(expected_head=result.trace.head_hash)
    assert report.ok
    result.execution.check_binding(task=request.task, harness=request.config, pool=request.pool)


def test_usage_object_is_captured_and_absent_usage_is_not_zero() -> None:
    present = usage_fields(
        {"usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4}}
    )
    assert present["usage_status"] == "present"
    assert present["prompt_tokens"] == 2
    assert present["cost_status"] == "not_computed"
    assert present["cost_amount"] is None
    missing = usage_fields({"ok": True, "text": "done"})
    assert missing["usage_status"] == "missing"
    assert missing["prompt_tokens"] is None
    assert missing["cost_amount"] is None
    status, error = interpret_agent_result('{"ok": false, "error": "nope"}', "", 0)
    assert status is ExecutionStatus.FAILED
    assert error == "nope"


def test_model_outside_the_pool_is_not_launched(tmp_path: Path) -> None:
    environment = sample_environment()
    pool = sample_pool()
    config = openbot_harness_config(commit=COMMIT, pool=pool, environment=environment)
    task = sample_task(input={"prompt": "hi", "model_id": "other/model"})
    request = HarnessRunRequest(
        task=task,
        pool=pool,
        config=config,
        environment=environment,
        experiment_id="exp-1",
        execution_id="exec-blocked",
        trace_id="trace-blocked",
        seed=7,
        fingerprint=sample_fingerprint(),
        state_dir=tmp_path / "state",
        started_at=NOW,
        finished_at=NOW,
    )
    with pytest.raises(OpenBotProfileError, match="allowlist"):
        OpenBotAdapter(binary="/no/such/openbot", api_key="litellm-test-key").run(request)
    assert not (tmp_path / "state" / "calls.jsonl").exists()


def test_docker_entry_uses_the_internal_network(tmp_path: Path) -> None:
    argv = docker_agent_argv(
        image=OPENBOT_IMAGE_UNAVAILABLE,
        network="ohbe_egress",
        state_dir=tmp_path / "state",
        message="say hi",
        timeout_seconds=30,
    )
    assert argv[argv.index("--network") + 1] == "ohbe_egress"
    assert "--deliver" not in argv
    assert argv[argv.index("openbot") + 1] == "agent"
    with pytest.raises(ValueError):
        docker_agent_argv(
            image=OPENBOT_IMAGE_UNAVAILABLE,
            network="none",
            state_dir=tmp_path / "state",
            message="say hi",
            timeout_seconds=30,
        )


@pytest.mark.skipif(not os.environ.get("OPENBOT_BIN"), reason="OPENBOT_BIN is not set")
def test_real_openbot_binary_responds() -> None:
    binary = os.environ["OPENBOT_BIN"]
    result = subprocess.run(
        [binary, "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0
    assert "agent" in (result.stdout + result.stderr).lower()
