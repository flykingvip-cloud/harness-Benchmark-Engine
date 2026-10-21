"""Release gates for a completed live matrix.

The gate ids match the fixture audit so `benchmark report` can render them.
A live matrix does not plant missing-usage or timeout fixtures. Missing usage
still cannot become zero, and an invalid run still cannot become a task failure.
Oracle and routing regret stay unset.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ohbe import __version__
from ohbe.adapters.base import HarnessRun
from ohbe.adapters.fairness import FairnessError, assert_same_task_and_pool, fairness_record
from ohbe.aggregate.report import OBSERVED_NOTE, PairedReport, ScoredRun
from ohbe.audit.gates import EGRESS_DOCKER_TEST, EGRESS_POLICY_TEST, AuditGate, AuditReport
from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.model import ModelPool
from ohbe.core.task import Task
from ohbe.core.trace import TraceEventType
from ohbe.cost.table import PricingTable
from ohbe.environment.policy import EgressDenied, EgressSimulator, controlled_track_policy
from ohbe.eval.judge import judge_execution
from ohbe.eval.objective import score_task, select_objective
from ohbe.live.spec import LivePilotSpec

LIVE_LIMITATIONS = (
    "Tool-surface parity between OpenBot and Inspect is not claimed.",
    "Oracle scoring and routing-regret metrics are deferred stubs and are not computed.",
    "Model cost is the offline table. A token meter prices reported token counts. "
    "normalized_gpu_seconds prices reported GPU seconds times gpu_cost_per_hour/3600. "
    "provider_cost is the provider token invoice (computed 0 for that local meter). "
    "normalized_cost is the comparable figure. A provider response-cost is not copied into either field.",
    "A null cost or token total is missing or unpriceable usage. It is not zero.",
    "Gateway bypass in this audit is the in-process policy check. "
    f"The Docker probe is {EGRESS_DOCKER_TEST} and benchmark check-egress.",
)


@dataclass(frozen=True)
class LiveAuditedTask:
    """One task after aggregation."""

    task: Task
    report: PairedReport
    runs: tuple[ScoredRun, ...]


def audit_live_matrix(
    spec: LivePilotSpec,
    *,
    tasks: tuple[LiveAuditedTask, ...],
    pool: ModelPool,
    table: PricingTable,
    environment: ExecutionEnvironment,
) -> AuditReport:
    """Evaluate the release gates on observed live executions."""

    gates = (
        _same_task_input(tasks),
        _same_initial_environment(tasks, environment),
        _same_model_pool(tasks, pool),
        _same_protocol(tasks, spec),
        _missing_usage_not_zero(tasks),
        _invalid_not_task_failure(tasks),
        _versions_recorded(tasks, pool, table),
        _evaluator_reliability(tasks),
        _gateway_bypass_denied(environment),
        _no_routing_claim(tasks),
    )
    return AuditReport(
        passed=all(gate.passed for gate in gates),
        gates=gates,
        limitations=LIVE_LIMITATIONS,
        schema_version=SCHEMA_VERSION,
        ohbe_version=__version__,
    )


def _same_task_input(tasks: tuple[LiveAuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    for item in tasks:
        evidence.append(f"task:{item.task.task_id} content_hash={item.task.content_hash}")
        for scored in item.runs:
            started = _started(scored)
            if started.get("task_id") != item.task.task_id or started.get("task_input") != item.task.input:
                problems.append(f"{scored.execution.execution_id}:task_input")
        by_harness: OrderedDict[str, HarnessRun] = OrderedDict()
        for scored in item.runs:
            by_harness.setdefault(
                scored.execution.harness.harness_id,
                HarnessRun(execution=scored.execution, trace=scored.trace),
            )
        try:
            left = by_harness["openbot"]
            right = by_harness["inspect-react"]
            assert_same_task_and_pool(
                fairness_record(left, task=item.task),
                fairness_record(right, task=item.task),
            )
        except (FairnessError, KeyError) as exc:
            problems.append(f"{item.task.task_id}:{exc}")
    return _gate(
        "same_task_input",
        not problems,
        "Both harnesses record the task id and input from the shared task document.",
        evidence,
        problems,
    )


def _same_initial_environment(
    tasks: tuple[LiveAuditedTask, ...],
    environment: ExecutionEnvironment,
) -> AuditGate:
    problems: list[str] = []
    for item in tasks:
        for scored in item.runs:
            if scored.execution.environment != environment:
                problems.append(f"{scored.execution.execution_id}:environment")
            started = _started(scored)
            if started.get("gateway_host") != environment.gateway_host:
                problems.append(f"{scored.execution.execution_id}:gateway_host")
            if started.get("gateway_port") != environment.gateway_port:
                problems.append(f"{scored.execution.execution_id}:gateway_port")
            if (
                "egress_policy_version" in started
                and started.get("egress_policy_version") != environment.egress_policy_version
            ):
                problems.append(f"{scored.execution.execution_id}:policy")
    evidence = (
        f"policy:{environment.egress_policy_version}",
        f"gateway:{environment.gateway_host}:{environment.gateway_port}",
        f"image:{environment.image_digest or 'unrecorded'}",
    )
    return _gate(
        "same_initial_environment",
        not problems,
        "Live executions share one egress environment and gateway.",
        evidence,
        problems,
    )


def _same_model_pool(tasks: tuple[LiveAuditedTask, ...], pool: ModelPool) -> AuditGate:
    problems: list[str] = []
    model_id = pool.models[0].model_id
    for item in tasks:
        if item.report.model_pool_id != pool.pool_id or item.report.model_pool_hash != pool.content_hash:
            problems.append(f"{item.report.task_id}:metrics_pool")
        for scored in item.runs:
            execution = scored.execution
            if execution.model_pool_id != pool.pool_id or execution.model_pool_hash != pool.content_hash:
                problems.append(f"{execution.execution_id}:pool")
            if _started(scored).get("model_id") != model_id:
                problems.append(f"{execution.execution_id}:model_id")
    return _gate(
        "same_model_pool",
        not problems,
        "Every live call uses the same model pool and selected model id.",
        (f"pool:{pool.pool_id}", f"model:{model_id}", f"pool_hash:{pool.content_hash}"),
        problems,
    )


def _same_protocol(tasks: tuple[LiveAuditedTask, ...], spec: LivePilotSpec) -> AuditGate:
    problems: list[str] = []
    for item in tasks:
        report = item.report
        if report.repetitions != spec.repetitions:
            problems.append(f"{report.task_id}:repetitions")
        if report.timeout_seconds != spec.timeout_seconds:
            problems.append(f"{report.task_id}:timeout")
        if report.max_total_tokens != spec.max_total_tokens or report.max_cost != spec.max_cost:
            problems.append(f"{report.task_id}:budget")
        counts: dict[str, int] = {}
        for scored in item.runs:
            harness_id = scored.execution.harness.harness_id
            counts[harness_id] = counts.get(harness_id, 0) + 1
        for harness_id, count in counts.items():
            if count != spec.repetitions:
                problems.append(f"{item.task.task_id}:{harness_id}:observed")
        for metrics in report.harnesses:
            if metrics.observed_count != spec.repetitions:
                problems.append(f"{item.task.task_id}:{metrics.harness_id}:metrics")
    evidence = (
        f"timeout_seconds:{spec.timeout_seconds}",
        f"repetitions:{spec.repetitions}",
        f"max_total_tokens:{spec.max_total_tokens}",
        f"max_cost:{spec.max_cost}",
    )
    return _gate(
        "same_protocol",
        not problems,
        "Timeout, budgets, and repetitions are the ceilings on every live task.",
        evidence,
        problems,
    )


def _missing_usage_not_zero(tasks: tuple[LiveAuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    saw_missing = False
    for item in tasks:
        for scored in item.runs:
            missing = any(invocation.usage.status.value != "present" for invocation in scored.cost.invocations)
            for invocation in scored.cost.invocations:
                if invocation.usage.status.value == "present":
                    continue
                saw_missing = True
                amount = invocation.cost.amount
                evidence.append(
                    f"invocation:{scored.execution.execution_id}:{invocation.record_id} cost_amount={amount}"
                )
                if amount is not None or invocation.cost.status.value == "computed":
                    problems.append(f"{scored.execution.execution_id}:invocation_filled")
            if missing and scored.cost.total.amount is not None:
                problems.append(f"{scored.execution.execution_id}:total_filled")
            if not scored.cost.invocations and scored.cost.total.amount is not None:
                problems.append(f"{scored.execution.execution_id}:empty_usage_filled")
        for metrics in item.report.harnesses:
            rows = [
                scored for scored in item.runs if scored.execution.harness.harness_id == metrics.harness_id
            ]
            every_row_unpriced = bool(rows) and all(_unpriced(scored) for scored in rows)
            if every_row_unpriced and (metrics.total_model_cost is not None or metrics.prompt_tokens is not None):
                problems.append(f"{item.task.task_id}:{metrics.harness_id}:partial_sum")
    if not saw_missing:
        evidence.append("no missing usage observed; absent counts were not written as zero")
    return _gate(
        "missing_usage_not_zero",
        not problems,
        "Missing token usage stays null. It is not added as zero and it does not create a partial cost.",
        evidence,
        problems,
    )


def _invalid_not_task_failure(tasks: tuple[LiveAuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    saw_invalid = False
    for item in tasks:
        invalid = [scored for scored in item.runs if scored.judgement.validity != "valid"]
        if invalid:
            saw_invalid = True
        for scored in invalid:
            judgement = scored.judgement
            evidence.append(
                f"execution:{scored.execution.execution_id} "
                f"validity={judgement.validity} outcome={judgement.outcome} "
                f"category={judgement.failure_category}"
            )
            if judgement.outcome != "not_scored" or judgement.failure_category == "eval_fail":
                problems.append(f"{scored.execution.execution_id}:counted")
        for metrics in item.report.harnesses:
            harness_invalid = sum(
                1
                for scored in item.runs
                if scored.execution.harness.harness_id == metrics.harness_id and scored.judgement.validity != "valid"
            )
            harness_failures = sum(
                1
                for scored in item.runs
                if scored.execution.harness.harness_id == metrics.harness_id and scored.judgement.outcome == "failed"
            )
            if metrics.invalid_count != harness_invalid or metrics.task_failure_count != harness_failures:
                problems.append(f"{item.task.task_id}:{metrics.harness_id}:counts")
    if not saw_invalid:
        evidence.append("no invalid runs observed")
    return _gate(
        "invalid_not_task_failure",
        not problems,
        "Invalid runs stay out of the task-failure count.",
        evidence,
        problems,
    )


def _versions_recorded(
    tasks: tuple[LiveAuditedTask, ...],
    pool: ModelPool,
    table: PricingTable,
) -> AuditGate:
    problems: list[str] = []
    evidence = [
        f"schema:{SCHEMA_VERSION}",
        f"ohbe:{__version__}",
        f"pricing:{table.pricing_id}@{table.version}#{table.content_hash}",
    ]
    for model in pool.models:
        evidence.append(f"model:{model.model_id}@{model.revision}")
        if not model.revision:
            problems.append(f"{model.model_id}:revision")
        if model.pricing_ref is None or model.pricing_ref.content_hash != table.content_hash:
            problems.append(f"{model.model_id}:pricing_ref")
    for item in tasks:
        report = item.report
        if (
            report.pricing_id != table.pricing_id
            or report.pricing_version != table.version
            or report.pricing_content_hash != table.content_hash
            or report.schema_version != SCHEMA_VERSION
        ):
            problems.append(f"{report.task_id}:versions")
        for scored in item.runs:
            execution = scored.execution
            evidence.append(f"harness:{execution.harness.harness_id}@{execution.harness.version}")
            if not execution.harness.version:
                problems.append(f"{execution.execution_id}:harness_version")
            if execution.schema_version != SCHEMA_VERSION or execution.fingerprint.ohbe_version != __version__:
                problems.append(f"{execution.execution_id}:runtime")
            if execution.task_content_hash != item.task.content_hash:
                problems.append(f"{execution.execution_id}:task_hash")
    evidence = list(dict.fromkeys(evidence))
    return _gate(
        "versions_recorded",
        not problems,
        "Harness, model, task, schema, and pricing versions are on the live records.",
        evidence,
        problems,
    )


def _evaluator_reliability(tasks: tuple[LiveAuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    for item in tasks:
        scorer, target = select_objective(item.task)
        evidence.append(f"task:{item.task.task_id} scorer={scorer} target={target}")
        if not score_task(item.task, target).passed or score_task(item.task, "nope").passed:
            problems.append(f"{item.task.task_id}:scorer")
        for scored in item.runs:
            fresh = judge_execution(execution=scored.execution, trace=scored.trace, task=item.task)
            judgement = scored.judgement
            if judgement.validity == "valid":
                if (
                    fresh.scorer != judgement.scorer
                    or fresh.passed != judgement.passed
                    or fresh.outcome != judgement.outcome
                    or fresh.answer != judgement.answer
                ):
                    problems.append(f"{scored.execution.execution_id}:unstable")
            elif judgement.outcome != "not_scored" or judgement.failure_category == "eval_fail":
                problems.append(f"{scored.execution.execution_id}:invalid_counted")
    return _gate(
        "evaluator_reliability",
        not problems,
        "Objective checks are stable for contains, exact, and match.",
        evidence,
        problems,
    )


def _gateway_bypass_denied(environment: ExecutionEnvironment) -> AuditGate:
    policy = controlled_track_policy(
        gateway_host=environment.gateway_host,
        gateway_port=environment.gateway_port,
    )
    calls: list[str] = []

    class _Transport:
        def exchange(self, target: object, body: bytes) -> bytes:
            calls.append(getattr(target, "url", "unknown"))
            return b"gateway"

    simulator = EgressSimulator(policy, _Transport())
    problems: list[str] = []
    denied: list[str] = []
    for url in (
        "https://api.openai.com/v1/chat/completions",
        "https://api.anthropic.com/v1/messages",
        "https://huggingface.co/api/models",
        "https://api-inference.huggingface.co/models",
        "http://1.1.1.1/",
    ):
        try:
            simulator.request(url, b"{}")
        except EgressDenied as exc:
            denied.append(f"denied:{exc.verdict.host}:{exc.verdict.port}")
        else:
            problems.append(f"allowed:{url}")
    gateway_url = f"http://{environment.gateway_host}:{environment.gateway_port}/v1/chat/completions"
    try:
        body = simulator.request(gateway_url, b"{}")
    except EgressDenied:
        problems.append("gateway_denied")
        body = b""
    if body != b"gateway" or calls != [gateway_url]:
        problems.append("provider_reached_transport")
    evidence = (
        f"policy:{policy.version}",
        *denied,
        f"checklist:{EGRESS_POLICY_TEST}",
        f"checklist:{EGRESS_DOCKER_TEST}",
        "command:benchmark check-egress",
    )
    return _gate(
        "gateway_bypass_denied",
        not problems,
        "Provider hosts and literal addresses fail the egress policy before the transport. "
        "The Docker probe is the network release gate named in the checklist.",
        evidence,
        problems,
    )


def _no_routing_claim(tasks: tuple[LiveAuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    for item in tasks:
        if item.report.note != OBSERVED_NOTE:
            problems.append(f"{item.report.task_id}:note")
        for scored in item.runs:
            execution = scored.execution
            if execution.oracle is not None or execution.routing_regret is not None:
                problems.append(f"{execution.execution_id}:stub_set")
    return _gate(
        "no_routing_claim",
        not problems,
        "The audit records observed results only. Oracle and routing regret stay unset.",
        (OBSERVED_NOTE,),
        problems,
    )


def _unpriced(scored: ScoredRun) -> bool:
    if not scored.cost.invocations:
        return True
    return any(invocation.usage.status.value != "present" for invocation in scored.cost.invocations)


def _started(scored: ScoredRun) -> dict[str, object]:
    for record in scored.trace.records:
        if record.event_type == TraceEventType.EXECUTION_STARTED:
            return record.payload
    return {}


def _gate(
    gate_id: str,
    passed: bool,
    summary: str,
    evidence: tuple[str, ...] | list[str],
    problems: list[str],
) -> AuditGate:
    rows = list(evidence)
    if problems:
        rows.extend(f"problem:{item}" for item in problems)
    return AuditGate(gate_id=gate_id, passed=passed, summary=summary, evidence=tuple(rows))
