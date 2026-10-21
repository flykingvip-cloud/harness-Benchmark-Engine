"""Optional check that the installed LiteLLM extra matches the pin.

Importing this module does not import LiteLLM. The benchmark client does not
use the LiteLLM SDK to place calls.
"""

from __future__ import annotations

from ohbe.gateway.constants import LITELLM_VERSION


def installed_litellm_version() -> str | None:
    """Return the installed LiteLLM distribution version, or None if it is absent.

    LiteLLM 1.104.2 does not publish ``__version__`` on the module. The
    package metadata is the pin OHBE checks.
    """

    try:
        import litellm  # noqa: F401
    except ImportError:
        return None
    from importlib.metadata import PackageNotFoundError, version

    try:
        found = version("litellm")
    except PackageNotFoundError:
        return None
    return found if isinstance(found, str) else None


def litellm_pin_matches() -> bool:
    """True when the installed extra is exactly the OHBE pin."""

    return installed_litellm_version() == LITELLM_VERSION
