"""Objective scorers for a task's expected answer.

`contains` then `match` follow the Inspect react adapter. `exact` is the
additional full-string check. Scoring reads the answer recorded on the trace.
It does not call a model and it does not invent a pass from a missing answer.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from ohbe.core.task import Task
from ohbe.core.trace import ExecutionTrace


class ObjectiveError(ValueError):
    """The task has no objective target."""


@dataclass(frozen=True)
class ObjectiveResult:
    """One recomputed objective check."""

    scorer: str
    target: str
    answer: str | None
    passed: bool


def select_objective(task: Task) -> tuple[str, str]:
    """Return `(scorer, target)` for the task's objective.

    A non-empty `expected.contains` wins, then `expected.match`, then
    `expected.exact`. That order matches the Inspect adapter for contains
    and match.
    """

    expected = task.expected or {}
    for name in ("contains", "match", "exact"):
        value = expected.get(name)
        if isinstance(value, str) and value:
            return name, value
    raise ObjectiveError("objective scorer requires expected.contains, expected.match, or expected.exact")


def score_answer(answer: str | None, *, scorer: str, target: str) -> bool:
    """Score one answer. None and the empty string do not pass."""

    if not isinstance(answer, str) or answer == "":
        return False
    if scorer == "contains":
        return target in answer
    if scorer in {"match", "exact"}:
        return answer.strip() == target.strip()
    raise ObjectiveError(f"unknown objective scorer {scorer}")


def score_task(task: Task, answer: str | None) -> ObjectiveResult:
    """Select the task's scorer and apply it to `answer`."""

    scorer, target = select_objective(task)
    return ObjectiveResult(
        scorer=scorer,
        target=target,
        answer=answer,
        passed=score_answer(answer, scorer=scorer, target=target),
    )


def extract_answer(trace: ExecutionTrace) -> str | None:
    """Last answer recorded on the trace, if the harness stored one.

    Inspect stores it on `inspect.transcript`. OpenBot stores agent stdout
    JSON with a `text` field on `openbot.transcript`. DeepSeek Harness stores
    the headless completion on `dsh.transcript`. OpenClaw stores the embedded
    agent ``final`` text on `openclaw.transcript`. WorkBuddy stores the bundled
    CLI ``result`` text on `workbuddy.transcript`. A later `evaluation.answer`
    event overrides those transcripts. Transcripts stay supplementary; this
    only reads them.
    """

    found: str | None = None
    for record in trace.records:
        if record.event_type == "inspect.transcript":
            answer = record.payload.get("answer")
            if isinstance(answer, str):
                found = answer
        elif record.event_type == "dsh.transcript":
            answer = record.payload.get("answer")
            if isinstance(answer, str):
                found = answer
        elif record.event_type == "openclaw.transcript":
            answer = record.payload.get("answer")
            if isinstance(answer, str):
                found = answer
        elif record.event_type == "workbuddy.transcript":
            answer = record.payload.get("answer")
            if isinstance(answer, str):
                found = answer
        elif record.event_type == "openbot.transcript":
            text = _openbot_text(record.payload)
            if text is not None:
                found = text
        elif record.event_type == "evaluation.answer":
            answer = record.payload.get("answer")
            if isinstance(answer, str):
                found = answer
    return found


def _openbot_text(payload: Mapping[str, object]) -> str | None:
    calls = payload.get("calls")
    if not isinstance(calls, list):
        return None
    found: str | None = None
    for call in calls:
        if not isinstance(call, Mapping) or call.get("command") != "agent":
            continue
        stdout = call.get("stdout")
        if not isinstance(stdout, str) or not stdout.strip():
            continue
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("text"), str):
            found = parsed["text"]
    return found
