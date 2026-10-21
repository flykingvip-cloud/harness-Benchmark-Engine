"""Selectable harness preinstall catalog.

The catalog is the list a workbench can register and the place version
detection is reported. It is not the live whitelist. ``registration="adapter"``
records a name that is already in the live pair. It does not by itself mean
every Adapter class is live. DeepSeek Harness, OpenClaw, and WorkBuddy have
opt-in Adapter classes and stay ``plan``, so they do not enter
``ohbe.live.spec.HARNESS_ORDER``. Plan entries stay out of that tuple until
it is changed on purpose. WorkBuddy's adapter is the bundled CodeBuddy CLI,
not the desktop installer.

Detection reads local package metadata, ``OPENBOT_COMMIT``, or a
per-harness version variable such as ``DSH_VERSION``. It does not install a
package, open a socket, or start a run. A profile version is a concrete pin
or a detected build. The floating token ``latest`` is rejected.

WorkBuddy, OpenClaw, DeepSeek Harness, 豆包工作, and 千问办公 are comparison
rows. Each stores a concrete version when the publisher has one, and a
versioned artifact URL when one was published. A floating ``/latest/`` URL
is not stored. Those rows stay out of ``HARNESS_ORDER``. Desktop and npm
installers are not executed by this catalog. A matching version variable
and binary can admit a row without running the installer.

千问办公 and 豆包工作 are closed. Their adapters refuse to run. A desktop
binary on disk is not a LiteLLM-only headless path. A local click installs
an npm pin with ``npm install --ignore-scripts``, or downloads the desktop
file for this OS and does not launch it. 千问办公 has no versioned URL, so
that click does not download.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PACKAGE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
_FLOATING_VERSIONS = frozenset({"latest", "unpinned", "main", "master"})

VersionKind = Literal["git_sha", "exact", "package"]
Registration = Literal["adapter", "plan"]
VersionState = Literal["pin", "detected", "unavailable", "differs"]
InstallChannel = Literal["pip", "npm", "desktop", "unpublished"]

OPENBOT_VERSION_PLACEHOLDER = "openbot-commit-unavailable"
INSPECT_PROFILE_VERSION = "0.3.277"
DSH_PROFILE_VERSION = "0.2.1-alpha.2"
DSH_INSTALL_URL = "https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-alpha.2.tgz"
OPENCLAW_NPM_VERSION = "2026.10.1"
OPENCLAW_INSTALL_URL = "https://registry.npmjs.org/openclaw/-/openclaw-2026.10.1.tgz"
WORKBUDDY_VERSION = "5.7.7"
# Official history page, v5.7.7, 2026-10-09. No Linux artifact is published.
WORKBUDDY_INSTALL_URLS = (
    "https://acc-1258344699.cos.ap-guangzhou.myqcloud.com/workbuddy/saas/darwin-arm64/WorkBuddy-darwin-arm64-5.7.7.40774747-2e8619da.dmg",
    "https://acc-1258344699.cos.ap-guangzhou.myqcloud.com/workbuddy/saas/darwin-x64/WorkBuddy-darwin-x64-5.7.7.40774747-2e8619da.dmg",
    "https://acc-1258344699.cos.ap-guangzhou.myqcloud.com/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.7.7.40774747-2e8619da.exe",
)
# docs.qwenwork.cn desktop notes, 2026-10-09, still the newest heading on
# 2026-10-11. assets.qwenwork.cn/release/1.2.5/ and /release/v1.2.5/ return
# 404. /release/latest/ returns 200 with an unversioned filename. That URL
# is not a pin and is not stored.
QWEN_WORK_VERSION = "1.2.5"
# microsoft/winget-pkgs ByteDance.DoubaoWork, newest of 2.25.17, 2.31.8, and
# 2.31.10. The URLs are the publisher CDN. macOS on doubao.com/work is still
# not a versioned artifact. These installers are not executed.
DOUBAO_WORK_VERSION = "2.31.10"
DOUBAO_WORK_INSTALL_URLS = (
    "https://lf-flow-web-cdn.doubao.com/obj/flow-doubao/doubao_pc/2.31.10/DoubaoWork_installer_2.31.10.exe",
    "https://lf-flow-web-cdn.doubao.com/obj/flow-doubao/doubao_pc/2.31.10/DoubaoWork_Arm64_installer_2.31.10.exe",
)
OFFICE_CLOSED_REASON = "no-litellm-only-headless"

HarnessAdapterName = Literal[
    "openbot",
    "inspect_react",
    "workbuddy",
    "openclaw",
    "deepseek_harness",
    "doubao_work",
    "qwen_work",
    "langgraph",
    "crewai",
    "autogen",
    "semantic_kernel",
    "claude_code",
]


class PreinstallVersionError(ValueError):
    """The requested version is not the catalog pin or the detected build."""


def floating_version(version: str) -> bool:
    return isinstance(version, str) and version.lower() in _FLOATING_VERSIONS


def is_concrete_package_version(version: str) -> bool:
    """True when a package version may be stored. ``latest`` is not concrete."""

    if not isinstance(version, str) or floating_version(version):
        return False
    if _PACKAGE_VERSION.fullmatch(version) is None or version.endswith("-unavailable"):
        return False
    lowered = version.lower()
    if "sk-" in lowered or "://" in version or "api_key" in lowered:
        return False
    if "openai.com" in lowered or "anthropic.com" in lowered:
        return False
    return True


@dataclass(frozen=True)
class PreinstallEntry:
    """One selectable preinstall. ``in_live_order`` mirrors an existing Adapter."""

    adapter: str
    harness_id: str
    name: str
    profile_id: str
    version: str
    version_kind: VersionKind
    registration: Registration
    distributions: tuple[str, ...] = ()
    install_channel: InstallChannel = "pip"
    install_url: str = ""
    install_urls: tuple[str, ...] = ()
    version_env: str = ""
    binary_env: str = ""
    runnable: bool = False
    closed_reason: str = ""

    @property
    def in_live_order(self) -> bool:
        return self.registration == "adapter"

    @property
    def package(self) -> str | None:
        return self.distributions[0] if self.distributions else None


# Order is the workbench order. Live order is not this tuple.
PREINSTALL_CATALOG: tuple[PreinstallEntry, ...] = (
    PreinstallEntry(
        adapter="openbot",
        harness_id="openbot",
        name="openbot-benchmark",
        profile_id="openbot-benchmark",
        version=OPENBOT_VERSION_PLACEHOLDER,
        version_kind="git_sha",
        registration="adapter",
        runnable=True,
    ),
    PreinstallEntry(
        adapter="inspect_react",
        harness_id="inspect-react",
        name="inspect-react",
        profile_id="inspect-react-pin",
        version=INSPECT_PROFILE_VERSION,
        version_kind="exact",
        registration="adapter",
        distributions=("inspect-ai",),
        runnable=True,
    ),
    PreinstallEntry(
        adapter="workbuddy",
        harness_id="workbuddy",
        name="workbuddy",
        profile_id="workbuddy-pin",
        version=WORKBUDDY_VERSION,
        version_kind="exact",
        registration="plan",
        install_channel="desktop",
        install_url=WORKBUDDY_INSTALL_URLS[2],
        install_urls=WORKBUDDY_INSTALL_URLS,
        version_env="WORKBUDDY_VERSION",
        binary_env="WORKBUDDY_BIN",
        runnable=True,
    ),
    PreinstallEntry(
        adapter="openclaw",
        harness_id="openclaw",
        name="openclaw-plan",
        profile_id="openclaw-plan",
        version=OPENCLAW_NPM_VERSION,
        version_kind="exact",
        registration="plan",
        install_channel="npm",
        install_url=OPENCLAW_INSTALL_URL,
        install_urls=(OPENCLAW_INSTALL_URL,),
        version_env="OPENCLAW_VERSION",
        binary_env="OPENCLAW_BIN",
        runnable=True,
    ),
    PreinstallEntry(
        adapter="deepseek_harness",
        harness_id="deepseek-harness",
        name="deepseek-harness",
        profile_id="deepseek-harness-pin",
        version=DSH_PROFILE_VERSION,
        version_kind="exact",
        registration="plan",
        install_channel="npm",
        install_url=DSH_INSTALL_URL,
        install_urls=(DSH_INSTALL_URL,),
        version_env="DSH_VERSION",
        binary_env="DSH_BIN",
        runnable=True,
    ),
    PreinstallEntry(
        adapter="doubao_work",
        harness_id="doubao-work",
        name="doubao-work",
        profile_id="doubao-work-pin",
        version=DOUBAO_WORK_VERSION,
        version_kind="exact",
        registration="plan",
        install_channel="desktop",
        install_url=DOUBAO_WORK_INSTALL_URLS[0],
        install_urls=DOUBAO_WORK_INSTALL_URLS,
        version_env="DOUBAO_WORK_VERSION",
        binary_env="DOUBAO_WORK_BIN",
        closed_reason=OFFICE_CLOSED_REASON,
    ),
    PreinstallEntry(
        adapter="qwen_work",
        harness_id="qwen-work",
        name="qwen-work",
        profile_id="qwen-work-pin",
        version=QWEN_WORK_VERSION,
        version_kind="exact",
        registration="plan",
        install_channel="desktop",
        version_env="QWEN_WORK_VERSION",
        binary_env="QWEN_WORK_BIN",
        closed_reason=OFFICE_CLOSED_REASON,
    ),
    PreinstallEntry(
        adapter="langgraph",
        harness_id="langgraph",
        name="langgraph-plan",
        profile_id="langgraph-plan",
        version="langgraph-version-unavailable",
        version_kind="package",
        registration="plan",
        distributions=("langgraph",),
    ),
    PreinstallEntry(
        adapter="crewai",
        harness_id="crewai",
        name="crewai-plan",
        profile_id="crewai-plan",
        version="crewai-version-unavailable",
        version_kind="package",
        registration="plan",
        distributions=("crewai",),
    ),
    PreinstallEntry(
        adapter="autogen",
        harness_id="autogen",
        name="autogen-plan",
        profile_id="autogen-plan",
        version="autogen-version-unavailable",
        version_kind="package",
        registration="plan",
        distributions=("autogen-agentchat", "pyautogen"),
    ),
    PreinstallEntry(
        adapter="semantic_kernel",
        harness_id="semantic-kernel",
        name="semantic-kernel-plan",
        profile_id="semantic-kernel-plan",
        version="semantic-kernel-version-unavailable",
        version_kind="package",
        registration="plan",
        distributions=("semantic-kernel",),
    ),
    PreinstallEntry(
        adapter="claude_code",
        harness_id="claude-code",
        name="claude-code-plan",
        profile_id="claude-code-plan",
        version="claude-code-version-unavailable",
        version_kind="package",
        registration="plan",
        distributions=("claude-agent-sdk",),
    ),
)

PREINSTALL_BY_ADAPTER: dict[str, PreinstallEntry] = {entry.adapter: entry for entry in PREINSTALL_CATALOG}


def catalog_adapter_ids() -> tuple[str, ...]:
    return tuple(entry.adapter for entry in PREINSTALL_CATALOG)


def catalog_live_ids() -> tuple[str, ...]:
    """Adapters that already exist. This is not a license to grow the live tuple."""

    return tuple(entry.adapter for entry in PREINSTALL_CATALOG if entry.registration == "adapter")


def entry_for(adapter: str) -> PreinstallEntry | None:
    if not isinstance(adapter, str):
        return None
    return PREINSTALL_BY_ADAPTER.get(adapter)


def distribution_version(name: str) -> str | None:
    """Installed distribution version, or None. Local metadata only."""

    from importlib.metadata import PackageNotFoundError, version

    try:
        found = version(name)
    except PackageNotFoundError:
        return None
    if not isinstance(found, str) or _PACKAGE_VERSION.fullmatch(found) is None:
        return None
    lowered = found.lower()
    if "sk-" in lowered or "://" in found or "api_key" in lowered:
        return None
    if "openai.com" in lowered or "anthropic.com" in lowered:
        return None
    return found


def detect_version(entry: PreinstallEntry, environ: Mapping[str, str]) -> str | None:
    """Report a local version. Missing tools stay undetected."""

    if entry.version_env:
        raw = environ.get(entry.version_env, "")
        if not isinstance(raw, str):
            return None
        found = raw.strip()
        if is_concrete_package_version(found):
            return found
        return None
    if entry.version_kind == "git_sha":
        raw = environ.get("OPENBOT_COMMIT", "")
        if not isinstance(raw, str):
            return None
        commit = raw.strip().lower()
        if _COMMIT.fullmatch(commit):
            return commit
        return None
    for name in entry.distributions:
        found = distribution_version(name)
        if found:
            return found
    return None


def admitted_version(entry: PreinstallEntry, detected: str | None) -> str | None:
    """Version that may enter the library.

    A placeholder, a missing build, a pin mismatch, or a floating token such
    as ``latest`` stays out. This does not add the name to the live whitelist.
    """

    if not isinstance(detected, str) or not detected:
        return None
    if entry.version_kind == "git_sha":
        return detected if _COMMIT.fullmatch(detected) else None
    if entry.version_kind == "exact":
        if detected == entry.version and is_concrete_package_version(detected):
            return detected
        return None
    if is_concrete_package_version(detected):
        return detected
    return None


def version_state(entry: PreinstallEntry, detected: str | None) -> VersionState:
    if entry.version_kind == "exact":
        if detected is None:
            return "unavailable"
        return "pin" if detected == entry.version else "differs"
    if detected is None:
        return "unavailable"
    return "detected"


def accept_version(
    entry: PreinstallEntry,
    version: str,
    environ: Mapping[str, str],
    *,
    strict: bool,
) -> None:
    """Accept a catalog version.

    Strict mode is the save path: a package version must be the placeholder or
    the version detected right now. Load mode still accepts a previously
    detected version so a profile can be read after the package is gone.
    OpenBot keeps the existing SHA rule. Inspect stays pinned.
    """

    if entry.version_kind == "git_sha":
        if version == entry.version or _COMMIT.fullmatch(version) is not None:
            return
        raise PreinstallVersionError(
            "openbot version must be a 40-character git SHA or openbot-commit-unavailable"
        )
    if entry.version_kind == "exact":
        if version != entry.version:
            raise PreinstallVersionError(f"{entry.adapter} version must be {entry.version}")
        return
    if floating_version(version):
        raise PreinstallVersionError(f"{entry.adapter} version must be a concrete package version")
    if version == entry.version:
        return
    if strict:
        detected = detect_version(entry, environ)
        if detected is not None and version == detected and is_concrete_package_version(version):
            return
        raise PreinstallVersionError(
            f"{entry.adapter} version must be {entry.version} or the detected package version"
        )
    if is_concrete_package_version(version):
        return
    raise PreinstallVersionError(f"{entry.adapter} version must be {entry.version}")


@dataclass(frozen=True)
class CatalogDetection:
    entry: PreinstallEntry
    detected_version: str | None
    state: VersionState


def describe_catalog(environ: Mapping[str, str]) -> tuple[CatalogDetection, ...]:
    rows: list[CatalogDetection] = []
    for entry in PREINSTALL_CATALOG:
        detected = detect_version(entry, environ)
        rows.append(CatalogDetection(entry, detected, version_state(entry, detected)))
    return tuple(rows)
