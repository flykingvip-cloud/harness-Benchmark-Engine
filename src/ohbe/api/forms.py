"""Validate and store workbench drafts.

Pool files are rendered here and then checked with ``ohbe.pools.template``.
Harness profiles register catalog preinstalls. An unknown harness cannot be
uploaded. Plan-only names stay out of ``HARNESS_ORDER``. Nothing in this
module reads an API key, opens a socket, or changes that tuple.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ohbe.api.doctor import live_spend_state
from ohbe.api.schemas import (
    Check,
    HarnessCatalogEntry,
    HarnessProfileIndex,
    HarnessProfileList,
    HarnessProfileRequest,
    HarnessProfileSaved,
    HarnessProfileValidation,
    HarnessRefView,
    PoolDraftRequest,
    PoolDraftSaved,
    PoolDraftValidation,
    PoolFileDetail,
    PoolFileList,
    PoolFileModel,
    PoolFileSummary,
)
from ohbe.adapters.deepseek_harness.profile import DSH_GIT_TAG, DSH_PACKAGE
from ohbe.adapters.doubao_work.profile import doubao_profile_parameters
from ohbe.adapters.qwen_work.profile import qwen_profile_parameters
from ohbe.adapters.openclaw.profile import (
    OPENCLAW_EXECUTION_PATH,
    OPENCLAW_GIT_TAG,
    OPENCLAW_PACKAGE,
    OPENCLAW_PLUGIN_BRIDGE,
    OPENCLAW_PRODUCT_LAYER,
    OPENCLAW_PROFILE,
    OPENCLAW_RUNTIME,
)
from ohbe.adapters.workbuddy.profile import (
    WORKBUDDY_BASE_URL_OVERRIDE,
    WORKBUDDY_CLI,
    WORKBUDDY_DESKTOP_INSTALL,
    WORKBUDDY_DSH_MODEL_SOURCE,
    WORKBUDDY_EXECUTION_PATH,
    WORKBUDDY_MODEL_ROUTE,
    WORKBUDDY_PERMISSIONS,
    WORKBUDDY_PLUGIN_BRIDGE,
    WORKBUDDY_PRODUCT_LAYER,
    WORKBUDDY_PROFILE,
)
from ohbe.preinstall import (
    OPENBOT_VERSION_PLACEHOLDER as CATALOG_OPENBOT_VERSION,
    INSPECT_PROFILE_VERSION as CATALOG_INSPECT_VERSION,
    PreinstallVersionError,
    accept_version,
    catalog_live_ids,
    describe_catalog,
    entry_for,
)
from ohbe.api.validate import ValidationRejected
from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.harness import HarnessConfig
from ohbe.pools.template import (
    API_KEY_ENV,
    BASE_URL_ENV,
    POOL_KIND,
    PoolTemplate,
    PoolTemplateError,
    load_pool_template,
    parse_pool_template,
)

# Kept equal to ohbe.live.spec.HARNESS_ORDER. The preinstall catalog may list
# more names. Saving a profile does not extend this tuple, and this module
# does not import the live runner.
LIVE_HARNESS_ORDER = ("openbot", "inspect_react")
if catalog_live_ids() != LIVE_HARNESS_ORDER:
    raise RuntimeError("preinstall adapters drifted from LIVE_HARNESS_ORDER")
OPENBOT_VERSION_PLACEHOLDER = CATALOG_OPENBOT_VERSION
INSPECT_PROFILE_VERSION = CATALOG_INSPECT_VERSION
HARNESS_PROFILE_KIND = "ohbe.harness_profile"
PROFILE_NOTE = (
    "The harness library lists registered installs. "
    "Preinstall and scan choose a catalog entry and can report a detected version. "
    "The catalog is not a free-form upload of an unknown harness. "
    "A live run still uses HARNESS_ORDER in ohbe.live.spec and does not grow because a file was saved. "
    "OpenClaw, DeepSeek Harness, and WorkBuddy have opt-in adapters and stay out of that order. "
    "WorkBuddy's adapter is the bundled CodeBuddy CLI print path, not the desktop GUI. "
    "千问办公 and 豆包工作 have closed adapters that refuse to run. They are not peer harnesses. "
    "Entries without an Adapter stay plan-only. "
    "Live spend stays locked."
)

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_POOL_FILE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,80}\.yaml$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PROFILE_KEYS = frozenset(
    {
        "adapter",
        "dev_only",
        "harness_id",
        "kind",
        "live_run",
        "name",
        "parameters",
        "profile_id",
        "schema_version",
        "status",
        "version",
    }
)


class HarnessProfileError(ValueError):
    """The harness profile cannot be registered or reloaded."""


@dataclass(frozen=True)
class BuiltProfile:
    profile_id: str
    adapter: str
    status: str
    dev_only: bool
    harness_id: str
    name: str
    version: str
    parameters: dict[str, Any]
    registration: str
    config: HarnessConfig
    text: str
    destination: str

    @property
    def ref(self) -> HarnessRefView:
        ref = self.config.to_ref()
        return HarnessRefView(
            harness_id=ref.harness_id,
            name=ref.name,
            version=ref.version,
            config_hash=ref.config_hash,
        )


def profile_live_decision(adapter: str, status: str) -> str:
    """Why a stored profile cannot start a live run.

    Catalog registration does not extend ``HARNESS_ORDER``. OpenClaw has an
    opt-in embedded-agent adapter, DeepSeek Harness has an opt-in adapter, and
    WorkBuddy has an opt-in bundled-CLI adapter. All three stay plan-only.
    The live tuple changes only on purpose.
    """

    entry = entry_for(adapter)
    if entry is None or status not in {"registered", "plan"}:
        return "draft adapter profiles are rejected for live run"
    if entry.closed_reason:
        return "a closed adapter does not start a live run or extend HARNESS_ORDER"
    if entry.registration != "adapter" or status != "registered":
        return "a plan-only preinstall does not start a live run or extend HARNESS_ORDER"
    return "a registered profile does not start a live run or extend HARNESS_ORDER"


def validate_pool_draft(body: PoolDraftRequest, environ: Mapping[str, str]) -> PoolDraftValidation:
    """Run the pool template rules. This does not write a file."""

    return _assess_pool(body, environ)


def save_pool_draft(root: Path, body: PoolDraftRequest, environ: Mapping[str, str]) -> PoolDraftSaved:
    """Validate, then write ``models/pools/<pool_id>.yaml``. Example files stay put."""

    assessed = _assess_pool(body, environ)
    if not assessed.ok or assessed.destination is None or assessed.content_hash is None or assessed.pool_id is None:
        failed = next((item.summary for item in assessed.checks if not item.passed), "model pool draft was rejected")
        raise ValidationRejected(422, "pool_template_rejected", failed)
    path = _pool_destination(root, body.pool_id)
    try:
        text = _render_pool(body)
        parse_pool_template(text, assessed.destination)
    except PoolTemplateError as exc:
        raise ValidationRejected(422, "pool_template_rejected", _public(str(exc))) from exc
    _atomic_write(path, text, "pool_template_rejected")
    loaded = load_pool_template(path)
    return PoolDraftSaved(
        live_spend=live_spend_state(environ),
        checks=assessed.checks,
        pool_id=loaded.pool.pool_id,
        content_hash=loaded.pool.content_hash,
        source=_relative(path, root),
    )


def list_pool_files(root: Path, environ: Mapping[str, str]) -> PoolFileList:
    """List YAML files under ``models/pools``. Invalid files are not echoed."""

    directory = _pool_directory(root)
    pools: list[PoolFileSummary] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            if not path.is_file() or path.is_symlink():
                continue
            if not _POOL_FILE.fullmatch(path.name) or ".." in path.name:
                continue
            pools.append(_summarize_pool_file(root, path))
    return PoolFileList(pools=pools, live_spend=live_spend_state(environ))


def read_pool_file(root: Path, filename: str, environ: Mapping[str, str]) -> PoolFileDetail:
    """Return one pool file as structured fields. The response does not spend."""

    path = _named_pool_file(root, filename)
    if not path.is_file():
        raise ValidationRejected(404, "pool_draft_not_found", f"Unknown pool file: {filename}")
    summary = _summarize_pool_file(root, path)
    return PoolFileDetail(
        filename=summary.filename,
        pool_id=summary.pool_id,
        source=summary.source,
        read_only=summary.read_only,
        ok=summary.ok,
        note=summary.note,
        content_hash=summary.content_hash,
        models=summary.models,
        live_spend=live_spend_state(environ),
    )


def validate_harness_profile(
    body: HarnessProfileRequest,
    environ: Mapping[str, str],
) -> HarnessProfileValidation:
    """Check a config profile. This does not write a file or start a run."""

    return _assess_profile(body, environ)


def save_harness_profile(
    root: Path,
    body: HarnessProfileRequest,
    environ: Mapping[str, str],
    *,
    gated: bool = False,
) -> HarnessProfileSaved:
    """Validate, then write ``harnesses/profiles/<profile_id>.yaml``."""

    assessed = _assess_profile(body, environ, gated=gated)
    if not assessed.ok or assessed.profile_id is None or assessed.ref is None or assessed.destination is None:
        failed = next((item.summary for item in assessed.checks if not item.passed), "harness profile was rejected")
        raise ValidationRejected(422, "harness_profile_rejected", failed)
    try:
        built = _build_profile(body, environ, gated=gated)
    except HarnessProfileError as exc:
        raise ValidationRejected(422, "harness_profile_rejected", _public(str(exc))) from exc
    path = _profile_destination(root, built.profile_id)
    _atomic_write(path, built.text, "harness_profile_rejected")
    loaded = _read_profile_path(path)
    return HarnessProfileSaved(
        live_spend=live_spend_state(environ),
        checks=assessed.checks,
        profile_id=loaded.profile_id,
        adapter=loaded.adapter,
        status=_status(loaded.status),
        registration=_registration(loaded.registration),
        dev_only=loaded.dev_only,
        harness_id=loaded.harness_id,
        name=loaded.name,
        version=loaded.version,
        parameters=loaded.parameters,
        config_hash=loaded.config.config_hash,
        ref=loaded.ref,
        source=_relative(path, root),
    )


def list_harness_profiles(root: Path, environ: Mapping[str, str]) -> HarnessProfileList:
    """List registered profiles. ``harness_order`` is the live pair, not the files."""

    directory = _profile_directory(root)
    profiles: list[HarnessProfileIndex] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            if not path.is_file() or path.is_symlink() or not _SLUG.fullmatch(path.stem):
                continue
            profiles.append(_index_profile(root, path))
    return HarnessProfileList(
        harness_order=list(LIVE_HARNESS_ORDER),
        catalog=_catalog_entries(environ),
        profiles=profiles,
        note=PROFILE_NOTE,
        live_spend=live_spend_state(environ),
    )


def read_harness_profile(root: Path, profile_id: str, environ: Mapping[str, str]) -> HarnessProfileSaved:
    """Return one profile and its ``HarnessRef``. Live run stays rejected."""

    if not _SLUG.fullmatch(profile_id):
        raise ValidationRejected(404, "profile_not_found", f"Unknown harness profile: {profile_id}")
    path = _profile_destination(root, profile_id)
    if not path.is_file():
        raise ValidationRejected(404, "profile_not_found", f"Unknown harness profile: {profile_id}")
    try:
        loaded = _read_profile_path(path)
    except HarnessProfileError as exc:
        raise ValidationRejected(422, "harness_profile_rejected", _public(str(exc))) from exc
    return HarnessProfileSaved(
        live_spend=live_spend_state(environ),
        checks=_profile_checks(loaded),
        profile_id=loaded.profile_id,
        adapter=loaded.adapter,
        status=_status(loaded.status),
        registration=_registration(loaded.registration),
        dev_only=loaded.dev_only,
        harness_id=loaded.harness_id,
        name=loaded.name,
        version=loaded.version,
        parameters=loaded.parameters,
        config_hash=loaded.config.config_hash,
        ref=loaded.ref,
        source=_relative(path, root),
    )


def _assess_pool(body: PoolDraftRequest, environ: Mapping[str, str]) -> PoolDraftValidation:
    live = live_spend_state(environ)
    try:
        destination = f"models/pools/{_pool_slug(body.pool_id)}.yaml"
        text = _render_pool(body)
        template = parse_pool_template(text, destination)
    except PoolTemplateError as exc:
        failed = Check(id=_pool_check_id(str(exc)), passed=False, summary=_public(str(exc)))
        return PoolDraftValidation(ok=False, live_spend=live, checks=[failed, *_lock_checks()])
    return PoolDraftValidation(
        ok=True,
        live_spend=live,
        checks=[*_pool_ok_checks(template), *_lock_checks()],
        pool_id=template.pool.pool_id,
        content_hash=template.pool.content_hash,
        destination=destination,
    )


def _assess_profile(
    body: HarnessProfileRequest,
    environ: Mapping[str, str],
    *,
    gated: bool = False,
) -> HarnessProfileValidation:
    live = live_spend_state(environ)
    try:
        built = _build_profile(body, environ, gated=gated)
    except HarnessProfileError as exc:
        failed = Check(id=_profile_check_id(str(exc)), passed=False, summary=_public(str(exc)))
        return HarnessProfileValidation(ok=False, live_spend=live, checks=[failed, *_lock_checks()])
    return HarnessProfileValidation(
        ok=True,
        live_spend=live,
        checks=_profile_checks(built),
        profile_id=built.profile_id,
        adapter=built.adapter,
        status=_status(built.status),
        registration=_registration(built.registration),
        dev_only=built.dev_only,
        harness_id=built.harness_id,
        name=built.name,
        version=built.version,
        parameters=built.parameters,
        config_hash=built.config.config_hash,
        ref=built.ref,
        destination=built.destination,
    )


def _pool_ok_checks(template: PoolTemplate) -> list[Check]:
    hosts = ", ".join(template.constraints.forbid_hosts)
    return [
        Check(id="pool_fail_closed", passed=True, summary="constraints.fail_closed is true"),
        Check(
            id="pool_forbid_hosts",
            passed=True,
            summary=f"constraints forbid {hosts}",
            evidence=list(template.constraints.forbid_hosts),
        ),
        Check(
            id="pool_gateway_env",
            passed=True,
            summary="gateway fields are environment names only",
            evidence=[template.gateway.base_url_env, template.gateway.api_key_env],
        ),
        Check(id="pool_local_provider", passed=True, summary="providers are vllm or ollama"),
        Check(id="pool_no_secret", passed=True, summary="the pool file has no secret"),
        Check(id="pool_no_url", passed=True, summary="the pool file has no URL"),
    ]


def _profile_checks(built: BuiltProfile) -> list[Check]:
    if built.registration == "plan":
        adapter = Check(
            id="harness_plan_only",
            passed=True,
            summary="catalog entry is a plan-only preinstall and does not enter HARNESS_ORDER",
            evidence=[built.adapter],
        )
    else:
        adapter = Check(
            id="harness_existing_adapter",
            passed=True,
            summary="profile adds a preinstalled adapter and does not extend HARNESS_ORDER",
            evidence=list(LIVE_HARNESS_ORDER),
        )
    return [
        adapter,
        Check(
            id="harness_order_unchanged",
            passed=True,
            summary="live harness order stays openbot, inspect_react",
            evidence=list(LIVE_HARNESS_ORDER),
        ),
        Check(id="harness_version", passed=True, summary="version matches the adapter rule"),
        Check(id="harness_no_secret", passed=True, summary="the profile has no secret and no URL"),
        *_lock_checks(),
    ]


def _lock_checks() -> list[Check]:
    return [
        Check(
            id="live_spend_not_unlocked",
            passed=True,
            summary="this route does not unlock live spend and does not contact a gateway",
        ),
        Check(id="spend_not_executed", passed=True, summary="spend was not executed"),
    ]


def _render_pool(body: PoolDraftRequest) -> str:
    pool_id = _pool_slug(body.pool_id)
    for model in body.models:
        _plain(model.model_id, "model_id")
        _plain(model.name, "name")
        _plain(model.revision, "revision")
        _plain(model.litellm_model, "litellm_model")
    lines = [
        f"schema_version: {json.dumps(SCHEMA_VERSION)}",
        f"kind: {POOL_KIND}",
        f"pool_id: {pool_id}",
        "gateway:",
        f"  base_url_env: {BASE_URL_ENV}",
        f"  api_key_env: {API_KEY_ENV}",
        "constraints:",
        "  fail_closed: true",
        "  forbid_hosts:",
        "    - api.openai.com",
        "    - api.anthropic.com",
        "models:",
    ]
    for model in body.models:
        lines.extend(
            [
                f"  - model_id: {_scalar(model.model_id)}",
                f"    provider: {model.provider}",
                f"    name: {_scalar(model.name)}",
                f"    revision: {_scalar(model.revision)}",
                "    metadata:",
                f"      litellm_model: {_scalar(model.litellm_model)}",
            ]
        )
    return "\n".join(lines) + "\n"


def _pool_slug(pool_id: str) -> str:
    if not isinstance(pool_id, str) or _SLUG.fullmatch(pool_id) is None:
        raise PoolTemplateError("pool_id must be a lowercase slug")
    return pool_id


def _require_profile_rules(
    body: HarnessProfileRequest,
    environ: Mapping[str, str],
    *,
    strict_version: bool,
) -> None:
    """Reject a profile that would smuggle a secret, a URL, or an unknown harness."""

    if entry_for(body.adapter) is None:
        raise HarnessProfileError("only catalog harnesses can be added")
    if body.draft_adapter_name not in (None, ""):
        raise HarnessProfileError("a third harness cannot be uploaded")
    if _SLUG.fullmatch(body.profile_id) is None:
        raise HarnessProfileError("profile_id must be a lowercase slug")
    _plain(body.name, "name", HarnessProfileError)
    _plain(body.version, "version", HarnessProfileError)
    entry = entry_for(body.adapter)
    assert entry is not None
    try:
        accept_version(entry, body.version, environ, strict=strict_version)
    except PreinstallVersionError as exc:
        raise HarnessProfileError(str(exc)) from exc


def _adapter_document(body: HarnessProfileRequest) -> tuple[str, str, bool, str, dict[str, Any]]:
    entry = entry_for(body.adapter)
    if entry is None:
        raise HarnessProfileError("only catalog harnesses can be added")
    if body.adapter == "openbot":
        return (
            "openbot",
            "registered",
            False,
            "adapter",
            {
                "base_url_env": BASE_URL_ENV,
                "channels": [],
                "deliver": False,
                "elevated": False,
                "local": True,
                "profile": "benchmark",
                "provider": "litellm",
                "timeout_seconds": body.timeout_seconds,
            },
        )
    if body.adapter == "inspect_react":
        return (
            "inspect-react",
            "registered",
            False,
            "adapter",
            {
                "base_url_env": BASE_URL_ENV,
                "provider": "litellm-proxy",
                "sandbox": "docker",
                "scorer": "includes",
                "solver": "react",
                "timeout_seconds": body.timeout_seconds,
            },
        )
    if body.adapter == "deepseek_harness":
        return (
            "deepseek-harness",
            "plan",
            False,
            "plan",
            {
                "base_url_env": BASE_URL_ENV,
                "channel": "npm",
                "git-tag": DSH_GIT_TAG,
                "package": DSH_PACKAGE,
                "profile": "headless",
                "provider": "litellm",
                "runner": "dsh",
                "timeout_seconds": body.timeout_seconds,
            },
        )
    if body.adapter == "openclaw":
        return (
            "openclaw",
            "plan",
            False,
            "plan",
            {
                "base_url_env": BASE_URL_ENV,
                "channel": "npm",
                "execution-path": OPENCLAW_EXECUTION_PATH,
                "git-tag": OPENCLAW_GIT_TAG,
                "package": OPENCLAW_PACKAGE,
                "plugin-bridge": OPENCLAW_PLUGIN_BRIDGE,
                "product-layer": OPENCLAW_PRODUCT_LAYER,
                "profile": OPENCLAW_PROFILE,
                "provider": "litellm",
                "runtime": OPENCLAW_RUNTIME,
                "runner": "openclaw",
                "timeout_seconds": body.timeout_seconds,
            },
        )
    if body.adapter == "workbuddy":
        return (
            "workbuddy",
            "plan",
            False,
            "plan",
            {
                "base_url_env": BASE_URL_ENV,
                "base-url-override": WORKBUDDY_BASE_URL_OVERRIDE,
                "channel": "desktop",
                "cli": WORKBUDDY_CLI,
                "desktop-install": WORKBUDDY_DESKTOP_INSTALL,
                "dsh-model-source": WORKBUDDY_DSH_MODEL_SOURCE,
                "execution-path": WORKBUDDY_EXECUTION_PATH,
                "model-route": WORKBUDDY_MODEL_ROUTE,
                "permissions": WORKBUDDY_PERMISSIONS,
                "plugin-bridge": WORKBUDDY_PLUGIN_BRIDGE,
                "product-layer": WORKBUDDY_PRODUCT_LAYER,
                "profile": WORKBUDDY_PROFILE,
                "provider": "litellm",
                "runner": WORKBUDDY_CLI,
                "timeout_seconds": body.timeout_seconds,
            },
        )
    if body.adapter == "qwen_work":
        return (
            "qwen-work",
            "plan",
            False,
            "plan",
            qwen_profile_parameters(timeout_seconds=body.timeout_seconds),
        )
    if body.adapter == "doubao_work":
        return (
            "doubao-work",
            "plan",
            False,
            "plan",
            doubao_profile_parameters(timeout_seconds=body.timeout_seconds),
        )
    if entry.registration != "plan":
        raise HarnessProfileError("only catalog harnesses can be added")
    return (
        entry.harness_id,
        "plan",
        False,
        "plan",
        {
            "base_url_env": BASE_URL_ENV,
            "channel": entry.install_channel,
            "profile": "plan",
            "provider": "litellm",
            "role": "plan",
            "runner": entry.harness_id,
            "timeout_seconds": body.timeout_seconds,
        },
    )


def _build_profile(
    body: HarnessProfileRequest,
    environ: Mapping[str, str],
    *,
    gated: bool = False,
) -> BuiltProfile:
    entry = entry_for(body.adapter)
    strict_version = not (gated and entry is not None and entry.version_kind == "package")
    _require_profile_rules(body, environ, strict_version=strict_version)
    harness_id, status, dev_only, registration, parameters = _adapter_document(body)
    config = HarnessConfig.build(
        harness_id=harness_id,
        name=body.name,
        version=body.version,
        parameters=parameters,
    )
    text = _render_profile(
        profile_id=body.profile_id,
        adapter=body.adapter,
        status=status,
        dev_only=dev_only,
        harness_id=harness_id,
        name=body.name,
        version=body.version,
        parameters=parameters,
    )
    parsed = _parse_profile_text(text, f"harnesses/profiles/{body.profile_id}.yaml")
    if parsed.config.config_hash != config.config_hash or parsed.registration != registration:
        raise HarnessProfileError("harness profile hash did not round-trip")
    return parsed


def _render_profile(
    *,
    profile_id: str,
    adapter: str,
    status: str,
    dev_only: bool,
    harness_id: str,
    name: str,
    version: str,
    parameters: Mapping[str, Any],
) -> str:
    lines = [
        f"schema_version: {json.dumps(SCHEMA_VERSION)}",
        f"kind: {HARNESS_PROFILE_KIND}",
        f"profile_id: {profile_id}",
        f"adapter: {adapter}",
        f"status: {status}",
        f"dev_only: {str(dev_only).lower()}",
        "live_run: rejected",
        f"harness_id: {harness_id}",
        f"name: {_scalar(name)}",
        f"version: {_scalar(version)}",
        "parameters:",
    ]
    for key in sorted(parameters):
        lines.extend(_parameter_lines(key, parameters[key]))
    return "\n".join(lines) + "\n"


def _parameter_lines(key: str, value: Any) -> list[str]:
    if not _SLUG.fullmatch(key) and key not in {"base_url_env", "timeout_seconds", "live_run"}:
        raise HarnessProfileError("harness parameter name was rejected")
    if isinstance(value, bool):
        return [f"  {key}: {str(value).lower()}"]
    if isinstance(value, int) and not isinstance(value, bool):
        return [f"  {key}: {value}"]
    if isinstance(value, str):
        _plain(value, key, HarnessProfileError)
        return [f"  {key}: {_scalar(value)}"]
    if isinstance(value, list) and not value:
        return [f"  {key}: []"]
    raise HarnessProfileError("harness parameter value was rejected")


def _parse_profile_text(text: str, source: str) -> BuiltProfile:
    if text.startswith("\ufeff") or "\r" in text:
        raise HarnessProfileError("harness profile file must use LF newlines")
    if "sk-" in text.lower() or "://" in text:
        raise HarnessProfileError("harness profile must not contain a secret or a URL")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HarnessProfileError("harness profile file is not YAML") from exc
    if not isinstance(loaded, dict):
        raise HarnessProfileError("harness profile file must be a YAML mapping")
    extra = set(loaded) - _PROFILE_KEYS
    if extra:
        raise HarnessProfileError("harness profile has unknown fields")
    if set(loaded) != _PROFILE_KEYS:
        raise HarnessProfileError("harness profile is missing required fields")
    if loaded.get("schema_version") != SCHEMA_VERSION:
        raise HarnessProfileError(f"schema_version must be {SCHEMA_VERSION}")
    if loaded.get("kind") != HARNESS_PROFILE_KIND:
        raise HarnessProfileError(f"kind must be {HARNESS_PROFILE_KIND}")
    if loaded.get("live_run") != "rejected":
        raise HarnessProfileError("live_run must stay rejected")
    adapter = loaded.get("adapter")
    if not isinstance(adapter, str) or entry_for(adapter) is None:
        raise HarnessProfileError("only catalog harnesses can be added")
    profile_id = loaded.get("profile_id")
    harness_id = loaded.get("harness_id")
    name = loaded.get("name")
    version = loaded.get("version")
    status = loaded.get("status")
    dev_only = loaded.get("dev_only")
    parameters = loaded.get("parameters")
    if not all(isinstance(item, str) for item in (profile_id, harness_id, name, version, status)):
        raise HarnessProfileError("harness profile fields must be strings")
    if not isinstance(dev_only, bool) or not isinstance(parameters, dict):
        raise HarnessProfileError("harness profile fields have the wrong type")
    assert isinstance(profile_id, str)
    assert isinstance(harness_id, str)
    assert isinstance(name, str)
    assert isinstance(version, str)
    request = HarnessProfileRequest(
        profile_id=profile_id,
        adapter=adapter,
        name=name,
        version=version,
        timeout_seconds=_timeout(parameters),
    )
    _require_profile_rules(request, {}, strict_version=False)
    rebuilt = _rebuild_without_parse(request)
    if rebuilt.text != text and yaml.safe_load(rebuilt.text) != loaded:
        raise HarnessProfileError("harness profile does not match the registered document")
    if (
        rebuilt.harness_id != harness_id
        or rebuilt.status != status
        or rebuilt.dev_only is not dev_only
        or rebuilt.parameters != parameters
    ):
        raise HarnessProfileError("harness profile does not match the adapter rules")
    return BuiltProfile(
        profile_id=rebuilt.profile_id,
        adapter=rebuilt.adapter,
        status=rebuilt.status,
        dev_only=rebuilt.dev_only,
        harness_id=rebuilt.harness_id,
        name=rebuilt.name,
        version=rebuilt.version,
        parameters=rebuilt.parameters,
        registration=rebuilt.registration,
        config=rebuilt.config,
        text=text,
        destination=source,
    )


def _rebuild_without_parse(body: HarnessProfileRequest) -> BuiltProfile:
    """Build a profile document without calling the YAML parser again."""

    harness_id, status, dev_only, registration, parameters = _adapter_document(body)
    config = HarnessConfig.build(
        harness_id=harness_id,
        name=body.name,
        version=body.version,
        parameters=parameters,
    )
    text = _render_profile(
        profile_id=body.profile_id,
        adapter=body.adapter,
        status=status,
        dev_only=dev_only,
        harness_id=harness_id,
        name=body.name,
        version=body.version,
        parameters=parameters,
    )
    return BuiltProfile(
        profile_id=body.profile_id,
        adapter=body.adapter,
        status=status,
        dev_only=dev_only,
        harness_id=harness_id,
        name=body.name,
        version=body.version,
        parameters=parameters,
        registration=registration,
        config=config,
        text=text,
        destination=f"harnesses/profiles/{body.profile_id}.yaml",
    )


def _timeout(parameters: Mapping[str, Any]) -> int:
    value = parameters.get("timeout_seconds")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86400:
        raise HarnessProfileError("timeout_seconds must be a positive integer")
    return value


def _read_profile_path(path: Path) -> BuiltProfile:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HarnessProfileError("could not read harness profile") from exc
    if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
        raise HarnessProfileError("harness profile file must use LF newlines")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HarnessProfileError("harness profile file is not valid UTF-8") from exc
    return _parse_profile_text(text, path.as_posix())


def _summarize_pool_file(root: Path, path: Path) -> PoolFileSummary:
    read_only = path.name.endswith(".example.yaml")
    source = _relative(path, root)
    try:
        template = load_pool_template(path)
    except PoolTemplateError as exc:
        return PoolFileSummary(
            filename=path.name,
            pool_id="",
            source=source,
            read_only=read_only,
            ok=False,
            note=_public(str(exc)),
        )
    note = "fail_closed pool file. Gateway settings are environment names only."
    if read_only:
        note = (
            "Example file. Saving writes models/pools/"
            f"{template.pool.pool_id}.yaml and leaves this example unchanged."
        )
    return PoolFileSummary(
        filename=path.name,
        pool_id=template.pool.pool_id,
        source=source,
        read_only=read_only,
        ok=True,
        note=note,
        content_hash=template.pool.content_hash,
        models=[
            PoolFileModel(
                model_id=model.model_id,
                provider=model.provider,
                name=model.name,
                revision=model.revision or "",
                litellm_model=str(model.metadata.get("litellm_model") or ""),
            )
            for model in template.pool.models
        ],
    )


def _index_profile(root: Path, path: Path) -> HarnessProfileIndex:
    source = _relative(path, root)
    try:
        loaded = _read_profile_path(path)
    except HarnessProfileError as exc:
        return HarnessProfileIndex(
            profile_id=path.stem,
            adapter="unregistered",
            status="invalid",
            dev_only=True,
            harness_id="",
            name="",
            version="",
            source=source,
            ok=False,
            note=_public(str(exc)),
        )
    return HarnessProfileIndex(
        profile_id=loaded.profile_id,
        adapter=loaded.adapter,
        status=_status(loaded.status) if loaded.status != "invalid" else "invalid",
        registration=_registration(loaded.registration),
        dev_only=loaded.dev_only,
        harness_id=loaded.harness_id,
        name=loaded.name,
        version=loaded.version,
        config_hash=loaded.config.config_hash,
        source=source,
        ok=True,
        note=profile_live_decision(loaded.adapter, loaded.status),
        parameters=loaded.parameters,
        ref=loaded.ref,
    )


def _named_pool_file(root: Path, filename: str) -> Path:
    if not isinstance(filename, str) or _POOL_FILE.fullmatch(filename) is None or ".." in filename:
        raise ValidationRejected(404, "pool_draft_not_found", "Unknown pool file")
    directory = _pool_directory(root)
    path = (directory / filename).resolve()
    if path.parent != directory:
        raise ValidationRejected(404, "pool_draft_not_found", "Unknown pool file")
    return path


def _pool_destination(root: Path, pool_id: str) -> Path:
    slug = _pool_slug(pool_id)
    directory = _pool_directory(root)
    path = (directory / f"{slug}.yaml").resolve()
    if path.parent != directory or path.name.endswith(".example.yaml") or path.is_symlink():
        raise ValidationRejected(422, "pool_template_rejected", "pool file path was rejected")
    return path


def _profile_destination(root: Path, profile_id: str) -> Path:
    if _SLUG.fullmatch(profile_id) is None:
        raise ValidationRejected(422, "harness_profile_rejected", "profile_id must be a lowercase slug")
    directory = _profile_directory(root)
    path = (directory / f"{profile_id}.yaml").resolve()
    if path.parent != directory or path.is_symlink():
        raise ValidationRejected(422, "harness_profile_rejected", "harness profile path was rejected")
    return path


def _pool_directory(root: Path) -> Path:
    return _contained(root, root / "models" / "pools", "pool_template_rejected")


def _profile_directory(root: Path) -> Path:
    return _contained(root, root / "harnesses" / "profiles", "harness_profile_rejected")


def _contained(root: Path, path: Path, code: str) -> Path:
    root_resolved = root.resolve()
    resolved = path.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValidationRejected(400, code, "path escapes the checkout")
    return resolved


def _atomic_write(path: Path, text: str, code: str) -> None:
    if "\r" in text or "sk-" in text.lower() or "://" in text:
        raise ValidationRejected(422, code, "draft was rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _plain(value: str, field: str, error: type[Exception] = PoolTemplateError) -> None:
    if any(ord(character) < 32 for character in value):
        raise error(f"{field} must not contain control characters")
    lowered = value.lower()
    if "sk-" in lowered or "api_key" in lowered:
        raise error(f"{field} must not contain a secret")
    if "://" in value:
        raise error(f"{field} must not contain a URL")
    if "openai.com" in lowered or "anthropic.com" in lowered:
        raise error(f"{field} must not name an OpenAI or Anthropic host")


def _public(message: str) -> str:
    lowered = message.lower()
    if "sk-" in lowered or "://" in message or "api_key" in lowered:
        return "draft was rejected"
    return message


def _pool_check_id(message: str) -> str:
    lowered = message.lower()
    if "secret" in lowered or "sk-" in lowered:
        return "pool_no_secret"
    if "url" in lowered or "://" in lowered:
        return "pool_no_url"
    if "openai" in lowered or "anthropic" in lowered or "forbid" in lowered:
        return "pool_forbid_hosts"
    if "fail-closed" in lowered or "fail_closed" in lowered:
        return "pool_fail_closed"
    if "base_url_env" in lowered or "api_key_env" in lowered or "gateway" in lowered:
        return "pool_gateway_env"
    if "vllm" in lowered or "ollama" in lowered or "provider" in lowered or "litellm_model" in lowered:
        return "pool_local_provider"
    return "pool_model_shape"


def _profile_check_id(message: str) -> str:
    lowered = message.lower()
    if "secret" in lowered or "url" in lowered:
        return "harness_no_secret"
    if "version" in lowered:
        return "harness_version"
    if "draft" in lowered or "hARNESS_ORDER".lower() in lowered or "openbot" in lowered or "inspect" in lowered:
        return "harness_draft_only"
    return "harness_profile_rejected"


def _catalog_entries(environ: Mapping[str, str]) -> list[HarnessCatalogEntry]:
    return [
        HarnessCatalogEntry(
            adapter=row.entry.adapter,
            harness_id=row.entry.harness_id,
            name=row.entry.name,
            profile_id=row.entry.profile_id,
            version=row.entry.version,
            detected_version=row.detected_version,
            version_state=row.state,
            version_kind=row.entry.version_kind,
            registration=row.entry.registration,
            in_live_order=row.entry.in_live_order,
            package=row.entry.package,
            install_channel=row.entry.install_channel,
            install_url=row.entry.install_url or None,
            install_urls=list(row.entry.install_urls),
            runnable=row.entry.runnable,
            closed_reason=row.entry.closed_reason,
        )
        for row in describe_catalog(environ)
    ]


def _registration(value: str) -> Any:
    if value == "adapter":
        return "adapter"
    if value == "plan":
        return "plan"
    raise HarnessProfileError("harness registration was rejected")


def _status(value: str) -> Any:
    if value == "registered":
        return "registered"
    if value == "draft":
        return "draft"
    if value == "plan":
        return "plan"
    raise HarnessProfileError("harness profile status was rejected")
