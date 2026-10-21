"""Release-gate checks for the fixture pilot.

Each gate returns pass or fail plus evidence pointers. A fail does not rewrite
the underlying execution. Gateway bypass is checked with the in-process policy.
The Docker probe remains a separate command and test.
"""

from __future__ import annotations

from dataclasses import dataclass

from ohbe import __version__
from ohbe.adapters.base import HarnessRun
from ohbe.adapters.fairness import FairnessError, assert_same_task_and_pool, fairness_record
from ohbe.aggregate.report import OBSERVED_NOTE, PairedReport, ScoredRun, aggregate_paired, report_document
from ohbe.audit.fixtures import (
    ROLE_EVAL_FAIL,
    ROLE_MISSING_USAGE,
    ROLE_SUCCESS,
    ROLE_TIMEOUT,
    PilotCase,
    PilotCorpus,
)
from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.trace import TraceEventType
from ohbe.environment.policy import EgressDenied, EgressSimulator, controlled_track_policy
from ohbe.eval.judge import judge_execution
from ohbe.eval.objective import score_task, select_objective

EGRESS_DOCKER_TEST = "tests/test_docker_egress.py::test_docker_blocks_provider_egress_and_allows_gateway"
EGRESS_POLICY_TEST = "tests/test_egress_policy.py"
LIMITATIONS = (
    "Tool-surface parity between OpenBot and Inspect is not claimed.",
    "Oracle scoring and routing-regret metrics are deferred stubs and are not computed.",
    "Pilot audit executions are fixtures. They are not live model spend.",
    "Gateway bypass in this audit is the in-process policy check. "
    f"The Docker probe is {EGRESS_DOCKER_TEST} and benchmark check-egress.",
)


@dataclass(frozen=True)
class AuditGate:
    """One release gate."""

    gate_id: str
    passed: bool
    summary: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class AuditedTask:
    """Metrics and scored rows for one pilot task."""

    cases: tuple[PilotCase, ...]
    report: PairedReport
    runs: tuple[ScoredRun, ...]


@dataclass(frozen=True)
class AuditReport:
    """Machine-readable pilot audit. `passed` is true only when every gate passes."""

    passed: bool
    gates: tuple[AuditGate, ...]
    limitations: tuple[str, ...]
    schema_version: str
    ohbe_version: str


def run_pilot_audit(corpus: PilotCorpus) -> tuple[AuditReport, tuple[dict[str, object], ...]]:
    """Score the fixture corpus and evaluate every release gate."""

    audited = tuple(_audit_task(corpus, task_id) for task_id in (task.task_id for task in corpus.tasks))
    gates = (
        _same_task_input(audited),
        _same_initial_environment(corpus, audited),
        _same_model_pool(corpus, audited),
        _same_protocol(corpus, audited),
        _missing_usage_not_zero(audited),
        _invalid_not_task_failure(audited),
        _versions_recorded(corpus, audited),
        _evaluator_reliability(audited),
        _gateway_bypass_denied(corpus),
        _no_routing_claim(audited),
    )
    report = AuditReport(
        passed=all(gate.passed for gate in gates),
        gates=gates,
        limitations=LIMITATIONS,
        schema_version=SCHEMA_VERSION,
        ohbe_version=__version__,
    )
    metrics = tuple(report_document(item.report) for item in audited)
    return report, metrics


def audit_document(report: AuditReport) -> dict[str, object]:
    """JSON object for the audit artifact."""

    return {
        "gates": [
            {
                "evidence": list(gate.evidence),
                "gate_id": gate.gate_id,
                "passed": gate.passed,
                "summary": gate.summary,
            }
            for gate in report.gates
        ],
        "kind": "ohbe.pilot_audit",
        "limitations": list(report.limitations),
        "ohbe_version": report.ohbe_version,
        "passed": report.passed,
        "schema_version": report.schema_version,
    }


def _audit_task(corpus: PilotCorpus, task_id: str) -> AuditedTask:
    task = next(item for item in corpus.tasks if item.task_id == task_id)
    cases = tuple(case for case in corpus.cases if case.task.task_id == task_id)
    report, runs = aggregate_paired(
        [(case.execution, case.trace) for case in cases],
        task=task,
        pool=corpus.pool,
        table=corpus.table,
        pricing_ref=corpus.pricing_ref,
        protocol=corpus.protocol,
    )
    if len(runs) != len(cases):
        raise RuntimeError("aggregate dropped a fixture execution")
    return AuditedTask(cases=cases, report=report, runs=runs)


def _same_task_input(audited: tuple[AuditedTask, ...]) -> AuditGate:
    evidence: list[str] = []
    problems: list[str] = []
    for item in audited:
        task = item.cases[0].task
        for case, scored in zip(item.cases, item.runs, strict=True):
            started = _started(scored)
            if started.get("task_input") != task.input or started.get("task_id") != task.task_id:
                problems.append(case.execution.execution_id)
        try:
            _fair_success_pair(item)
        except (FairnessError, KeyError) as exc:
            problems.append(f"{task.task_id}:{exc}")
        evidence.append(f"task:{task.task_id} content_hash={task.content_hash}")
    return _gate(
        "same_task_input",
        not problems,
        "Both harnesses record the task id and input from the shared task document.",
        evidence,
        problems,
    )


def _same_initial_environment(corpus: PilotCorpus, audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    for item in audited:
        for case, scored in zip(item.cases, item.runs, strict=True):
            environment = case.execution.environment
            started = _started(scored)
            if environment != corpus.environment:
                problems.append(f"{case.execution.execution_id}:environment")
            if started.get("gateway_host") != corpus.environment.gateway_host:
                problems.append(f"{case.execution.execution_id}:gateway_host")
            if started.get("gateway_port") != corpus.environment.gateway_port:
                problems.append(f"{case.execution.execution_id}:gateway_port")
            if started.get("egress_policy_version") != corpus.environment.egress_policy_version:
                problems.append(f"{case.execution.execution_id}:policy")
            parameters = case.config.parameters
            if parameters.get("gateway_host") != corpus.environment.gateway_host:
                problems.append(f"{case.harness_id}:config_gateway_host")
            if parameters.get("gateway_port") != corpus.environment.gateway_port:
                problems.append(f"{case.harness_id}:config_gateway_port")
    evidence = (
        f"policy:{corpus.environment.egress_policy_version}",
        f"gateway:{corpus.environment.gateway_host}:{corpus.environment.gateway_port}",
        f"image:{corpus.environment.image_digest}",
    )
    return _gate(
        "same_initial_environment",
        not problems,
        "Controlled-track executions share one egress environment and gateway.",
        evidence,
        problems,
    )


def _same_model_pool(corpus: PilotCorpus, audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    model_id = corpus.pool.models[0].model_id
    for item in audited:
        if item.report.model_pool_id != corpus.pool.pool_id or item.report.model_pool_hash != corpus.pool.content_hash:
            problems.append(f"{item.report.task_id}:metrics_pool")
        for case, scored in zip(item.cases, item.runs, strict=True):
            if case.execution.model_pool_id != corpus.pool.pool_id:
                problems.append(f"{case.execution.execution_id}:pool_id")
            if case.execution.model_pool_hash != corpus.pool.content_hash:
                problems.append(f"{case.execution.execution_id}:pool_hash")
            if _started(scored).get("model_id") != model_id:
                problems.append(f"{case.execution.execution_id}:model_id")
    return _gate(
        "same_model_pool",
        not problems,
        "Every fixture call uses the same model pool and selected model id.",
        (f"pool:{corpus.pool.pool_id}", f"model:{model_id}", f"pool_hash:{corpus.pool.content_hash}"),
        problems,
    )


def _same_protocol(corpus: PilotCorpus, audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    protocol = corpus.protocol
    for item in audited:
        report = item.report
        if report.timeout_seconds != protocol.timeout_seconds:
            problems.append(f"{report.task_id}:timeout")
        if report.max_total_tokens != protocol.max_total_tokens or report.max_cost != protocol.max_cost:
            problems.append(f"{report.task_id}:budget")
        if report.repetitions != protocol.repetitions:
            problems.append(f"{report.task_id}:repetitions")
        for case in item.cases:
            if case.config.parameters.get("timeout_seconds") != protocol.timeout_seconds:
                problems.append(f"{case.execution.execution_id}:config_timeout")
            if not case.execution.harness.matches(case.config):
                problems.append(f"{case.execution.execution_id}:harness_binding")
    evidence = (
        f"timeout_seconds:{protocol.timeout_seconds}",
        f"repetitions:{protocol.repetitions}",
        f"max_total_tokens:{protocol.max_total_tokens}",
        f"max_cost:{protocol.max_cost}",
    )
    return _gate(
        "same_protocol",
        not problems,
        "Timeout, budgets, and repetitions are the same on every pilot task.",
        evidence,
        problems,
    )


def _missing_usage_not_zero(audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    found = False
    for item in audited:
        for case, scored in zip(item.cases, item.runs, strict=True):
            if case.role != ROLE_MISSING_USAGE:
                continue
            found = True
            amount = scored.cost.total.amount
            evidence.append(f"execution:{case.execution.execution_id} cost_amount={amount}")
            if amount is not None or scored.cost.total.status.value == "computed":
                problems.append(f"{case.execution.execution_id}:cost_filled")
            if any(invocation.cost.amount == "0" for invocation in scored.cost.invocations):
                problems.append(f"{case.execution.execution_id}:invocation_zero")
        for metrics in item.report.harnesses:
            if any(case.role == ROLE_MISSING_USAGE and case.harness_id == metrics.harness_id for case in item.cases):
                evidence.append(
                    f"harness:{item.report.task_id}:{metrics.harness_id} total_model_cost={metrics.total_model_cost}"
                )
                if metrics.total_model_cost is not None or metrics.prompt_tokens is not None:
                    problems.append(f"{metrics.harness_id}:partial_sum")
                if metrics.cost_status == "computed" or metrics.usage_status == "present":
                    problems.append(f"{metrics.harness_id}:usage_marked_present")
    if not found:
        problems.append("fixture has no missing-usage execution")
    return _gate(
        "missing_usage_not_zero",
        not problems,
        "Missing token usage stays null. It is not added as zero and it does not create a partial cost.",
        tuple(evidence),
        problems,
    )


def _invalid_not_task_failure(audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    found = False
    for item in audited:
        timeouts = [case for case in item.cases if case.role == ROLE_TIMEOUT]
        if not timeouts:
            continue
        found = True
        for case, scored in zip(item.cases, item.runs, strict=True):
            if case.role != ROLE_TIMEOUT:
                continue
            judgement = scored.judgement
            evidence.append(
                f"execution:{case.execution.execution_id} "
                f"validity={judgement.validity} outcome={judgement.outcome} "
                f"category={judgement.failure_category}"
            )
            if judgement.validity != "invalid" or judgement.outcome != "not_scored":
                problems.append(f"{case.execution.execution_id}:counted")
            if judgement.failure_category != ROLE_TIMEOUT:
                problems.append(f"{case.execution.execution_id}:category")
        for metrics in item.report.harnesses:
            evidence.append(
                f"harness:{item.report.task_id}:{metrics.harness_id} "
                f"task_failure_count={metrics.task_failure_count} "
                f"invalid_count={metrics.invalid_count} "
                f"valid_count={metrics.valid_count}"
            )
            if metrics.task_failure_count != 0 or metrics.invalid_count < 1:
                problems.append(f"{metrics.harness_id}:task_failure_count")
    if not found:
        problems.append("fixture has no timeout execution")
    return _gate(
        "invalid_not_task_failure",
        not problems,
        "Timeout runs stay invalid. They are not task failures and they stay out of the success denominator.",
        tuple(evidence),
        problems,
    )


def _versions_recorded(corpus: PilotCorpus, audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence = [
        f"schema:{SCHEMA_VERSION}",
        f"ohbe:{__version__}",
        f"pricing:{corpus.table.pricing_id}@{corpus.table.version}#{corpus.table.content_hash}",
        f"task_versions:{','.join(task.version for task in corpus.tasks)}",
    ]
    for item in audited:
        report = item.report
        if (
            report.pricing_id != corpus.table.pricing_id
            or report.pricing_version != corpus.table.version
            or report.pricing_content_hash != corpus.table.content_hash
        ):
            problems.append(f"{report.task_id}:pricing")
        if report.schema_version != SCHEMA_VERSION:
            problems.append(f"{report.task_id}:schema")
        for case in item.cases:
            version = case.execution.harness.version
            evidence.append(f"harness:{case.harness_id}@{version}")
            if not version:
                problems.append(f"{case.execution.execution_id}:harness_version")
            if case.execution.schema_version != SCHEMA_VERSION:
                problems.append(f"{case.execution.execution_id}:schema")
            if case.execution.fingerprint.ohbe_version != __version__:
                problems.append(f"{case.execution.execution_id}:ohbe_version")
            if case.execution.task_content_hash != case.task.content_hash:
                problems.append(f"{case.execution.execution_id}:task_hash")
            model = corpus.pool.models[0]
            if model.pricing_ref is None or model.pricing_ref.content_hash != corpus.table.content_hash:
                problems.append(f"{model.model_id}:pricing_ref")
            if not model.revision:
                problems.append(f"{model.model_id}:revision")
    # Harness lines repeat once per case. Keep one line per harness id and version.
    evidence = list(dict.fromkeys(evidence))
    return _gate(
        "versions_recorded",
        not problems,
        "Harness, model, task, schema, and pricing versions are on the fixture records.",
        tuple(evidence),
        problems,
    )


def _evaluator_reliability(audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    evidence: list[str] = []
    for item in audited:
        task = item.cases[0].task
        scorer, target = select_objective(task)
        evidence.append(f"task:{task.task_id} scorer={scorer} target={target}")
        if not score_task(task, target).passed or score_task(task, "nope").passed:
            problems.append(f"{task.task_id}:scorer")
        for case, scored in zip(item.cases, item.runs, strict=True):
            again = judge_execution(execution=case.execution, trace=case.trace, task=task)
            judgement = scored.judgement
            if (
                again.validity != judgement.validity
                or again.outcome != judgement.outcome
                or again.failure_category != judgement.failure_category
                or again.passed != judgement.passed
                or again.scorer != judgement.scorer
            ):
                problems.append(f"{case.execution.execution_id}:unstable")
            if case.role == ROLE_SUCCESS and (judgement.outcome != "passed" or judgement.scorer != scorer):
                problems.append(f"{case.execution.execution_id}:success")
            if case.role == ROLE_EVAL_FAIL and (
                judgement.validity != "valid" or judgement.failure_category != "eval_fail"
            ):
                problems.append(f"{case.execution.execution_id}:eval_fail")
            if case.role == ROLE_MISSING_USAGE and judgement.outcome != "passed":
                problems.append(f"{case.execution.execution_id}:missing_usage_outcome")
    return _gate(
        "evaluator_reliability",
        not problems,
        "Objective checks are stable and match the fixture roles for contains, exact, and match.",
        tuple(evidence),
        problems,
    )


def _gateway_bypass_denied(corpus: PilotCorpus) -> AuditGate:
    policy = controlled_track_policy(
        gateway_host=corpus.environment.gateway_host,
        gateway_port=corpus.environment.gateway_port,
    )
    calls: list[str] = []

    class _Transport:
        def exchange(self, target: object, body: bytes) -> bytes:
            calls.append(getattr(target, "url", "unknown"))
            return b"gateway"

    simulator = EgressSimulator(policy, _Transport())
    problems: list[str] = []
    denied: list[str] = []
    for url in ("https://api.openai.com/v1/chat/completions", "https://api.anthropic.com/v1/messages", "http://1.1.1.1/"):
        try:
            simulator.request(url, b"{}")
        except EgressDenied as exc:
            denied.append(f"denied:{exc.verdict.host}:{exc.verdict.port}")
        else:
            problems.append(f"allowed:{url}")
    gateway_url = f"http://{corpus.environment.gateway_host}:{corpus.environment.gateway_port}/v1/chat/completions"
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
        "The Docker probe is the release gate named in the checklist.",
        evidence,
        problems,
    )


def _no_routing_claim(audited: tuple[AuditedTask, ...]) -> AuditGate:
    problems: list[str] = []
    for item in audited:
        if item.report.note != OBSERVED_NOTE:
            problems.append(f"{item.report.task_id}:note")
        for case in item.cases:
            if case.execution.oracle is not None or case.execution.routing_regret is not None:
                problems.append(f"{case.execution.execution_id}:stub_set")
    return _gate(
        "no_routing_claim",
        not problems,
        "The audit records observed results only. Oracle and routing regret stay unset.",
        (OBSERVED_NOTE,),
        problems,
    )


def _fair_success_pair(item: AuditedTask) -> None:
    successes = [
        (case, scored)
        for case, scored in zip(item.cases, item.runs, strict=True)
        if case.role == ROLE_SUCCESS
    ]
    by_harness: dict[str, HarnessRun] = {}
    for case, scored in successes:
        by_harness[case.harness_id] = HarnessRun(execution=scored.execution, trace=scored.trace)
    left = by_harness["openbot"]
    right = by_harness["inspect-react"]
    assert_same_task_and_pool(
        fairness_record(left, task=item.cases[0].task),
        fairness_record(right, task=item.cases[0].task),
    )


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
