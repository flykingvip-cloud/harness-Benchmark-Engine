"""Local click-to-install for pinned competitor harnesses.

npm rows install one ``registry.npmjs.org`` tarball with ``npm install
--ignore-scripts``. The profile is admitted only after ``package.json``
matches the catalog pin and the package bin is an executable inside the
install prefix. Desktop rows download the catalog file for this OS and do
not launch it. A desktop product with no versioned URL is not fetched.
``latest`` is not a target. Secrets and URLs are not written to the receipt
or the harness profile.

Remote install is not done here. SSH auth still ends at ``true``.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from ohbe.environment.policy import is_cloud_litellm_host, is_denied_provider_host, normalize_host
from ohbe.preinstall import PreinstallEntry, is_concrete_package_version

_NPM_HOST = "registry.npmjs.org"
_ADAPTER = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")
_RELATIVE = re.compile(r"^[A-Za-z0-9_@][A-Za-z0-9_@./-]{0,240}$")
_MAX_BYTES = 512 * 1024 * 1024
_RECEIPT_NAME = "receipt.yaml"

ClickReason = Literal[
    "admitted",
    "deps_missing",
    "desktop_registered",
    "install_failed",
    "install_unsupported",
    "installer_unpinned",
    "npm_missing",
    "os_unsupported",
    "version_rejected",
]


class ClickInstallError(Exception):
    """The click stopped before a profile write. ``reason`` is public."""

    def __init__(self, reason: ClickReason, *, offer_install: bool = False) -> None:
        self.reason: ClickReason = reason
        self.offer_install = offer_install
        super().__init__(reason)


@dataclass(frozen=True)
class PreparedInstall:
    """A local pin that is on disk. ``admit`` writes the harness profile."""

    version: str
    reason: Literal["admitted", "desktop_registered"]
    admit: bool


def current_platform() -> tuple[str, str]:
    """``(system, machine)`` from the host. Tests replace this."""

    return platform.system(), platform.machine()


def npm_executable() -> str | None:
    """``npm`` on ``PATH``, or None. Tests replace this."""

    found = shutil.which("npm")
    return found if isinstance(found, str) and found else None


def run_npm(
    argv: list[str],
    env: Mapping[str, str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Run one npm argv. The caller built it. No shell."""

    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
        env=dict(env),
        cwd=str(cwd),
        shell=False,
    )


def npm_tarball_ok(entry: PreinstallEntry) -> bool:
    """True when the catalog URL is the pinned registry tarball."""

    url = entry.install_url
    if entry.install_channel != "npm" or not url or url not in entry.install_urls:
        return False
    if not is_concrete_package_version(entry.version) or "latest" in url.lower():
        return False
    name = npm_package_name(url)
    if name is None:
        return False
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    short = name.split("/")[-1]
    return filename == f"{short}-{entry.version}.tgz"


def npm_package_name(url: str) -> str | None:
    """Package name encoded in a ``registry.npmjs.org`` tarball URL."""

    if not _https_host(url, _NPM_HOST):
        return None
    path = urlsplit(url).path
    if "/-/" not in path:
        return None
    head = path.split("/-/", 1)[0].strip("/")
    parts = head.split("/")
    if not head or any(part in {"", ".", ".."} for part in parts):
        return None
    if head.startswith("@") and len(parts) != 2:
        return None
    if not head.startswith("@") and len(parts) != 1:
        return None
    return head


def preview_local_install(
    entry: PreinstallEntry,
    *,
    system: str | None = None,
    machine: str | None = None,
) -> tuple[ClickReason, bool] | None:
    """Check-only status for a missing npm or desktop pin.

    ``None`` means this is not an npm or desktop row. The pair is
    ``(reason, offer_install)``. Nothing is downloaded.
    """

    if entry.install_channel == "npm":
        if not npm_tarball_ok(entry):
            return "install_unsupported", False
        if npm_executable() is None:
            return "npm_missing", False
        return "deps_missing", True
    if entry.install_channel != "desktop":
        return None
    kind = _desktop_kind(entry, *_resolve_platform(system, machine))
    if kind == "unpinned":
        return "installer_unpinned", False
    if kind == "unsupported_os":
        return "os_unsupported", False
    if kind == "download":
        return "deps_missing", True
    return "install_unsupported", False


def prepare_local_install(
    root: Path,
    entry: PreinstallEntry,
    *,
    fetch: bool,
    parent_env: Mapping[str, str],
) -> PreparedInstall:
    """Put the pinned artifact on disk, or raise ``ClickInstallError``.

    An npm success asks the caller to admit the profile. A desktop download
    admits only a closed office row. WorkBuddy's GUI package is registered
    on disk and is not treated as the bundled CLI.
    """

    if entry.install_channel == "npm":
        return _prepare_npm(root, entry, fetch=fetch, parent_env=parent_env)
    if entry.install_channel == "desktop":
        return _prepare_desktop(root, entry, fetch=fetch)
    raise ClickInstallError("install_unsupported")


def receipt_version(root: Path, entry: PreinstallEntry) -> str | None:
    """Catalog pin already on disk, or None.

    npm counts when the bin and ``package.json`` match. A closed desktop row
    counts when the installer file is present and was not executed. A
    runnable desktop app does not count: the GUI package is not the CLI.
    """

    if entry.install_channel == "npm":
        binary = installed_executable(root, entry.adapter)
        return entry.version if binary is not None else None
    if entry.install_channel == "desktop" and entry.closed_reason and not entry.runnable:
        receipt = _read_receipt(root, entry.adapter)
        if receipt is None or receipt.get("status") != "downloaded-not-executed":
            return None
        if receipt.get("version") != entry.version or receipt.get("channel") != "desktop":
            return None
        filename = receipt.get("filename")
        if not isinstance(filename, str) or _safe_name(filename, entry.version) is None:
            return None
        path = _prefix(root, entry.adapter) / filename
        if path.is_file() and path.stat().st_size > 0:
            return entry.version
    return None


def installed_executable(root: Path, adapter: str) -> Path | None:
    """Executable recorded for an npm pin, or None."""

    entry_version = _receipt_field(root, adapter, "version")
    binary_rel = _receipt_field(root, adapter, "binary")
    channel = _receipt_field(root, adapter, "channel")
    status = _receipt_field(root, adapter, "status")
    if channel != "npm" or status != "installed" or not isinstance(binary_rel, str):
        return None
    if not isinstance(entry_version, str) or not is_concrete_package_version(entry_version):
        return None
    prefix = _prefix(root, adapter)
    if not _relative_ok(binary_rel):
        return None
    recorded = prefix / binary_rel
    if not _inside(recorded, prefix):
        return None
    if recorded.is_symlink() and not _inside(recorded.resolve(), prefix):
        return None
    if not _executable(recorded):
        return None
    if not _package_json_matches(prefix, recorded, entry_version):
        return None
    return recorded


def download_file(url: str, dest: Path, *, allowed_host: str) -> None:
    """Save one https object. Redirects that leave ``allowed_host`` are dropped."""

    if not _https_host(url, allowed_host):
        raise ClickInstallError("install_failed")
    opener = build_opener(_SameHostRedirect(allowed_host), HTTPSHandler(), ProxyHandler({}))
    request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "ohbe-preinstall"})
    tmp = dest.with_name(dest.name + ".part")
    try:
        with opener.open(request, timeout=120) as response:
            final = response.geturl()
            if not isinstance(final, str) or not _https_host(final, allowed_host):
                raise ClickInstallError("install_failed")
            dest.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with tmp.open("wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise ClickInstallError("install_failed")
                    handle.write(chunk)
            if total < 1:
                raise ClickInstallError("install_failed")
            tmp.replace(dest)
    except ClickInstallError:
        tmp.unlink(missing_ok=True)
        raise
    except (OSError, TimeoutError, ValueError):
        tmp.unlink(missing_ok=True)
        raise ClickInstallError("install_failed") from None


def select_desktop_url(entry: PreinstallEntry, system: str, machine: str) -> str | None:
    """The one catalog URL for this OS, or None."""

    urls = [url for url in entry.install_urls if _catalog_url_ok(url, entry)]
    if entry.adapter == "workbuddy":
        token = _workbuddy_token(system, machine)
        if token is None:
            return None
        matches = [url for url in urls if f"/{token}/" in urlsplit(url).path]
        return matches[0] if len(matches) == 1 else None
    if entry.adapter == "doubao_work":
        if system != "Windows":
            return None
        arch = _arch(machine)
        if arch == "arm64":
            matches = [url for url in urls if "Arm64" in urlsplit(url).path]
        elif arch == "x64":
            matches = [url for url in urls if "Arm64" not in urlsplit(url).path and url.endswith(".exe")]
        else:
            return None
        return matches[0] if len(matches) == 1 else None
    return None


def _prepare_npm(
    root: Path,
    entry: PreinstallEntry,
    *,
    fetch: bool,
    parent_env: Mapping[str, str],
) -> PreparedInstall:
    if not npm_tarball_ok(entry):
        raise ClickInstallError("install_unsupported")
    if installed_executable(root, entry.adapter) is not None:
        return PreparedInstall(version=entry.version, reason="admitted", admit=True)
    if not fetch:
        if npm_executable() is None:
            raise ClickInstallError("npm_missing")
        raise ClickInstallError("deps_missing", offer_install=True)
    _install_npm(root, entry, parent_env)
    if installed_executable(root, entry.adapter) is None:
        raise ClickInstallError("version_rejected")
    return PreparedInstall(version=entry.version, reason="admitted", admit=True)


def _prepare_desktop(root: Path, entry: PreinstallEntry, *, fetch: bool) -> PreparedInstall:
    system, machine = current_platform()
    kind = _desktop_kind(entry, system, machine)
    if kind == "unpinned":
        raise ClickInstallError("installer_unpinned")
    if kind == "unsupported_os":
        raise ClickInstallError("os_unsupported")
    if kind != "download":
        raise ClickInstallError("install_unsupported")
    url = select_desktop_url(entry, system, machine)
    if url is None:
        raise ClickInstallError("os_unsupported")
    filename = _safe_name(urlsplit(url).path.rsplit("/", 1)[-1], entry.version)
    host = normalize_host(urlsplit(url).hostname or "")
    if filename is None or not host:
        raise ClickInstallError("install_unsupported")
    dest = _prefix(root, entry.adapter) / filename
    receipt = _read_receipt(root, entry.adapter)
    ready = (
        receipt is not None
        and receipt.get("status") == "downloaded-not-executed"
        and receipt.get("filename") == filename
        and receipt.get("version") == entry.version
        and dest.is_file()
        and dest.stat().st_size > 0
    )
    if not ready:
        if not fetch:
            raise ClickInstallError("deps_missing", offer_install=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        download_file(url, dest, allowed_host=host)
        if not dest.is_file() or dest.stat().st_size < 1:
            raise ClickInstallError("install_failed")
        _write_receipt(
            root,
            entry.adapter,
            {
                "adapter": entry.adapter,
                "binary": "",
                "channel": "desktop",
                "filename": filename,
                "status": "downloaded-not-executed",
                "version": entry.version,
            },
        )
    admit = bool(entry.closed_reason) and not entry.runnable
    return PreparedInstall(version=entry.version, reason="desktop_registered", admit=admit)


def _install_npm(root: Path, entry: PreinstallEntry, parent_env: Mapping[str, str]) -> None:
    npm = npm_executable()
    if npm is None:
        raise ClickInstallError("npm_missing")
    url = entry.install_url
    package = npm_package_name(url)
    if package is None or not npm_tarball_ok(entry):
        raise ClickInstallError("install_unsupported")
    prefix = _prefix(root, entry.adapter)
    prefix.mkdir(parents=True, exist_ok=True)
    marker = prefix / "package.json"
    if not marker.exists():
        marker.write_text(
            '{"name":"ohbe-install","private":true,"version":"0.0.0"}\n',
            encoding="utf-8",
        )
    argv = [
        npm,
        "install",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--prefix",
        str(prefix),
        url,
    ]
    if any("://" in part for part in argv[:-1]):
        raise ClickInstallError("install_failed")
    env = _npm_env(prefix, parent_env)
    try:
        completed = run_npm(argv, env, prefix)
    except (OSError, subprocess.TimeoutExpired):
        raise ClickInstallError("install_failed") from None
    if completed.returncode != 0:
        raise ClickInstallError("install_failed")
    binary = _ensure_npm_bin(prefix, package, entry.version)
    relative = binary.relative_to(prefix).as_posix()
    _write_receipt(
        root,
        entry.adapter,
        {
            "adapter": entry.adapter,
            "binary": relative,
            "channel": "npm",
            "filename": "",
            "status": "installed",
            "version": entry.version,
        },
    )


def _ensure_npm_bin(prefix: Path, package: str, version: str) -> Path:
    package_dir = prefix / "node_modules" / package
    meta_path = package_dir / "package.json"
    if not meta_path.is_file():
        raise ClickInstallError("install_failed")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ClickInstallError("install_failed") from exc
    if not isinstance(meta, dict) or meta.get("name") != package or meta.get("version") != version:
        raise ClickInstallError("version_rejected")
    relative = _bin_relative(meta, package)
    if relative is None:
        raise ClickInstallError("install_failed")
    target = _relative_file(package_dir, relative)
    if target is None:
        raise ClickInstallError("install_failed")
    short = package.split("/")[-1]
    linked = prefix / "node_modules" / ".bin" / short
    if not _executable(linked):
        linked.parent.mkdir(parents=True, exist_ok=True)
        if linked.is_symlink() or linked.exists():
            linked.unlink()
        linked.symlink_to(target)
        linked.chmod(linked.stat().st_mode | stat.S_IXUSR)
    if not _executable(linked) or not _inside(linked.resolve(), prefix):
        raise ClickInstallError("install_failed")
    return linked


def _bin_relative(meta: Mapping[str, object], package: str) -> str | None:
    bins = meta.get("bin")
    short = package.split("/")[-1]
    if isinstance(bins, str) and _relative_ok(bins):
        return bins
    if isinstance(bins, dict):
        preferred = bins.get(short)
        if isinstance(preferred, str) and _relative_ok(preferred):
            return preferred
        if len(bins) == 1:
            only = next(iter(bins.values()))
            if isinstance(only, str) and _relative_ok(only):
                return only
    return None


def _package_json_matches(prefix: Path, binary: Path, version: str) -> bool:
    """True when some parent ``package.json`` under the prefix names ``version``."""

    if not _inside(binary, prefix):
        return False
    resolved = binary.resolve()
    modules = (prefix / "node_modules").resolve()
    current = resolved.parent
    while _inside(current, modules) or current == modules:
        meta_path = current / "package.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return False
            if isinstance(meta, dict) and meta.get("version") == version and meta.get("name") != "ohbe-install":
                return True
        if current == modules:
            break
        current = current.parent
    return False


def _desktop_kind(entry: PreinstallEntry, system: str, machine: str) -> str:
    urls = tuple(url for url in entry.install_urls if url)
    if not urls and not entry.install_url:
        return "unpinned"
    valid = [url for url in urls if _catalog_url_ok(url, entry)]
    if not valid:
        return "install_unsupported"
    if select_desktop_url(entry, system, machine) is None:
        return "unsupported_os"
    return "download"


def _catalog_url_ok(url: str, entry: PreinstallEntry) -> bool:
    if not isinstance(url, str) or url not in entry.install_urls:
        return False
    if "latest" in url.lower() or not is_concrete_package_version(entry.version):
        return False
    parts = urlsplit(url)
    host = normalize_host(parts.hostname or "")
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.query or parts.fragment:
        return False
    if is_denied_provider_host(host) or is_cloud_litellm_host(host):
        return False
    return _safe_name(parts.path.rsplit("/", 1)[-1], entry.version) is not None


def _https_host(url: str, host: str) -> bool:
    if not isinstance(url, str) or not url.startswith("https://") or len(url) > 2000:
        return False
    parts = urlsplit(url)
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    found = normalize_host(parts.hostname or "")
    if found != host or is_denied_provider_host(found) or is_cloud_litellm_host(found):
        return False
    return True


def _workbuddy_token(system: str, machine: str) -> str | None:
    arch = _arch(machine)
    if system == "Darwin" and arch == "arm64":
        return "darwin-arm64"
    if system == "Darwin" and arch == "x64":
        return "darwin-x64"
    if system == "Windows" and arch == "x64":
        return "win32-x64-user"
    return None


def _arch(machine: str) -> str:
    text = machine.lower()
    if text in {"arm64", "aarch64"}:
        return "arm64"
    if text in {"x86_64", "amd64", "x64"}:
        return "x64"
    return "other"


def _resolve_platform(system: str | None, machine: str | None) -> tuple[str, str]:
    current_system, current_machine = current_platform()
    return system or current_system, machine or current_machine


def _safe_name(filename: str, version: str) -> str | None:
    if not isinstance(filename, str) or _FILENAME.fullmatch(filename) is None:
        return None
    if filename != Path(filename).name or ".." in filename or "latest" in filename.lower():
        return None
    if version not in filename:
        return None
    return filename


def _relative_ok(value: str) -> bool:
    if not isinstance(value, str) or _RELATIVE.fullmatch(value) is None:
        return False
    if value.startswith("/") or value.startswith("\\") or ".." in Path(value).parts:
        return False
    return "://" not in value


def _prefix(root: Path, adapter: str) -> Path:
    if _ADAPTER.fullmatch(adapter) is None:
        raise ClickInstallError("install_unsupported")
    root_resolved = root.resolve()
    prefix = (root_resolved / "harnesses" / "installs" / adapter).resolve()
    if not _inside(prefix, root_resolved):
        raise ClickInstallError("install_unsupported")
    return prefix


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _relative_file(root: Path, relative: str) -> Path | None:
    if not _relative_ok(relative):
        return None
    candidate = (root / relative).resolve()
    if not _inside(candidate, root.resolve()) or not candidate.is_file():
        return None
    return candidate


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _npm_env(prefix: Path, parent: Mapping[str, str]) -> dict[str, str]:
    path = parent.get("PATH", "")
    env = {
        "PATH": path if isinstance(path, str) else "",
        "HOME": str(prefix),
        "npm_config_audit": "false",
        "npm_config_cache": str(prefix / "npm-cache"),
        "npm_config_fund": "false",
        "npm_config_ignore_scripts": "true",
        "npm_config_update_notifier": "false",
    }
    return env


def _receipt_path(root: Path, adapter: str) -> Path:
    return _prefix(root, adapter) / _RECEIPT_NAME


def _write_receipt(root: Path, adapter: str, fields: Mapping[str, str]) -> None:
    allowed = ("adapter", "binary", "channel", "filename", "status", "version")
    if tuple(fields) != allowed or fields["adapter"] != adapter:
        raise ClickInstallError("install_failed")
    lines = [f"{key}: {json.dumps(fields[key], ensure_ascii=False)}" for key in allowed]
    text = "\n".join(lines) + "\n"
    if "://" in text or "sk-" in text.lower() or "latest" in text.lower():
        raise ClickInstallError("install_failed")
    path = _receipt_path(root, adapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_receipt(root: Path, adapter: str) -> dict[str, str] | None:
    path = _receipt_path(root, adapter)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if "://" in text or "sk-" in text.lower() or "latest" in text.lower():
        return None
    found: dict[str, str] = {}
    for raw in text.splitlines():
        if ": " not in raw:
            return None
        key, encoded = raw.split(": ", 1)
        try:
            value = json.loads(encoded)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, str):
            return None
        found[key] = value
    if found.get("adapter") != adapter:
        return None
    return found


def _receipt_field(root: Path, adapter: str, key: str) -> str | None:
    receipt = _read_receipt(root, adapter)
    if receipt is None:
        return None
    value = receipt.get(key)
    return value if isinstance(value, str) else None


class _SameHostRedirect(HTTPRedirectHandler):
    """Refuse a redirect that leaves the catalog host."""

    def __init__(self, host: str) -> None:
        super().__init__()
        self._host = host

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not isinstance(newurl, str) or not _https_host(newurl, self._host):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)
