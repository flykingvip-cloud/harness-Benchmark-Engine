"""Subprocess command for one pinned ``openclaw agent exec`` turn.

The command is the embedded agent. It does not start ``openclaw gateway``
and it does not pass a plugin runtime. Provider API keys and proxy variables
are removed from the child environment. State lives in an isolated directory,
not the user home and not ``~/.openclaw``.
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
_OPENCLAW_POINTERS = frozenset(
    {
        "OPENCLAW_AGENT_RUNTIME",
        "OPENCLAW_CONFIG",
        "OPENCLAW_CONFIG_PATH",
        "OPENCLAW_HOME",
        "OPENCLAW_STATE_DIR",
    }
)
_FORBIDDEN_FLAGS = frozenset({"--auth-env-only", "--deliver", "--fallback", "--isolated", "--local"})


def agent_exec_argv(
    binary: str,
    *,
    config: Path,
    state_dir: Path,
    message_file: Path,
    model_ref: str,
    timeout_seconds: int,
) -> list[str]:
    """One embedded turn. The prompt is a file so a leading dash is not a flag.

    ``openclaw`` 2026.10.1 reads ``--config`` instead of the ambient config.
    ``--auth-env-only`` cannot be combined with ``--config`` and is not used.
    ``--state-dir`` must already exist. There is no Gateway subcommand here.
    """

    if not binary:
        raise ValueError("binary must be the openclaw executable")
    if not model_ref.startswith("litellm/") or model_ref == "litellm/":
        raise ValueError("model ref must name the litellm provider")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    for path in (config, state_dir, message_file):
        text = str(path)
        if not text or text.startswith("-"):
            raise ValueError("openclaw path must be a file path")
    argv = [
        binary,
        "agent",
        "exec",
        "--json",
        "--config",
        str(config),
        "--state-dir",
        str(state_dir),
        "--cwd",
        str(state_dir),
        "--model",
        model_ref,
        "--timeout",
        str(timeout_seconds),
        "--message-file",
        str(message_file),
    ]
    if any(flag in argv for flag in _FORBIDDEN_FLAGS):
        raise ValueError("openclaw benchmark command must stay on embedded agent exec")
    if "gateway" in argv:
        raise ValueError("openclaw benchmark command must not start the gateway")
    return argv


def benchmark_child_env(
    parent: Mapping[str, str],
    *,
    state_dir: Path,
    api_key: str,
) -> dict[str, str]:
    """Environment for one ``openclaw`` process. Only the LiteLLM key is added."""

    if not api_key or any(ch.isspace() for ch in api_key):
        raise ValueError("LITELLM_API_KEY must be a non-empty token")
    env: dict[str, str] = {}
    for key, value in parent.items():
        if _blocked_env(key):
            continue
        env[key] = value
    env["HOME"] = str(state_dir)
    env["OPENCLAW_STATE_DIR"] = str(state_dir)
    env["LITELLM_API_KEY"] = api_key
    env.pop("LITELLM_MASTER_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    env.pop("OPENCLAW_AGENT_RUNTIME", None)
    return env


def isolated_openclaw_state(path: Path) -> Path:
    """Refuse the user home directory and the default OpenClaw state dir."""

    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    if resolved == home or resolved == home / ".openclaw" or resolved == home / ".openbot" or resolved == home / ".dsh":
        raise ValueError("OPENCLAW_STATE_DIR must be an isolated directory")
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError("OPENCLAW_STATE_DIR is not a directory")
    return resolved


def which_openclaw(binary: str | None = None) -> str | None:
    """Return ``OPENCLAW_BIN``, then ``binary``, then the click-install shim.

    The shim is ``OHBE_ROOT/harnesses/installs/openclaw``. It is the bin
    recorded for the pinned tarball.
    """

    candidate = os.environ.get("OPENCLAW_BIN") or binary
    if candidate:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return _installed_executable("openclaw")


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
    if upper in _OPENCLAW_POINTERS or upper in {"DSH_HOME", "LITELLM_MASTER_KEY", "DEEPSEEK_API_KEY"}:
        return True
    if upper.endswith("_API_KEY") or upper.endswith("_TOKEN") or upper.endswith("_SECRET"):
        return True
    return any(marker in upper for marker in _PROVIDER_MARKERS)


__all__ = [
    "agent_exec_argv",
    "benchmark_child_env",
    "isolated_openclaw_state",
    "run_command",
    "which_openclaw",
]
