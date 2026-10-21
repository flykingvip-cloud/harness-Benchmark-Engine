"""Live pilot runner.

This package loads an experiment manifest and, when the operator has set the
live prerequisites, runs the OpenBot and Inspect adapters. It does not invent
token counts or prices. Importing it does not import Inspect AI or LiteLLM.
"""

from ohbe.live.context import LiveContext, resolve_live_context
from ohbe.live.runner import LiveRunResult, run_live_pilot
from ohbe.live.spec import LivePilotError, LivePilotSpec, load_live_pilot

__all__ = [
    "LiveContext",
    "LivePilotError",
    "LivePilotSpec",
    "LiveRunResult",
    "load_live_pilot",
    "resolve_live_context",
    "run_live_pilot",
]
