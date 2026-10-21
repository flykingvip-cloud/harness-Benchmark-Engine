"""Interval estimates for observed rates and means.

These describe the sample that was run. They are not a routing decision.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from ohbe.cost.decimal import format_rate

_Z = Decimal("1.96")


def wilson95(successes: int, total: int) -> tuple[str, str] | None:
    """Wilson score interval at 95% for a binomial proportion."""

    if total < 1:
        return None
    if successes < 0 or successes > total:
        raise ValueError("successes must be between 0 and total")
    n = Decimal(total)
    p = Decimal(successes) / n
    z2 = _Z * _Z
    denom = Decimal(1) + z2 / n
    center = (p + z2 / (Decimal(2) * n)) / denom
    radicand = p * (Decimal(1) - p) / n + z2 / (Decimal(4) * n * n)
    margin = _Z * radicand.sqrt() / denom
    low = max(Decimal(0), center - margin)
    high = min(Decimal(1), center + margin)
    return format_rate(low), format_rate(high)


def mean_interval95(values: Sequence[int]) -> tuple[str, str | None, str | None] | None:
    """Normal approximation around the sample mean. None when `values` is empty.

    The interval is omitted when fewer than two observations are present.
    """

    if not values:
        return None
    n = Decimal(len(values))
    mean = sum((Decimal(value) for value in values), Decimal(0)) / n
    if len(values) < 2:
        return format_rate(mean), None, None
    variance = sum(((Decimal(value) - mean) ** 2 for value in values), Decimal(0)) / Decimal(len(values) - 1)
    error = _Z * variance.sqrt() / n.sqrt()
    return format_rate(mean), format_rate(mean - error), format_rate(mean + error)
