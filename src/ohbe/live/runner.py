"""Run one live-pilot manifest through the existing OHBE pipeline.

Adapters produce traces. Evaluation, cost, and aggregation read those traces.
This module does not fill token counts or prices when a trace omitted them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ohbe import __version__
from ohbe.adapters.base import AdapterError, HarnessAdapter, HarnessRun, HarnessRunRequest
from ohbe.adapters.inspect_react.plan import INSPECT_AI_VERSION, inspect_harness_config
from ohbe.adapters.openbot.profile import (
    BASE_URL_ENV,
    OpenBotProfileError,
    benchmark_profile,
    litellm_base_url,
    openbot_harness_config,
)
from ohbe.adapters.openbot.runner import isolated_state_dir
from ohbe.aggregate.report import (
    OBSERVED_NOTE,
    AggregationError,
    PairedProtocol,
    PairedReport,
    ScoredRun,
    aggregate_paired,
    dump_json,
    dump_jsonl,
    execution_documents,
    report_document,
)
from ohbe.audit.gates import audit_document
from ohbe.audit.report_input import REPORT_INPUT_KIND
from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.execution import Execution, ExecutionEnvironment
from ohbe.core.fingerprint import current_runtime_fingerprint
from ohbe.core.integrity import canonical_json
from ohbe.core.model import ModelDefinition, ModelPool
from ohbe.core.pricing import PricingRef
from ohbe.core.task import Task
from ohbe.core.trace import ExecutionTrace
from ohbe.cost.table import PricingError, PricingTable, load_pricing_table
from ohbe.live.audit import LIVE_LIMITATIONS, LiveAuditedTask, audit_live_matrix
from ohbe.live.context import LiveContext
from ohbe.live.spec import HARNESS_ORDER, LivePilotError, LivePilotSpec

Clock = Callable[[], datetime]
LIVE_REPORT_NOTE = (
    "Observed live-pilot executions. Oracle scoring and routing regret are not computed."
)


@dataclass(frozen=True)
class LiveRunResult:
    """Where a finished or refused matrix was written."""

    experiment_id: str
    out_dir: Path
    published: bool
    passed: bool
    message: str


def run_live_pilot(
    spec: LivePilotSpec,
    *,
    context: LiveContext,
    out_dir: str | Path,
    adapters: Mapping[str, HarnessAdapter] | None = None,
    clock: Clock | None = None,
) -> LiveRunResult:
    """Wire adapters, evaluation, cost, and aggregation for one experiment.

    `adapters` exists so tests can supply harnesses. The CLI omits it and
    builds the OpenBot and Inspect adapters from `context`.
    """

    destination = Path(out_dir) / spec.experiment_id
    destination.mkdir(parents=True, exist_ok=True)
    _reset_outputs(destination)
    try:
        table = load_pricing_table(spec.pricing_path)
        pricing_ref = _pricing_ref(table)
    except (OSError, PricingError) as exc:
        raise LivePilotError(str(exc)) from exc
    pool = _pool(spec, pricing_ref)
    tasks = tuple(_task(item) for item in spec.tasks)
    environment = ExecutionEnvironment(
        egress_policy_version=spec.egress.policy_version,
        gateway_host=context.gateway_host,
        gateway_port=context.gateway_port,
        image_ref=context.litellm_image,
        image_digest=context.litellm_image_digest,
    )
    fingerprint = current_runtime_fingerprint()
    configs = {
        "openbot": openbot_harness_config(
            commit=context.openbot_commit,
            pool=pool,
            environment=environment,
            timeout_seconds=spec.timeout_seconds,
            base_url_env=BASE_URL_ENV,
        ),
        "inspect_react": inspect_harness_config(
            pool=pool,
            environment=environment,
            timeout_seconds=spec.timeout_seconds,
        ),
    }
    if configs["inspect_react"].version != INSPECT_AI_VERSION:
        raise LivePilotError("inspect harness is not pinned")
    try:
        profile = benchmark_profile(
            pool=pool,
            environment=environment,
            timeout_seconds=spec.timeout_seconds,
        )
    except OpenBotProfileError as exc:
        raise LivePilotError(str(exc)) from exc
    openbot_base_url = litellm_base_url(environment)
    if profile["models"]["providers"]["litellm"]["baseUrl"] != openbot_base_url:
        raise LivePilotError("OpenBot baseUrl is not the GPU LiteLLM gateway")
    if profile["models"]["providers"]["litellm"]["apiKey"] != "${LITELLM_API_KEY}":
        raise LivePilotError("OpenBot profile must reference LITELLM_API_KEY and no cloud provider key")
    manifest = _manifest(
        spec,
        context,
        pool,
        tasks,
        table,
        pricing_ref,
        openbot_base_url=openbot_base_url,
    )
    dump_json(destination / "manifest.json", manifest)
    harnesses = adapters if adapters is not None else default_adapters(context)
    missing = [name for name in HARNESS_ORDER if name not in harnesses]
    if missing:
        raise LivePilotError("missing harness adapters: " + ", ".join(missing))
    now = clock or (lambda: datetime.now(UTC))
    protocol = PairedProtocol(
        repetitions=spec.repetitions,
        timeout_seconds=spec.timeout_seconds,
        max_total_tokens=spec.max_total_tokens,
        max_cost=spec.max_cost,
    )
    cells: list[tuple[Task, HarnessRun]] = []
    for task in tasks:
        for repetition in range(spec.repetitions):
            seed = spec.seed + repetition
            for harness_name in HARNESS_ORDER:
                config = configs[harness_name]
                execution_id = f"exec-{task.task_id}-{config.harness_id}-r{repetition}"
                state_dir = isolated_state_dir(destination / "state" / execution_id)
                started = now()
                request = HarnessRunRequest(
                    task=task,
                    pool=pool,
                    config=config,
                    environment=environment,
                    experiment_id=spec.experiment_id,
                    execution_id=execution_id,
                    trace_id=f"trace-{execution_id}",
                    seed=seed,
                    fingerprint=fingerprint,
                    state_dir=state_dir,
                    started_at=started,
                    finished_at=started,
                )
                try:
                    produced = harnesses[harness_name].run(request)
                except (AdapterError, OSError, ValueError) as exc:
                    _write_stop(
                        destination,
                        spec.experiment_id,
                        task_id=task.task_id,
                        harness_id=config.harness_id,
                        repetition=repetition,
                        message=str(exc),
                    )
                    return LiveRunResult(
                        experiment_id=spec.experiment_id,
                        out_dir=destination,
                        published=False,
                        passed=False,
                        message=str(exc),
                    )
                finished = now()
                if finished < started:
                    raise LivePilotError("live clock moved backwards")
                execution = _restamp(produced.execution, started_at=started, finished_at=finished)
                cells.append((task, HarnessRun(execution=execution, trace=produced.trace)))
    try:
        audited = _aggregate(cells, pool=pool, table=table, pricing_ref=pricing_ref, protocol=protocol)
    except AggregationError as exc:
        _write_stop(
            destination,
            spec.experiment_id,
            task_id=None,
            harness_id=None,
            repetition=None,
            message=str(exc),
        )
        return LiveRunResult(
            experiment_id=spec.experiment_id,
            out_dir=destination,
            published=False,
            passed=False,
            message=str(exc),
        )
    audit = audit_live_matrix(
        spec,
        tasks=audited,
        pool=pool,
        table=table,
        environment=environment,
    )
    _write_traces(destination, [run for _, run in cells])
    scored = [scored for item in audited for scored in item.runs]
    dump_jsonl(destination / "executions.jsonl", execution_documents(scored))
    metrics = [report_document(item.report) for item in audited]
    dump_jsonl(destination / "paired.jsonl", metrics)
    document = audit_document(audit)
    document["corpus"] = "live"
    dump_json(destination / "audit.json", document)
    bundle = {
        "audit": document,
        "kind": REPORT_INPUT_KIND,
        "metrics": metrics,
        "note": LIVE_REPORT_NOTE,
        "schema_version": SCHEMA_VERSION,
    }
    dump_json(destination / "report-input.json", bundle)
    message = f"ok gates={len(audit.gates)} out={destination}" if audit.passed else "fail gates"
    return LiveRunResult(
        experiment_id=spec.experiment_id,
        out_dir=destination,
        published=True,
        passed=audit.passed,
        message=message,
    )


def default_adapters(context: LiveContext) -> dict[str, HarnessAdapter]:
    """OpenBot CLI and Inspect react(), both pointed at the resolved gateway."""

    from ohbe.adapters.inspect_react import InspectReactAdapter
    from ohbe.adapters.openbot import OpenBotAdapter

    return {
        "openbot": OpenBotAdapter(binary=context.openbot_bin, api_key=context.api_key),
        "inspect_react": InspectReactAdapter(api_key=context.api_key),
    }


def _aggregate(
    cells: Sequence[tuple[Task, HarnessRun]],
    *,
    pool: ModelPool,
    table: PricingTable,
    pricing_ref: PricingRef,
    protocol: PairedProtocol,
) -> tuple[LiveAuditedTask, ...]:
    grouped: dict[str, list[tuple[Task, HarnessRun]]] = {}
    for task, run in cells:
        grouped.setdefault(task.task_id, []).append((task, run))
    audited: list[LiveAuditedTask] = []
    for rows in grouped.values():
        task = rows[0][0]
        report, scored = aggregate_paired(
            [(run.execution, run.trace) for _, run in rows],
            task=task,
            pool=pool,
            table=table,
            pricing_ref=pricing_ref,
            protocol=protocol,
        )
        _reject_claims(report, scored)
        audited.append(LiveAuditedTask(task=task, report=report, runs=scored))
    return tuple(audited)


def _reject_claims(report: PairedReport, scored: tuple[ScoredRun, ...]) -> None:
    if report.note != OBSERVED_NOTE:
        raise AggregationError("paired report changed the observed-results note")
    for item in scored:
        if item.execution.oracle is not None or item.execution.routing_regret is not None:
            raise AggregationError("live pilot does not record oracle or routing regret")


def _pricing_ref(table: PricingTable) -> PricingRef:
    """Pin the table by id, version, filename, and content hash.

    The URI is the pricing filename, so a relative path and an absolute path
    to the same file build the same model pool. Core does not fetch it.
    """

    return PricingRef(
        pricing_id=table.pricing_id,
        version=table.version,
        uri=f"pricing/{Path(table.source).name}",
        content_hash=table.content_hash,
    )


def _pool(spec: LivePilotSpec, pricing_ref: PricingRef) -> ModelPool:
    models: list[ModelDefinition] = []
    for item in spec.models:
        metadata = {"litellm_model": item.litellm_model}
        models.append(
            ModelDefinition(
                model_id=item.model_id,
                provider=item.provider,
                name=item.name,
                revision=item.revision,
                pricing_ref=pricing_ref,
                metadata=metadata,
            )
        )
    return ModelPool.build(pool_id=spec.pool_id, models=tuple(models))


def _task(item: Any) -> Task:
    return Task.build(
        task_id=item.task_id,
        name=item.name,
        version=item.version,
        input={"prompt": item.prompt},
        expected={item.scorer: item.target},
        tags=("live-pilot",),
        metadata={"corpus": "live", "objective": item.scorer},
    )


def _restamp(execution: Execution, *, started_at: datetime, finished_at: datetime) -> Execution:
    data: dict[str, Any] = {
        "execution_id": execution.execution_id,
        "experiment_id": execution.experiment_id,
        "task_id": execution.task_id,
        "task_content_hash": execution.task_content_hash,
        "harness": execution.harness,
        "model_pool_id": execution.model_pool_id,
        "model_pool_hash": execution.model_pool_hash,
        "seed": execution.seed,
        "status": execution.status,
        "started_at": started_at,
        "finished_at": finished_at,
        "fingerprint": execution.fingerprint,
        "environment": execution.environment,
    }
    if execution.oracle is not None:
        data["oracle"] = execution.oracle
    if execution.routing_regret is not None:
        data["routing_regret"] = execution.routing_regret
    return Execution.build(**data)


def _manifest(
    spec: LivePilotSpec,
    context: LiveContext,
    pool: ModelPool,
    tasks: tuple[Task, ...],
    table: PricingTable,
    pricing_ref: PricingRef,
    *,
    openbot_base_url: str,
) -> dict[str, Any]:
    return {
        "budget": {
            "max_cost": spec.max_cost,
            "max_total_tokens": spec.max_total_tokens,
            "note": (
                "Token ceiling is enforced when usage is present. "
                "A cost ceiling is optional and is not observed spend."
            ),
        },
        "egress": {
            "allowlist": f"{context.gateway_host}:{context.gateway_port}",
            "base_url": context.base_url,
            "base_url_env": BASE_URL_ENV,
            "gateway_host": context.gateway_host,
            "gateway_port": context.gateway_port,
            "litellm_image": context.litellm_image,
            "litellm_image_digest": context.litellm_image_digest,
            "network": spec.egress.network,
            "openbot_base_url": openbot_base_url,
            "policy_version": spec.egress.policy_version,
        },
        "experiment_id": spec.experiment_id,
        "harnesses": [
            {
                "harness_id": "openbot",
                "image": context.openbot_image,
                "version": context.openbot_commit,
            },
            {
                "harness_id": "inspect-react",
                "version": INSPECT_AI_VERSION,
            },
        ],
        "kind": "ohbe.live_pilot_manifest",
        "limitations": list(LIVE_LIMITATIONS),
        "model_pool": {
            "content_hash": pool.content_hash,
            "model_ids": [model.model_id for model in pool.models],
            "pool_file": spec.pool_file,
            "pool_id": pool.pool_id,
        },
        "name": spec.name,
        "note": "This manifest records pins and ceilings. It does not record observed spend.",
        "ohbe_version": __version__,
        "oracle": None,
        "pricing": {
            "content_hash": table.content_hash,
            "pricing_id": table.pricing_id,
            "uri": pricing_ref.uri,
            "version": table.version,
        },
        "repetitions": spec.repetitions,
        "routing_regret": None,
        "schema_version": SCHEMA_VERSION,
        "seed": spec.seed,
        "tasks": [
            {"content_hash": task.content_hash, "task_id": task.task_id, "version": task.version}
            for task in tasks
        ],
        "timeout_seconds": spec.timeout_seconds,
        "topology": {
            "gateway_role": "gpu-farm-litellm",
            "harness_host": "private-openbot",
            "model_backends": sorted({model.provider for model in pool.models}),
        },
    }


def _reset_outputs(destination: Path) -> None:
    for name in ("report-input.json", "paired.jsonl", "executions.jsonl", "audit.json", "error.json"):
        (destination / name).unlink(missing_ok=True)
    trace_dir = destination / "traces"
    if trace_dir.is_dir():
        for child in trace_dir.glob("*.jsonl"):
            child.unlink()


def _write_traces(destination: Path, runs: Sequence[HarnessRun]) -> None:
    directory = destination / "traces"
    directory.mkdir(parents=True, exist_ok=True)
    for run in runs:
        _write_trace(directory / f"{run.execution.execution_id}.jsonl", run.trace)


def _write_trace(path: Path, trace: ExecutionTrace) -> None:
    text = "".join(canonical_json(record.model_dump(mode="json")) + "\n" for record in trace.records)
    path.write_text(text, encoding="utf-8")


def _write_stop(
    destination: Path,
    experiment_id: str,
    *,
    task_id: str | None,
    harness_id: str | None,
    repetition: int | None,
    message: str,
) -> None:
    dump_json(
        destination / "error.json",
        {
            "experiment_id": experiment_id,
            "harness_id": harness_id,
            "kind": "ohbe.live_pilot_error",
            "message": message,
            "observed_cost": None,
            "published": False,
            "repetition": repetition,
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
        },
    )
