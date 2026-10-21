"""Runtime identity recorded on a manifest. Callers supply any git fields."""

from __future__ import annotations

import platform as platform_module

from pydantic import Field

from ohbe import __version__
from ohbe.core.base import OHBEModel


class RuntimeFingerprint(OHBEModel):
    """Versions captured so a run can be compared later.

    `git_commit` and `git_dirty` are optional and are not discovered by core.
    """

    ohbe_version: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    git_commit: str | None = None
    git_dirty: bool | None = None


def current_runtime_fingerprint(
    *,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> RuntimeFingerprint:
    """Fingerprint this interpreter. Does not invoke git."""

    return RuntimeFingerprint(
        ohbe_version=__version__,
        python_version=platform_module.python_version(),
        platform=platform_module.platform(),
        git_commit=git_commit,
        git_dirty=git_dirty,
    )
