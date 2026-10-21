"""Same pilot task through OpenBot and Inspect react()."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from ohbe.adapters.fairness import FairnessError, assert_same_task_and_pool, fairness_record
from ohbe.adapters.inspect_react import INSPECT_AI_VERSION, InspectEvalOutcome, InspectReactAdapter
from ohbe.adapters.inspect_react.plan import (
    LITELLM_API_KEY,
    LITELLM_PROXY_BASE_URL,
    InspectEvalPlan,
    InspectProfileError,
    build_eval_plan,
    inspect_harness_config,
    objective_target,
)
from ohbe.adapters.inspect_react.version import installed_inspect_version
from ohbe.adapters.openbot.adapter import OpenBotAdapter
from ohbe.adapters.pilot import PILOT_PROMPT, PILOT_TARGET, PILOT_TASK_ID, pilot_requests, pilot_task
from ohbe.core.execution import Execution, ExecutionEnvironment, ExecutionStatus
from ohbe.core.task import Task
from ohbe.core.trace import TraceEventType, TraceLog
from tests.samples import NOW, sample_environment, sample_fingerprint, sample_pool
from tests.test_openbot_adapter import _fake

API_KEY = "litellm-test-key"


def _requests(tmp_path: Path):
    return pilot_requests(
        environment=sample_environment(),
        pool=sample_pool(),
        fingerprint=sample_fingerprint(),
        state_root=tmp_path,
        started_at=NOW,
        finished_at=NOW,
    )


def test_pilot_requests_share_one_task(tmp_path: Path) -> None:
    openbot, inspect = _requests(tmp_path)
    assert openbot.task is inspect.task
    assert openbot.task.task_id == PILOT_TASK_ID
    assert openbot.task.input == {"prompt": PILOT_PROMPT}
    assert openbot.task.expected == {"contains": PILOT_TARGET}
    assert openbot.pool is inspect.pool
    assert openbot.environment is inspect.environment
    assert openbot.config.config_hash != inspect.config.config_hash
    assert openbot.config.harness_id == "openbot"
    assert inspect.config.harness_id == "inspect-react"
    assert inspect.config.version == INSPECT_AI_VERSION
    assert objective_target(openbot.task) == ("includes", PILOT_TARGET)


def test_paired_runs_record_the_same_task_and_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-direct-provider")
    openbot_request, inspect_request = _requests(tmp_path)
    binary_path = tmp_path / "bin" / "openbot"
    binary_path.parent.mkdir()
    binary = _fake(binary_path)
    openbot_run = OpenBotAdapter(binary=str(binary), api_key=API_KEY).run(openbot_request)
    seen: list[InspectEvalPlan] = []

    def runner(plan: InspectEvalPlan) -> InspectEvalOutcome:
        seen.append(plan)
        return InspectEvalOutcome(success=True, score="C", answer=PILOT_TARGET, usage=None)

    inspect_run = InspectReactAdapter(api_key=API_KEY, runner=runner).run(inspect_request)
    assert len(seen) == 1
    plan = seen[0]
    pool = openbot_request.pool
    assert plan.solver == "react"
    assert plan.sandbox == "docker"
    assert plan.tools == ("bash",)
    assert plan.scorer == "includes"
    assert plan.model == f"litellm-proxy/{pool.models[0].model_id}"
    assert plan.base_url == "http://ohbe-gateway:8080/v1"
    assert plan.env[LITELLM_PROXY_BASE_URL] == plan.base_url
    assert plan.env[LITELLM_API_KEY] == API_KEY
    assert set(plan.env) == {LITELLM_PROXY_BASE_URL, LITELLM_API_KEY}
    assert "OPENAI_API_KEY" not in plan.env
    assert plan.task_input == openbot_request.task.input
    assert plan.model_pool_ids == tuple(model.model_id for model in pool.models)

    for run, request in ((openbot_run, openbot_request), (inspect_run, inspect_request)):
        assert run.execution.status is ExecutionStatus.SUCCEEDED
        assert run.execution.task_id == PILOT_TASK_ID
        assert run.execution.task_content_hash == request.task.content_hash
        assert run.execution.model_pool_id == pool.pool_id
        assert run.execution.model_pool_hash == pool.content_hash
        assert run.execution.environment == request.environment
        assert run.execution.oracle is None
        assert run.execution.routing_regret is None
        report = TraceLog(request.state_dir / "trace.jsonl").verify(expected_head=run.trace.head_hash)
        assert report.ok
        started = next(record for record in run.trace.records if record.event_type == TraceEventType.EXECUTION_STARTED)
        assert started.payload["task_input"] == request.task.input
        assert started.payload["model_pool_id"] == pool.pool_id
        usage = next(record for record in run.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
        assert usage.payload["usage_status"] == "missing"
        assert usage.payload["prompt_tokens"] is None
        assert usage.payload["cost_amount"] is None
        text = (request.state_dir / "trace.jsonl").read_text(encoding="utf-8")
        assert API_KEY not in text
        assert "sk-direct-provider" not in text

    left = fairness_record(openbot_run, task=openbot_request.task)
    right = fairness_record(inspect_run, task=inspect_request.task)
    assert_same_task_and_pool(left, right)
    assert left.harness_id == "openbot"
    assert right.harness_id == "inspect-react"
    assert left.config_hash != right.config_hash
    assert left.model_id == pool.models[0].model_id
    calls = (openbot_request.state_dir / "calls.jsonl").read_text(encoding="utf-8")
    assert '"openai": null' in calls


def test_inspect_rejects_a_model_outside_the_pool_before_eval(tmp_path: Path) -> None:
    _, inspect_request = _requests(tmp_path)
    task = pilot_task()
    outside = Task.build(
        task_id=task.task_id,
        name=task.name,
        version=task.version,
        input={**task.input, "model_id": "missing-model"},
        expected=task.expected,
        tags=task.tags,
        metadata=task.metadata,
    )
    request = dataclasses.replace(inspect_request, task=outside)
    called = False

    def runner(plan: InspectEvalPlan) -> InspectEvalOutcome:
        nonlocal called
        called = True
        return InspectEvalOutcome(success=True)

    with pytest.raises(InspectProfileError, match="allowlist"):
        InspectReactAdapter(api_key=API_KEY, runner=runner).run(request)
    assert called is False


def test_inspect_requires_an_objective_target_and_the_gateway() -> None:
    environment = sample_environment()
    pool = sample_pool()
    task = pilot_task()
    with pytest.raises(InspectProfileError, match="objective"):
        objective_target(
            Task.build(
                task_id=task.task_id,
                name=task.name,
                version=task.version,
                input=task.input,
                expected={"note": "no scorer"},
            )
        )
    match_task = Task.build(
        task_id=task.task_id,
        name=task.name,
        version=task.version,
        input=task.input,
        expected={"match": PILOT_TARGET},
    )
    assert objective_target(match_task) == ("match", PILOT_TARGET)
    exact_task = Task.build(
        task_id=task.task_id,
        name=task.name,
        version=task.version,
        input=task.input,
        expected={"exact": PILOT_TARGET},
    )
    assert objective_target(exact_task) == ("match", PILOT_TARGET)
    provider = ExecutionEnvironment(
        egress_policy_version="ohbe-egress-0.1",
        gateway_host="api.openai.com",
        gateway_port=443,
    )
    with pytest.raises(InspectProfileError, match="gateway"):
        build_eval_plan(task=task, pool=pool, environment=provider, api_key=API_KEY)
    with pytest.raises(InspectProfileError, match="pinned"):
        inspect_harness_config(pool=pool, environment=environment, version="0.3.0")
    assert INSPECT_AI_VERSION == "0.3.277"


def test_present_usage_is_copied_and_a_failed_score_stays_failed(tmp_path: Path) -> None:
    _, request = _requests(tmp_path)

    def runner(plan: InspectEvalPlan) -> InspectEvalOutcome:
        return InspectEvalOutcome(
            success=False,
            score="I",
            answer="nope",
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            error="objective missed",
        )

    result = InspectReactAdapter(api_key=API_KEY, runner=runner).run(request)
    assert result.execution.status is ExecutionStatus.FAILED
    assert result.execution.oracle is None
    usage = next(record for record in result.trace.records if record.event_type == TraceEventType.INVOCATION_COMPLETED)
    assert usage.payload["usage_status"] == "present"
    assert usage.payload["prompt_tokens"] == 2
    assert usage.payload["completion_tokens"] == 1
    assert usage.payload["total_tokens"] == 3
    assert usage.payload["cost_status"] == "not_computed"
    assert usage.payload["cost_amount"] is None
    transcript = next(record for record in result.trace.records if record.event_type == "inspect.transcript")
    assert transcript.payload["supplementary"] is True
    assert API_KEY not in str(transcript.payload)
    report = TraceLog(request.state_dir / "trace.jsonl").verify(expected_head=result.trace.head_hash)
    assert report.ok


def test_fairness_rejects_oracle_fields(tmp_path: Path) -> None:
    openbot_request, inspect_request = _requests(tmp_path)

    def runner(plan: InspectEvalPlan) -> InspectEvalOutcome:
        return InspectEvalOutcome(success=True, score="C", answer=PILOT_TARGET)

    result = InspectReactAdapter(api_key=API_KEY, runner=runner).run(inspect_request)
    payload = result.execution.model_dump(mode="python")
    payload.pop("execution_hash")
    payload["oracle"] = {"ref": "dataset://oracle/echo"}
    forged_run = dataclasses.replace(result, execution=Execution.build(**payload))
    with pytest.raises(FairnessError, match="oracle"):
        fairness_record(forged_run, task=inspect_request.task)
    assert openbot_request.task.task_id == inspect_request.task.task_id


def test_installed_inspect_matches_the_pin() -> None:
    found = installed_inspect_version()
    if found is None:
        pytest.skip("inspect extra is not installed")
    assert found == INSPECT_AI_VERSION


@pytest.mark.skipif(os.environ.get("OHBE_INSPECT_LIVE") != "1", reason="set OHBE_INSPECT_LIVE=1 for a live react() eval")
def test_live_inspect_react_eval(tmp_path: Path) -> None:
    _, request = _requests(tmp_path)
    InspectReactAdapter(api_key=os.environ[LITELLM_API_KEY]).run(request)
