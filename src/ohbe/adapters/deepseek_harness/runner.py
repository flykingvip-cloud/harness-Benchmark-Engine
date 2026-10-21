"""Subprocess commands for one pinned ``dsh --profile headless`` run.

Provider API keys and proxy variables are removed from the child environment.
``DSH_HOME`` is an isolated directory, not the user home and not ``~/.dsh``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from ohbe.adapters.openbot.runner import run_command

_PROXY_ENV = frozenset(
    {
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    }
)
_PROVIDER_MARKERS = (
    "ANTHROPIC",
    "AZURE_OPENAI",
    "COHERE",
    "DEEPSEEK",
    "FIREWORKS",
    "GEMINI",
    "GROQ",
    "HUGGINGFACE",
    "MISTRAL",
    "OPENAI",
    "OPENROUTER",
    "PERPLEXITY",
    "TOGETHER",
)


def headless_argv(binary: str, message: str, *, patch: Path) -> list[str]:
    """One-shot headless task. Launcher flags come before the task text.

    ``dsh`` 0.2.1 parses ``--profile`` and ``--patch`` itself, then passes the
    remaining positional string to the headless app. There is no documented
    ``--json`` switch on that app. The patch file is the model route.
    """

    if not binary:
        raise ValueError("binary must be the dsh executable")
    if not message:
        raise ValueError("message must be non-empty")
    path = str(patch)
    if not path or path.startswith("-"):
        raise ValueError("patch path must be a file path")
    return [binary, "--profile", "headless", "--patch", path, message]


def benchmark_child_env(
    parent: Mapping[str, str],
    *,
    state_dir: Path,
    api_key: str,
) -> dict[str, str]:
    """Environment for one ``dsh`` process. Only the LiteLLM key is added."""

    if not api_key or any(ch.isspace() for ch in api_key):
        raise ValueError("LITELLM_API_KEY must be a non-empty token")
    env: dict[str, str] = {}
    for key, value in parent.items():
        if _blocked_env(key):
            continue
        env[key] = value
    env["HOME"] = str(state_dir)
    env["DSH_HOME"] = str(state_dir)
    env["LITELLM_API_KEY"] = api_key
    env.pop("LITELLM_MASTER_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    return env


def isolated_dsh_home(path: Path) -> Path:
    """Refuse the user home directory and the default DSH home."""

    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    if resolved == home or resolved == home / ".dsh" or resolved == home / ".openbot" or resolved == home / ".openclaw":
        raise ValueError("DSH_HOME must be an isolated directory")
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError("DSH_HOME is not a directory")
    return resolved


def which_dsh(binary: str | None = None) -> str | None:
    """Return ``DSH_BIN``, then ``binary``, then the click-install shim.

    The shim is ``OHBE_ROOT/harnesses/installs/deepseek_harness``. It is the
    bin recorded for the pinned tarball, not a floating ``latest`` install.
    """

    candidate = os.environ.get("DSH_BIN") or binary
    if candidate:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return _installed_executable("deepseek_harness")


def _installed_executable(adapter: str) -> str | None:
    raw = os.environ.get("OHBE_ROOT", "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    root = Path(raw)
    if not root.is_dir():
        return None
    from ohbe.click_install import installed_executable

    found = installed_executable(root, adapter)
    if found is None:
        return None
    return str(found)


def _blocked_env(key: str) -> bool:
    if key in _PROXY_ENV:
        return True
    if key == "LITELLM_API_KEY":
        return False
    upper = key.upper()
    if upper in {"DSH_HOME", "LITELLM_MASTER_KEY", "DEEPSEEK_API_KEY"}:
        return True
    if upper.endswith("_API_KEY") or upper.endswith("_TOKEN") or upper.endswith("_SECRET"):
        return True
    return any(marker in upper for marker in _PROVIDER_MARKERS)


__all__ = [
    "benchmark_child_env",
    "headless_argv",
    "isolated_dsh_home",
    "run_command",
    "which_dsh",
]
