"""Optional check that the installed Inspect extra matches the pin."""

from __future__ import annotations

from ohbe.adapters.inspect_react.plan import INSPECT_AI_VERSION


def installed_inspect_version() -> str | None:
    """Return the installed inspect-ai version, or None when the extra is absent."""

    try:
        import inspect_ai  # noqa: F401
    except ImportError:
        return None
    from importlib.metadata import PackageNotFoundError, version

    try:
        found = version("inspect-ai")
    except PackageNotFoundError:
        return None
    return found if isinstance(found, str) else None


def inspect_pin_matches() -> bool:
    return installed_inspect_version() == INSPECT_AI_VERSION
