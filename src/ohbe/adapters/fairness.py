"""Comparable fields for one task run on two harnesses.

This is not a cost report and not an oracle. It checks that both executions
saw the same task, model pool, and gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ohbe.adapters.base import HarnessRun
from ohbe.core.task import Task
from ohbe.core.trace import TraceEventType


class FairnessError(ValueError):
    """The two runs are not comparable."""


@dataclass(frozen=True)
class FairnessRecord:
    """Fields that must match before harness scores can be compared later."""

    harness_id: str
    harness_version: str
    config_hash: str
    task_id: str
    task_content_hash: str
    task_input: dict[str, Any]
    model_pool_id: str
    model_pool_hash: str
    model_id: str
    gateway_host: str
    gateway_port: int
    egress_policy_version: str
    status: str


def fairness_record(run: HarnessRun, *, task: Task) -> FairnessRecord:
    """Read the comparable fields from an execution and its trace."""

    if run.execution.task_id != task.task_id or run.execution.task_content_hash != task.content_hash:
        raise FairnessError("execution is not bound to this task")
    if run.execution.oracle is not None or run.execution.routing_regret is not None:
        raise FairnessError("paired runs do not record oracle or routing regret")
    if run.execution.environment is None:
        raise FairnessError("paired runs require an egress environment")
    started = next(
        record for record in run.trace.records if record.event_type == TraceEventType.EXECUTION_STARTED
    )
    if started.payload.get("task_input") != task.input:
        raise FairnessError("trace task_input does not match the task")
    if started.payload.get("task_id") != task.task_id:
        raise FairnessError("trace task_id does not match the task")
    if started.payload.get("model_pool_id") != run.execution.model_pool_id:
        raise FairnessError("trace model_pool_id does not match the execution")
    return FairnessRecord(
        harness_id=run.execution.harness.harness_id,
        harness_version=run.execution.harness.version,
        config_hash=run.execution.harness.config_hash,
        task_id=run.execution.task_id,
        task_content_hash=run.execution.task_content_hash,
        task_input=dict(task.input),
        model_pool_id=run.execution.model_pool_id,
        model_pool_hash=run.execution.model_pool_hash,
        model_id=str(started.payload["model_id"]),
        gateway_host=run.execution.environment.gateway_host,
        gateway_port=run.execution.environment.gateway_port,
        egress_policy_version=run.execution.environment.egress_policy_version,
        status=run.execution.status.value,
    )


def assert_same_task_and_pool(left: FairnessRecord, right: FairnessRecord) -> None:
    """Raise when the task, pool, selected model, or gateway differ."""

    compared = (
        "task_id",
        "task_content_hash",
        "task_input",
        "model_pool_id",
        "model_pool_hash",
        "model_id",
        "gateway_host",
        "gateway_port",
        "egress_policy_version",
    )
    for name in compared:
        if getattr(left, name) != getattr(right, name):
            raise FairnessError(f"{name} differs between harnesses")
