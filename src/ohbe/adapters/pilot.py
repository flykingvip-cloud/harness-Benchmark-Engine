"""Pilot tasks shared by the OpenBot and Inspect adapters.

Both harnesses receive each document unchanged: same `task_id`, input, and
expected objective target. The suite is three micro-tasks, one per objective
scorer. It does not claim that the harnesses expose the same tools.
"""

from __future__ import annotations

from pathlib import Path

from ohbe.adapters.base import HarnessRunRequest
from ohbe.adapters.deepseek_harness.profile import dsh_harness_config
from ohbe.adapters.inspect_react.plan import inspect_harness_config
from ohbe.adapters.openclaw.profile import openclaw_harness_config
from ohbe.adapters.openbot.profile import openbot_harness_config
from ohbe.adapters.workbuddy.profile import workbuddy_harness_config
from ohbe.core.base import AwareDatetime
from ohbe.core.execution import ExecutionEnvironment
from ohbe.core.fingerprint import RuntimeFingerprint
from ohbe.core.model import ModelPool
from ohbe.core.task import Task

PILOT_TASK_ID = "ohbe-pilot-echo"
PILOT_PROMPT = "Reply with the single word pong."
PILOT_TARGET = "pong"
PILOT_EXACT_TASK_ID = "ohbe-pilot-exact"
PILOT_EXACT_PROMPT = "Reply with the single word ok."
PILOT_EXACT_TARGET = "ok"
PILOT_MATCH_TASK_ID = "ohbe-pilot-match"
PILOT_MATCH_PROMPT = "Reply with the single word ready."
PILOT_MATCH_TARGET = "ready"
PILOT_OPENBOT_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def pilot_task() -> Task:
    """Objective echo task. The target is a substring an `includes` scorer can check."""

    return Task.build(
        task_id=PILOT_TASK_ID,
        name="pilot-echo",
        version="1",
        input={"prompt": PILOT_PROMPT},
        expected={"contains": PILOT_TARGET},
        tags=("pilot", "paired"),
        metadata={"objective": "contains"},
    )


def pilot_exact_task() -> Task:
    """Full-string objective. Extra words do not pass."""

    return Task.build(
        task_id=PILOT_EXACT_TASK_ID,
        name="pilot-exact",
        version="1",
        input={"prompt": PILOT_EXACT_PROMPT},
        expected={"exact": PILOT_EXACT_TARGET},
        tags=("pilot", "paired", "exact"),
        metadata={"objective": "exact"},
    )


def pilot_match_task() -> Task:
    """Full-string objective selected by `expected.match`."""

    return Task.build(
        task_id=PILOT_MATCH_TASK_ID,
        name="pilot-match",
        version="1",
        input={"prompt": PILOT_MATCH_PROMPT},
        expected={"match": PILOT_MATCH_TARGET},
        tags=("pilot", "paired", "match"),
        metadata={"objective": "match"},
    )


def pilot_suite() -> tuple[Task, Task, Task]:
    """Echo, exact, and match tasks. Order is part of the pilot audit."""

    return (pilot_task(), pilot_exact_task(), pilot_match_task())


def pilot_requests(
    *,
    environment: ExecutionEnvironment,
    pool: ModelPool,
    fingerprint: RuntimeFingerprint,
    state_root: Path,
    started_at: AwareDatetime,
    finished_at: AwareDatetime,
    experiment_id: str = "exp-pilot",
    seed: int = 7,
    timeout_seconds: int = 30,
) -> tuple[HarnessRunRequest, HarnessRunRequest]:
    """Two requests that share the task, pool, and egress environment."""

    task = pilot_task()
    shared = {
        "task": task,
        "pool": pool,
        "environment": environment,
        "experiment_id": experiment_id,
        "seed": seed,
        "fingerprint": fingerprint,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    openbot = HarnessRunRequest(
        config=openbot_harness_config(
            commit=PILOT_OPENBOT_COMMIT,
            pool=pool,
            environment=environment,
            timeout_seconds=timeout_seconds,
        ),
        execution_id="exec-pilot-openbot",
        trace_id="trace-pilot-openbot",
        state_dir=state_root / "openbot",
        **shared,
    )
    inspect = HarnessRunRequest(
        config=inspect_harness_config(
            pool=pool,
            environment=environment,
            timeout_seconds=timeout_seconds,
        ),
        execution_id="exec-pilot-inspect",
        trace_id="trace-pilot-inspect",
        state_dir=state_root / "inspect",
        **shared,
    )
    return openbot, inspect


def pilot_dsh_request(partner: HarnessRunRequest, *, state_dir: Path | None = None) -> HarnessRunRequest:
    """DeepSeek Harness request bound to one partner's task, pool, and egress.

    The partner is an OpenBot or Inspect request from ``pilot_requests``.
    The harness config hash differs. The task object, pool, and gateway do not.
    This does not add the harness to ``HARNESS_ORDER``.
    """

    timeout = partner.config.parameters.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        timeout = 30
    return HarnessRunRequest(
        task=partner.task,
        pool=partner.pool,
        config=dsh_harness_config(
            pool=partner.pool,
            environment=partner.environment,
            timeout_seconds=timeout,
        ),
        environment=partner.environment,
        experiment_id=partner.experiment_id,
        execution_id="exec-pilot-dsh",
        trace_id="trace-pilot-dsh",
        seed=partner.seed,
        fingerprint=partner.fingerprint,
        state_dir=state_dir or partner.state_dir.parent / "dsh",
        started_at=partner.started_at,
        finished_at=partner.finished_at,
    )


def pilot_openclaw_request(partner: HarnessRunRequest, *, state_dir: Path | None = None) -> HarnessRunRequest:
    """OpenClaw request bound to one partner's task, pool, and egress.

    The partner is an OpenBot or Inspect request from ``pilot_requests``.
    The harness config hash differs. The task object, pool, and gateway do not.
    The run is the embedded agent, not an OpenClaw Gateway session and not a
    bridge to DeepSeek Harness. This does not add the harness to ``HARNESS_ORDER``.
    """

    timeout = partner.config.parameters.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        timeout = 30
    return HarnessRunRequest(
        task=partner.task,
        pool=partner.pool,
        config=openclaw_harness_config(
            pool=partner.pool,
            environment=partner.environment,
            timeout_seconds=timeout,
        ),
        environment=partner.environment,
        experiment_id=partner.experiment_id,
        execution_id="exec-pilot-openclaw",
        trace_id="trace-pilot-openclaw",
        seed=partner.seed,
        fingerprint=partner.fingerprint,
        state_dir=state_dir or partner.state_dir.parent / "openclaw",
        started_at=partner.started_at,
        finished_at=partner.finished_at,
    )


def pilot_workbuddy_request(partner: HarnessRunRequest, *, state_dir: Path | None = None) -> HarnessRunRequest:
    """WorkBuddy request bound to one partner's task, pool, and egress.

    The partner is an OpenBot or Inspect request from ``pilot_requests``.
    The harness config hash differs. The task object, pool, and gateway do not.
    The run is the bundled CodeBuddy CLI print path, not the WorkBuddy GUI and
    not a DeepSeek Harness model source. This does not add the harness to
    ``HARNESS_ORDER``.
    """

    timeout = partner.config.parameters.get("timeout_seconds", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, int):
        timeout = 30
    return HarnessRunRequest(
        task=partner.task,
        pool=partner.pool,
        config=workbuddy_harness_config(
            pool=partner.pool,
            environment=partner.environment,
            timeout_seconds=timeout,
        ),
        environment=partner.environment,
        experiment_id=partner.experiment_id,
        execution_id="exec-pilot-workbuddy",
        trace_id="trace-pilot-workbuddy",
        seed=partner.seed,
        fingerprint=partner.fingerprint,
        state_dir=state_dir or partner.state_dir.parent / "workbuddy",
        started_at=partner.started_at,
        finished_at=partner.finished_at,
    )
