"""Library gates for harness preinstall and model admission.

Local install resolves a concrete pin: an exact spec already in the catalog,
or the newest admitted release and its ``files.pythonhosted.org`` artifact.
The floating token ``latest`` is never installed and never stored.

A model remote target is still only a TCP probe. A harness remote target
must authenticate. After SSH auth, package inventory and install are not
executed: ``discover_remote_harnesses`` returns ``None`` and
``remote_exec_supported`` stays false until a safe executor exists.
npm pins are installed by ``ohbe.click_install`` from the catalog tarball.
Desktop pins are downloaded for the matching OS and are not executed.
``install_on_remote`` stays closed.
``install_on_remote`` is that hook. Nothing here changes ``HARNESS_ORDER``.
Local model discovery may GET an operator vLLM ``/v1/models``. It does not
call ``/v1/chat/completions``. Credentials are not written to YAML.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from ohbe.environment.policy import is_cloud_litellm_host, is_denied_provider_host, normalize_host
from ohbe.gateway.probe import LIST_PROBE_PATH, ProbeError, UrllibProbe, guard_probe_url
import ohbe.preinstall as catalog
from ohbe.preinstall import PREINSTALL_CATALOG, PreinstallEntry, entry_for
from ohbe.remote_auth import RemoteCredential

_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,253}$")
_PINNED_SPEC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,80}==[A-Za-z0-9][A-Za-z0-9._+-]{0,40}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_UNVERSIONED = frozenset({"latest", "unpinned", "main", "master"})
_ALLOWED_DISTRIBUTIONS = frozenset(name for entry in PREINSTALL_CATALOG for name in entry.distributions)
_ARTIFACT_HOST = "files.pythonhosted.org"
_PYPI_HOST = "pypi.org"


class LibraryGateError(ValueError):
    """The target or package spec was rejected before any probe or install."""


@dataclass(frozen=True)
class DepInfo:
    name: str
    present: bool
    installable: bool
    spec: str | None


@dataclass(frozen=True)
class DiscoveredModel:
    provider: str
    name: str
    revision: str


def revision_admitted(revision: str) -> bool:
    """True when a model revision is concrete enough to enter the library."""

    if not isinstance(revision, str):
        return False
    text = revision.strip()
    if not text or text != revision:
        return False
    lowered = text.lower()
    if lowered in _UNVERSIONED:
        return False
    if "://" in text or "sk-" in lowered or "api_key" in lowered:
        return False
    if "openai.com" in lowered or "anthropic.com" in lowered:
        return False
    return True


def classify_host(host: str) -> str:
    """Return ``ok`` or ``rejected`` without opening a socket."""

    if not isinstance(host, str) or _HOST.fullmatch(host.strip()) is None:
        return "rejected"
    if "://" in host or "/" in host:
        return "rejected"
    normalized = normalize_host(host)
    if not normalized or is_denied_provider_host(normalized) or is_cloud_litellm_host(normalized):
        return "rejected"
    lowered = normalized.lower()
    if "sk-" in lowered or "api_key" in lowered:
        return "rejected"
    return "ok"


def probe_host(host: str, port: int, *, timeout: float = 2.0) -> bool:
    """TCP reachability. Caller must already have classified the host."""

    import socket

    if classify_host(host) != "ok":
        raise LibraryGateError("remote host was rejected")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise LibraryGateError("remote port was rejected")
    try:
        with socket.create_connection((normalize_host(host), port), timeout=timeout):
            return True
    except OSError:
        return False


def _executable_present(environ: Mapping[str, str], name: str) -> bool:
    candidate = environ.get(name, "")
    if not isinstance(candidate, str) or not candidate.strip():
        return False
    path = Path(candidate)
    return path.is_file() and os.access(path, os.X_OK)


def _openbot_bin_present(environ: Mapping[str, str]) -> bool:
    return _executable_present(environ, "OPENBOT_BIN")


def dependency_report(entry: PreinstallEntry, environ: Mapping[str, str]) -> tuple[DepInfo, ...]:
    """Local dependency check. Plan rows stay out of the live whitelist.

    An npm or desktop pin is present only when its version variable equals
    the catalog pin and its binary variable names an executable. Those rows
    are not pip distributions, so this does not resolve ``latest`` or a PyPI
    artifact for them. A publisher that has not released a concrete pin stays
    absent.
    """

    if entry.adapter == "openbot":
        commit = catalog.detect_version(entry, environ)
        return (
            DepInfo("OPENBOT_COMMIT", commit is not None, False, None),
            DepInfo("OPENBOT_BIN", _openbot_bin_present(environ), False, None),
        )
    if entry.version_env:
        detected = catalog.detect_version(entry, environ)
        pinned = catalog.is_concrete_package_version(entry.version) and detected == entry.version
        rows = [DepInfo(entry.version_env, pinned, False, None)]
        if entry.binary_env:
            rows.append(DepInfo(entry.binary_env, _executable_present(environ, entry.binary_env), False, None))
        return tuple(rows)
    if entry.version_kind == "exact":
        package = entry.package or entry.adapter
        found = catalog.distribution_version(package) if entry.package else None
        present = found == entry.version
        spec = f"{package}=={entry.version}" if entry.package else None
        return (DepInfo(package, present, spec is not None, spec),)
    package = entry.package
    present = catalog.detect_version(entry, environ) is not None
    return (DepInfo(package or entry.adapter, present, package is not None, package),)


def missing_install_specs(report: tuple[DepInfo, ...]) -> tuple[str, ...] | None:
    """Pinned ``name==version`` specs, or None when a dep is not already pinned.

    A bare distribution name is not an install spec. Call
    ``resolve_pinned_install`` for that case. ``latest`` is rejected.
    """

    specs: list[str] = []
    for dep in report:
        if dep.present:
            continue
        if not dep.installable or not dep.spec or not _pinned_spec_ok(dep.spec):
            return None
        specs.append(dep.spec)
    return tuple(specs)


def can_resolve_install(entry: PreinstallEntry) -> bool:
    """True when the catalog names an allowlisted pip distribution that can be pinned.

    npm tarballs and desktop installers are not pip targets. ``click_install``
    owns those channels. ``latest`` is not an install target.
    """

    if entry.install_channel != "pip":
        return False
    package = entry.package
    if entry.version_kind == "git_sha" or package is None:
        return False
    return package in _ALLOWED_DISTRIBUTIONS


def resolve_pinned_install(entry: PreinstallEntry) -> str | None:
    """Concrete pip spec or artifact URL. Never the floating token ``latest``."""

    if not can_resolve_install(entry):
        return None
    package = entry.package
    assert package is not None
    if entry.version_kind == "exact":
        if not catalog.is_concrete_package_version(entry.version):
            return None
        return f"{package}=={entry.version}"
    released = lookup_released_version(package)
    if released is None:
        return None
    version, artifact = released
    if not catalog.is_concrete_package_version(version):
        return None
    if artifact and pinned_artifact_url(artifact, package, version):
        return artifact
    return f"{package}=={version}"


def pin_matches(target: str, distribution: str, version: str) -> bool:
    """True when ``version`` is the concrete pin ``target`` names."""

    if not catalog.is_concrete_package_version(version) or "latest" in target.lower():
        return False
    if target == f"{distribution}=={version}":
        return True
    return pinned_artifact_url(target, distribution, version)


def lookup_released_version(name: str) -> tuple[str, str | None] | None:
    """Newest concrete PyPI release and an optional pinned artifact URL.

    The lookup is the background pin for a package that has no exact spec in
    the catalog. A failure returns None so the caller does not install a
    floating name. Tests replace this function.
    """

    if name not in _ALLOWED_DISTRIBUTIONS:
        return None
    raw = _read_pypi_json(name)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    version = info.get("version")
    if not isinstance(version, str) or not catalog.is_concrete_package_version(version):
        return None
    return version, _select_artifact(payload.get("urls"), name, version)


def pinned_artifact_url(url: str, distribution: str, version: str) -> bool:
    """True for one https wheel or sdist on ``files.pythonhosted.org``."""

    if not isinstance(url, str) or not url or len(url) > 2000 or "latest" in url.lower():
        return False
    if not catalog.is_concrete_package_version(version) or distribution not in _ALLOWED_DISTRIBUTIONS:
        return False
    parts = urlsplit(url)
    host = normalize_host(parts.hostname or "")
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        return False
    if host != _ARTIFACT_HOST or is_denied_provider_host(host) or is_cloud_litellm_host(host):
        return False
    return _version_in_artifact(parts.path, distribution, version)


def install_distributions(specs: tuple[str, ...]) -> None:
    """Install pinned specs or pinned artifact URLs. The caller decided to do this."""

    if not specs:
        return
    for spec in specs:
        if not _install_target_ok(spec):
            raise LibraryGateError("package spec was rejected")
    completed = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *specs],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise LibraryGateError("dependency install failed")


def remote_exec_supported() -> bool:
    """Remote package inventory and install are not executed yet.

    SSH authentication can succeed while this stays false. A later executor
    replaces ``discover_remote_harnesses`` and ``install_on_remote``.
    """

    return False


def install_on_remote(host: str, port: int, credential: RemoteCredential, target: str) -> str | None:
    """Hook for a future remote install. The default refuses to run it.

    A replacement returns the concrete version installed on the remote host.
    It must not write ``credential.secret`` into a file, a profile, or git.
    """

    del host, port, credential, target
    raise LibraryGateError("remote install is not supported yet")


def installable_version(entry: PreinstallEntry, environ: Mapping[str, str]) -> str | None:
    return catalog.admitted_version(entry, catalog.detect_version(entry, environ))


def local_harness_hits(environ: Mapping[str, str]) -> tuple[tuple[PreinstallEntry, str | None], ...]:
    rows: list[tuple[PreinstallEntry, str | None]] = []
    for entry in PREINSTALL_CATALOG:
        rows.append((entry, installable_version(entry, environ)))
    return tuple(rows)


def discover_remote_harnesses(
    host: str,
    port: int,
    environ: Mapping[str, str],
) -> tuple[tuple[str, str], ...] | None:
    """Remote inventory.

    ``None`` means a safe remote executor is not available. An empty tuple
    means the executor ran and found nothing. Versions are not invented from
    a TCP probe or from SSH auth alone.
    """

    del host, port, environ
    if not remote_exec_supported():
        return None
    return ()


def parse_ollama_list(text: str) -> tuple[DiscoveredModel, ...]:
    found: list[DiscoveredModel] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("name "):
            continue
        token = line.split()[0]
        if ":" not in token:
            continue
        name, revision = token.split(":", 1)
        if _MODEL_NAME.fullmatch(name) is None or not revision_admitted(revision):
            continue
        found.append(DiscoveredModel(provider="ollama", name=name, revision=revision))
    return tuple(found)


VLLM_BASE_URL_ENV = "OHBE_VLLM_BASE_URL"
VLLM_API_KEY_ENV = "OHBE_VLLM_API_KEY"
_VLLM_ORIGIN_PATHS = frozenset({"", "/", "/v1", "/v1/"})
_DENIED_VLLM_PREFIXES = frozenset(
    {
        "openai",
        "anthropic",
        "google",
        "mistral",
        "groq",
        "cohere",
        "together",
        "deepseek",
        "fireworks",
        "perplexity",
        "ollama",
        "azure",
        "bedrock",
        "vertex_ai",
        "huggingface",
        "gemini",
        "openrouter",
    }
)
_REVISION_FIELDS = ("revision", "version", "model_revision")


class ListingProbe(Protocol):
    def get_json(
        self,
        url: str,
        *,
        timeout: float = 2.0,
        bearer: str | None = None,
    ) -> tuple[int, Mapping[str, Any]]:
        """GET one listing URL. Implementations must not call chat completions."""


class LocalDiscoveryError(LibraryGateError):
    """vLLM discovery stopped before any catalog write.

    ``contacted`` is true only after a listing GET was attempted. The message
    is a reason code and does not carry the URL or the bearer.
    """

    def __init__(self, reason: str, *, contacted: bool) -> None:
        self.reason = reason
        self.contacted = contacted
        super().__init__(reason)


def configured_vllm_base_url(environ: Mapping[str, str] | None, base_url: str) -> str:
    """Request URL when set, otherwise ``OHBE_VLLM_BASE_URL``. Empty means skip."""

    if isinstance(base_url, str) and base_url.strip():
        return base_url
    if not environ:
        return ""
    raw = environ.get(VLLM_BASE_URL_ENV, "")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    return raw


def listed_revision(body: Mapping[str, Any], name: str) -> str | None:
    """Concrete revision for ``name`` on an OpenAI-style listing, or None.

    A bare served id is its own revision. ``latest`` and other floating tags
    are not returned. This does not invent a pin the listing did not provide.
    """

    if not isinstance(name, str) or not name:
        return None
    try:
        found = parse_vllm_listing(body)
    except LibraryGateError:
        return None
    for item in found:
        if item.name == name and revision_admitted(item.revision):
            return item.revision
    return None


def parse_vllm_listing(body: Mapping[str, Any]) -> tuple[DiscoveredModel, ...]:
    """OpenAI-compatible ``/v1/models`` rows that can enter the local library.

    A bare served id is its own concrete revision. ``latest`` and other
    provider prefixes are dropped. A missing ``data`` list is an error.
    """

    data = body.get("data")
    if not isinstance(data, list):
        raise LibraryGateError("listing has no model data")
    found: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in data[:100]:
        parsed = _one_vllm_model(item)
        if parsed is None or parsed.name in seen:
            continue
        seen.add(parsed.name)
        found.append(parsed)
    return tuple(found)


def vllm_models_url(base_url: str) -> str:
    """``{origin}/v1/models`` or a fail-closed error before any socket."""

    if not isinstance(base_url, str) or not base_url or base_url != base_url.strip():
        raise LocalDiscoveryError("policy_mismatch", contacted=False)
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise LocalDiscoveryError("policy_mismatch", contacted=False)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise LocalDiscoveryError("policy_mismatch", contacted=False)
    if parts.path not in _VLLM_ORIGIN_PATHS:
        raise LocalDiscoveryError("policy_mismatch", contacted=False)
    host = normalize_host(parts.hostname)
    lowered = host.lower()
    if (
        not host
        or is_denied_provider_host(host)
        or is_cloud_litellm_host(host)
        or "sk-" in lowered
        or "api_key" in lowered
    ):
        raise LocalDiscoveryError("policy_mismatch", contacted=False)
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise LocalDiscoveryError("policy_mismatch", contacted=False)
    rendered = f"[{host}]" if ":" in host else host
    url = f"{parts.scheme}://{rendered}:{port}{LIST_PROBE_PATH}"
    try:
        guard_probe_url(url)
    except ProbeError as exc:
        raise LocalDiscoveryError("policy_mismatch", contacted=False) from exc
    return url


def discover_vllm_models(
    base_url: str,
    *,
    bearer: str | None = None,
    probe: ListingProbe | None = None,
) -> tuple[DiscoveredModel, ...]:
    """GET the vLLM listing. Unreachable and non-listings fail closed."""

    url = vllm_models_url(base_url)
    client: ListingProbe = probe if probe is not None else UrllibProbe()
    try:
        status, payload = client.get_json(url, timeout=2.0, bearer=bearer)
    except ProbeError:
        raise LocalDiscoveryError("connectivity_failed", contacted=True) from None
    if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status < 300:
        raise LocalDiscoveryError("connectivity_failed", contacted=True)
    if not isinstance(payload, Mapping):
        raise LocalDiscoveryError("connectivity_failed", contacted=True)
    try:
        return parse_vllm_listing(payload)
    except LocalDiscoveryError:
        raise
    except LibraryGateError:
        raise LocalDiscoveryError("connectivity_failed", contacted=True) from None


def discover_ollama_models() -> tuple[DiscoveredModel, ...]:
    """``ollama list``. A missing binary or a failed command yields no rows."""

    binary = shutil.which("ollama")
    if not binary:
        return ()
    try:
        completed = subprocess.run(
            [binary, "list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if completed.returncode != 0:
        return ()
    return parse_ollama_list(completed.stdout)


def discover_local_models(
    environ: Mapping[str, str] | None = None,
    *,
    vllm_base_url: str = "",
    api_key: str = "",
    probe: ListingProbe | None = None,
) -> tuple[DiscoveredModel, ...]:
    """Ollama list plus an optional vLLM ``/v1/models`` listing.

    The bearer is the request token or ``OHBE_VLLM_API_KEY``. It is sent only
    on that GET. A configured vLLM origin that cannot be read raises
    ``LocalDiscoveryError`` and does not return the Ollama rows.
    """

    chosen = configured_vllm_base_url(environ, vllm_base_url)
    bearer = _chosen_bearer(environ, api_key, needed=bool(chosen.strip()) if isinstance(chosen, str) else False)
    vllm_rows: tuple[DiscoveredModel, ...] = ()
    if chosen:
        vllm_rows = discover_vllm_models(chosen, bearer=bearer, probe=probe)
    return vllm_rows + discover_ollama_models()


def _chosen_bearer(environ: Mapping[str, str] | None, api_key: str, *, needed: bool) -> str | None:
    if not isinstance(api_key, str):
        raise LocalDiscoveryError("fields_rejected", contacted=False)
    if api_key:
        if not _bearer_shape_ok(api_key):
            raise LocalDiscoveryError("fields_rejected", contacted=False)
        return api_key
    if not needed:
        return None
    raw = "" if not environ else environ.get(VLLM_API_KEY_ENV, "")
    if not isinstance(raw, str):
        raise LocalDiscoveryError("fields_rejected", contacted=False)
    if not raw:
        return None
    if not _bearer_shape_ok(raw):
        raise LocalDiscoveryError("fields_rejected", contacted=False)
    return raw


def _bearer_shape_ok(token: str) -> bool:
    if len(token) < 8 or len(token) > 256:
        return False
    if any(character.isspace() for character in token) or "://" in token:
        return False
    return True


def _one_vllm_model(item: object) -> DiscoveredModel | None:
    if not isinstance(item, Mapping):
        return None
    raw = _vllm_upstream_id(item)
    if raw is None:
        return None
    identity = _vllm_identity(raw)
    if identity is None:
        return None
    name, colon = identity
    revision = _listed_revision(item, colon, name)
    if revision is None:
        return None
    return DiscoveredModel(provider="vllm", name=name, revision=revision)


def _vllm_upstream_id(item: Mapping[str, Any]) -> str | None:
    params = item.get("litellm_params")
    if isinstance(params, Mapping):
        model = params.get("model")
        if isinstance(model, str) and model.strip():
            return model.strip()
    raw = item.get("id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _vllm_identity(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    lowered = text.lower()
    if not text or any(character.isspace() for character in text) or "://" in text:
        return None
    if "sk-" in lowered or "api_key" in lowered or "openai.com" in lowered or "anthropic.com" in lowered:
        return None
    if text.startswith("hosted_vllm/"):
        text = text[len("hosted_vllm/") :]
    elif text.startswith("vllm/"):
        text = text[len("vllm/") :]
    head = text.split("/", 1)[0].lower()
    if head in _DENIED_VLLM_PREFIXES:
        return None
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    colon: str | None = None
    if ":" in text:
        name, colon = text.split(":", 1)
    else:
        name = text
    if _MODEL_NAME.fullmatch(name) is None:
        return None
    if colon is not None and not revision_admitted(colon):
        return None
    return name, colon


def _listed_revision(item: Mapping[str, Any], colon: str | None, name: str) -> str | None:
    for key in _REVISION_FIELDS:
        if key not in item:
            continue
        value = item.get(key)
        if isinstance(value, str) and revision_admitted(value):
            return value
        return None
    if colon is not None:
        return colon
    if revision_admitted(name):
        return name
    return None


def discover_remote_models(host: str, port: int) -> tuple[DiscoveredModel, ...]:
    """A reachable remote does not invent model rows."""

    del host, port
    return ()


def remote_harness_hits(
    host: str,
    port: int,
    environ: Mapping[str, str],
) -> tuple[tuple[PreinstallEntry, str | None], ...] | None:
    """Admitted remote rows, or None when remote inventory is not supported."""

    found = discover_remote_harnesses(host, port, environ)
    if found is None:
        return None
    rows: list[tuple[PreinstallEntry, str | None]] = []
    for adapter, version in found:
        entry = entry_for(adapter)
        if entry is None:
            continue
        rows.append((entry, catalog.admitted_version(entry, version)))
    return tuple(rows)


def _pinned_spec_ok(spec: str) -> bool:
    if _PINNED_SPEC.fullmatch(spec) is None or "latest" in spec.lower():
        return False
    name, version = spec.split("==", 1)
    return name in _ALLOWED_DISTRIBUTIONS and catalog.is_concrete_package_version(version)


def _install_target_ok(target: str) -> bool:
    if _pinned_spec_ok(target):
        return True
    if not isinstance(target, str) or not target.startswith("https://"):
        return False
    parts = urlsplit(target)
    path = parts.path
    for name in _ALLOWED_DISTRIBUTIONS:
        version = _version_from_artifact_path(path, name)
        if version and pinned_artifact_url(target, name, version):
            return True
    return False


def _version_from_artifact_path(path: str, distribution: str) -> str | None:
    for name in _distribution_names(distribution):
        marker = f"{name}-"
        start = path.rfind(marker)
        if start < 0:
            continue
        rest = path[start + len(marker) :]
        for suffix in (".tar.gz", ".tar.bz2", ".whl", ".zip"):
            if suffix in rest:
                rest = rest.split(suffix, 1)[0]
                break
        candidate = rest.split("-", 1)[0]
        if catalog.is_concrete_package_version(candidate) and _version_in_artifact(path, distribution, candidate):
            return candidate
    return None


def _distribution_names(distribution: str) -> tuple[str, ...]:
    names = {
        distribution,
        distribution.replace("_", "-"),
        distribution.replace("-", "_"),
    }
    return tuple(names)


def _version_in_artifact(path: str, distribution: str, version: str) -> bool:
    for name in _distribution_names(distribution):
        needle = f"{name}-{version}"
        start = 0
        while True:
            index = path.find(needle, start)
            if index < 0:
                break
            after = index + len(needle)
            if after == len(path) or path[after] in "-.+":
                return True
            start = index + 1
    return False


def _select_artifact(urls: object, name: str, version: str) -> str | None:
    if not isinstance(urls, list):
        return None
    fallback: str | None = None
    for item in urls:
        if not isinstance(item, dict):
            continue
        link = item.get("url")
        if not isinstance(link, str) or not pinned_artifact_url(link, name, version):
            continue
        if link.endswith(".whl"):
            return link
        fallback = fallback or link
    return fallback


def _read_pypi_json(name: str) -> bytes | None:
    from urllib.error import HTTPError, URLError

    quoted = quote(name, safe="")
    url = f"https://{_PYPI_HOST}/pypi/{quoted}/json"
    opener = build_opener(_PinnedRedirect(), HTTPSHandler(), ProxyHandler({}))
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "ohbe-preinstall"})
    try:
        with opener.open(request, timeout=8) as response:
            final = response.geturl()
            if not _pypi_json_url(final, quoted):
                return None
            return response.read(2_000_000)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return None


def _pypi_json_url(url: str, quoted_name: str) -> bool:
    parts = urlsplit(url)
    host = normalize_host(parts.hostname or "")
    if parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment:
        return False
    if host != _PYPI_HOST or is_denied_provider_host(host) or is_cloud_litellm_host(host):
        return False
    return parts.path == f"/pypi/{quoted_name}/json"


def _allowed_download_host(url: str) -> bool:
    parts = urlsplit(url)
    host = normalize_host(parts.hostname or "")
    if parts.scheme != "https" or parts.username or parts.password:
        return False
    if host not in {_PYPI_HOST, _ARTIFACT_HOST}:
        return False
    return not is_denied_provider_host(host) and not is_cloud_litellm_host(host)


class _PinnedRedirect(HTTPRedirectHandler):
    """Refuse a redirect that leaves the pinned package hosts."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not isinstance(newurl, str) or not _allowed_download_host(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)
