"""Subprocess command for one WorkBuddy bundled ``codebuddy --print`` turn.

The command is the headless CLI. It does not launch the desktop GUI and it
does not pass ``-y`` or ``--dangerously-skip-permissions``. The prompt is
stdin, so a leading dash cannot become a flag. Provider keys, proxy
variables, and ``CODEBUDDY_BASE_URL`` are removed. State lives in an isolated
directory, not the user home and not ``~/.codebuddy``.
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
_FORBIDDEN_FLAGS = frozenset(
    {
        "-c",
        "-r",
        "-y",
        "--continue",
        "--dangerously-skip-permissions",
        "--fallback-model",
        "--ide",
        "--resume",
        "--serve",
    }
)
_ISOLATED_DIRS = (".codebuddy", ".dsh", ".openbot", ".openclaw", ".workbuddy")


def headless_print_argv(
    binary: str,
    *,
    model_id: str,
    settings: Path,
    mcp_config: Path,
    disallowed_tools: str,
) -> list[str]:
    """One print turn. The task text is not an argument.

    Public CodeBuddy Code docs (``--print``, ``--output-format json``,
    ``--model``, ``--settings``, ``--setting-sources``, ``--strict-mcp-config``,
    ``--disallowedTools``) describe this shape. WorkBuddy 5.7.7 does not
    document a separate ``workbuddy`` exec subcommand.
    """

    if not binary:
        raise ValueError("binary must be the bundled codebuddy executable")
    if not model_id or model_id.startswith("-") or any(ch.isspace() for ch in model_id):
        raise ValueError("model id must be a pool member")
    if disallowed_tools != "WebFetch,WebSearch":
        raise ValueError("network tools must stay disallowed")
    for path in (settings, mcp_config):
        text = str(path)
        if not text or text.startswith("-"):
            raise ValueError("codebuddy path must be a file path")
    argv = [
        binary,
        "--print",
        "--output-format",
        "json",
        "--model",
        model_id,
        "--settings",
        str(settings),
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config),
        "--disallowedTools",
        disallowed_tools,
    ]
    if any(flag in argv for flag in _FORBIDDEN_FLAGS):
        raise ValueError("codebuddy print must not skip permissions or resume a session")
    if argv.count("--print") != 1:
        raise ValueError("codebuddy print must be a single non-interactive turn")
    return argv


def benchmark_child_env(
    parent: Mapping[str, str],
    *,
    state_dir: Path,
    api_key: str,
    model_id: str,
    timeout_seconds: int,
) -> dict[str, str]:
    """Environment for one ``codebuddy`` process. Only the LiteLLM key is added.

    ``CODEBUDDY_*`` values from the parent are dropped, including
    ``CODEBUDDY_BASE_URL`` and ``CODEBUDDY_API_KEY``. The model id is then
    pinned for the main turn and for the lite, reasoning, and sub-agent
    variants. ``CODEBUDDY_MAX_RETRIES=0`` asks the CLI not to repeat a failed
    call. A real binary may clamp that value; CI does not.
    """

    if not api_key or any(ch.isspace() for ch in api_key):
        raise ValueError("LITELLM_API_KEY must be a non-empty token")
    if not model_id or any(ch.isspace() for ch in model_id):
        raise ValueError("model id must be a pool member")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        raise ValueError("timeout_seconds must be a positive integer")
    config_dir = state_dir / ".codebuddy"
    env: dict[str, str] = {}
    for key, value in parent.items():
        if _blocked_env(key):
            continue
        env[key] = value
    timeout_ms = str(timeout_seconds * 1000)
    env["HOME"] = str(state_dir)
    env["CODEBUDDY_CONFIG_DIR"] = str(config_dir)
    env["LITELLM_API_KEY"] = api_key
    env["CODEBUDDY_MODEL"] = model_id
    env["CODEBUDDY_SMALL_FAST_MODEL"] = model_id
    env["CODEBUDDY_BIG_SLOW_MODEL"] = model_id
    env["CODEBUDDY_CODE_SUBAGENT_MODEL"] = model_id
    env["CODEBUDDY_SKIP_BUILTIN_MARKETPLACE"] = "1"
    env["CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
    env["CODEBUDDY_DISABLE_CRON"] = "1"
    env["CODEBUDDY_DISABLE_FORK_SUBAGENT"] = "1"
    env["CODEBUDDY_DISABLE_AUTO_MEMORY"] = "1"
    env["CODEBUDDY_MAX_RETRIES"] = "0"
    env["CODEBUDDY_FIRST_TOKEN_TIMEOUT_MS"] = timeout_ms
    env["CODEBUDDY_STREAM_TIMEOUT_MS"] = timeout_ms
    env.pop("LITELLM_MASTER_KEY", None)
    env.pop("CODEBUDDY_API_KEY", None)
    env.pop("CODEBUDDY_AUTH_TOKEN", None)
    env.pop("CODEBUDDY_BASE_URL", None)
    env.pop("CODEBUDDY_IS_SANDBOX", None)
    return env


def isolated_workbuddy_state(path: Path) -> Path:
    """Refuse the user home and the default CodeBuddy or WorkBuddy config dirs."""

    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    if resolved == home or any(_under(resolved, home / name) for name in _ISOLATED_DIRS):
        raise ValueError("WORKBUDDY state must be an isolated directory")
    resolved.mkdir(parents=True, exist_ok=True)
    if not resolved.is_dir():
        raise ValueError("WORKBUDDY state is not a directory")
    config = resolved / ".codebuddy"
    config.mkdir(parents=True, exist_ok=True)
    return resolved


def which_workbuddy(binary: str | None = None) -> str | None:
    """Return ``WORKBUDDY_BIN``, then ``binary``, when that file is executable."""

    candidate = os.environ.get("WORKBUDDY_BIN") or binary
    if not candidate:
        return None
    path = Path(candidate)
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    return None


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout_seconds: int,
    stdin_text: str,
) -> subprocess.CompletedProcess[str]:
    """Run one print process. The prompt is stdin, not an argv slot."""

    if stdin_text == "":
        raise ValueError("stdin must carry the task text")
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=dict(env),
        input=stdin_text,
        timeout=timeout_seconds + 5,
    )


def _blocked_env(key: str) -> bool:
    if key in _PROXY_ENV:
        return True
    upper = key.upper()
    if upper.startswith("CODEBUDDY_") or upper.startswith("CBC_"):
        return True
    if key == "LITELLM_API_KEY":
        return False
    if upper in {"DEEPSEEK_API_KEY", "LITELLM_MASTER_KEY"}:
        return True
    if upper.endswith("_API_KEY") or upper.endswith("_TOKEN") or upper.endswith("_SECRET"):
        return True
    return any(marker in upper for marker in _PROVIDER_MARKERS)


def _under(path: Path, parent: Path) -> bool:
    return path == parent or path.is_relative_to(parent)


__all__ = [
    "benchmark_child_env",
    "headless_print_argv",
    "isolated_workbuddy_state",
    "run_command",
    "which_workbuddy",
]
