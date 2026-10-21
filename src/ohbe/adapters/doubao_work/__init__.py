"""豆包工作 closed adapter.

The product is a desktop app. This package records a Windows installer pin
and does not run a task. See ``docs/doubao-work.md``.
"""

from ohbe.adapters.doubao_work.adapter import DoubaoWorkAdapter
from ohbe.adapters.doubao_work.profile import DOUBAO_RUN_REFUSAL, doubao_profile_parameters
from ohbe.preinstall import DOUBAO_WORK_VERSION

__all__ = [
    "DOUBAO_RUN_REFUSAL",
    "DOUBAO_WORK_VERSION",
    "DoubaoWorkAdapter",
    "doubao_profile_parameters",
]
