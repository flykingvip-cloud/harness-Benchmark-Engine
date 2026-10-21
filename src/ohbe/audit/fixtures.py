"""Deterministic pilot executions for the fairness audit.

These records are fixtures. They are not live model calls and they do not
contain provider spend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ohbe import __version__
from ohbe.adapters.inspect_react.plan import INSPECT_AI_VERSION, inspect_harness_config
from ohbe.adapters.openbot.profile import openbot_harness_config
from ohbe.adapters.pilot import PILOT_OPENBOT_COMMIT, pilot_suite
from ohbe.aggregate.report import PairedProtocol
from ohbe.core.execution import Execution, ExecutionEnvironment, ExecutionStatus
from ohbe.core.fingerprint import RuntimeFingerprint
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelDefinition, ModelPool
from ohbe.core.pricing import PricingRef
from ohbe.core.task import Task
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog
from ohbe.cost.table import PricingTable
from ohbe.eval.objective import select_objective

FIXTURE_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
HARNESS_IDS = ("openbot", "inspect-react")
ROLE_SUCCESS = "success"
ROLE_MISSING_USAGE = "missing_usage"
ROLE_EVAL_FAIL = "eval_fail"
ROLE_TIMEOUT = "timeout"

# Both harnesses see the same schedule for a task.
_ROLES = {
    "ohbe-pilot-echo": (ROLE_SUCCESS, ROLE_MISSING_USAGE),
    "ohbe-pilot-exact": (ROLE_SUCCESS, ROLE_EVAL_FAIL),
    "ohbe-pilot-match": (ROLE_SUCCESS, ROLE_TIMEOUT),
}
_PROMPT_TOKENS = 100
_COMPLETION_TOKENS = 20


class PilotFixtureError(ValueError):
    """The pilot fixture could not be built."""


def pilot_protocol() -> PairedProtocol:
    """Timeout shared by every pilot task. Token and cost budgets are unset."""

    return PairedProtocol(repetitions=2, timeout_seconds=30)


def pilot_environment() -> ExecutionEnvironment:
    """One egress record for every fixture execution."""

    return ExecutionEnvironment(
        egress_policy_version="ohbe-egress-0.1",
        gateway_host="ohbe-gateway",
        gateway_port=8080,
        image_ref="python:3.12-alpine",
        image_digest="sha256:" + "ab" * 32,
    )


def pilot_fingerprint() -> RuntimeFingerprint:
    return RuntimeFingerprint(
        ohbe_version=__version__,
        python_version="3.12.3",
        platform="ohbe-pilot-fixture",
        git_commit="c0ffee",
        git_dirty=False,
    )


def pilot_pricing_ref(table: PricingTable) -> PricingRef:
    """Pin the table by id, version, filename, and content hash.

    The URI is the pricing filename. A relative path and an absolute path to
    the same file therefore build the same model pool. Core does not fetch it.
    """

    return PricingRef(
        pricing_id=table.pricing_id,
        version=table.version,
        uri=f"pricing/{Path(table.source).name}",
        content_hash=table.content_hash,
    )


def pilot_pool(pricing_ref: PricingRef) -> ModelPool:
    """Two-model pool. The audit selects the first id for every fixture call."""

    primary = ModelDefinition(
        model_id="openai/gpt-test",
        provider="openai",
        name="gpt-test",
        revision="2026-10-01",
        pricing_ref=pricing_ref,
    )
    secondary = ModelDefinition(
        model_id="openai/gpt-test-mini",
        provider="openai",
        name="gpt-test-mini",
        revision="2026-10-01",
        pricing_ref=pricing_ref,
    )
    return ModelPool.build(pool_id="pool-pilot", models=(primary, secondary))


class PilotCase:
    """One fixture execution and the harness config that produced it."""

    def __init__(
        self,
        *,
        task: Task,
        role: str,
        config: HarnessConfig,
        execution: Execution,
        trace: ExecutionTrace,
    ) -> None:
        self.task = task
        self.role = role
        self.config = config
        self.execution = execution
        self.trace = trace

    @property
    def harness_id(self) -> str:
        return self.execution.harness.harness_id


class PilotCorpus:
    """The whole fixture suite: tasks, pool, protocol, and executions."""

    def __init__(
        self,
        *,
        tasks: tuple[Task, ...],
        pool: ModelPool,
        environment: ExecutionEnvironment,
        protocol: PairedProtocol,
        table: PricingTable,
        pricing_ref: PricingRef,
        cases: tuple[PilotCase, ...],
    ) -> None:
        self.tasks = tasks
        self.pool = pool
        self.environment = environment
        self.protocol = protocol
        self.table = table
        self.pricing_ref = pricing_ref
        self.cases = cases


def build_pilot_corpus(
    table: PricingTable,
    state_root: Path,
    *,
    input_overrides: dict[tuple[str, str, str], dict[str, object]] | None = None,
) -> PilotCorpus:
    """Write fixture traces under `state_root` and return the corpus.

    `input_overrides` maps `(task_id, harness_id, role)` to a replacement
    task input. The audit uses that to prove a divergent input fails a gate.
    The default corpus does not set it.
    """

    pricing_ref = pilot_pricing_ref(table)
    pool = pilot_pool(pricing_ref)
    environment = pilot_environment()
    protocol = pilot_protocol()
    tasks = pilot_suite()
    fingerprint = pilot_fingerprint()
    configs = {
        "openbot": openbot_harness_config(
            commit=PILOT_OPENBOT_COMMIT,
            pool=pool,
            environment=environment,
            timeout_seconds=protocol.timeout_seconds,
        ),
        "inspect-react": inspect_harness_config(
            pool=pool,
            environment=environment,
            timeout_seconds=protocol.timeout_seconds,
            version=INSPECT_AI_VERSION,
        ),
    }
    cases: list[PilotCase] = []
    overrides = input_overrides or {}
    for task_index, task in enumerate(tasks):
        roles = _ROLES.get(task.task_id)
        if roles is None:
            raise PilotFixtureError(f"{task.task_id} has no fixture schedule")
        for harness_index, harness_id in enumerate(HARNESS_IDS):
            for role_index, role in enumerate(roles):
                key = (task.task_id, harness_id, role)
                task_input = dict(overrides.get(key, task.input))
                cases.append(
                    _case(
                        state_root=state_root,
                        task=task,
                        role=role,
                        config=configs[harness_id],
                        pool=pool,
                        environment=environment,
                        fingerprint=fingerprint,
                        task_input=task_input,
                        seed=100 + task_index * 10 + harness_index * 4 + role_index,
                    )
                )
    return PilotCorpus(
        tasks=tasks,
        pool=pool,
        environment=environment,
        protocol=protocol,
        table=table,
        pricing_ref=pricing_ref,
        cases=tuple(cases),
    )


def _case(
    *,
    state_root: Path,
    task: Task,
    role: str,
    config: HarnessConfig,
    pool: ModelPool,
    environment: ExecutionEnvironment,
    fingerprint: RuntimeFingerprint,
    task_input: dict[str, object],
    seed: int,
) -> PilotCase:
    harness_id = config.harness_id
    execution_id = f"exec-{task.task_id}-{harness_id}-{role}"
    model_id = pool.models[0].model_id
    latency_ms, status, error, category, usage, answer = _role_shape(task, role, model_id)
    started = FIXTURE_AT
    finished = started + timedelta(milliseconds=latency_ms)
    execution = Execution.build(
        execution_id=execution_id,
        experiment_id="exp-pilot-audit",
        task_id=task.task_id,
        task_content_hash=task.content_hash,
        harness=config.to_ref(),
        model_pool_id=pool.pool_id,
        model_pool_hash=pool.content_hash,
        seed=seed,
        status=status,
        started_at=started,
        finished_at=finished,
        fingerprint=fingerprint,
        environment=environment,
    )
    events: list[tuple[str, dict[str, object]]] = [
        (
            TraceEventType.EXECUTION_STARTED,
            {
                "egress_policy_version": environment.egress_policy_version,
                "gateway_host": environment.gateway_host,
                "gateway_port": environment.gateway_port,
                "model_id": model_id,
                "model_pool_id": pool.pool_id,
                "task_id": task.task_id,
                "task_input": task_input,
            },
        )
    ]
    if usage is not None:
        events.append((TraceEventType.INVOCATION_COMPLETED, usage))
    if answer is not None:
        events.append(("evaluation.answer", {"answer": answer}))
    finished_payload: dict[str, object] = {"error": error, "status": status.value}
    if category is not None:
        finished_payload["failure_category"] = category
    events.append((TraceEventType.EXECUTION_FINISHED, finished_payload))
    trace = _write_trace(state_root / execution_id / "trace.jsonl", execution_id, events)
    return PilotCase(task=task, role=role, config=config, execution=execution, trace=trace)


def _role_shape(
    task: Task,
    role: str,
    model_id: str,
) -> tuple[int, ExecutionStatus, str | None, str | None, dict[str, object] | None, str | None]:
    _scorer, target = select_objective(task)
    present = {
        "completion_tokens": _COMPLETION_TOKENS,
        "cost_amount": "999",
        "model_id": model_id,
        "prompt_tokens": _PROMPT_TOKENS,
        "total_tokens": _PROMPT_TOKENS + _COMPLETION_TOKENS,
        "usage_status": "present",
    }
    if role == ROLE_SUCCESS:
        return 10, ExecutionStatus.SUCCEEDED, None, None, present, target
    if role == ROLE_MISSING_USAGE:
        missing = {
            "completion_tokens": None,
            "cost_amount": "0",
            "model_id": model_id,
            "prompt_tokens": None,
            "total_tokens": None,
            "usage_status": "missing",
        }
        return 15, ExecutionStatus.SUCCEEDED, None, None, missing, target
    if role == ROLE_EVAL_FAIL:
        return 20, ExecutionStatus.FAILED, "objective missed", None, present, "nope"
    if role == ROLE_TIMEOUT:
        return 60_000, ExecutionStatus.FAILED, "timed out", ROLE_TIMEOUT, present, target
    raise PilotFixtureError(f"unknown fixture role {role}")


def _write_trace(path: Path, execution_id: str, events: list[tuple[str, dict[str, object]]]) -> ExecutionTrace:
    path.parent.mkdir(parents=True, exist_ok=True)
    log = TraceLog(path)
    for index, (event_type, payload) in enumerate(events):
        log.append(
            execution_id=execution_id,
            event_type=event_type,
            payload=payload,
            recorded_at=FIXTURE_AT,
            record_id=f"{execution_id}-{index}",
        )
    return ExecutionTrace.build(
        trace_id=f"trace-{execution_id}",
        execution_id=execution_id,
        records=tuple(log.read()),
    )
