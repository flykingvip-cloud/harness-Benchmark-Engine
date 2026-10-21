"""Resolve the operator environment for one live pilot.

Missing credentials, a floating image tag, or a provider URL stop the run
before a harness is started.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ohbe.adapters.inspect_react.version import inspect_pin_matches, installed_inspect_version
from ohbe.adapters.openbot.profile import OPENBOT_COMMIT_UNAVAILABLE, OPENBOT_IMAGE_UNAVAILABLE
from ohbe.environment.policy import is_denied_provider_host, normalize_host
from ohbe.gateway.constants import LITELLM_IMAGE
from ohbe.live.spec import LivePilotError, LivePilotSpec

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED = ("LITELLM_API_KEY", "LITELLM_BASE_URL", "OPENBOT_COMMIT", "OPENBOT_BIN")


@dataclass(frozen=True)
class LiveContext:
    """Pins and endpoints for one live run. The API key is not written to disk."""

    api_key: str
    base_url: str
    gateway_host: str
    gateway_port: int
    openbot_bin: str
    openbot_commit: str
    openbot_image: str | None
    litellm_image: str
    litellm_image_digest: str | None


def resolve_live_context(environ: Mapping[str, str], spec: LivePilotSpec) -> LiveContext:
    """Read live settings from `environ` and reject a run that is not pinned."""

    missing = [name for name in _REQUIRED if not str(environ.get(name, "")).strip()]
    if missing:
        raise LivePilotError("live pilot refuses to run without " + ", ".join(missing))
    api_key = str(environ["LITELLM_API_KEY"])
    if any(character.isspace() for character in api_key):
        raise LivePilotError("LITELLM_API_KEY must be a non-empty token")
    host, port, base_url = parse_litellm_base_url(str(environ["LITELLM_BASE_URL"]))
    if host != spec.egress.gateway_host or port != spec.egress.gateway_port:
        raise LivePilotError(
            "LITELLM_BASE_URL host and port must match the experiment egress gateway "
            f"({spec.egress.gateway_host}:{spec.egress.gateway_port})"
        )
    commit = str(environ["OPENBOT_COMMIT"]).strip().lower()
    if commit == OPENBOT_COMMIT_UNAVAILABLE or _COMMIT.fullmatch(commit) is None:
        raise LivePilotError("OPENBOT_COMMIT must be the 40-character git SHA of the OpenBot image")
    binary = str(environ["OPENBOT_BIN"])
    if not _executable(binary):
        raise LivePilotError("OPENBOT_BIN is not an executable file")
    image = str(environ.get("OPENBOT_IMAGE", "")).strip() or None
    if image is not None:
        _check_image(image, commit)
    digest_text = str(environ.get("LITELLM_IMAGE_DIGEST", "")).strip() or None
    if digest_text is not None and _DIGEST.fullmatch(digest_text) is None:
        raise LivePilotError("LITELLM_IMAGE_DIGEST must be sha256:<64 lowercase hex>")
    if installed_inspect_version() is None or not inspect_pin_matches():
        raise LivePilotError("inspect-ai is not installed at the OHBE pin; pip install -e '.[inspect]'")
    return LiveContext(
        api_key=api_key,
        base_url=base_url,
        gateway_host=host,
        gateway_port=port,
        openbot_bin=str(Path(binary).resolve()),
        openbot_commit=commit,
        openbot_image=image,
        litellm_image=LITELLM_IMAGE,
        litellm_image_digest=digest_text,
    )


def parse_litellm_base_url(value: str) -> tuple[str, int, str]:
    """Return host, port, and the normalized origin. Does not open a socket."""

    return _base_url(value)


def _base_url(value: str) -> tuple[str, int, str]:
    parts = urlsplit(value.strip())
    if parts.scheme != "http":
        raise LivePilotError("LITELLM_BASE_URL must be an http origin on the OHBE internal network")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise LivePilotError("LITELLM_BASE_URL must not carry credentials, a query, or a fragment")
    if parts.port is None or parts.hostname is None:
        raise LivePilotError("LITELLM_BASE_URL must include an explicit host and port")
    path = parts.path.rstrip("/")
    if path not in {"", "/v1"}:
        raise LivePilotError("LITELLM_BASE_URL path must be empty or /v1")
    host = normalize_host(parts.hostname)
    if is_denied_provider_host(host):
        raise LivePilotError("LITELLM_BASE_URL must not target a model provider")
    return host, parts.port, f"http://{host}:{parts.port}{path}"


def _check_image(image: str, commit: str) -> None:
    lowered = image.lower()
    if (
        image == OPENBOT_IMAGE_UNAVAILABLE
        or lowered == "latest"
        or lowered.endswith(":latest")
        or lowered.endswith("@latest")
        or any(character.isspace() for character in image)
    ):
        raise LivePilotError("OPENBOT_IMAGE must be pinned by the commit SHA or a sha256 digest")
    digest = image.split("@", 1)[1] if "@" in image else ""
    if commit not in image and _DIGEST.fullmatch(digest) is None:
        raise LivePilotError("OPENBOT_IMAGE must contain OPENBOT_COMMIT or a sha256 digest")


def _executable(path: str) -> bool:
    file = Path(path)
    return file.is_file() and os.access(file, os.X_OK)
