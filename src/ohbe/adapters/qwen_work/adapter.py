"""Non-runnable 千问办公 adapter.

``run`` raises before any subprocess, socket, or model call. It does not
pretend the desktop GUI or ``qoderclicn`` is a LiteLLM-only peer of OpenBot.
"""

from __future__ import annotations

from ohbe.adapters.base import HarnessRun, HarnessRunRequest
from ohbe.adapters.closed import refuse_run
from ohbe.adapters.qwen_work.profile import QWEN_RUN_REFUSAL


class QwenWorkAdapter:
    """Fail closed. There is no LiteLLM-only headless path to launch."""

    def run(self, request: HarnessRunRequest) -> HarnessRun:
        return refuse_run(request, QWEN_RUN_REFUSAL)
