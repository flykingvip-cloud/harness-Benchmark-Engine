"""Server install inventory for the closed harness registry.

The host may have none of these harnesses. Auto-detect marks a row
``preinstalled`` only when the observed version is allowed. An operator may
also select a registered id and an allowed version. Only those version-gated
rows enter the plans whitelist. The live whitelist is the same set filtered
through ``HARNESS_ORDER``, so request order cannot reorder live.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from ohbe.adapters.inspect_react.plan import INSPECT_AI_VERSION
from ohbe.api.schemas import (
    HarnessInventoryItem,
    HarnessInventoryResponse,
    HarnessSelectRequest,
)
from ohbe.api.validate import ValidationRejected
from ohbe.live.spec import HARNESS_ORDER

OPENCLAW_VERSION = "0.1.0"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA_IN_TEXT = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])")
_SEMVER_IN_TEXT = re.compile(r"\b(\d+\.\d+\.\d+)\b")
_ENV = {
    "openbot": "OHBE_HARNESS_OPENBOT_VERSION",
    "inspect-react": "OHBE_HARNESS_INSPECT_REACT_VERSION",
    "openclaw": "OHBE_HARNESS_OPENCLAW_VERSION",
}
_BINS = {
    "openbot": "openbot",
    "openclaw": "openclaw",
}
_NOTE = (
    "The host may have no harnesses. A row is preinstalled only when auto-detect "
    "finds an allowed version. Plans accept a registered harness at its whitelist "
    "version. Live order stays openbot, then inspect_react. OpenClaw can enter "
    "plans and does not enter live. This route does not install packages and does not spend."
)


@dataclass(frozen=True)
class RegisteredHarness:
    harness_id: str
    name: str
    live_name: str | None
    allowed_versions: tuple[str, ...]
    sha_allowed: bool


REGISTRY: tuple[RegisteredHarness, ...] = (
    RegisteredHarness("openbot", "OpenBot", "openbot", ("fixture",), True),
    RegisteredHarness("inspect-react", "Inspect react()", "inspect_react", (INSPECT_AI_VERSION,), False),
    RegisteredHarness("openclaw", "OpenClaw", None, (OPENCLAW_VERSION,), False),
)
_BY_ID = {item.harness_id: item for item in REGISTRY}


class HarnessDetector(Protocol):
    """Return harness id to observed version. Must not raise."""

    def detect(self) -> Mapping[str, str]: ...


class StaticHarnessDetector:
    """Test and operator double. Unknown ids are ignored by the inventory."""

    def __init__(self, found: Mapping[str, str] | None = None) -> None:
        self._found = dict(found or {})

    def detect(self) -> Mapping[str, str]:
        return dict(self._found)


class HostHarnessDetector:
    """Read env pins, Inspect's installed version, then ``--version`` on PATH.

    Env wins when set. Command lookup uses ``shutil.which`` and runs
    ``[binary, --version]`` with ``shell=False`` and a one-second timeout.
    Failures become an absent observation.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def detect(self) -> Mapping[str, str]:
        found: dict[str, str] = {}
        for harness_id, env_name in _ENV.items():
            raw = self._environ.get(env_name, "")
            if isinstance(raw, str) and raw.strip():
                found[harness_id] = raw.strip()
        if "inspect-react" not in found:
            inspected = _installed_inspect_version()
            if inspected:
                found["inspect-react"] = inspected
        for harness_id, binary in _BINS.items():
            if harness_id in found:
                continue
            observed = _binary_version(binary)
            if observed:
                found[harness_id] = observed
        return found


class HarnessInventory:
    """Process-local selections plus a detector snapshot on each read."""

    def __init__(self, detector: HarnessDetector | None = None) -> None:
        self._detector = detector if detector is not None else HostHarnessDetector()
        self._selected: dict[str, str] = {}

    def view(self) -> HarnessInventoryResponse:
        detected = _safe_detect(self._detector)
        items: list[HarnessInventoryItem] = []
        plans: dict[str, bool] = {}
        for spec in REGISTRY:
            found = detected.get(spec.harness_id)
            selected = self._selected.get(spec.harness_id)
            detected_ok = found is not None and _version_allowed(spec, found)
            if found is None:
                presence = "absent"
            elif detected_ok:
                presence = "preinstalled"
            else:
                presence = "version_rejected"
            whitelisted = selected is not None or detected_ok
            plans[spec.harness_id] = whitelisted
            if selected is not None:
                whitelist_version = selected
            elif detected_ok and found is not None:
                whitelist_version = found
            else:
                whitelist_version = None
            items.append(
                HarnessInventoryItem(
                    harness_id=spec.harness_id,
                    name=spec.name,
                    live_name=spec.live_name,
                    allowed_versions=list(spec.allowed_versions),
                    detected_version=found,
                    presence=presence,
                    selected_version=selected,
                    whitelist_version=whitelist_version,
                    plans_whitelisted=whitelisted,
                    live_whitelisted=False,
                )
            )
        live_whitelist = _live_whitelist(plans)
        live_ids = set(live_whitelist)
        stamped = [
            item.model_copy(update={"live_whitelisted": item.live_name is not None and item.live_name in live_ids})
            for item in items
        ]
        return HarnessInventoryResponse(
            harnesses=stamped,
            plans_whitelist=_plans_whitelist(plans),
            live_whitelist=live_whitelist,
            live_order=list(HARNESS_ORDER),
            note=_NOTE,
        )

    def select(self, body: HarnessSelectRequest) -> HarnessInventoryResponse:
        chosen: dict[str, str] = {}
        seen: set[str] = set()
        for item in body.selections:
            if item.harness_id in seen:
                raise ValidationRejected(422, "invalid_request", "duplicate harness selection")
            seen.add(item.harness_id)
            spec = _BY_ID.get(item.harness_id)
            if spec is None:
                raise ValidationRejected(
                    403,
                    "harness_not_registered",
                    f"Harness is not registered: {item.harness_id}",
                )
            if not _version_allowed(spec, item.version):
                raise ValidationRejected(
                    422,
                    "version_not_allowed",
                    f"Version is not allowed for {item.harness_id}",
                )
            chosen[item.harness_id] = item.version
        self._selected = chosen
        return self.view()

    def require_plan(self, harness_id: str, name: str, version: str) -> None:
        """Refuse a round plan whose harness is outside the version gate."""

        current = self.view()
        row = next((item for item in current.harnesses if item.harness_id == harness_id), None)
        if row is None or not row.plans_whitelisted or row.whitelist_version != version:
            raise ValidationRejected(
                403,
                "harness_not_whitelisted",
                "Only a version-gated registered harness on the plans whitelist can enter a round plan.",
            )
        if name != row.name:
            raise ValidationRejected(422, "invalid_request", "Harness name must match the registered name.")


def _version_allowed(spec: RegisteredHarness, version: str) -> bool:
    if version in spec.allowed_versions:
        return True
    return spec.sha_allowed and _SHA.fullmatch(version) is not None


def _plans_whitelist(plans: Mapping[str, bool]) -> list[str]:
    by_live = {spec.live_name: spec.harness_id for spec in REGISTRY if spec.live_name}
    ordered = [by_live[name] for name in HARNESS_ORDER if name in by_live]
    ordered.extend(spec.harness_id for spec in REGISTRY if spec.harness_id not in ordered)
    return [harness_id for harness_id in ordered if plans.get(harness_id)]


def _live_whitelist(plans: Mapping[str, bool]) -> list[str]:
    by_live = {spec.live_name: spec.harness_id for spec in REGISTRY if spec.live_name}
    names: list[str] = []
    for live_name in HARNESS_ORDER:
        harness_id = by_live.get(live_name)
        if harness_id is not None and plans.get(harness_id):
            names.append(live_name)
    return names


def _safe_detect(detector: HarnessDetector) -> dict[str, str]:
    try:
        found = detector.detect()
    except Exception:
        return {}
    if not isinstance(found, Mapping):
        return {}
    clean: dict[str, str] = {}
    for key, value in found.items():
        if isinstance(key, str) and isinstance(value, str) and value:
            clean[key] = value
    return clean


def _installed_inspect_version() -> str | None:
    try:
        from ohbe.adapters.inspect_react.version import installed_inspect_version
    except Exception:
        return None
    try:
        found = installed_inspect_version()
    except Exception:
        return None
    if not isinstance(found, str) or not found.strip():
        return None
    return found.strip()


def _binary_version(binary: str) -> str | None:
    try:
        path = shutil.which(binary)
    except OSError:
        return None
    if not path:
        return None
    try:
        completed = subprocess.run(
            [path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _parse_version(f"{completed.stdout}\n{completed.stderr}")


def _parse_version(text: str) -> str | None:
    lowered = text.lower()
    sha = _SHA_IN_TEXT.search(lowered)
    if sha is not None:
        return sha.group(1)
    semver = _SEMVER_IN_TEXT.search(text)
    if semver is not None:
        return semver.group(1)
    return None
