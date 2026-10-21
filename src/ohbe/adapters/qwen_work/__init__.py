"""千问办公 closed adapter.

The product is a desktop app. This package does not install it and does not
run a task. See ``docs/qwen-work.md``.
"""

from ohbe.adapters.qwen_work.adapter import QwenWorkAdapter
from ohbe.adapters.qwen_work.profile import QWEN_RUN_REFUSAL, qwen_profile_parameters
from ohbe.preinstall import QWEN_WORK_VERSION

__all__ = [
    "QWEN_RUN_REFUSAL",
    "QWEN_WORK_VERSION",
    "QwenWorkAdapter",
    "qwen_profile_parameters",
]
