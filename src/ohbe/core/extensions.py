"""Deferred Oracle and Routing Regret extension points.

v0.1 can name an external reference. It does not score oracles or compute
routing regret, and these models have no numeric result fields.
"""

from __future__ import annotations

from typing import Literal

from ohbe.core.base import OHBEModel


class OracleStub(OHBEModel):
    """Placeholder for a later oracle attachment."""

    status: Literal["not_implemented"] = "not_implemented"
    ref: str | None = None
    notes: str | None = None


class RoutingRegretStub(OHBEModel):
    """Placeholder for a later routing-regret attachment."""

    status: Literal["not_implemented"] = "not_implemented"
    ref: str | None = None
    notes: str | None = None
