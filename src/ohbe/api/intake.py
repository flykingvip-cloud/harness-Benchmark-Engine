"""Catalog files and two-gate model intake.

``models/catalog/*.yaml`` records modality, strengths, and placement for a
vllm or ollama alias. Those fields are not pool metadata. Saving a catalog
file does not write a pool file, open a socket, or unlock spend.

Cloud API-key intake and server scan intake admit an in-memory catalog row
only when connectivity (or a confirmed handshake) succeeds and the LiteLLM
gateway policy matches. A policy miss is saved as pending. Pending rows are
not catalog members and are not round comparison slots. The connectivity
probe is a scoped GET. When an API key is present the probe is
``GET /v1/models`` with ``Authorization: Bearer``. An unauthenticated
``/health/liveliness`` GET is not the proof in that case. The probe never
calls ``/v1/chat/completions``, never writes the key into pool YAML, and
never unlocks live spend.

Startup loads every valid ``models/catalog/*.yaml`` into the intake catalog.
Saving a catalog file updates that same catalog. After a restart those
admitted entries can fill a comparison slot again. Pending rows are not
files, so they do not come back. The load does not open a socket.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from ohbe.api.doctor import live_spend_state
from ohbe.api.forms import (
    _atomic_write,
    _contained,
    _lock_checks,
    _plain,
    _public,
    _relative,
    _scalar,
    validate_pool_draft,
)
from ohbe.api.schemas import (
    CatalogEntryRequest,
    CatalogEntryView,
    CatalogList,
    CatalogSaved,
    CatalogValidation,
    Check,
    PoolDraftRequest,
    PoolModelDraft,
)
from ohbe.api.validate import ValidationRejected
from ohbe.core.base import SCHEMA_VERSION

CATALOG_KIND = "ohbe.model_catalog_entry"
CATALOG_NOTE = (
    "Catalog intake registers a vllm or ollama model. "
    "Modality, strengths, and placement stay in this document and are not written into pool YAML. "
    "Placement local means a private-host alias and placement gateway means a gateway-routed alias. "
    "Both still use LiteLLM only. This route does not contact a gateway and does not unlock live spend."
)
STRENGTHS = (
    "reasoning",
    "code",
    "tool_use",
    "long_context",
    "instruction",
    "multilingual",
    "low_latency",
    "low_cost",
)
MODALITIES = ("text", "code", "vision", "multimodal")
PLACEMENTS = ("local", "gateway")

_MODEL_ID = re.compile(r"^(vllm|ollama)/[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_CATALOG_FILE = re.compile(r"^[a-z0-9][A-Za-z0-9._-]{0,160}\.yaml$")
_ENTRY_KEYS = frozenset(
    {
        "kind",
        "litellm_model",
        "live_run",
        "modality",
        "model_id",
        "name",
        "placement",
        "provider",
        "revision",
        "schema_version",
        "strengths",
    }
)


class CatalogError(ValueError):
    """The catalog entry cannot be stored or reloaded."""


def validate_catalog_entry(body: CatalogEntryRequest, environ: Mapping[str, str]) -> CatalogValidation:
    """Check one catalog entry. This does not write a file."""

    return _assess(body, environ)


def save_catalog_entry(root: Path, body: CatalogEntryRequest, environ: Mapping[str, str]) -> CatalogSaved:
    """Validate, then write ``models/catalog/<provider>__<name>.yaml``."""

    assessed = _assess(body, environ)
    if not assessed.ok or assessed.filename is None or assessed.destination is None or assessed.model_id is None:
        failed = next((item.summary for item in assessed.checks if not item.passed), "catalog entry was rejected")
        raise ValidationRejected(422, "catalog_rejected", failed)
    path = _destination(root, body.model_id)
    try:
        text = _render(body)
        _parse(text, assessed.destination)
    except CatalogError as exc:
        raise ValidationRejected(422, "catalog_rejected", _public(str(exc))) from exc
    _atomic_write(path, text, "catalog_rejected")
    try:
        loaded = _read_path(root, path)
    except CatalogError as exc:
        raise ValidationRejected(422, "catalog_rejected", _public(str(exc))) from exc
    return CatalogSaved(
        live_spend=live_spend_state(environ),
        checks=assessed.checks,
        model_id=loaded.model_id,
        filename=loaded.filename,
        source=loaded.source,
        entry=loaded,
    )


def list_catalog_entries(root: Path, environ: Mapping[str, str]) -> CatalogList:
    """List catalog YAML files. Invalid files are summarized without secrets."""

    directory = _directory(root)
    entries: list[CatalogEntryView] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.yaml")):
            if not path.is_file() or path.is_symlink() or _CATALOG_FILE.fullmatch(path.name) is None or ".." in path.name:
                continue
            entries.append(_summarize(root, path).model_copy(update={"live_spend": live_spend_state(environ)}))
    return CatalogList(entries=entries, note=CATALOG_NOTE, live_spend=live_spend_state(environ))


def read_catalog_entry(root: Path, filename: str, environ: Mapping[str, str]) -> CatalogEntryView:
    """Return one catalog file. The response does not spend."""

    path = _named(root, filename)
    if not path.is_file():
        raise ValidationRejected(404, "catalog_not_found", f"Unknown catalog file: {filename}")
    view = _summarize(root, path)
    if not view.ok:
        raise ValidationRejected(422, "catalog_rejected", view.note)
    return view.model_copy(update={"live_spend": live_spend_state(environ)})


def _assess(body: CatalogEntryRequest, environ: Mapping[str, str]) -> CatalogValidation:
    live = live_spend_state(environ)
    try:
        filename = _filename(body.model_id)
        _metadata_rules(body)
        rendered = _render(body)
        _parse(rendered, f"models/catalog/{filename}")
    except CatalogError as exc:
        failed = Check(id=_check_id(str(exc)), passed=False, summary=_public(str(exc)))
        return CatalogValidation(ok=False, live_spend=live, checks=[failed, *_lock_checks()])
    probe = validate_pool_draft(_probe(body), environ)
    checks = [*_ok_checks(body), *probe.checks]
    if not probe.ok:
        return CatalogValidation(ok=False, live_spend=live, checks=checks, model_id=body.model_id)
    return CatalogValidation(
        ok=True,
        live_spend=live,
        checks=checks,
        model_id=body.model_id,
        filename=filename,
        destination=f"models/catalog/{filename}",
    )


def _probe(body: CatalogEntryRequest) -> PoolDraftRequest:
    return PoolDraftRequest(
        pool_id="pool-catalog-check",
        models=[
            PoolModelDraft(
                model_id=body.model_id,
                provider=body.provider,
                name=body.name,
                revision=body.revision,
                litellm_model=body.litellm_model,
            )
        ],
    )


def _metadata_rules(body: CatalogEntryRequest) -> None:
    _filename(body.model_id)
    if body.modality not in MODALITIES:
        raise CatalogError("modality must be text, code, vision, or multimodal")
    if body.placement not in PLACEMENTS:
        raise CatalogError("placement must be local or gateway")
    if not body.strengths or len(body.strengths) != len(set(body.strengths)):
        raise CatalogError("strengths must be a unique list")
    for strength in body.strengths:
        if strength not in STRENGTHS:
            raise CatalogError("strengths must use the catalog vocabulary")
    for value, field in (
        (body.model_id, "model_id"),
        (body.name, "name"),
        (body.revision, "revision"),
        (body.litellm_model, "litellm_model"),
    ):
        _plain(value, field, CatalogError)


def _ok_checks(body: CatalogEntryRequest) -> list[Check]:
    return [
        Check(
            id="catalog_metadata",
            passed=True,
            summary="modality and strengths stay in the catalog document, not the pool file",
            evidence=[body.modality, *body.strengths],
        ),
        Check(
            id="catalog_placement",
            passed=True,
            summary=f"placement {body.placement} is metadata and the model still uses LiteLLM only",
            evidence=[body.placement],
        ),
    ]


def _render(body: CatalogEntryRequest) -> str:
    _metadata_rules(body)
    lines = [
        f"schema_version: {json.dumps(SCHEMA_VERSION)}",
        f"kind: {CATALOG_KIND}",
        f"model_id: {_scalar(body.model_id)}",
        f"provider: {body.provider}",
        f"name: {_scalar(body.name)}",
        f"revision: {_scalar(body.revision)}",
        f"litellm_model: {_scalar(body.litellm_model)}",
        f"modality: {body.modality}",
        "strengths:",
    ]
    lines.extend(f"  - {strength}" for strength in body.strengths)
    lines.extend(
        [
            f"placement: {body.placement}",
            "live_run: rejected",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse(text: str, source: str) -> dict[str, Any]:
    if text.startswith("\ufeff") or "\r" in text or "sk-" in text.lower() or "://" in text:
        raise CatalogError("catalog file must not contain a secret, a URL, or CR")
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError("catalog file is not YAML") from exc
    if not isinstance(loaded, dict) or set(loaded) != _ENTRY_KEYS:
        raise CatalogError("catalog file fields do not match the intake document")
    if loaded.get("schema_version") != SCHEMA_VERSION or loaded.get("kind") != CATALOG_KIND:
        raise CatalogError("catalog kind must stay ohbe.model_catalog_entry")
    if loaded.get("live_run") != "rejected":
        raise CatalogError("live_run must stay rejected")
    strengths = loaded.get("strengths")
    if not isinstance(strengths, list) or any(not isinstance(item, str) for item in strengths):
        raise CatalogError("strengths must be a unique list")
    request = CatalogEntryRequest(
        model_id=loaded["model_id"],
        provider=loaded["provider"],
        name=loaded["name"],
        revision=loaded["revision"],
        litellm_model=loaded["litellm_model"],
        modality=loaded["modality"],
        strengths=strengths,
        placement=loaded["placement"],
    )
    _metadata_rules(request)
    if _render(request) != text and yaml.safe_load(_render(request)) != loaded:
        raise CatalogError("catalog file does not match the intake document")
    if source and ".." in source:
        raise CatalogError("catalog path was rejected")
    return loaded


def _read_path(root: Path, path: Path) -> CatalogEntryView:
    view = _summarize(root, path)
    if not view.ok:
        raise CatalogError(view.note)
    return view


def _summarize(root: Path, path: Path) -> CatalogEntryView:
    source = _relative(path, root)
    filename = path.name
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf") or b"\r" in raw:
            raise CatalogError("catalog file must not contain a secret, a URL, or CR")
        text = raw.decode("utf-8")
        loaded = _parse(text, source)
    except (CatalogError, UnicodeDecodeError, OSError, ValueError) as exc:
        return CatalogEntryView(
            filename=filename,
            model_id="",
            provider="",
            name="",
            revision="",
            litellm_model="",
            modality="",
            strengths=[],
            placement="",
            source=source,
            ok=False,
            note=_public(str(exc)),
        )
    return CatalogEntryView(
        filename=filename,
        model_id=str(loaded["model_id"]),
        provider=str(loaded["provider"]),
        name=str(loaded["name"]),
        revision=str(loaded["revision"]),
        litellm_model=str(loaded["litellm_model"]),
        modality=str(loaded["modality"]),
        strengths=list(loaded["strengths"]),
        placement=str(loaded["placement"]),
        source=source,
        ok=True,
        note=CATALOG_NOTE,
    )


def _filename(model_id: str) -> str:
    if not isinstance(model_id, str) or _MODEL_ID.fullmatch(model_id) is None or ".." in model_id:
        raise CatalogError("model_id must look like vllm/<name> or ollama/<name>")
    return f"{model_id.replace('/', '__')}.yaml"


def _destination(root: Path, model_id: str) -> Path:
    filename = _filename(model_id)
    directory = _directory(root)
    path = (directory / filename).resolve()
    if path.parent != directory or path.is_symlink() or ".." in path.name:
        raise ValidationRejected(422, "catalog_rejected", "catalog path was rejected")
    return path


def _named(root: Path, filename: str) -> Path:
    if not isinstance(filename, str) or _CATALOG_FILE.fullmatch(filename) is None or ".." in filename:
        raise ValidationRejected(404, "catalog_not_found", "Unknown catalog file")
    directory = _directory(root)
    path = (directory / filename).resolve()
    if path.parent != directory:
        raise ValidationRejected(404, "catalog_not_found", "Unknown catalog file")
    return path


def _directory(root: Path) -> Path:
    return _contained(root, root / "models" / "catalog", "catalog_rejected")


def _check_id(message: str) -> str:
    lowered = message.lower()
    if "secret" in lowered or "url" in lowered or "sk-" in lowered:
        return "catalog_no_secret"
    if "placement" in lowered:
        return "catalog_placement"
    if "modality" in lowered or "strength" in lowered:
        return "catalog_metadata"
    return "catalog_shape"

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import uuid4

from ohbe.api.schemas import (
    CatalogModel,
    CatalogModelList,
    Check,
    CloudIntakeRequest,
    ComparisonSlotsRequest,
    ComparisonSlotsResponse,
    DiscoveredModel,
    HandshakeRequest,
    HandshakeResponse,
    ServerScanRequest,
    ServerScanResponse,
)
from ohbe.api.intake_fields import cloud_provider_issues, credential_fingerprint
from ohbe.api.validate import ValidationRejected
from ohbe.environment.policy import (
    CloudLiteLLMDenied,
    EgressDenied,
    EgressSimulator,
    HttpTarget,
    controlled_track_policy,
    is_cloud_litellm_host,
    is_denied_provider_host,
    parse_http_target,
)
from ohbe.gateway.probe import (
    HEALTH_PROBE_PATH,
    LIST_PROBE_PATH,
    ProbeError,
    UrllibProbe,
    guard_probe_url,
    split_gateway_url,
)

_GATEWAY_PATHS = frozenset({"", "/", "/v1", "/v1/"})
_INTAKE_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DENIED_PROBE_URLS = (
    "https://api.openai.com/v1/chat/completions",
    "https://api.anthropic.com/v1/messages",
    "https://api.litellm.ai/v1/chat/completions",
)
_CATALOG_NOTE = (
    "A model enters the catalog only when connectivity or handshake succeeds and the "
    "LiteLLM gateway policy matches, or when a valid models/catalog file is loaded. "
    "Catalog membership is not a round comparison slot. Loading a file does not open a socket."
)
_PENDING_NOTE = (
    "Pending rows are saved and are not in the catalog. A policy mismatch or a failed "
    "connectivity check keeps them out. Pending models cannot fill a comparison slot."
)
_LISTED_NOTE = (
    "Listing succeeded under a matching LiteLLM gateway policy. No model enters the "
    "catalog until the handshake and the gateway policy both succeed."
)
_SCAN_PENDING_NOTE = (
    "Saved as pending. The gateway policy or the listing check did not pass, so nothing "
    "from this scan entered the catalog."
)
_HANDSHAKE_NOTE = (
    "Handshake does not open a socket and does not unlock benchmarking. A result enters "
    "the catalog only when the handshake and the gateway policy both succeed."
)
_SLOTS_NOTE = (
    "Residual process list kept for migration. Plan tabs do not read it. "
    "Each plan stores its own slots at PUT /v1/rounds/plans/{plan_id}/slots. "
    "POST on this route is slots_unscoped. Nothing here spends."
)
BackendName = Literal["vllm", "ollama", "other"]
ConnectivityName = Literal["ok", "failed", "not_run"]
NetworkName = Literal["not_contacted", "probed", "unreachable"]
RouteName = Literal["litellm_gateway", "rejected"]


class ProbeTransport(Protocol):
    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        """GET one already-authorized probe URL and return status plus a JSON object."""


def catalog_admission(*, gate_ok: bool, policy_match: bool) -> Literal["admitted", "pending"]:
    """Catalog entry requires both gates. Anything else stays pending."""

    if gate_ok and policy_match:
        return "admitted"
    return "pending"


class _RefuseTransport:
    def exchange(self, target: HttpTarget, body: bytes) -> bytes:
        raise AssertionError(f"policy transport was invoked for {target.host}")


@dataclass(frozen=True)
class GatewayDecision:
    match: bool
    reason: str | None
    host: str | None
    port: int | None
    origin: str | None
    checks: tuple[Check, ...]
    route: RouteName


@dataclass(frozen=True)
class ListedModel:
    model_id: str
    display_name: str
    provider: str
    litellm_model: str
    backend: BackendName
    admissible: bool


@dataclass
class StoredScan:
    scan_id: str
    decision: GatewayDecision
    connectivity: ConnectivityName
    network: NetworkName
    models: tuple[ListedModel, ...]
    reason: str | None


class IntakeService:
    """Process-local intake catalog, pending rows, and round comparison slots."""

    def __init__(self, probe: ProbeTransport | None = None) -> None:
        self.probe: ProbeTransport = probe if probe is not None else UrllibProbe()
        self.records: dict[str, CatalogModel] = {}
        self.scans: dict[str, StoredScan] = {}
        self.slots: list[str] = []
        self._last_body: Mapping[str, Any] = {}

    def listing_body(self) -> Mapping[str, Any]:
        """JSON from the most recent connectivity probe, or an empty mapping."""

        return self._last_body

    def hydrate(self, root: Path) -> None:
        """Load valid catalog files into this process. Does not probe or spend."""

        for entry in list_catalog_entries(root, {}).entries:
            self.adopt_catalog_entry(entry)

    def adopt_catalog_entry(self, entry: CatalogEntryView) -> CatalogModel | None:
        """Record one already-valid catalog file. An accepted row for the same id stays."""

        if not entry.ok or not entry.model_id or entry.provider not in {"vllm", "ollama"}:
            return None
        if entry.placement not in {"local", "gateway"}:
            return None
        for row in self.records.values():
            if row.accepted and row.model_id == entry.model_id:
                return row
        key = f"catalog_file:{entry.model_id}"
        current = self.records.get(key)
        backend: BackendName = "vllm" if entry.provider == "vllm" else "ollama"
        row = CatalogModel(
            record_id=current.record_id if current is not None else _catalog_record_id(entry.model_id),
            model_id=entry.model_id,
            display_name=entry.name or entry.model_id,
            provider=entry.provider,
            intake_path="server_scan",
            admission="admitted",
            accepted=True,
            catalog_member=True,
            connectivity_or_handshake=True,
            connectivity="ok",
            handshake="confirmed",
            policy_match=True,
            policy_checks=[
                Check(
                    id="catalog_hydrated",
                    passed=True,
                    summary="Loaded from an admitted catalog file. No socket was opened and no key was read.",
                    evidence=[entry.source, f"placement:{entry.placement}"],
                )
            ],
            route="litellm_gateway",
            gateway_host=None,
            gateway_port=None,
            backend=backend,
            reason=None,
            credential="not_applicable",
            credential_fingerprint=None,
            network="not_contacted",
            scope="handshake",
        )
        return self._store(key, row)

    def admitted_model_ids(self) -> set[str]:
        return {row.model_id for row in self.records.values() if row.accepted}

    def catalog(self) -> CatalogModelList:
        return CatalogModelList(
            models=[row for row in self.records.values() if row.accepted],
            note=_CATALOG_NOTE,
        )

    def pending(self) -> CatalogModelList:
        return CatalogModelList(
            models=[row for row in self.records.values() if not row.accepted],
            note=_PENDING_NOTE,
        )

    def comparison_slots(self) -> ComparisonSlotsResponse:
        return ComparisonSlotsResponse(model_ids=list(self.slots), note=_SLOTS_NOTE)

    def validate_slot_ids(self, model_ids: Sequence[str]) -> None:
        """Catalog membership, uniqueness, and the length cap. Does not store."""

        if len(model_ids) != len(set(model_ids)):
            raise ValidationRejected(422, "invalid_request", "comparison slots contain a duplicate")
        if len(model_ids) > 16:
            raise ValidationRejected(422, "invalid_request", "too many comparison slots")
        admitted = self.admitted_model_ids()
        for model_id in model_ids:
            if model_id not in admitted:
                raise ValidationRejected(
                    404,
                    "not_in_catalog",
                    f"Model is not in the catalog: {model_id}",
                )

    def replace_slots(self, body: ComparisonSlotsRequest) -> ComparisonSlotsResponse:
        self.validate_slot_ids(body.model_ids)
        self.slots = list(body.model_ids)
        return self.comparison_slots()

    def cloud(self, body: CloudIntakeRequest) -> CatalogModel:
        _refuse_live(body.live)
        api_key = _api_key(body.api_key)
        model_id = _cloud_model_id(body.provider, body.model_id)
        display_name = _display_name(body.display_name)
        if api_key in display_name or api_key in model_id:
            raise ValidationRejected(422, "invalid_request", "api_key must be a token")
        if cloud_provider_issues(provider=body.provider, model_id=model_id, litellm_model=body.litellm_model):
            raise ValidationRejected(422, "invalid_request", "intake fields are incomplete")
        if api_key in body.litellm_model:
            raise ValidationRejected(422, "invalid_request", "api_key must be a token")
        decision, connectivity, network, reason = self.connect(body.gateway_url, bearer=api_key)
        return self._save_cloud(
            model_id=model_id,
            display_name=display_name,
            provider=body.provider,
            decision=decision,
            connectivity=connectivity,
            network=network,
            reason=reason,
            fingerprint=_fingerprint(api_key),
        )

    def scan(self, body: ServerScanRequest) -> ServerScanResponse:
        _refuse_live(body.live)
        decision = evaluate_gateway(body.gateway_url)
        connectivity: ConnectivityName = "not_run"
        network: NetworkName = "not_contacted"
        models: tuple[ListedModel, ...] = ()
        reason = decision.reason
        if decision.match and decision.origin is not None:
            _status, network, connectivity, probe_reason = self._probe(decision.origin + LIST_PROBE_PATH)
            if probe_reason is not None:
                reason = probe_reason
            elif connectivity == "ok":
                try:
                    models = _parse_listing(self._last_body)
                except ProbeError:
                    connectivity = "failed"
                    reason = "connectivity_failed"
                    models = ()
                else:
                    reason = None
            elif _status < 200 or _status >= 300:
                reason = "connectivity_failed"
        scan_id = f"scan-{uuid4().hex[:12]}"
        stored = StoredScan(
            scan_id=scan_id,
            decision=decision,
            connectivity=connectivity,
            network=network,
            models=models,
            reason=reason,
        )
        self.scans[scan_id] = stored
        self._sync_scan_pending(stored)
        listed = decision.match and connectivity == "ok" and reason is None
        return ServerScanResponse(
            scan_id=scan_id,
            admission="listed" if listed else "pending",
            policy_match=decision.match,
            policy_checks=list(decision.checks),
            connectivity=connectivity,
            route=decision.route,
            gateway_host=decision.host,
            gateway_port=decision.port,
            models=[_discovered(item, decision.match) for item in models],
            reason=reason,
            network=network,
            note=_LISTED_NOTE if listed else _SCAN_PENDING_NOTE,
        )

    def handshake(self, body: HandshakeRequest) -> HandshakeResponse:
        _refuse_live(body.live)
        if len(body.model_ids) != len(set(body.model_ids)):
            raise ValidationRejected(422, "invalid_request", "handshake contains a duplicate model_id")
        scan = self.scans.get(body.scan_id)
        if scan is None:
            raise ValidationRejected(404, "scan_not_found", "Unknown scan")
        if not scan.decision.match or scan.connectivity != "ok":
            raise ValidationRejected(
                409,
                "scan_not_ready",
                "Handshake cannot admit models until the gateway policy and the listing both succeed.",
            )
        found = {item.model_id: item for item in scan.models}
        missing = [model_id for model_id in body.model_ids if model_id not in found]
        if missing:
            raise ValidationRejected(404, "model_not_in_scan", "A selected model is not in this scan")
        results = [self._save_handshake(scan, found[model_id]) for model_id in body.model_ids]
        return HandshakeResponse(scan_id=scan.scan_id, results=results, note=_HANDSHAKE_NOTE)

    def connect(
        self,
        gateway_url: str,
        *,
        bearer: str | None,
    ) -> tuple[GatewayDecision, ConnectivityName, NetworkName, str | None]:
        """Policy first. A key selects ``GET /v1/models`` with Bearer."""

        decision = evaluate_gateway(gateway_url)
        if not decision.match or decision.origin is None:
            return decision, "not_run", "not_contacted", decision.reason
        path = LIST_PROBE_PATH if bearer else HEALTH_PROBE_PATH
        status, network, connectivity, probe_reason = self._probe(decision.origin + path, bearer=bearer)
        if probe_reason is not None:
            return decision, connectivity, network, probe_reason
        if status < 200 or status >= 300:
            return decision, "failed", network, "connectivity_failed"
        return decision, "ok", network, None

    def remember_gateway(
        self,
        *,
        model_id: str,
        display_name: str,
        provider: str,
        decision: GatewayDecision,
        connectivity: ConnectivityName,
        network: NetworkName,
        reason: str | None,
        fingerprint: str,
        backend: BackendName,
    ) -> CatalogModel:
        """Same row the legacy cloud path writes, for a catalog admit that passed both gates."""

        return self._save_cloud(
            model_id=model_id,
            display_name=display_name,
            provider=provider,
            decision=decision,
            connectivity=connectivity,
            network=network,
            reason=reason,
            fingerprint=fingerprint,
            backend=backend,
        )

    def remember_local(
        self,
        *,
        model_id: str,
        display_name: str,
        provider: str,
        litellm_model: str,
    ) -> CatalogModel:
        """Catalog scan admit. Handshake is the connectivity gate; policy is the local backend."""

        backend: BackendName = provider if provider in {"vllm", "ollama"} else "other"
        checks = [
            Check(
                id="local_backend",
                passed=True,
                summary="Server intake only admits vLLM and Ollama models discovered on the gateway.",
                evidence=[f"backend:{backend}"],
            ),
            Check(
                id="intake_triad",
                passed=True,
                summary="model_id, litellm_model, and the pricing key name the same model",
                evidence=[model_id, litellm_model],
            ),
        ]
        key = f"server_scan:{model_id}"
        current = self.records.get(key)
        row = CatalogModel(
            record_id=current.record_id if current is not None else f"rec-{uuid4().hex[:12]}",
            model_id=model_id,
            display_name=display_name,
            provider=provider,
            intake_path="server_scan",
            admission="admitted",
            accepted=True,
            catalog_member=True,
            connectivity_or_handshake=True,
            connectivity="ok",
            handshake="confirmed",
            policy_match=True,
            policy_checks=checks,
            route="litellm_gateway",
            gateway_host=None,
            gateway_port=None,
            backend=backend,
            reason=None,
            credential="not_applicable",
            credential_fingerprint=None,
            network="not_contacted",
            scope="handshake",
        )
        return self._store(key, row)

    def _probe(
        self,
        url: str,
        bearer: str | None = None,
    ) -> tuple[int, NetworkName, ConnectivityName, str | None]:
        guard_probe_url(url)
        try:
            status, body = self.probe.get_json(url, timeout=2.0, bearer=bearer)
        except ProbeError:
            self._last_body = {}
            return 0, "unreachable", "failed", "connectivity_failed"
        if not isinstance(body, Mapping):
            self._last_body = {}
            return status, "probed", "failed", "connectivity_failed"
        self._last_body = body
        if 200 <= status < 300:
            return status, "probed", "ok", None
        return status, "probed", "failed", "connectivity_failed"

    def _save_cloud(
        self,
        *,
        model_id: str,
        display_name: str,
        provider: str,
        decision: GatewayDecision,
        connectivity: ConnectivityName,
        network: NetworkName,
        reason: str | None,
        fingerprint: str,
        backend: BackendName = "remote",
    ) -> CatalogModel:
        gate_ok = connectivity == "ok"
        admission = catalog_admission(gate_ok=gate_ok, policy_match=decision.match)
        accepted = admission == "admitted"
        key = f"cloud_api_key:{model_id}"
        current = self.records.get(key)
        row = CatalogModel(
            record_id=current.record_id if current is not None else f"rec-{uuid4().hex[:12]}",
            model_id=model_id,
            display_name=display_name,
            provider=provider,
            intake_path="cloud_api_key",
            admission=admission,
            accepted=accepted,
            catalog_member=accepted,
            connectivity_or_handshake=gate_ok,
            connectivity=connectivity,
            handshake="not_applicable",
            policy_match=decision.match,
            policy_checks=list(decision.checks),
            route=decision.route,
            gateway_host=decision.host,
            gateway_port=decision.port,
            backend=backend,
            reason=None if accepted else reason,
            credential="api_key_accepted",
            credential_fingerprint=fingerprint,
            network=network,
            scope="connectivity",
        )
        return self._store(key, row)

    def _save_handshake(self, scan: StoredScan, item: ListedModel) -> CatalogModel:
        policy_match = scan.decision.match and item.admissible
        checks = list(scan.decision.checks)
        checks.append(
            Check(
                id="local_backend",
                passed=item.admissible,
                summary="Server intake only admits vLLM and Ollama models discovered on the gateway.",
                evidence=[f"backend:{item.backend}"],
            )
        )
        reason = None if policy_match else "local_backend_required"
        admission = catalog_admission(gate_ok=True, policy_match=policy_match)
        accepted = admission == "admitted"
        key = f"server_scan:{item.model_id}"
        current = self.records.get(key)
        row = CatalogModel(
            record_id=current.record_id if current is not None else f"rec-{uuid4().hex[:12]}",
            model_id=item.model_id,
            display_name=item.display_name,
            provider=item.provider,
            intake_path="server_scan",
            admission=admission,
            accepted=accepted,
            catalog_member=accepted,
            connectivity_or_handshake=True,
            connectivity="ok",
            handshake="confirmed",
            policy_match=policy_match,
            policy_checks=checks,
            route=scan.decision.route if policy_match else "rejected",
            gateway_host=scan.decision.host,
            gateway_port=scan.decision.port,
            backend=item.backend if item.backend != "other" else "other",
            reason=reason,
            credential="not_applicable",
            credential_fingerprint=None,
            network=scan.network,
            scope="handshake",
        )
        return self._store(key, row)

    def _store(self, key: str, row: CatalogModel) -> CatalogModel:
        if row.accepted:
            for existing_key, existing in list(self.records.items()):
                if existing_key != key and existing.accepted and existing.model_id == row.model_id:
                    del self.records[existing_key]
        self.records[key] = row
        return row

    def _sync_scan_pending(self, scan: StoredScan) -> None:
        key = _scan_pending_key(scan.decision)
        listed = scan.decision.match and scan.connectivity == "ok" and scan.reason is None
        if listed:
            current = self.records.get(key)
            if current is not None and not current.accepted:
                self.records.pop(key, None)
            return
        host = scan.decision.host or "unknown"
        port = scan.decision.port or 0
        model_id = f"scan/{host}:{port}"
        current = self.records.get(key)
        self._store(
            key,
            CatalogModel(
                record_id=current.record_id if current is not None else f"rec-{uuid4().hex[:12]}",
                model_id=model_id,
                display_name=host,
                provider="gateway",
                intake_path="server_scan",
                admission="pending",
                accepted=False,
                catalog_member=False,
                connectivity_or_handshake=False,
                connectivity=scan.connectivity,
                handshake="not_applicable",
                policy_match=scan.decision.match,
                policy_checks=list(scan.decision.checks),
                route=scan.decision.route,
                gateway_host=scan.decision.host,
                gateway_port=scan.decision.port,
                backend="other",
                reason=scan.reason or "policy_mismatch",
                credential="not_applicable",
                network=scan.network,
                scope="discovery",
            ),
        )


def _catalog_record_id(model_id: str) -> str:
    digest = hashlib.sha256(model_id.encode("utf-8")).hexdigest()[:12]
    return f"rec-{digest}"


def evaluate_gateway(url: str) -> GatewayDecision:
    """Fail closed before any probe. The raw URL is not copied into the result."""

    text = url.strip()
    parts = split_gateway_url(text)
    userinfo = bool(parts.username or parts.password)
    query_or_fragment = bool(parts.query or parts.fragment)
    path_allowed = parts.path in _GATEWAY_PATHS
    host: str | None = None
    port: int | None = None
    scheme = parts.scheme if parts.scheme in {"http", "https"} else None
    try:
        target = parse_http_target(text)
    except ValueError:
        target = None
    else:
        host = target.host
        port = target.port
        scheme = target.scheme
    cloud = bool(host) and is_cloud_litellm_host(host or "")
    provider = bool(host) and is_denied_provider_host(host or "")
    policy_built = False
    egress_ok = False
    if (
        target is not None
        and scheme is not None
        and not userinfo
        and not query_or_fragment
        and path_allowed
        and not cloud
        and not provider
    ):
        try:
            policy = controlled_track_policy(gateway_host=target.host, gateway_port=target.port)
        except (CloudLiteLLMDenied, ValueError):
            policy_built = False
        else:
            policy_built = True
            egress_ok = _egress_denies_forbidden(policy)
    route_ok = policy_built and egress_ok
    checks = (
        Check(
            id="gateway_not_cloud",
            passed=bool(host) and not cloud and not userinfo,
            summary="The gateway host is not a known cloud LiteLLM endpoint.",
            evidence=[f"host:{host}"] if host else [],
        ),
        Check(
            id="provider_bypass_denied",
            passed=bool(host) and not provider and not userinfo and path_allowed and not query_or_fragment,
            summary="Direct model-provider hosts are denied before any transport.",
            evidence=[f"host:{host}"] if host else [],
        ),
        Check(
            id="route_litellm",
            passed=route_ok,
            summary="The route is the operator LiteLLM gateway, not a provider or hosted LiteLLM.",
            evidence=["route:litellm_gateway"] if route_ok else ["route:rejected"],
        ),
        Check(
            id="egress_fail_closed",
            passed=egress_ok,
            summary="The egress policy denies provider and cloud LiteLLM URLs before any socket.",
            evidence=["spend:none", "network:not_contacted"],
        ),
    )
    if userinfo:
        reason: str | None = "url_userinfo_denied"
    elif cloud:
        reason = "cloud_litellm_denied"
    elif target is None or provider or not path_allowed or query_or_fragment or not policy_built:
        reason = "gateway_rejected"
    elif not egress_ok:
        reason = "policy_mismatch"
    else:
        reason = None
    origin = _origin(scheme, host, port) if route_ok and scheme and host and port else None
    return GatewayDecision(
        match=reason is None and origin is not None,
        reason=reason,
        host=host,
        port=port,
        origin=origin,
        checks=checks,
        route="litellm_gateway" if reason is None and origin is not None else "rejected",
    )


def _egress_denies_forbidden(policy: Any) -> bool:
    simulator = EgressSimulator(policy, _RefuseTransport())
    denied = 0
    for url in _DENIED_PROBE_URLS:
        try:
            simulator.request(url)
        except EgressDenied:
            denied += 1
        except AssertionError:
            return False
    return denied == len(_DENIED_PROBE_URLS)


def _origin(scheme: str, host: str, port: int) -> str:
    rendered = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{scheme}://{rendered}:{port}"


def _refuse_live(live: bool) -> None:
    if live:
        raise ValidationRejected(
            403,
            "live_spend_locked",
            "Intake does not unlock live spend for benchmarking. The connectivity check was not executed.",
        )


def _api_key(value: str) -> str:
    if len(value) < 8 or len(value) > 256 or any(character.isspace() for character in value) or "://" in value:
        raise ValidationRejected(422, "invalid_request", "api_key must be a token")
    return value


def _display_name(value: str) -> str:
    text = value.strip()
    if not text or len(text) > 80 or any(character in text for character in "\r\n"):
        raise ValidationRejected(422, "invalid_request", "display_name is not valid")
    return text


def _cloud_model_id(provider: str, model_id: str) -> str:
    if not _INTAKE_MODEL_ID.match(model_id) or not model_id.startswith(f"{provider}/"):
        raise ValidationRejected(422, "invalid_request", "model_id is not valid")
    lowered = model_id.lower()
    if "://" in model_id or "openai.com" in lowered or "anthropic.com" in lowered:
        raise ValidationRejected(422, "invalid_request", "model_id is not valid")
    return model_id


def _fingerprint(api_key: str) -> str:
    return credential_fingerprint(api_key)


def _parse_listing(body: Mapping[str, Any]) -> tuple[ListedModel, ...]:
    data = body.get("data")
    if not isinstance(data, list):
        raise ProbeError("listing has no model data")
    found: list[ListedModel] = []
    seen: set[str] = set()
    for item in data[:100]:
        parsed = _one_listed(item)
        if parsed is None or parsed.model_id in seen:
            continue
        seen.add(parsed.model_id)
        found.append(parsed)
    return tuple(found)


def _one_listed(item: object) -> ListedModel | None:
    if not isinstance(item, Mapping):
        return None
    upstream = _upstream_id(item)
    if upstream is None:
        return None
    backend, provider, model_id = _classify(upstream)
    if any(character.isspace() for character in model_id) or "://" in model_id:
        return None
    display = model_id.split("/", 1)[1] if "/" in model_id else model_id
    name = item.get("model_name")
    if isinstance(name, str) and name.strip() and len(name.strip()) <= 80:
        display = name.strip()
    return ListedModel(
        model_id=model_id,
        display_name=display,
        provider=provider,
        litellm_model=upstream,
        backend=backend,
        admissible=backend in {"vllm", "ollama"},
    )


def _upstream_id(item: Mapping[str, Any]) -> str | None:
    params = item.get("litellm_params")
    if isinstance(params, Mapping):
        model = params.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    raw = item.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _classify(upstream: str) -> tuple[BackendName, str, str]:
    if upstream.startswith("hosted_vllm/") and upstream != "hosted_vllm/":
        return "vllm", "vllm", f"vllm/{upstream[len('hosted_vllm/'):]}"
    if upstream.startswith("vllm/") and upstream != "vllm/":
        return "vllm", "vllm", upstream
    if upstream.startswith("ollama/") and upstream != "ollama/":
        return "ollama", "ollama", upstream
    return "other", "other", upstream[:160]


def _discovered(item: ListedModel, gateway_match: bool) -> DiscoveredModel:
    return DiscoveredModel(
        model_id=item.model_id,
        display_name=item.display_name,
        provider=item.provider,
        litellm_model=item.litellm_model,
        backend=item.backend,
        admissible=item.admissible,
        policy_match=gateway_match and item.admissible,
    )


def _scan_pending_key(decision: GatewayDecision) -> str:
    host = decision.host or "unknown"
    port = decision.port if decision.port is not None else 0
    return f"server_scan:scan/{host}:{port}"
