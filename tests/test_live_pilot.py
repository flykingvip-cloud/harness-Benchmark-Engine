"""Live pilot manifest, fail-closed prerequisites, and the run pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.adapters.pilot import pilot_suite
from ohbe.cli import main
from ohbe.core.execution import Execution, ExecutionStatus
from ohbe.core.extensions import OracleStub
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog
from ohbe.eval.objective import select_objective
from ohbe.live import LiveContext, load_live_pilot, resolve_live_context, run_live_pilot
from ohbe.live.context import LiveContext as ContextType
from ohbe.live.spec import LivePilotError
from ohbe.report import load_report_input, render_report

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "experiments" / "live-pilot-smoke.yaml"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
API_KEY = "live-pilot-test-token"


class StepClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 9, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(milliseconds=20)
        return current


class ScriptedAdapter:
    """Test double. Usage comes from the double, never from the experiment file."""

    def __init__(self, usage: str, *, oracle: bool = False, fail: bool = False) -> None:
        self.usage = usage
        self.oracle = oracle
        self.fail = fail
        self.timeouts: list[int] = []

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        if self.fail:
            raise AdapterError("harness failed to start")
        self.timeouts.append(int(request.config.parameters["timeout_seconds"]))
        _scorer, target = select_objective(request.task)
        model_id = request.pool.models[0].model_id
        if self.usage == "present":
            usage = {
                "completion_tokens": 20,
                "cost_amount": "999",
                "model_id": model_id,
                "normalized_gpu_seconds": "2",
                "prompt_tokens": 100,
                "total_tokens": 120,
                "usage_status": "present",
            }
        elif self.usage == "missing":
            usage = {
                "completion_tokens": None,
                "cost_amount": "0",
                "model_id": model_id,
                "prompt_tokens": None,
                "total_tokens": None,
                "usage_status": "missing",
            }
        else:
            raise AssertionError(self.usage)
        execution = Execution.build(
            execution_id=request.execution_id,
            experiment_id=request.experiment_id,
            task_id=request.task.task_id,
            task_content_hash=request.task.content_hash,
            harness=request.config.to_ref(),
            model_pool_id=request.pool.pool_id,
            model_pool_hash=request.pool.content_hash,
            seed=request.seed,
            status=ExecutionStatus.SUCCEEDED,
            started_at=request.started_at,
            finished_at=request.finished_at,
            fingerprint=request.fingerprint,
            environment=request.environment,
            oracle=OracleStub() if self.oracle else None,
        )
        log = TraceLog(request.state_dir / "trace.jsonl")
        log.append(
            execution_id=request.execution_id,
            event_type=TraceEventType.EXECUTION_STARTED,
            payload={
                "gateway_host": request.environment.gateway_host,
                "gateway_port": request.environment.gateway_port,
                "model_id": model_id,
                "model_pool_id": request.pool.pool_id,
                "task_id": request.task.task_id,
                "task_input": request.task.input,
            },
            recorded_at=request.started_at,
            record_id=f"{request.execution_id}-started",
        )
        log.append(
            execution_id=request.execution_id,
            event_type=TraceEventType.INVOCATION_COMPLETED,
            payload=usage,
            recorded_at=request.started_at,
            record_id=f"{request.execution_id}-usage",
        )
        log.append(
            execution_id=request.execution_id,
            event_type="evaluation.answer",
            payload={"answer": target},
            recorded_at=request.started_at,
            record_id=f"{request.execution_id}-answer",
        )
        log.append(
            execution_id=request.execution_id,
            event_type=TraceEventType.EXECUTION_FINISHED,
            payload={"error": None, "status": ExecutionStatus.SUCCEEDED.value},
            recorded_at=request.started_at,
            record_id=f"{request.execution_id}-finished",
        )
        trace = ExecutionTrace.build(
            trace_id=request.trace_id,
            execution_id=request.execution_id,
            records=tuple(log.read()),
        )
        return HarnessRun(execution=execution, trace=trace)


def _context(binary: Path, **overrides: object) -> ContextType:
    data: dict[str, object] = {
        "api_key": API_KEY,
        "base_url": "http://gpu-litellm:4000/v1",
        "gateway_host": "gpu-litellm",
        "gateway_port": 4000,
        "openbot_bin": str(binary),
        "openbot_commit": COMMIT,
        "openbot_image": f"ghcr.example/openbot:{COMMIT}",
        "litellm_image": "ghcr.io/berriai/litellm:v1.104.2",
        "litellm_image_digest": None,
    }
    data.update(overrides)
    return LiveContext(**data)  # type: ignore[arg-type]


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_smoke_manifest_matches_the_objective_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    text = SMOKE.read_text(encoding="utf-8")
    for forbidden in (
        "oracle",
        "prompt_tokens",
        "success_rate",
        "cost_amount",
        "routing_regret",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "HUGGINGFACE_API_KEY",
        "api.openai.com",
        "api.anthropic.com",
        "huggingface.co",
    ):
        assert forbidden not in text
    spec = load_live_pilot(SMOKE)
    assert spec.experiment_id == "live-pilot-smoke"
    assert spec.pool_id == "pool-gpu-farm"
    assert spec.pool_file == "models/pools/pool-gpu-farm.example.yaml"
    assert spec.harnesses == ("openbot", "inspect_react")
    assert spec.repetitions == 1
    assert 1 <= len(spec.tasks) <= 3
    assert spec.max_cost is None
    assert [model.provider for model in spec.models] == ["vllm", "ollama"]
    assert [model.model_id for model in spec.models] == [
        "vllm/REPLACE_ME-instruct",
        "ollama/REPLACE_ME",
    ]
    assert spec.models[0].litellm_model == "hosted_vllm/REPLACE_ME-instruct"
    assert spec.egress.gateway_host == "gpu-litellm"
    assert spec.egress.gateway_port == 4000
    assert spec.egress.network == "ohbe_egress"
    suite = pilot_suite()
    assert [task.task_id for task in spec.tasks] == [task.task_id for task in suite]
    for live, pilot in zip(spec.tasks, suite, strict=True):
        assert live.prompt == pilot.input["prompt"]
        assert pilot.expected is not None
        assert live.target == pilot.expected[live.scorer]


def test_manifest_rejects_spend_oracle_and_bad_protocol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    original = SMOKE.read_text(encoding="utf-8")

    def _load(text: str) -> None:
        path = tmp_path / "pilot.yaml"
        path.write_text(text, encoding="utf-8")
        load_live_pilot(path)

    with pytest.raises(LivePilotError, match="repetitions"):
        _load(original.replace("repetitions: 1", "repetitions: 4", 1))
    with pytest.raises(LivePilotError, match="harnesses"):
        _load(original.replace("  - inspect_react\n", ""))
    with pytest.raises(LivePilotError, match="oracle"):
        _load(original + "oracle: {}\n")
    with pytest.raises(LivePilotError, match="max_cost"):
        _load(original.replace("max_total_tokens: 4096", 'max_total_tokens: 4096\nmax_cost: 1.00', 1))
    with pytest.raises(LivePilotError, match="provider"):
        _load(original.replace("gateway_host: gpu-litellm", "gateway_host: api.openai.com", 1))
    with pytest.raises(LivePilotError, match="provider"):
        _load(original.replace("gateway_host: gpu-litellm", "gateway_host: foo.openai.com", 1))
    with pytest.raises(LivePilotError, match="provider"):
        _load(original.replace("gateway_host: gpu-litellm", "gateway_host: bar.anthropic.com", 1))
    with pytest.raises(LivePilotError, match="models/pools"):
        _load(
            original.replace(
                "model_pool_file: models/pools/pool-gpu-farm.example.yaml",
                "model_pool_file: pricing/ohbe-offline-2026-10-09.yaml",
                1,
            )
        )
    with pytest.raises(LivePilotError, match="exactly one"):
        _load(original.replace("model_pool_file:", "model_pool: {}\nmodel_pool_file:", 1))
    inline = original.replace(
        "model_pool_file: models/pools/pool-gpu-farm.example.yaml\n",
        "model_pool:\n"
        "  pool_id: pool-gpu-farm\n"
        "  models:\n"
        "    - model_id: openai/REPLACE_ME\n"
        "      provider: openai\n"
        "      name: REPLACE_ME\n"
        "      revision: unpinned\n"
        "      metadata:\n"
        "        litellm_model: hosted_vllm/REPLACE_ME\n",
        1,
    )
    with pytest.raises(LivePilotError, match="vllm and ollama"):
        _load(inline)
    keyed = original.replace(
        "model_pool_file: models/pools/pool-gpu-farm.example.yaml\n",
        "model_pool:\n"
        "  pool_id: pool-gpu-farm\n"
        "  models:\n"
        "    - model_id: vllm/REPLACE_ME-instruct\n"
        "      provider: vllm\n"
        "      name: REPLACE_ME-instruct\n"
        "      revision: unpinned\n"
        "      metadata:\n"
        "        litellm_model: hosted_vllm/REPLACE_ME-instruct\n"
        "        api_key_env: OPENAI_API_KEY\n",
        1,
    )
    with pytest.raises(LivePilotError, match="cloud provider API key"):
        _load(keyed)


def test_context_fails_closed_before_a_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    spec = load_live_pilot(SMOKE)
    with pytest.raises(LivePilotError, match="LITELLM_API_KEY"):
        resolve_live_context({}, spec)
    binary = _executable(tmp_path / "openbot")
    base = {
        "LITELLM_API_KEY": API_KEY,
        "LITELLM_BASE_URL": "http://gpu-litellm:4000/v1",
        "OPENBOT_COMMIT": COMMIT,
        "OPENBOT_BIN": str(binary),
    }
    with pytest.raises(LivePilotError, match="provider"):
        resolve_live_context({**base, "LITELLM_BASE_URL": "http://api.openai.com:443/v1"}, spec)
    with pytest.raises(LivePilotError, match="provider"):
        resolve_live_context({**base, "LITELLM_BASE_URL": "http://api.anthropic.com:443/v1"}, spec)
    with pytest.raises(LivePilotError, match="provider"):
        resolve_live_context({**base, "LITELLM_BASE_URL": "http://huggingface.co:443/v1"}, spec)
    with pytest.raises(LivePilotError, match="provider"):
        resolve_live_context({**base, "LITELLM_BASE_URL": "http://foo.openai.com:443/v1"}, spec)
    with pytest.raises(LivePilotError, match="provider"):
        resolve_live_context({**base, "LITELLM_BASE_URL": "http://bar.anthropic.com:443/v1"}, spec)
    with pytest.raises(LivePilotError, match="match the experiment"):
        resolve_live_context({**base, "LITELLM_BASE_URL": "http://127.0.0.1:4000/v1"}, spec)
    with pytest.raises(LivePilotError, match="SHA"):
        resolve_live_context({**base, "OPENBOT_COMMIT": "latest"}, spec)
    with pytest.raises(LivePilotError, match="OPENBOT_IMAGE"):
        resolve_live_context({**base, "OPENBOT_IMAGE": "openbot:latest"}, spec)
    monkeypatch.setattr("ohbe.live.context.installed_inspect_version", lambda: None)
    monkeypatch.setattr("ohbe.live.context.inspect_pin_matches", lambda: False)
    with pytest.raises(LivePilotError, match="inspect-ai"):
        resolve_live_context(base, spec)


def test_run_prices_reported_usage_and_leaves_missing_usage_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    spec = load_live_pilot(SMOKE)
    binary = _executable(tmp_path / "openbot")
    openbot = ScriptedAdapter("missing")
    inspect = ScriptedAdapter("present")
    result = run_live_pilot(
        spec,
        context=_context(binary),
        out_dir=tmp_path / "live",
        adapters={"openbot": openbot, "inspect_react": inspect},
        clock=StepClock(),
    )
    assert result.published is True
    assert result.passed is True
    assert openbot.timeouts == [120, 120, 120]
    assert inspect.timeouts == [120, 120, 120]
    out = result.out_dir
    blob = "\n".join(path.read_text(encoding="utf-8") for path in out.rglob("*") if path.is_file())
    assert API_KEY not in blob
    executions = (out / "executions.jsonl").read_text(encoding="utf-8")
    paired = (out / "paired.jsonl").read_text(encoding="utf-8")
    report_input = (out / "report-input.json").read_text(encoding="utf-8")
    assert "999" not in executions
    assert "999" not in paired
    assert "999" not in report_input
    rows = [json.loads(line) for line in executions.splitlines()]
    assert rows
    present = [row for row in rows if row["harness_id"] == "inspect-react"]
    missing = [row for row in rows if row["harness_id"] == "openbot"]
    assert present and missing
    # 2 GPU-seconds * (3.60 / 3600) = 0.002. provider_cost stays the computed zero.
    assert all(
        row["cost_status"] == "computed"
        and row["cost_amount"] == "0.002"
        and row["normalized_cost"] == "0.002"
        and row["provider_cost"] == "0"
        and row["provider_cost_status"] == "computed"
        for row in present
    )
    assert all(
        row["cost_amount"] is None
        and row["provider_cost"] is None
        and row["normalized_cost"] is None
        and row["cost_status"] != "computed"
        for row in missing
    )
    assert all(row["latency_ms"] == 20 for row in rows)
    document = load_report_input(out / "report-input.json")
    assert document["audit"]["corpus"] == "live"
    assert document["audit"]["passed"] is True
    limitations = " ".join(document["audit"]["limitations"]).lower()
    assert "fixture" not in limitations
    assert "not live" not in limitations
    rendered = json.loads(render_report(document).report_json)
    assert rendered["spend"] == "as_supplied"
    assert rendered["routing_optimality"] is None
    assert rendered["model_independence"] is None
    assert all(gate["passed"] is True for gate in rendered["release_gates"])
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["oracle"] is None
    assert manifest["routing_regret"] is None
    assert manifest["budget"]["max_cost"] is None
    assert manifest["model_pool"]["pool_file"] == "models/pools/pool-gpu-farm.example.yaml"
    assert manifest["model_pool"]["model_ids"] == ["vllm/REPLACE_ME-instruct", "ollama/REPLACE_ME"]
    assert manifest["egress"]["allowlist"] == "gpu-litellm:4000"
    assert manifest["egress"]["base_url_env"] == "LITELLM_BASE_URL"
    assert manifest["egress"]["openbot_base_url"] == "http://gpu-litellm:4000"
    assert manifest["topology"] == {
        "gateway_role": "gpu-farm-litellm",
        "harness_host": "private-openbot",
        "model_backends": ["ollama", "vllm"],
    }
    assert manifest["harnesses"][0]["version"] == COMMIT
    assert API_KEY not in json.dumps(manifest)


def test_run_does_not_publish_when_a_harness_fails_or_sets_oracle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(ROOT)
    spec = load_live_pilot(SMOKE)
    binary = _executable(tmp_path / "openbot")
    failed = run_live_pilot(
        spec,
        context=_context(binary),
        out_dir=tmp_path / "failed",
        adapters={"openbot": ScriptedAdapter("present"), "inspect_react": ScriptedAdapter("present", fail=True)},
        clock=StepClock(),
    )
    assert failed.published is False
    assert not (failed.out_dir / "report-input.json").exists()
    assert not (failed.out_dir / "paired.jsonl").exists()
    error = json.loads((failed.out_dir / "error.json").read_text(encoding="utf-8"))
    assert error["published"] is False
    assert error["observed_cost"] is None
    assert error["harness_id"] == "inspect-react"
    claimed = run_live_pilot(
        spec,
        context=_context(binary),
        out_dir=tmp_path / "oracle",
        adapters={
            "openbot": ScriptedAdapter("present", oracle=True),
            "inspect_react": ScriptedAdapter("present"),
        },
        clock=StepClock(),
    )
    assert claimed.published is False
    assert "oracle" in claimed.message
    assert not (claimed.out_dir / "report-input.json").exists()
    oracle_error = json.loads((claimed.out_dir / "error.json").read_text(encoding="utf-8"))
    assert oracle_error["observed_cost"] is None


def test_cli_refuses_to_start_without_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.chdir(ROOT)
    for name in ("LITELLM_API_KEY", "LITELLM_BASE_URL", "OPENBOT_COMMIT", "OPENBOT_BIN", "OPENBOT_IMAGE"):
        monkeypatch.delenv(name, raising=False)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("runner started")

    monkeypatch.setattr("ohbe.cli.run_live_pilot", _boom)
    out = tmp_path / "live"
    code = main(["run", "--experiment", str(SMOKE), "--out", str(out)])
    assert code == 2
    err = capsys.readouterr().err
    for name in ("LITELLM_API_KEY", "LITELLM_BASE_URL", "OPENBOT_COMMIT", "OPENBOT_BIN"):
        assert name in err
    assert not out.exists()
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    assert "run" in capsys.readouterr().out


def test_live_results_are_gitignored() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "results/live/**" in text
    assert "!results/live/README.md" in text
    assert (ROOT / "results" / "live" / "README.md").is_file()
    assert list((ROOT / "results" / "live").glob("*.json")) == []
