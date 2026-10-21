"""Objective scorers and the failure taxonomy."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from ohbe.adapters.pilot import PILOT_TARGET, pilot_task
from ohbe.core.execution import ExecutionStatus
from ohbe.core.trace import ExecutionTrace, TraceEventType, TraceLog
from ohbe.eval.failure import FailureCategory
from ohbe.eval.judge import judge_execution
from ohbe.eval.objective import extract_answer, score_answer, score_task, select_objective
from tests.samples import NOW, sample_execution

ROOT = Path(__file__).resolve().parents[1]


def _trace(tmp_path: Path, execution_id: str, events: list[tuple[str, dict]]) -> ExecutionTrace:
    log = TraceLog(tmp_path / f"{execution_id}.jsonl")
    for index, (event_type, payload) in enumerate(events):
        log.append(
            execution_id=execution_id,
            event_type=event_type,
            payload=payload,
            recorded_at=NOW,
            record_id=f"{execution_id}-{index}",
        )
    return ExecutionTrace.build(
        trace_id=f"trace-{execution_id}",
        execution_id=execution_id,
        records=tuple(log.read()),
    )


def test_pilot_task_uses_contains_and_exact_match_differ() -> None:
    task = pilot_task()
    assert select_objective(task) == ("contains", PILOT_TARGET)
    assert score_task(task, "pong").passed is True
    assert score_task(task, "please pong now").passed is True
    assert score_task(task, "ping").passed is False
    assert score_task(task, None).passed is False
    assert score_answer(" pong ", scorer="exact", target="pong") is True
    assert score_answer("say pong", scorer="exact", target="pong") is False
    assert score_answer("pong", scorer="match", target="pong") is True
    assert score_answer("say pong", scorer="match", target="pong") is False
    assert score_answer("PONG", scorer="contains", target="pong") is False


def test_extracts_inspect_and_openbot_answers(tmp_path: Path) -> None:
    inspect = _trace(
        tmp_path,
        "exec-inspect",
        [("inspect.transcript", {"answer": "pong", "supplementary": True})],
    )
    openbot = _trace(
        tmp_path,
        "exec-openbot",
        [
            (
                "openbot.transcript",
                {
                    "calls": [
                        {"command": "agent", "stdout": json.dumps({"ok": True, "text": "pong"})},
                    ],
                    "supplementary": True,
                },
            )
        ],
    )
    openclaw = _trace(
        tmp_path,
        "exec-openclaw",
        [("openclaw.transcript", {"answer": "pong", "plugin_bridge": "refused", "supplementary": True})],
    )
    workbuddy = _trace(
        tmp_path,
        "exec-workbuddy",
        [("workbuddy.transcript", {"answer": "pong", "desktop_install": "unsupported", "supplementary": True})],
    )
    assert extract_answer(inspect) == "pong"
    assert extract_answer(openbot) == "pong"
    assert extract_answer(openclaw) == "pong"
    assert extract_answer(workbuddy) == "pong"


def test_eval_fail_is_a_task_failure_and_infra_is_not(tmp_path: Path) -> None:
    task = pilot_task()
    missed = _finished(tmp_path, "exec-miss", answer="ping", status="failed", error="objective missed")
    judgement = judge_execution(
        execution=_execution("exec-miss", task, status=ExecutionStatus.FAILED),
        trace=missed,
        task=task,
    )
    assert judgement.validity == "valid"
    assert judgement.outcome == "failed"
    assert judgement.failure_category == FailureCategory.EVAL_FAIL.value
    assert judgement.passed is False

    crashed = _finished(tmp_path, "exec-crash", answer=None, status="failed", error="openbot onboard failed")
    crash = judge_execution(
        execution=_execution("exec-crash", task, status=ExecutionStatus.FAILED),
        trace=crashed,
        task=task,
    )
    assert crash.validity == "invalid"
    assert crash.outcome == "not_scored"
    assert crash.failure_category == FailureCategory.HARNESS_ERROR.value

    timed = _finished(
        tmp_path,
        "exec-time",
        answer="pong",
        status="failed",
        error="timed out",
        category=FailureCategory.TIMEOUT.value,
    )
    timeout = judge_execution(
        execution=_execution("exec-time", task, status=ExecutionStatus.FAILED),
        trace=timed,
        task=task,
    )
    assert timeout.validity == "invalid"
    assert timeout.failure_category == FailureCategory.TIMEOUT.value
    assert timeout.outcome == "not_scored"

    egress = _finished(tmp_path, "exec-egress", answer=None, status="failed", error="EgressDenied by policy")
    blocked = judge_execution(
        execution=_execution("exec-egress", task, status=ExecutionStatus.FAILED),
        trace=egress,
        task=task,
    )
    assert blocked.failure_category == FailureCategory.EGRESS_VIOLATION.value
    assert blocked.validity == "invalid"


def _execution(execution_id: str, task, *, status: ExecutionStatus):
    return sample_execution(
        execution_id=execution_id,
        experiment_id="exp-h",
        task_id=task.task_id,
        task_content_hash=task.content_hash,
        status=status,
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=10),
    )


def _finished(
    tmp_path: Path,
    execution_id: str,
    *,
    answer: str | None,
    status: str,
    error: str | None,
    category: str | None = None,
) -> ExecutionTrace:
    events: list[tuple[str, dict]] = [
        (TraceEventType.EXECUTION_STARTED, {"model_id": "openai/gpt-test", "task_id": "ohbe-pilot-echo"}),
    ]
    if answer is not None:
        events.append(("evaluation.answer", {"answer": answer}))
    finished: dict = {"status": status, "error": error}
    if category is not None:
        finished["failure_category"] = category
    events.append((TraceEventType.EXECUTION_FINISHED, finished))
    return _trace(tmp_path, execution_id, events)
