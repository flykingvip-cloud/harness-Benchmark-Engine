"""Harness library and model library actions.

Preinstall resolves a pinned spec or artifact and can install it locally.
The profile version is that concrete build. ``latest`` is not written.
A harness remote target requires a host plus an SSH key or password. The
secret stays on the request or in a named environment variable and is not
written to pool YAML, catalog YAML, or a profile. After auth, remote
inventory and remote install report ``inventory_unsupported`` until a safe
executor exists. A local npm pin installs that registry tarball with
``npm install --ignore-scripts`` and admits the profile when the package
version and bin match. A desktop pin downloads the catalog file for this OS
and does not launch it. A desktop row with no versioned URL stays
``installer_unpinned``. Denied hosts fail closed. Neither path extends
``HARNESS_ORDER`` or places a model call.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ohbe.api.doctor import live_spend_state
from ohbe.api.forms import LIVE_HARNESS_ORDER, save_harness_profile
from ohbe.api.intake import IntakeService, save_catalog_entry
from ohbe.api.intake_fields import cloud_gateway_issues, credential_fingerprint, local_identity_issues
from ohbe.api.schemas import (
    CatalogEntryRequest,
    HarnessCheckRequest,
    HarnessInstallRequest,
    HarnessProfileRequest,
    HarnessScanRequest,
    LibraryActionResult,
    LibraryTarget,
    ModelAdmitRequest,
    ModelScanRequest,
    ProbeRequest,
)
from ohbe.api.validate import ValidationRejected
import ohbe.library as gates
from ohbe.library import LibraryGateError
from ohbe.preinstall import admitted_version, entry_for
import ohbe.remote_auth as remote_auth
from ohbe.remote_auth import RemoteAuthError

_AUTH_REASONS = frozenset({"auth_required", "auth_failed", "auth_unavailable", "rejected_host", "unreachable"})


def probe_remote(body: ProbeRequest, environ: Mapping[str, str]) -> LibraryActionResult:
    if _auth_fields_present(body):
        network, reachable, reason = _authenticate_remote(body.host, body.port, body, environ)
    else:
        network, reachable, reason = _assess_target("remote", body.host, body.port)
    return _result(
        environ,
        ok=reason == "ready",
        network=network,
        target="remote",
        reachable=reachable,
        reason="ready" if reason == "ready" else reason,
    )


def check_harness(body: HarnessCheckRequest, environ: Mapping[str, str]) -> LibraryActionResult:
    gated = _gated(body, environ, require_auth=True)
    if gated is not None:
        return gated
    entry = entry_for(body.adapter)
    assert entry is not None
    if body.target == "remote":
        return _check_remote(body, entry, environ)
    report = gates.dependency_report(entry, environ)
    missing = [item.name for item in report if not item.present]
    version = gates.installable_version(entry, environ)
    ready = not missing and version is not None
    if missing and entry.install_channel in {"npm", "desktop"}:
        reason, offer = _click_preview(entry)
        return _result(
            environ,
            ok=False,
            network="not_contacted",
            target=body.target,
            reachable=None,
            reason=reason,
            offer_install=offer,
            missing=missing,
            registration=entry.registration,
            in_live_order=entry.in_live_order,
            version=_published_pin(entry),
        )
    if missing and entry.install_channel != "pip":
        return _result(
            environ,
            ok=False,
            network="not_contacted",
            target=body.target,
            reachable=None,
            reason="install_unsupported",
            offer_install=False,
            missing=missing,
            registration=entry.registration,
            in_live_order=entry.in_live_order,
            version=_published_pin(entry),
        )
    return _result(
        environ,
        ok=ready,
        network="not_contacted",
        target=body.target,
        reachable=None,
        reason="ready" if ready else "deps_missing",
        offer_install=bool(missing) and gates.can_resolve_install(entry),
        missing=missing,
        registration=entry.registration,
        in_live_order=entry.in_live_order,
        version=version,
    )


def install_harness(
    root: Path,
    body: HarnessInstallRequest,
    environ: Mapping[str, str],
) -> LibraryActionResult:
    gated = _gated(body, environ, require_auth=True)
    if gated is not None:
        return gated
    entry = entry_for(body.adapter)
    assert entry is not None
    if body.target == "remote":
        return _install_remote(root, body, entry, environ)
    return _install_local(root, body, entry, environ)


def scan_harnesses(
    root: Path,
    body: HarnessScanRequest,
    environ: Mapping[str, str],
) -> LibraryActionResult:
    gated = _gated(body, environ, require_auth=True)
    if gated is not None:
        return gated
    if body.target == "local":
        hits = _with_local_receipts(root, gates.local_harness_hits(environ))
    else:
        hits = gates.remote_harness_hits(body.host, body.port or 0, environ)
        if hits is None:
            return _unsupported(environ, body)
    admitted: list[str] = []
    for entry, version in hits:
        if version is None:
            continue
        saved = save_harness_profile(
            root,
            HarnessProfileRequest(
                profile_id=entry.profile_id,
                adapter=entry.adapter,  # type: ignore[arg-type]
                name=entry.name,
                version=version,
                timeout_seconds=600,
            ),
            environ,
            gated=True,
        )
        admitted.append(saved.profile_id)
    return _result(
        environ,
        ok=True,
        network=_network(body.target),
        target=body.target,
        reachable=True if body.target == "remote" else None,
        reason="admitted" if admitted else "empty",
        admitted=admitted,
    )


def admit_model(
    root: Path,
    body: ModelAdmitRequest,
    environ: Mapping[str, str],
    intake: IntakeService | None = None,
) -> LibraryActionResult:
    """添加云端. Field checks and both admit gates run before a catalog file is written."""

    gated = _gated(body, environ)
    if gated is not None:
        return gated
    if not gates.revision_admitted(body.revision):
        return _result(
            environ,
            ok=False,
            network=_network(body.target),
            target=body.target,
            reachable=True if body.target == "remote" else None,
            reason="version_rejected",
        )
    issues = cloud_gateway_issues(
        provider=body.provider,
        name=body.name,
        revision=body.revision,
        model_id=body.model_id,
        litellm_model=body.litellm_model,
        api_key=body.api_key,
        gateway_url=body.gateway_url,
    )
    if issues:
        return _result(
            environ,
            ok=False,
            network=_network(body.target),
            target=body.target,
            reachable=True if body.target == "remote" else None,
            reason="fields_rejected",
        )
    service = intake if intake is not None else IntakeService()
    decision, connectivity, network, reason = service.connect(body.gateway_url, bearer=body.api_key)
    if not decision.match:
        return _result(
            environ,
            ok=False,
            network="not_contacted",
            target=body.target,
            reachable=True if body.target == "remote" else None,
            reason="policy_mismatch",
        )
    if connectivity != "ok":
        return _result(
            environ,
            ok=False,
            network=network if network in {"not_contacted", "probed"} else "probed",
            target=body.target,
            reachable=True if body.target == "remote" else None,
            reason="connectivity_failed",
        )
    saved = save_catalog_entry(
        root,
        CatalogEntryRequest(
            model_id=body.model_id,
            provider=body.provider,
            name=body.name,
            revision=body.revision,
            litellm_model=body.litellm_model,
            modality=body.modality,
            strengths=list(body.strengths),
            placement="gateway",
        ),
        environ,
    )
    service.remember_gateway(
        model_id=saved.model_id,
        display_name=body.name,
        provider=body.provider,
        decision=decision,
        connectivity=connectivity,
        network=network,
        reason=None,
        fingerprint=credential_fingerprint(body.api_key),
        backend=body.provider,
    )
    probed = network if network in {"not_contacted", "probed"} else "probed"
    return _result(
        environ,
        ok=True,
        network=probed,
        target=body.target,
        reachable=True if body.target == "remote" else None,
        reason="admitted",
        admitted=[saved.model_id],
        source=saved.source,
        version=body.revision,
    )


def scan_models(
    root: Path,
    body: ModelScanRequest,
    environ: Mapping[str, str],
    intake: IntakeService | None = None,
) -> LibraryActionResult:
    """添加本地. Discovered rows must satisfy the local field standard before they are written.

    Ollama still comes from ``ollama list``. A vLLM base URL on the request or
    in ``OHBE_VLLM_BASE_URL`` also GETs ``/v1/models``. The bearer is not
    written to YAML. An unreachable listing fails closed and writes nothing.
    """

    gated = _gated(body, environ)
    if gated is not None:
        return gated
    wanted = _declared_local_model(body)
    if wanted is False:
        return _result(
            environ,
            ok=False,
            network=_network(body.target),
            target=body.target,
            reachable=True if body.target == "remote" else None,
            reason="fields_rejected",
        )
    service = intake if intake is not None else IntakeService()
    contacted = False
    try:
        if body.target == "local":
            found = gates.discover_local_models(
                environ,
                vllm_base_url=body.vllm_base_url,
                api_key=body.api_key,
                probe=service.probe,
            )
            contacted = bool(gates.configured_vllm_base_url(environ, body.vllm_base_url).strip())
        else:
            found = gates.discover_remote_models(body.host, body.port or 0)
    except gates.LocalDiscoveryError as exc:
        reason = exc.reason if exc.reason in {"policy_mismatch", "fields_rejected", "connectivity_failed"} else "connectivity_failed"
        return _result(
            environ,
            ok=False,
            network="probed" if exc.contacted else _network(body.target),
            target=body.target,
            reachable=None if body.target == "local" else False,
            reason=reason,
        )
    admitted: list[str] = []
    for model in found:
        if model.provider not in {"vllm", "ollama"} or not gates.revision_admitted(model.revision):
            continue
        prefix = "hosted_vllm/" if model.provider == "vllm" else "ollama/"
        model_id = f"{model.provider}/{model.name}"
        litellm_model = f"{prefix}{model.name}"
        if wanted is not None and model_id != wanted:
            continue
        if local_identity_issues(
            provider=model.provider,
            name=model.name,
            revision=model.revision,
            model_id=model_id,
            litellm_model=litellm_model,
        ):
            continue
        try:
            saved = save_catalog_entry(
                root,
                CatalogEntryRequest(
                    model_id=model_id,
                    provider=model.provider,  # type: ignore[arg-type]
                    name=model.name,
                    revision=model.revision,
                    litellm_model=litellm_model,
                    modality="text",
                    strengths=["reasoning"],
                    placement="local",
                ),
                environ,
            )
        except ValidationRejected:
            continue
        service.remember_local(
            model_id=saved.model_id,
            display_name=model.name,
            provider=model.provider,
            litellm_model=litellm_model,
        )
        admitted.append(saved.model_id)
    network = "probed" if body.target == "local" and contacted else _network(body.target)
    return _result(
        environ,
        ok=True,
        network=network,  # type: ignore[arg-type]
        target=body.target,
        reachable=True if body.target == "remote" else None,
        reason="admitted" if admitted else "empty",
        admitted=admitted,
    )


def _declared_local_model(body: ModelScanRequest) -> str | None | bool:
    """``None`` when the caller did not send identity. ``False`` when it is incomplete."""

    supplied = any((body.provider, body.name, body.revision, body.model_id, body.litellm_model))
    if not supplied:
        return None
    if body.provider is None or local_identity_issues(
        provider=body.provider,
        name=body.name,
        revision=body.revision,
        model_id=body.model_id,
        litellm_model=body.litellm_model,
    ):
        return False
    return body.model_id


_CLICK_REASONS = frozenset(
    {
        "deps_missing",
        "desktop_registered",
        "install_failed",
        "install_unsupported",
        "installer_unpinned",
        "npm_missing",
        "os_unsupported",
        "version_rejected",
    }
)


def _click_preview(entry: object) -> tuple[str, bool]:
    from ohbe.click_install import preview_local_install
    from ohbe.preinstall import PreinstallEntry

    if not isinstance(entry, PreinstallEntry):
        return "install_unsupported", False
    preview = preview_local_install(entry)
    if preview is None:
        return "install_unsupported", False
    return preview


def _with_local_receipts(
    root: Path,
    hits: tuple[tuple[object, str | None], ...],
) -> tuple[tuple[object, str | None], ...]:
    from ohbe.click_install import receipt_version
    from ohbe.preinstall import PreinstallEntry

    rows: list[tuple[object, str | None]] = []
    for entry, version in hits:
        if version is None and isinstance(entry, PreinstallEntry):
            version = receipt_version(root, entry)
        rows.append((entry, version))
    return tuple(rows)


def _install_pinned_channel(
    root: Path,
    body: HarnessInstallRequest,
    entry: object,
    environ: Mapping[str, str],
    missing: list[str],
) -> LibraryActionResult:
    from ohbe.click_install import ClickInstallError, prepare_local_install
    from ohbe.preinstall import PreinstallEntry

    catalog_entry = entry if isinstance(entry, PreinstallEntry) else entry_for(body.adapter)
    assert catalog_entry is not None
    try:
        prepared = prepare_local_install(
            root,
            catalog_entry,
            fetch=body.install_deps,
            parent_env=environ,
        )
    except ClickInstallError as exc:
        reason = exc.reason if exc.reason in _CLICK_REASONS else "install_failed"
        return _result(
            environ,
            ok=False,
            network="not_contacted",
            target="local",
            reachable=None,
            reason=reason,  # type: ignore[arg-type]
            offer_install=exc.offer_install,
            missing=missing,
            registration=catalog_entry.registration,
            in_live_order=catalog_entry.in_live_order,
            version=_published_pin(catalog_entry),
        )
    if not prepared.admit:
        return _result(
            environ,
            ok=True,
            network="not_contacted",
            target="local",
            reachable=None,
            reason=prepared.reason,
            registration=catalog_entry.registration,
            in_live_order=catalog_entry.in_live_order,
            version=prepared.version,
        )
    admitted = _admit(root, body, catalog_entry, environ, prepared.version, gated=False)
    if prepared.reason == "admitted":
        return admitted
    return _result(
        environ,
        ok=True,
        network="not_contacted",
        target="local",
        reachable=None,
        reason=prepared.reason,
        admitted=list(admitted.admitted),
        registration=admitted.registration,
        in_live_order=catalog_entry.in_live_order,
        version=admitted.version,
        source=admitted.source,
    )


def _install_local(
    root: Path,
    body: HarnessInstallRequest,
    entry: object,
    environ: Mapping[str, str],
) -> LibraryActionResult:
    catalog_entry = entry_for(body.adapter)
    assert catalog_entry is not None
    del entry
    report = gates.dependency_report(catalog_entry, environ)
    missing = [item.name for item in report if not item.present]
    if missing and catalog_entry.install_channel in {"npm", "desktop"}:
        return _install_pinned_channel(root, body, catalog_entry, environ, missing)
    if missing and catalog_entry.install_channel != "pip":
        return _result(
            environ,
            ok=False,
            network="not_contacted",
            target="local",
            reachable=None,
            reason="install_unsupported",
            offer_install=False,
            missing=missing,
            registration=catalog_entry.registration,
            in_live_order=catalog_entry.in_live_order,
            version=_published_pin(catalog_entry),
        )
    if missing:
        offer = gates.can_resolve_install(catalog_entry)
        if not body.install_deps or not offer:
            return _result(
                environ,
                ok=False,
                network="not_contacted",
                target="local",
                reachable=None,
                reason="deps_missing",
                offer_install=offer,
                missing=missing,
                registration=catalog_entry.registration,
                in_live_order=catalog_entry.in_live_order,
            )
        target = gates.resolve_pinned_install(catalog_entry)
        if target is None:
            return _result(
                environ,
                ok=False,
                network="not_contacted",
                target="local",
                reachable=None,
                reason="version_rejected",
                offer_install=False,
                missing=missing,
                registration=catalog_entry.registration,
                in_live_order=catalog_entry.in_live_order,
            )
        try:
            gates.install_distributions((target,))
        except LibraryGateError as exc:
            raise ValidationRejected(422, "deps_missing", _public(str(exc), _secret_text(body))) from exc
        report = gates.dependency_report(catalog_entry, environ)
        missing = [item.name for item in report if not item.present]
        if missing:
            return _result(
                environ,
                ok=False,
                network="not_contacted",
                target="local",
                reachable=None,
                reason="deps_missing",
                offer_install=False,
                missing=missing,
                registration=catalog_entry.registration,
                in_live_order=catalog_entry.in_live_order,
            )
        version = gates.installable_version(catalog_entry, environ)
        package = catalog_entry.package or ""
        if version is None or not gates.pin_matches(target, package, version):
            return _result(
                environ,
                ok=False,
                network="not_contacted",
                target="local",
                reachable=None,
                reason="version_rejected",
                registration=catalog_entry.registration,
                in_live_order=catalog_entry.in_live_order,
            )
    else:
        version = gates.installable_version(catalog_entry, environ)
        if version is None:
            return _result(
                environ,
                ok=False,
                network="not_contacted",
                target="local",
                reachable=None,
                reason="version_rejected",
                registration=catalog_entry.registration,
                in_live_order=catalog_entry.in_live_order,
            )
    return _admit(root, body, catalog_entry, environ, version, gated=False)


def _install_remote(
    root: Path,
    body: HarnessInstallRequest,
    entry: object,
    environ: Mapping[str, str],
) -> LibraryActionResult:
    catalog_entry = entry_for(body.adapter)
    assert catalog_entry is not None
    del entry
    if not gates.remote_exec_supported():
        return _unsupported(environ, body, catalog_entry)
    target = gates.resolve_pinned_install(catalog_entry)
    if target is None or body.port is None:
        return _result(
            environ,
            ok=False,
            network="probed",
            target="remote",
            reachable=True,
            reason="version_rejected",
            registration=catalog_entry.registration,
            in_live_order=catalog_entry.in_live_order,
        )
    try:
        credential = remote_auth.load_credential(
            username=body.username,
            auth_method=body.auth_method,
            secret=body.secret,
            secret_env=body.secret_env,
            environ=environ,
        )
        installed = gates.install_on_remote(body.host, body.port, credential, target)
    except (RemoteAuthError, LibraryGateError):
        return _unsupported(environ, body, catalog_entry)
    version = admitted_version(catalog_entry, installed)
    package = catalog_entry.package or ""
    if version is None or not gates.pin_matches(target, package, version):
        return _result(
            environ,
            ok=False,
            network="probed",
            target="remote",
            reachable=True,
            reason="version_rejected",
            registration=catalog_entry.registration,
            in_live_order=catalog_entry.in_live_order,
        )
    return _admit(root, body, catalog_entry, environ, version, gated=True)


def _check_remote(body: HarnessCheckRequest, entry: object, environ: Mapping[str, str]) -> LibraryActionResult:
    catalog_entry = entry_for(body.adapter)
    assert catalog_entry is not None
    del entry
    found = gates.remote_harness_hits(body.host, body.port or 0, environ)
    if found is None:
        return _unsupported(environ, body, catalog_entry)
    version = None
    for item, detected in found:
        if item.adapter == catalog_entry.adapter:
            version = detected
            break
    ready = version is not None
    missing = [] if ready else [catalog_entry.package or catalog_entry.adapter]
    return _result(
        environ,
        ok=ready,
        network="probed",
        target="remote",
        reachable=True,
        reason="ready" if ready else "deps_missing",
        offer_install=not ready and gates.can_resolve_install(catalog_entry),
        missing=missing,
        registration=catalog_entry.registration,
        in_live_order=catalog_entry.in_live_order,
        version=version,
    )


def _admit(
    root: Path,
    body: HarnessInstallRequest,
    entry: object,
    environ: Mapping[str, str],
    version: str,
    *,
    gated: bool,
) -> LibraryActionResult:
    catalog_entry = entry_for(body.adapter)
    assert catalog_entry is not None
    del entry
    saved = save_harness_profile(
        root,
        HarnessProfileRequest(
            profile_id=catalog_entry.profile_id,
            adapter=catalog_entry.adapter,  # type: ignore[arg-type]
            name=catalog_entry.name,
            version=version,
            timeout_seconds=body.timeout_seconds,
        ),
        environ,
        gated=gated,
    )
    remote = body.target == "remote"
    return _result(
        environ,
        ok=True,
        network="probed" if remote else "not_contacted",
        target=body.target,
        reachable=True if remote else None,
        reason="admitted",
        admitted=[saved.profile_id],
        registration=saved.registration,
        in_live_order=catalog_entry.in_live_order,
        version=saved.version,
        source=saved.source,
    )


def _unsupported(
    environ: Mapping[str, str],
    body: LibraryTarget,
    entry: object | None = None,
) -> LibraryActionResult:
    registration = None
    in_live_order = None
    catalog_entry = None
    if entry is not None:
        catalog_entry = entry_for(getattr(entry, "adapter", ""))
        if catalog_entry is not None:
            registration = catalog_entry.registration
            in_live_order = catalog_entry.in_live_order
    return _result(
        environ,
        ok=False,
        network="probed",
        target="remote",
        reachable=True,
        reason="inventory_unsupported",
        registration=registration,  # type: ignore[arg-type]
        in_live_order=in_live_order,
        version=_published_pin(catalog_entry) if catalog_entry is not None else None,
    )


def _gated(
    body: LibraryTarget,
    environ: Mapping[str, str],
    *,
    require_auth: bool = False,
) -> LibraryActionResult | None:
    if require_auth and body.target == "remote":
        network, reachable, reason = _authenticate_remote(body.host, body.port, body, environ)
    else:
        network, reachable, reason = _assess_target(body.target, body.host, body.port)
    if reason == "ready":
        return None
    return _result(
        environ,
        ok=False,
        network=network,
        target=body.target,  # type: ignore[arg-type]
        reachable=reachable,
        reason=reason,  # type: ignore[arg-type]
    )


def _authenticate_remote(
    host: str,
    port: int | None,
    body: object,
    environ: Mapping[str, str],
) -> tuple[str, bool | None, str]:
    if gates.classify_host(host) != "ok" or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return "not_contacted", False, "rejected_host"
    try:
        credential = remote_auth.load_credential(
            username=getattr(body, "username", ""),
            auth_method=getattr(body, "auth_method", ""),
            secret=getattr(body, "secret", ""),
            secret_env=getattr(body, "secret_env", ""),
            environ=environ,
        )
        remote_auth.authenticate(host, port, credential)
    except RemoteAuthError as exc:
        reason = exc.reason if exc.reason in _AUTH_REASONS else "auth_failed"
        if reason in {"rejected_host", "auth_required", "auth_unavailable"}:
            return "not_contacted", False, reason
        if reason == "unreachable":
            return "probed", False, "unreachable"
        return "probed", False, "auth_failed"
    return "probed", True, "ready"


def _auth_fields_present(body: object) -> bool:
    for name in ("username", "auth_method", "secret", "secret_env"):
        value = getattr(body, name, "")
        if isinstance(value, str) and value.strip():
            return True
    return False


def _secret_text(body: object) -> str:
    secret = getattr(body, "secret", "")
    return secret if isinstance(secret, str) else ""


def _assess_target(target: str, host: str, port: int | None) -> tuple[str, bool | None, str]:
    if target == "local":
        return "not_contacted", None, "ready"
    if gates.classify_host(host) != "ok" or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return "not_contacted", False, "rejected_host"
    try:
        reachable = gates.probe_host(host, port)
    except LibraryGateError:
        return "not_contacted", False, "rejected_host"
    if not reachable:
        return "probed", False, "unreachable"
    return "probed", True, "ready"


def _network(target: str) -> str:
    return "not_contacted" if target == "local" else "probed"


def _published_pin(entry: object) -> str | None:
    """Concrete catalog version, or None when the publisher has not pinned one."""

    from ohbe.preinstall import is_concrete_package_version

    version = getattr(entry, "version", None)
    if isinstance(version, str) and is_concrete_package_version(version):
        return version
    return None


def _result(
    environ: Mapping[str, str],
    *,
    ok: bool,
    network: str,
    target: str,
    reachable: bool | None,
    reason: str,
    offer_install: bool = False,
    missing: list[str] | None = None,
    admitted: list[str] | None = None,
    registration: str | None = None,
    in_live_order: bool | None = None,
    version: str | None = None,
    source: str | None = None,
) -> LibraryActionResult:
    return LibraryActionResult(
        ok=ok,
        network=network,  # type: ignore[arg-type]
        live_spend=live_spend_state(environ),
        target=target,  # type: ignore[arg-type]
        reachable=reachable,
        reason=reason,  # type: ignore[arg-type]
        offer_install=offer_install,
        missing=missing or [],
        admitted=admitted or [],
        harness_order=list(LIVE_HARNESS_ORDER),
        registration=registration,  # type: ignore[arg-type]
        in_live_order=in_live_order,
        version=version,
        source=source,
    )


def _public(message: str, secret: str = "") -> str:
    if secret and secret in message:
        return "draft was rejected"
    lowered = message.lower()
    if "sk-" in lowered or "://" in message or "api_key" in lowered or "private key" in lowered:
        return "draft was rejected"
    return message
