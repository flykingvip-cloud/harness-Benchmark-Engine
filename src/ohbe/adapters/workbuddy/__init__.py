"""WorkBuddy black-box adapter.

WorkBuddy the product is a desktop app. This package runs one bundled
CodeBuddy Code ``--print`` turn against the OHBE LiteLLM gateway. It does
not launch the GUI, it does not install the desktop package, and it does
not bridge the turn into DeepSeek Harness.
"""

from ohbe.adapters.workbuddy.adapter import WorkBuddyAdapter
from ohbe.adapters.workbuddy.profile import (
    WORKBUDDY_CLI,
    WORKBUDDY_EXECUTION_PATH,
    WORKBUDDY_PRODUCT_LAYER,
    WORKBUDDY_VERSION,
    workbuddy_harness_config,
)
from ohbe.adapters.workbuddy.runner import which_workbuddy

__all__ = [
    "WORKBUDDY_CLI",
    "WORKBUDDY_EXECUTION_PATH",
    "WORKBUDDY_PRODUCT_LAYER",
    "WORKBUDDY_VERSION",
    "WorkBuddyAdapter",
    "which_workbuddy",
    "workbuddy_harness_config",
]
