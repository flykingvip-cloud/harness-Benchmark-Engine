"""Local click-to-install for the five comparison harnesses.

npm installs are faked. Desktop downloads are faked. Neither test contacts
a registry or launches an installer.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.adapters.deepseek_harness.runner import which_dsh
from ohbe.adapters.openclaw.runner import which_openclaw
from ohbe.api.app import create_app
from ohbe.click_install import npm_tarball_ok, select_desktop_url
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import (
    DSH_INSTALL_URL,
    DSH_PROFILE_VERSION,
    DOUBAO_WORK_INSTALL_URLS,
    DOUBAO_WORK_VERSION,
    OPENCLAW_INSTALL_URL,
    OPENCLAW_NPM_VERSION,
    WORKBUDDY_INSTALL_URLS,
    WORKBUDDY_VERSION,
    entry_for,
)

SECRET = "sk-click-install-secret"


def _client(tmp_path: Path, environ: dict[str, str] | None = None) -> TestClient:
    return TestClient(create_app(tmp_path, environ=environ or {}))


def _install(adapter: str, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "adapter": adapter,
        "target": "local",
        "host": "",
        "port": None,
        "install_deps": True,
        "timeout_seconds": 30,
    }
    body.update(extra)
    return body


def _stage_npm(prefix: Path, package: str, version: str, bin_name: str) -> None:
    package_dir = prefix / "node_modules" / package
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "package.json").write_text(
        json.dumps({"name": package, "version": version, "bin": {bin_name: "cli.js"}}),
        encoding="utf-8",
    )
    script = package_dir / "cli.js"
    script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def test_desktop_selection_follows_the_os_and_rejects_latest() -> None:
    workbuddy = entry_for("workbuddy")
    doubao = entry_for("doubao_work")
    dsh = entry_for("deepseek_harness")
    openclaw = entry_for("openclaw")
    qwen = entry_for("qwen_work")
    assert workbuddy is not None and doubao is not None and dsh is not None
    assert openclaw is not None and qwen is not None
    assert select_desktop_url(workbuddy, "Darwin", "arm64") == WORKBUDDY_INSTALL_URLS[0]
    assert select_desktop_url(workbuddy, "Darwin", "x86_64") == WORKBUDDY_INSTALL_URLS[1]
    assert select_desktop_url(workbuddy, "Windows", "AMD64") == WORKBUDDY_INSTALL_URLS[2]
    assert select_desktop_url(workbuddy, "Linux", "x86_64") is None
    assert select_desktop_url(workbuddy, "Windows", "ARM64") is None
    assert select_desktop_url(doubao, "Windows", "AMD64") == DOUBAO_WORK_INSTALL_URLS[0]
    assert select_desktop_url(doubao, "Windows", "ARM64") == DOUBAO_WORK_INSTALL_URLS[1]
    assert select_desktop_url(doubao, "Darwin", "arm64") is None
    assert select_desktop_url(qwen, "Windows", "AMD64") is None
    assert npm_tarball_ok(dsh) is True
    assert npm_tarball_ok(openclaw) is True
    assert npm_tarball_ok(workbuddy) is False
    assert "latest" not in DSH_INSTALL_URL
    assert "latest" not in OPENCLAW_INSTALL_URL


def test_npm_click_installs_the_pin_and_registers_the_bin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    seen_env: list[dict[str, str]] = []

    def fake_npm(argv: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        seen_env.append(dict(env))
        assert "--ignore-scripts" in argv
        assert argv[-1] == DSH_INSTALL_URL
        assert SECRET not in env.values()
        assert all(SECRET not in part for part in argv)
        _stage_npm(Path(cwd), "@deepseek-ai/dsh", DSH_PROFILE_VERSION, "dsh")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ohbe.click_install.npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr("ohbe.click_install.run_npm", fake_npm)
    monkeypatch.setattr("ohbe.click_install.download_file", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("download")))
    client = _client(tmp_path, {"PATH": "/usr/bin", "LITELLM_API_KEY": SECRET})
    saved = client.post("/v1/harness-library/install", json=_install("deepseek_harness"))
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["reason"] == "admitted"
    assert body["version"] == DSH_PROFILE_VERSION
    assert body["executed"] is False
    assert body["spend"] == "none"
    assert body["in_live_order"] is False
    assert body["harness_order"] == ["openbot", "inspect_react"]
    assert body["admitted"] == ["deepseek-harness-pin"]
    assert SECRET not in saved.text
    assert "://" not in saved.text
    assert "latest" not in saved.text
    text = (tmp_path / "harnesses" / "profiles" / "deepseek-harness-pin.yaml").read_text(encoding="utf-8")
    receipt = (tmp_path / "harnesses" / "installs" / "deepseek_harness" / "receipt.yaml").read_text(encoding="utf-8")
    assert "live_run: rejected" in text
    assert 'channel: "npm"' in text
    assert DSH_PROFILE_VERSION in text
    assert DSH_INSTALL_URL not in text
    assert "://" not in receipt
    assert "installed" in receipt
    assert SECRET not in text and SECRET not in receipt
    assert calls and calls[0][0] == "/usr/bin/npm"
    binary = tmp_path / "harnesses" / "installs" / "deepseek_harness" / "node_modules" / ".bin" / "dsh"
    assert binary.is_file()
    monkeypatch.setenv("OHBE_ROOT", str(tmp_path))
    assert which_dsh() == str(binary)

    again = client.post("/v1/harness-library/install", json=_install("deepseek_harness"))
    assert again.json()["reason"] == "admitted"
    assert len(calls) == 1
    assert HARNESS_ORDER == ("openbot", "inspect_react")


def test_openclaw_version_mismatch_does_not_admit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_npm(argv: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        del env
        assert argv[-1] == OPENCLAW_INSTALL_URL
        assert "--ignore-scripts" in argv
        _stage_npm(Path(cwd), "openclaw", "0.0.1", "openclaw")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("ohbe.click_install.npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr("ohbe.click_install.run_npm", fake_npm)
    client = _client(tmp_path, {"OPENCLAW_VERSION": "latest"})
    saved = client.post("/v1/harness-library/install", json=_install("openclaw"))
    assert saved.json()["ok"] is False
    assert saved.json()["reason"] == "version_rejected"
    assert saved.json()["version"] == OPENCLAW_NPM_VERSION
    assert not (tmp_path / "harnesses" / "profiles").exists()
    assert "latest" not in saved.text
    assert "://" not in saved.text


def test_windows_downloads_are_recorded_and_not_executed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    def fake_download(url: str, dest: Path, *, allowed_host: str) -> None:
        fetched.append(url)
        assert allowed_host
        assert url.startswith("https://")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"installer")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("execute")

    monkeypatch.setattr("ohbe.click_install.current_platform", lambda: ("Windows", "AMD64"))
    monkeypatch.setattr("ohbe.click_install.download_file", fake_download)
    monkeypatch.setattr("ohbe.click_install.run_npm", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    workbuddy_root = tmp_path / "workbuddy"
    workbuddy = _client(workbuddy_root)
    saved = workbuddy.post("/v1/harness-library/install", json=_install("workbuddy"))
    body = saved.json()
    assert body["ok"] is True
    assert body["reason"] == "desktop_registered"
    assert body["admitted"] == []
    assert body["version"] == WORKBUDDY_VERSION
    assert body["executed"] is False
    assert body["spend"] == "none"
    assert body["harness_order"] == ["openbot", "inspect_react"]
    assert fetched == [WORKBUDDY_INSTALL_URLS[2]]
    assert not (workbuddy_root / "harnesses" / "profiles").exists()
    receipt = (workbuddy_root / "harnesses" / "installs" / "workbuddy" / "receipt.yaml").read_text(encoding="utf-8")
    assert "downloaded-not-executed" in receipt
    assert "://" not in receipt
    assert WORKBUDDY_INSTALL_URLS[2] not in receipt
    assert WORKBUDDY_INSTALL_URLS[2] not in saved.text

    doubao_root = tmp_path / "doubao"
    doubao = _client(doubao_root)
    admitted = doubao.post("/v1/harness-library/install", json=_install("doubao_work"))
    assert admitted.json()["reason"] == "desktop_registered"
    assert admitted.json()["admitted"] == ["doubao-work-pin"]
    assert admitted.json()["in_live_order"] is False
    text = (doubao_root / "harnesses" / "profiles" / "doubao-work-pin.yaml").read_text(encoding="utf-8")
    assert "runnable: false" in text
    assert 'install-pin: "windows-cdn-not-executed"' in text
    assert "://" not in text
    for url in DOUBAO_WORK_INSTALL_URLS:
        assert url not in text
        assert url not in admitted.text
    assert fetched[-1] == DOUBAO_WORK_INSTALL_URLS[0]
    assert HARNESS_ORDER == ("openbot", "inspect_react")


def test_scan_reads_an_npm_receipt_and_ignores_a_gui_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "harnesses" / "installs" / "openclaw"
    _stage_npm(prefix, "openclaw", OPENCLAW_NPM_VERSION, "openclaw")
    bindir = prefix / "node_modules" / ".bin"
    bindir.mkdir(parents=True)
    binary = bindir / "openclaw"
    target = prefix / "node_modules" / "openclaw" / "cli.js"
    binary.symlink_to(target)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    receipt = prefix / "receipt.yaml"
    receipt.write_text(
        "\n".join(
            [
                'adapter: "openclaw"',
                'binary: "node_modules/.bin/openclaw"',
                'channel: "npm"',
                'filename: ""',
                'status: "installed"',
                f'version: "{OPENCLAW_NPM_VERSION}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    gui = tmp_path / "harnesses" / "installs" / "workbuddy"
    gui.mkdir(parents=True)
    (gui / "WorkBuddy-win32-x64-user-5.7.7.exe").write_bytes(b"installer")
    (gui / "receipt.yaml").write_text(
        "\n".join(
            [
                'adapter: "workbuddy"',
                'binary: ""',
                'channel: "desktop"',
                'filename: "WorkBuddy-win32-x64-user-5.7.7.exe"',
                'status: "downloaded-not-executed"',
                f'version: "{WORKBUDDY_VERSION}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("ohbe.click_install.run_npm", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("npm")))
    client = _client(tmp_path)
    scanned = client.post("/v1/harness-library/scan", json={"target": "local", "host": "", "port": None})
    assert scanned.json()["ok"] is True
    assert "openclaw-plan" in scanned.json()["admitted"]
    assert "workbuddy-pin" not in scanned.json()["admitted"]
    monkeypatch.setenv("OHBE_ROOT", str(tmp_path))
    assert which_openclaw() == str(binary)
    assert os.access(binary, os.X_OK)
