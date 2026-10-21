"""Failure categories for one execution.

A task failure means the run stayed inside the protocol and missed the
objective. An invalid run broke the protocol. Invalid runs are not task
failures. Missing token usage is its own category and is never a zero cost.
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    """Why a run missed the objective or cannot be compared."""

    TIMEOUT = "timeout"
    BUDGET = "budget"
    HARNESS_ERROR = "harness_error"
    EVAL_FAIL = "eval_fail"
    INVALID_USAGE = "invalid_usage"
    EGRESS_VIOLATION = "egress_violation"
    INFRA_INVALID = "infra_invalid"
    CANCELLED = "cancelled"


TASK_FAILURES = frozenset({FailureCategory.EVAL_FAIL})
INFRA_INVALID = frozenset(
    {
        FailureCategory.TIMEOUT,
        FailureCategory.BUDGET,
        FailureCategory.HARNESS_ERROR,
        FailureCategory.EGRESS_VIOLATION,
        FailureCategory.INFRA_INVALID,
        FailureCategory.CANCELLED,
        FailureCategory.INVALID_USAGE,
    }
)


def parse_failure_category(value: object) -> FailureCategory | None:
    """Return a known category, or None when the trace does not name one."""

    if not isinstance(value, str):
        return None
    try:
        return FailureCategory(value)
    except ValueError:
        return None
