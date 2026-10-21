"""Non-runnable 豆包工作 adapter.

``run`` raises before any subprocess, socket, or model call. Recording the
Windows installer pin does not make the desktop app a peer harness.
"""

from __future__ import annotations

from ohbe.adapters.base import HarnessRun, HarnessRunRequest
from ohbe.adapters.closed import refuse_run
from ohbe.adapters.doubao_work.profile import DOUBAO_RUN_REFUSAL


class DoubaoWorkAdapter:
    """Fail closed. There is no LiteLLM-only headless path to launch."""

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        return refuse_run(request, DOUBAO_RUN_REFUSAL)
