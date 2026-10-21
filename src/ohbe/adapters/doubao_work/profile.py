"""Closed profile for 豆包工作.

The product surface is the desktop client, the Doubao app, and Feishu.
Login is a phone number or a Feishu account. No headless command is
documented that takes one task and sends it only to an OpenAI-compatible
gateway.

Windows x64 and arm64 installers for ``2.31.10`` are on the publisher CDN
and are recorded as catalog pins. A Windows click downloads the matching
file and does not launch it. Other systems return ``os_unsupported``. macOS on
``doubao.com/work`` still has no versioned artifact URL, so none is stored.
A third-party mirror is not a pin.
"""

from __future__ import annotations

from typing import Any

from ohbe.adapters.closed import closed_parameters
from ohbe.preinstall import DOUBAO_WORK_VERSION, OFFICE_CLOSED_REASON

DOUBAO_RUNNER = "doubao-work"
DOUBAO_INSTALL_PIN = "windows-cdn-not-executed"
DOUBAO_RUN_REFUSAL = (
    "doubao_work is not runnable: desktop GUI only and no LiteLLM-only headless path. "
    "Windows installers for 2.31.10 are catalog pins and are not executed. "
    "This adapter does not launch a process."
)


class DoubaoWorkProfileError(ValueError):
    """The requested version is not the Windows pin."""


def normalize_doubao_version(version: str) -> str:
    """Accept only ``2.31.10``. ``latest`` is rejected."""

    if not isinstance(version, str) or version != DOUBAO_WORK_VERSION:
        raise DoubaoWorkProfileError(f"豆包工作 version must be {DOUBAO_WORK_VERSION}")
    return version


def doubao_profile_parameters(*, timeout_seconds: int) -> dict[str, Any]:
    """Catalog profile. ``runnable`` stays false. No URL and no key."""

    parameters = closed_parameters(
        timeout_seconds=timeout_seconds,
        channel="desktop",
        runner=DOUBAO_RUNNER,
        install_pin=DOUBAO_INSTALL_PIN,
    )
    if parameters["closed-reason"] != OFFICE_CLOSED_REASON or parameters["runnable"] is not False:
        raise DoubaoWorkProfileError("豆包工作 profile must stay closed")
    return parameters
