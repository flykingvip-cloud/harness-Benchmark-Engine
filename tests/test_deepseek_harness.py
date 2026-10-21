"""DeepSeek Harness adapter against a fake dsh. CI does not spend."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from ohbe.adapters.deepseek_harness.adapter import DeepSeekHarnessAdapter
from ohbe.adapters.deepseek_harness.profile import (
    DSH_PACKAGE,
    DSH_VERSION,
    DeepSeekHarnessProfileError,
    dsh_harness_config,
    normalize_dsh_version,
    render_litellm_patch,
)
from ohbe.adapters.deepseek_harness.runner import which_dsh
from ohbe.adapters.fairness import fairness_record
from ohbe.adapters.pilot import pilot_dsh_request, pilot_requests
from ohbe.core.execution import ExecutionStatus
from ohbe.core.trace import TraceEventType
from ohbe.eval.objective import extract_answer
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import DSH_PROFILE_VERSION
from tests.samples import NOW, sample_environment, sample_fingerprint, sample_pool

_FAKE = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

home = Path(os.environ["DSH_HOME"])
record = {
    "argv": sys.argv[1:],
    "litellm": os.environ.get("LITELLM_API_KEY"),
    "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
    "openai": os.environ.get("OPENAI_API_KEY"),
    "deepseek": os.environ.get("DEEPSEEK_API_KEY"),
    "proxy": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy"),
    "home": os.environ.get("DSH_HOME"),
}
(home / "calls.json").write_text(json.dumps(record), encoding="utf-8")
message = sys.argv[-1]
if message.startswith("fail"):
    print("task failed", file=sys.stderr)
    raise SystemExit(1)
if message.startswith("json"):
    print(json.dumps({
        "text": "pong",
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }))
    raise SystemExit(0)
if message.startswith("zero"):
    print(json.dumps({
        "text": "pong",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }))
    raise SystemExit(0)
print("pong")
"""


def _fake(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FAKE, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _request(state: Path, *, prompt: str, model_id: str | None = None):
    environment = sample_environment()
    pool = sample_pool()
    config = dsh_harness_config(pool=pool, environment=environment, timeout_seconds=30)
    task_input: dict[str, str] = {"prompt": prompt}
    if model_id is not None:
        task_input["model_id"] = model_id
    from ohbe.adapters.base import HarnessRunRequest
    from tests.samples import sample_task

    return HarnessRunRequest(
        task=sample_task(input=task_input),
        pool=pool,
        config=config,
        environment=environment,
        experiment_id="exp-dsh",
        execution_id="exec-dsh",
        trace_id="trace-dsh",
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
    binary = _fake(tmp_path / "dsh")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct-provider")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-provider")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-provider")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example")
    request = _request(tmp_path / name, prompt=prompt, model_id=model_id)
    run = DeepSeekHarnessAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    return request, run


def test_pin_rejects_latest_and_matches_the_catalog() -> None:
    assert DSH_PROFILE_VERSION == DSH_VERSION
    assert HARNESS_ORDER == ("openbot", "inspect_react")
    with pytest.raises(DeepSeekHarnessProfileError):
        normalize_dsh_version("latest")
    with pytest.raises(DeepSeekHarnessProfileError):
        normalize_dsh_version("main")


def test_headless_success_keeps_missing_usage_and_strips_provider_keys(
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
    calls = json.loads((tmp_path / "state" / "calls.json").read_text(encoding="utf-8"))
    assert calls["argv"][:4] == ["--profile", "headless", "--patch", str(tmp_path / "state" / "ohbe-litellm.patch.yml")]
    assert calls["litellm"] == "litellm-test-key"
    assert calls["anthropic"] is None
    assert calls["openai"] is None
    assert calls["deepseek"] is None
    assert calls["proxy"] is None
    assert calls["home"] == str(tmp_path / "state")
    patch = (tmp_path / "state" / "ohbe-litellm.patch.yml").read_text(encoding="utf-8")
    assert "api.deepseek.com" not in patch
    assert "api.openai.com" not in patch
    assert "sk-" not in patch.lower()
    assert "apiKeyEnv: LITELLM_API_KEY" in patch
    assert "http://ohbe-gateway:8080/v1" in patch
    blob = (tmp_path / "state" / "trace.jsonl").read_text(encoding="utf-8")
    assert "litellm-test-key" not in blob
    assert "sk-direct-provider" not in blob
    assert "sk-anthropic-provider" not in blob
    assert "sk-deepseek-provider" not in blob
    assert request.config.parameters["package"] == DSH_PACKAGE


def test_json_usage_is_kept_and_explicit_zero_stays_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, present = _run(tmp_path, monkeypatch, prompt="json please", name="present")
    usage = next(record for record in present.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert usage.payload["usage_status"] == "present"
    assert usage.payload["prompt_tokens"] == 2
    assert extract_answer(present.trace) == "pong"
    _, zeros = _run(tmp_path, monkeypatch, prompt="zero please", name="zeros")
    zero_usage = next(record for record in zeros.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert zero_usage.payload["usage_status"] == "present"
    assert zero_usage.payload["prompt_tokens"] == 0


def test_failed_exit_and_unknown_model_do_not_spend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, failed = _run(tmp_path, monkeypatch, prompt="fail this")
    assert failed.execution.status is ExecutionStatus.FAILED
    finished = next(record for record in failed.trace.records if record.event_type == TraceEventType.EXECUTION_FINISHED)
    assert finished.payload["error"] == "task failed"
    with pytest.raises(DeepSeekHarnessProfileError):
        _run(tmp_path, monkeypatch, prompt="Reply pong", model_id="not-in-pool")


def test_fairness_shares_task_pool_and_gateway(tmp_path: Path) -> None:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = _fake(binary_dir / "dsh")
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
    request = pilot_dsh_request(openbot, state_dir=tmp_path / "dsh")
    assert request.task is openbot.task
    assert request.pool is openbot.pool
    assert request.environment is openbot.environment
    assert request.config.config_hash != openbot.config.config_hash
    run = DeepSeekHarnessAdapter(binary=str(binary), api_key="litellm-test-key").run(request)
    record = fairness_record(run, task=request.task)
    assert record.task_id == openbot.task.task_id
    assert record.model_pool_hash == openbot.pool.content_hash
    assert record.gateway_host == environment.gateway_host
    assert record.gateway_port == environment.gateway_port
    assert record.harness_version == DSH_VERSION
    assert run.execution.oracle is None
    assert "deepseek_harness" not in HARNESS_ORDER


def test_patch_render_rejects_a_provider_bypass() -> None:
    environment = sample_environment()
    pool = sample_pool()
    text = render_litellm_patch(pool=pool, environment=environment, model_id=pool.models[0].model_id)
    assert "providers:" in text
    assert "litellm" in text
    assert "api.anthropic.com" not in text


@pytest.mark.skipif(not os.environ.get("DSH_BIN"), reason="DSH_BIN is not set")
def test_real_dsh_version_flag_does_not_run_a_task() -> None:
    binary = which_dsh()
    assert binary is not None
    completed = subprocess.run(
        [binary, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert " " not in " ".join(completed.args[1:])
    assert completed.args[1:] == ["--version"]
