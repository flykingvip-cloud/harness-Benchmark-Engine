"""Shared facts for adapters that refuse to run.

千问办公 and 豆包工作 implement ``HarnessAdapter.run`` and then stop. They do
not launch a desktop GUI, a bundled CLI, or a model call. A profile that
records these facts is not a peer of OpenBot or Inspect ``react()``.
"""

from __future__ import annotations

from typing import Any

from ohbe.adapters.base import AdapterError, HarnessRun, HarnessRunRequest
from ohbe.preinstall import OFFICE_CLOSED_REASON

CLOSED_PROFILE = "closed"
CLOSED_EXECUTION_PATH = "unavailable"
CLOSED_PRODUCT_LAYER = "desktop-app"
CLOSED_DESKTOP_INSTALL = "unsupported"
CLOSED_MODEL_ROUTE = "refused"
CLOSED_PROVIDER = "refused"


def closed_parameters(
    *,
    timeout_seconds: int,
    channel: str,
    runner: str,
    install_pin: str,
) -> dict[str, Any]:
    """Profile parameters for one closed office harness.

    The document names the refusal. It does not name an endpoint or a key.
    """

    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    if channel != "desktop":
        raise ValueError("closed office channel must be desktop")
    if not runner or "://" in runner or "sk-" in runner.lower():
        raise ValueError("closed office runner was rejected")
    if not install_pin or "://" in install_pin or "latest" in install_pin.lower():
        raise ValueError("closed office install pin must not be a URL or latest")
    return {
        "channel": channel,
        "closed-reason": OFFICE_CLOSED_REASON,
        "desktop-install": CLOSED_DESKTOP_INSTALL,
        "execution-path": CLOSED_EXECUTION_PATH,
        "install-pin": install_pin,
        "model-route": CLOSED_MODEL_ROUTE,
        "product-layer": CLOSED_PRODUCT_LAYER,
        "profile": CLOSED_PROFILE,
        "provider": CLOSED_PROVIDER,
        "runnable": False,
        "runner": runner,
        "timeout_seconds": timeout_seconds,
    }


def refuse_run(request: HarnessRunRequest, reason: str) -> HarnessRun:
    """Stop before a process, a socket, or a model call."""

    del request
    raise AdapterError(reason)
