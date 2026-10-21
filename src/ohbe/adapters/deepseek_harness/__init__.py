"""DeepSeek Harness (``dsh``) black-box adapter."""

from ohbe.adapters.deepseek_harness.adapter import DeepSeekHarnessAdapter
from ohbe.adapters.deepseek_harness.profile import (
    DSH_GIT_TAG,
    DSH_NPM_SPEC,
    DSH_PACKAGE,
    DSH_VERSION,
    dsh_harness_config,
)
from ohbe.adapters.deepseek_harness.runner import which_dsh

__all__ = [
    "DSH_GIT_TAG",
    "DSH_NPM_SPEC",
    "DSH_PACKAGE",
    "DSH_VERSION",
    "DeepSeekHarnessAdapter",
    "dsh_harness_config",
    "which_dsh",
]
