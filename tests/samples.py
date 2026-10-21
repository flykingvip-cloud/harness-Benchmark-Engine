"""Deterministic documents shared by the schema tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ohbe import __version__
from ohbe.core import (
    GENESIS_HASH,
    CostAssessment,
    Execution,
    ExecutionEnvironment,
    ExecutionStatus,
    ExecutionTrace,
    Experiment,
    HarnessConfig,
    InvocationStatus,
    ModelDefinition,
    ModelInvocation,
    ModelPool,
    OracleStub,
    PricingRef,
    RoutingRegretStub,
    RuntimeFingerprint,
    Task,
    TokenUsage,
    TraceEventType,
    TraceRecord,
    hash_task_set,
)

NOW = datetime(2026, 10, 9, 11, 2, 3, 456789, tzinfo=UTC)
PRICING_TABLE_DIGEST = hashlib.sha256(b"pricing-table-v1").hexdigest()


def sample_pricing_ref(**overrides: Any) -> PricingRef:
    data: dict[str, Any] = {
        "pricing_id": "openrouter-public",
        "version": "2026-10-01",
        "uri": "memory://pricing/openrouter-public/2026-10-01",
        "content_hash": None,
    }
    data.update(overrides)
    return PricingRef(**data)


def sample_task(**overrides: Any) -> Task:
    data: dict[str, Any] = {
        "task_id": "task-001",
        "name": "echo",
        "version": "1",
        "input": {"prompt": "Say hi 模型"},
        "expected": {"contains": "hi"},
        "tags": ("smoke",),
        "metadata": {"source": "ohbe-v0.1"},
    }
    data.update(overrides)
    return Task.build(**data)


def sample_harness(**overrides: Any) -> HarnessConfig:
    data: dict[str, Any] = {
        "harness_id": "harness-echo",
        "name": "echo-harness",
        "version": "0.1.0",
        "parameters": {"max_steps": 2, "tools": ["read"]},
    }
    data.update(overrides)
    return HarnessConfig.build(**data)


def sample_model(**overrides: Any) -> ModelDefinition:
    data: dict[str, Any] = {
        "model_id": "openai/gpt-test",
        "provider": "openai",
        "name": "gpt-test",
        "revision": "2026-10-01",
        "pricing_ref": sample_pricing_ref(),
        "metadata": {"family": "test"},
    }
    data.update(overrides)
    return ModelDefinition(**data)


def sample_pool(**overrides: Any) -> ModelPool:
    data: dict[str, Any] = {
        "pool_id": "pool-a",
        "models": (
            sample_model(),
            sample_model(model_id="openai/gpt-test-mini", name="gpt-test-mini"),
        ),
    }
    data.update(overrides)
    return ModelPool.build(**data)


def sample_fingerprint() -> RuntimeFingerprint:
    return RuntimeFingerprint(
        ohbe_version=__version__,
        python_version="3.12.3",
        platform="test-platform",
        git_commit="c0ffee",
        git_dirty=False,
    )


def sample_invocation(**overrides: Any) -> ModelInvocation:
    data: dict[str, Any] = {
        "invocation_id": "inv-1",
        "execution_id": "exec-1",
        "seq": 0,
        "model": sample_model(),
        "status": InvocationStatus.COMPLETED,
        "request": {"messages": [{"role": "user", "content": "hi 模型"}]},
        "response": {"text": "hi"},
        "started_at": NOW,
        "finished_at": NOW,
        "latency_ms": 12,
    }
    data.update(overrides)
    return ModelInvocation.build(**data)


def sample_execution(**overrides: Any) -> Execution:
    task = sample_task()
    harness = sample_harness()
    pool = sample_pool()
    data: dict[str, Any] = {
        "execution_id": "exec-1",
        "experiment_id": "exp-1",
        "task_id": task.task_id,
        "task_content_hash": task.content_hash,
        "harness": harness.to_ref(),
        "model_pool_id": pool.pool_id,
        "model_pool_hash": pool.content_hash,
        "seed": 7,
        "status": ExecutionStatus.SUCCEEDED,
        "started_at": NOW,
        "finished_at": NOW,
        "fingerprint": sample_fingerprint(),
    }
    data.update(overrides)
    return Execution.build(**data)


def sample_experiment(**overrides: Any) -> Experiment:
    data: dict[str, Any] = {
        "experiment_id": "exp-1",
        "name": "step-c",
        "created_at": NOW,
        "seed": 7,
        "task_set_hash": hash_task_set([sample_task()]),
        "harness": sample_harness().to_ref(),
        "model_pool": sample_pool(),
        "pricing_ref": sample_pricing_ref(),
        "fingerprint": sample_fingerprint(),
        "metadata": {"step": "C"},
    }
    data.update(overrides)
    return Experiment.build(**data)


def sample_records() -> tuple[TraceRecord, TraceRecord]:
    first = TraceRecord.build(
        record_id="rec-0",
        seq=0,
        execution_id="exec-1",
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"seed": 7, "note": "模型"},
        recorded_at=NOW,
        prev_hash=GENESIS_HASH,
    )
    second = TraceRecord.build(
        record_id="rec-1",
        seq=1,
        execution_id="exec-1",
        event_type=TraceEventType.EXECUTION_FINISHED,
        payload={"status": "succeeded"},
        recorded_at=NOW,
        prev_hash=first.record_hash,
    )
    return first, second


def sample_trace() -> ExecutionTrace:
    return ExecutionTrace.build(
        trace_id="trace-1",
        execution_id="exec-1",
        records=sample_records(),
    )


def sample_environment() -> ExecutionEnvironment:
    return ExecutionEnvironment(
        egress_policy_version="ohbe-egress-0.1",
        gateway_host="ohbe-gateway",
        gateway_port=8080,
        image_ref="python:3.12-alpine",
        image_digest="sha256:" + "ab" * 32,
    )


def roundtrip_cases() -> list[tuple[str, Any]]:
    from tests.round_samples import round_documents

    priced = sample_pricing_ref(content_hash=PRICING_TABLE_DIGEST)
    return [
        ("pricing-ref", sample_pricing_ref()),
        ("pricing-ref-external-hash", priced),
        ("usage-missing", TokenUsage.missing(reason="provider omitted usage")),
        ("usage-invalid", TokenUsage.invalid(reason="usage object was blank")),
        ("usage-present", TokenUsage.present(prompt_tokens=3, completion_tokens=1, total_tokens=4)),
        ("usage-present-zero", TokenUsage.present(prompt_tokens=0, completion_tokens=0, total_tokens=0)),
        ("cost-not-computed", CostAssessment.not_computed()),
        ("cost-invalid", CostAssessment.invalid(reason="usage missing")),
        (
            "cost-computed",
            CostAssessment.computed(amount="0.0001", currency="USD", pricing_ref=priced),
        ),
        ("task", sample_task()),
        ("harness-config", sample_harness()),
        ("harness-ref", sample_harness().to_ref()),
        ("model", sample_model()),
        ("model-pool", sample_pool()),
        ("invocation-usage-omitted", sample_invocation()),
        (
            "invocation-usage-present",
            sample_invocation(
                usage=TokenUsage.present(prompt_tokens=3, completion_tokens=1, total_tokens=4),
                pricing_ref=sample_pricing_ref(),
            ),
        ),
        (
            "invocation-usage-invalid",
            sample_invocation(
                status=InvocationStatus.FAILED,
                response=None,
                error="blank usage",
                usage=TokenUsage.invalid(reason="provider sent a blank usage object"),
                cost=CostAssessment.invalid(reason="usage invalid"),
            ),
        ),
        ("execution", sample_execution()),
        ("execution-environment", sample_environment()),
        ("execution-with-environment", sample_execution(environment=sample_environment())),
        ("experiment", sample_experiment()),
        ("experiment-with-environment", sample_experiment(environment=sample_environment())),
        (
            "experiment-with-stubs",
            sample_experiment(
                oracle=OracleStub(ref="dataset://oracle/echo", notes="deferred"),
                routing_regret=RoutingRegretStub(ref="metric://routing-regret", notes="deferred"),
            ),
        ),
        ("fingerprint", sample_fingerprint()),
        ("oracle-stub", OracleStub(ref="dataset://oracle/echo")),
        ("routing-regret-stub", RoutingRegretStub()),
        ("trace-record", sample_records()[0]),
        ("execution-trace", sample_trace()),
        ("execution-trace-empty", ExecutionTrace.build(trace_id="trace-empty", execution_id="exec-1")),
        *round_documents(),
    ]
