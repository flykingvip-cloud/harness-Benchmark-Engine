"""OpenClaw black-box adapter.

OpenClaw the product is an orchestration and channel layer. This package
runs one embedded ``agent exec`` turn on the built-in runtime. It does not
start the Gateway and it does not bridge the turn to DeepSeek Harness or
another plugin runtime.
"""

from ohbe.adapters.openclaw.adapter import OpenClawAdapter
from ohbe.adapters.openclaw.profile import (
    OPENCLAW_GIT_TAG,
    OPENCLAW_NPM_SPEC,
    OPENCLAW_PACKAGE,
    OPENCLAW_VERSION,
    openclaw_harness_config,
)
from ohbe.adapters.openclaw.runner import which_openclaw

__all__ = [
    "OPENCLAW_GIT_TAG",
    "OPENCLAW_NPM_SPEC",
    "OPENCLAW_PACKAGE",
    "OPENCLAW_VERSION",
    "OpenClawAdapter",
    "openclaw_harness_config",
    "which_openclaw",
]
