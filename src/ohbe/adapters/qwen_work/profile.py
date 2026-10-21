"""Closed profile for 千问办公.

The product surface is the desktop app (and the web app). Desktop notes name
``1.2.5``. The download page only publishes ``/release/latest/`` with an
unversioned filename. Versioned paths under that host return 404. The
floating URL is not stored. A click returns ``installer_unpinned`` and does
not download it.

A third-party note says some installers contain ``qoderclicn --print``.
Official Qoder CN docs for that CLI require a Qoder account token and add
BYOK models through the interactive ``/model`` wizard. They say not to write
BYOK into ``settings.json``. That is not a file-pinned LiteLLM-only path.
This profile does not launch that CLI.
"""

from __future__ import annotations

from typing import Any

from ohbe.adapters.closed import closed_parameters
from ohbe.preinstall import OFFICE_CLOSED_REASON, QWEN_WORK_VERSION

QWEN_RUNNER = "qwen-work"
QWEN_INSTALL_PIN = "unversioned-download-not-stored"
QWEN_RUN_REFUSAL = (
    "qwen_work is not runnable: desktop GUI only and no LiteLLM-only headless path. "
    "The download page publishes only /release/latest/, which is not stored. "
    "qoderclicn BYOK is an interactive Qoder account wizard, not an OHBE gateway file. "
    "This adapter does not launch a process."
)


class QwenWorkProfileError(ValueError):
    """The requested version is not the desktop pin."""


def normalize_qwen_version(version: str) -> str:
    """Accept only ``1.2.5``. ``latest`` is rejected."""

    if not isinstance(version, str) or version != QWEN_WORK_VERSION:
        raise QwenWorkProfileError(f"千问办公 version must be {QWEN_WORK_VERSION}")
    return version


def qwen_profile_parameters(*, timeout_seconds: int) -> dict[str, Any]:
    """Catalog profile. ``runnable`` stays false. No URL and no key."""

    parameters = closed_parameters(
        timeout_seconds=timeout_seconds,
        channel="desktop",
        runner=QWEN_RUNNER,
        install_pin=QWEN_INSTALL_PIN,
    )
    if parameters["closed-reason"] != OFFICE_CLOSED_REASON or parameters["runnable"] is not False:
        raise QwenWorkProfileError("千问办公 profile must stay closed")
    return parameters
