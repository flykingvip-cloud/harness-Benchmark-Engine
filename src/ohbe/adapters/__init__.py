"""Harness adapters.

Core schemas do not import this package. Importing it does not import
Inspect AI or LiteLLM.
"""

from ohbe.adapters.base import AdapterError, HarnessAdapter, HarnessRun, HarnessRunRequest
from ohbe.adapters.deepseek_harness import DeepSeekHarnessAdapter
from ohbe.adapters.doubao_work import DoubaoWorkAdapter
from ohbe.adapters.inspect_react import InspectReactAdapter
from ohbe.adapters.openbot import OpenBotAdapter
from ohbe.adapters.openclaw import OpenClawAdapter
from ohbe.adapters.qwen_work import QwenWorkAdapter
from ohbe.adapters.workbuddy import WorkBuddyAdapter

__all__ = [
    "AdapterError",
    "DeepSeekHarnessAdapter",
    "DoubaoWorkAdapter",
    "HarnessAdapter",
    "HarnessRun",
    "HarnessRunRequest",
    "InspectReactAdapter",
    "OpenBotAdapter",
    "OpenClawAdapter",
    "QwenWorkAdapter",
    "WorkBuddyAdapter",
]
