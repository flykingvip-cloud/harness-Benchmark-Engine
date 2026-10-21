"""Subprocess commands for the OpenBot benchmark profile.

`--deliver` is never set. Provider API keys and proxy variables are removed
from the child environment so the process cannot be pointed at a direct provider.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

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


def agent_argv(
    binary: str,
    message: str,
    *,
    timeout_seconds: int,
    local: bool = True,
) -> list[str]:
    """One-shot `openbot agent` without delivery."""

    if not message:
        raise ValueError("message must be non-empty")
    argv = [binary, "agent", "-m", message, "--json", "--timeout", str(timeout_seconds)]
    if local:
        argv.append("--local")
    if "--deliver" in argv:
        raise ValueError("benchmark agent command must not deliver")
    return argv


def onboard_argv(binary: str) -> list[str]:
    """Non-interactive LiteLLM auth. The written profile remains authoritative."""

    return [
        binary,
        "onboard",
        "--non-interactive",
        "--auth-choice",
        "litellm-api-key",
    ]


def docker_agent_argv(
    *,
    image: str,
    network: str,
    state_dir: Path,
    message: str,
    timeout_seconds: int,
    local: bool = True,
) -> list[str]:
    """Docker entry on the OHBE internal network, not `--network none`.

    `network none` would also block the gateway, and it does not constrain a
    host OpenBot process. The container joins the internal network that can
    reach only the LiteLLM proxy.
    """

    if not image or any(ch.isspace() for ch in image):
        raise ValueError("image must be a non-empty reference")
    if network in {"", "none", "host", "bridge"} or any(ch.isspace() for ch in network):
        raise ValueError("docker network must be the OHBE internal network")
    inner = agent_argv("openbot", message, timeout_seconds=timeout_seconds, local=local)
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        network,
        "-e",
        "OPENBOT_STATE_DIR=/state",
        "-e",
        "LITELLM_API_KEY",
        "-v",
        f"{state_dir.resolve()}:/state",
        image,
        *inner,
    ]


def benchmark_child_env(
    parent: Mapping[str, str],
    *,
    state_dir: Path,
    api_key: str,
) -> dict[str, str]:
    """Environment for one OpenBot process."""

    if not api_key or any(ch.isspace() for ch in api_key):
        raise ValueError("LITELLM_API_KEY must be a non-empty token")
    env: dict[str, str] = {}
    for key, value in parent.items():
        if _blocked_env(key):
            continue
        env[key] = value
    env["HOME"] = str(state_dir)
    env["OPENBOT_STATE_DIR"] = str(state_dir)
    env["LITELLM_API_KEY"] = api_key
    env.pop("LITELLM_MASTER_KEY", None)
    return env


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(env),
        timeout=timeout_seconds + 5,
    )


def _blocked_env(key: str) -> bool:
    if key in _PROXY_ENV:
        return True
    if key == "LITELLM_API_KEY":
        return False
    upper = key.upper()
    if upper == "LITELLM_MASTER_KEY":
        return True
    if upper.endswith("_API_KEY") or upper.endswith("_TOKEN") or upper.endswith("_SECRET"):
        return True
    return any(marker in upper for marker in _PROVIDER_MARKERS)


def isolated_state_dir(path: Path) -> Path:
    """Refuse the user home directory and the default OpenBot state dir."""

    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    if resolved == home or resolved == home / ".openbot" or resolved == home / ".openclaw":
        raise ValueError("OPENBOT_STATE_DIR must be an isolated directory")
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError("OPENBOT_STATE_DIR is not a directory")
    return resolved


def which_openbot(binary: str | None = None) -> str | None:
    """Return OPENBOT_BIN, then `binary`, when that file is executable."""

    candidate = os.environ.get("OPENBOT_BIN") or binary
    if not candidate:
        return None
    path = Path(candidate)
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    return None
