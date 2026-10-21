"""Thin harness adapter contract.

An adapter receives one task, the model pool, the harness config, and the
egress environment. It returns an OHBE execution and the trace that is the
source of truth. Harness transcripts are not a substitute for that trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ohbe.core.base import AwareDatetime
from ohbe.core.execution import Execution, ExecutionEnvironment
from ohbe.core.fingerprint import RuntimeFingerprint
from ohbe.core.harness import HarnessConfig
from ohbe.core.model import ModelPool
from ohbe.core.task import Task
from ohbe.core.trace import ExecutionTrace


class AdapterError(RuntimeError):
    """The adapter could not start a run."""


@dataclass(frozen=True)
class HarnessRunRequest:
    """Inputs for one black-box harness task."""

    task: Task
    pool: ModelPool
    config: HarnessConfig
    environment: ExecutionEnvironment
    experiment_id: str
    execution_id: str
    trace_id: str
    seed: int
    fingerprint: RuntimeFingerprint
    state_dir: Path
    started_at: AwareDatetime
    finished_at: AwareDatetime


@dataclass(frozen=True)
class HarnessRun:
    """OHBE records for one adapter run. `trace` is the source of truth."""

    execution: Execution
    trace: ExecutionTrace


class HarnessAdapter(Protocol):
    """Task + environment + model pool + config → execution trace."""

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        """Run one task and return the sealed execution and trace."""
