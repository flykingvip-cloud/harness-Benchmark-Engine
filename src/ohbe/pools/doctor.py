"""Offline preflight for a private LiteLLM host.

``benchmark doctor`` reads the environment, a pool template, and a live-pilot
manifest. It renders the LiteLLM-only OpenBot profile and prints the next
smoke commands from the checklist. It does not open a socket and it does not
start a harness.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ohbe.adapters.openbot.profile import render_benchmark_profile
from ohbe.core.execution import ExecutionEnvironment
from ohbe.live.context import parse_litellm_base_url
from ohbe.live.spec import LivePilotError, load_live_pilot
from ohbe.pools.template import (
    API_KEY_ENV,
    BASE_URL_ENV,
    PoolTemplate,
    PoolTemplateError,
    load_pool_template,
    repository_root,
)

SMOKE_START = "<!-- ohbe:next-smoke -->"
SMOKE_END = "<!-- /ohbe:next-smoke -->"


class DoctorError(ValueError):
    """The doctor could not read a file. The CLI maps this to exit 2."""


@dataclass(frozen=True)
class Check:
    """One offline check. ``detail`` never includes an API key."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    """Checks plus, when they pass, the profile and the next commands."""

    checks: tuple[Check, ...]
    profile: str | None
    smoke_commands: str | None

    @property
    def ok(self) -> bool:
        return (
            all(check.ok for check in self.checks)
            and self.profile is not None
            and bool(self.smoke_commands)
        )


def extract_smoke_commands(text: str) -> str:
    """Return the bash block marked for the operator to run after doctor."""

    start = text.find(SMOKE_START)
    end = text.find(SMOKE_END)
    if start < 0 or end < 0 or end <= start:
        raise DoctorError("checklist is missing the ohbe:next-smoke block")
    body = text[start + len(SMOKE_START) : end]
    fence = "```bash"
    if fence not in body:
        raise DoctorError("checklist smoke commands must be a bash block")
    inner = body.split(fence, 1)[1]
    commands, separator, _rest = inner.partition("```")
    if not separator or not commands.strip():
        raise DoctorError("checklist smoke commands are empty")
    return commands.strip("\n") + "\n"


def run_doctor(
    *,
    pool_path: str | Path,
    experiment_path: str | Path,
    checklist_path: str | Path,
    environ: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Validate env, pool, and experiment. No network."""

    env = os.environ if environ is None else environ
    pool_file = _existing(pool_path, "model pool")
    experiment_file = _existing(experiment_path, "experiment")
    checklist_file = _existing(checklist_path, "checklist")
    try:
        commands = extract_smoke_commands(checklist_file.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DoctorError(f"could not read checklist: {exc}") from exc

    checks: list[Check] = []
    template = _load_pool(pool_file, checks)
    spec_ok = _load_experiment(experiment_file, template, pool_file, checks)
    secret = str(env.get(API_KEY_ENV, ""))
    base_ok = _check_base_url(env, spec_ok, checks, secret)
    _check_api_key(secret, checks)
    _check_openbot(env.get("OPENBOT_BIN"), checks)
    profile = None
    if all(check.ok for check in checks) and template is not None and spec_ok is not None and base_ok is not None:
        host, port, _base = base_ok
        try:
            profile = render_benchmark_profile(
                pool=template.pool,
                environment=ExecutionEnvironment(
                    egress_policy_version=spec_ok.egress.policy_version,
                    gateway_host=host,
                    gateway_port=port,
                ),
                timeout_seconds=spec_ok.timeout_seconds,
            )
        except (OSError, ValueError) as exc:
            checks.append(Check("profile", False, _redact(str(exc), secret)))
        else:
            if secret and secret in profile:
                checks.append(Check("profile", False, "rendered profile contains the API key"))
                profile = None
            else:
                checks.append(Check("profile", True, "litellm-only"))
    report_commands = commands if profile is not None else None
    return DoctorReport(checks=tuple(checks), profile=profile, smoke_commands=report_commands)


def format_doctor_report(report: DoctorReport) -> str:
    """Text for stdout on success and stderr on failure."""

    lines = [f"{'ok' if check.ok else 'fail'} {check.name} {check.detail}" for check in report.checks]
    if report.ok and report.profile is not None and report.smoke_commands is not None:
        lines.append("--- profile ---")
        lines.append(report.profile.rstrip("\n"))
        lines.append("--- next ---")
        lines.append(report.smoke_commands.rstrip("\n"))
    return "\n".join(lines) + "\n"


def _existing(path: str | Path, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    rooted = repository_root() / candidate
    if rooted.is_file():
        return rooted
    raise DoctorError(f"{label} file not found: {path}")


def _load_pool(path: Path, checks: list[Check]) -> PoolTemplate | None:
    try:
        template = load_pool_template(path)
    except PoolTemplateError as exc:
        checks.append(Check("pool", False, str(exc)))
        return None
    detail = f"pool_id={template.pool.pool_id} models={len(template.pool.models)}"
    if any("REPLACE_ME" in model.model_id for model in template.pool.models):
        detail += " placeholders=REPLACE_ME"
    checks.append(Check("pool", True, detail))
    return template


def _load_experiment(path: Path, template: PoolTemplate | None, pool_file: Path, checks: list[Check]):
    try:
        spec = load_live_pilot(path)
    except LivePilotError as exc:
        checks.append(Check("experiment", False, str(exc)))
        return None
    if spec.pool_file is None:
        checks.append(Check("experiment", False, "experiment must reference models/pools/*.yaml"))
        return None
    expected = _resolve_pool(spec.pool_file, path)
    if expected is None or expected.resolve() != pool_file.resolve():
        checks.append(Check("experiment", False, "experiment pool file does not match --pool"))
        return None
    if template is not None and spec.pool_id != template.pool.pool_id:
        checks.append(Check("experiment", False, "experiment pool id does not match the pool file"))
        return None
    if template is not None and [model.model_id for model in spec.models] != [
        model.model_id for model in template.pool.models
    ]:
        checks.append(Check("experiment", False, "experiment models do not match the pool file"))
        return None
    checks.append(
        Check(
            "experiment",
            True,
            f"{spec.experiment_id} fail_closed=true pool_file={spec.pool_file}",
        )
    )
    return spec


def _resolve_pool(relative: str, experiment_file: Path) -> Path | None:
    cwd_path = Path.cwd() / relative
    if cwd_path.is_file():
        return cwd_path
    repo_path = experiment_file.resolve().parent.parent / relative
    if repo_path.is_file():
        return repo_path
    return None


def _check_base_url(env: Mapping[str, str], spec, checks: list[Check], secret: str):
    raw = str(env.get(BASE_URL_ENV, "")).strip()
    if not raw:
        checks.append(Check("litellm_base_url", False, f"{BASE_URL_ENV} is missing"))
        return None
    try:
        host, port, _base = parse_litellm_base_url(raw)
    except LivePilotError as exc:
        checks.append(Check("litellm_base_url", False, _redact(str(exc), secret)))
        return None
    if spec is not None and (host != spec.egress.gateway_host or port != spec.egress.gateway_port):
        checks.append(
            Check(
                "litellm_base_url",
                False,
                "LITELLM_BASE_URL host and port must match the experiment egress gateway "
                f"({spec.egress.gateway_host}:{spec.egress.gateway_port})",
            )
        )
        return None
    checks.append(Check("litellm_base_url", True, f"host={host} port={port}"))
    return host, port, _base


def _check_api_key(secret: str, checks: list[Check]) -> None:
    if not secret.strip():
        checks.append(Check("litellm_api_key", False, f"{API_KEY_ENV} is missing"))
        return
    if any(character.isspace() for character in secret):
        checks.append(Check("litellm_api_key", False, f"{API_KEY_ENV} must be a non-empty token"))
        return
    checks.append(Check("litellm_api_key", True, "present"))


def _check_openbot(value: object, checks: list[Check]) -> None:
    text = "" if value is None else str(value).strip()
    if not text:
        checks.append(Check("openbot_bin", True, "optional"))
        return
    path = Path(text)
    if path.is_file() and os.access(path, os.X_OK):
        checks.append(Check("openbot_bin", True, "executable"))
        return
    checks.append(Check("openbot_bin", False, "OPENBOT_BIN is not an executable file"))


def _redact(text: str, secret: str) -> str:
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text
