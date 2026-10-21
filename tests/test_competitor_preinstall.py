"""Pinned preinstall paths for the five comparison harnesses.

Local click installs an npm pin with ``npm install --ignore-scripts`` and
downloads a desktop pin for this OS without launching it. 千问办公 has no
pinned URL. Remote install still requires an address and SSH auth, then
reports that inventory is unsupported. Secrets stay off YAML.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.api.app import create_app
from ohbe.library import resolve_pinned_install
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import (
    DSH_INSTALL_URL,
    DSH_PROFILE_VERSION,
    DOUBAO_WORK_INSTALL_URLS,
    DOUBAO_WORK_VERSION,
    OFFICE_CLOSED_REASON,
    OPENCLAW_INSTALL_URL,
    OPENCLAW_NPM_VERSION,
    QWEN_WORK_VERSION,
    WORKBUDDY_INSTALL_URLS,
    WORKBUDDY_VERSION,
    entry_for,
    is_concrete_package_version,
)

COMPETITORS = ("workbuddy", "openclaw", "deepseek_harness", "doubao_work", "qwen_work")


def _client(tmp_path: Path, environ: dict[str, str] | None = None) -> TestClient:
    return TestClient(create_app(tmp_path, environ=environ or {}))


def _install(adapter: str, *, target: str = "local", **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "adapter": adapter,
        "target": target,
        "host": "",
        "port": None,
        "install_deps": True,
        "timeout_seconds": 30,
    }
    body.update(extra)
    return body


def test_five_pins_are_concrete_and_stay_out_of_the_live_order() -> None:
    assert "latest" not in HARNESS_ORDER
    for adapter in COMPETITORS:
        entry = entry_for(adapter)
        assert entry is not None
        assert entry.registration == "plan"
        assert entry.in_live_order is False
        assert entry.distributions == ()
        assert resolve_pinned_install(entry) is None
        urls = (entry.install_url, *entry.install_urls)
        for url in urls:
            assert "latest" not in url.lower()
            if url:
                assert url.startswith("https://")
                assert "://" in url
    workbuddy = entry_for("workbuddy")
    openclaw = entry_for("openclaw")
    dsh = entry_for("deepseek_harness")
    doubao = entry_for("doubao_work")
    qwen = entry_for("qwen_work")
    assert workbuddy is not None and workbuddy.version == WORKBUDDY_VERSION
    assert workbuddy.install_urls == WORKBUDDY_INSTALL_URLS
    assert openclaw is not None and openclaw.version == OPENCLAW_NPM_VERSION
    assert openclaw.install_url == OPENCLAW_INSTALL_URL
    assert dsh is not None and dsh.version == DSH_PROFILE_VERSION
    assert dsh.install_url == DSH_INSTALL_URL
    assert qwen is not None and qwen.version == QWEN_WORK_VERSION
    assert qwen.install_url == ""
    assert qwen.runnable is False
    assert qwen.closed_reason == OFFICE_CLOSED_REASON
    assert doubao is not None and doubao.version == DOUBAO_WORK_VERSION
    assert is_concrete_package_version(doubao.version) is True
    assert doubao.install_channel == "desktop"
    assert doubao.install_urls == DOUBAO_WORK_INSTALL_URLS
    assert doubao.runnable is False
    assert doubao.closed_reason == OFFICE_CLOSED_REASON
    assert workbuddy is not None and workbuddy.runnable is True
    assert workbuddy.closed_reason == ""


def test_local_click_on_linux_is_honest_about_each_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("fetch")

    monkeypatch.setattr("ohbe.library.install_distributions", refuse)
    monkeypatch.setattr("ohbe.click_install.download_file", refuse)
    monkeypatch.setattr("ohbe.click_install.run_npm", refuse)
    monkeypatch.setattr("ohbe.click_install.current_platform", lambda: ("Linux", "x86_64"))
    client = _client(tmp_path)
    expected = {
        "workbuddy": ("os_unsupported", WORKBUDDY_VERSION),
        "doubao_work": ("os_unsupported", DOUBAO_WORK_VERSION),
        "qwen_work": ("installer_unpinned", QWEN_WORK_VERSION),
    }
    for adapter, (reason, version) in expected.items():
        saved = client.post("/v1/harness-library/install", json=_install(adapter))
        assert saved.status_code == 200
        body = saved.json()
        assert body["ok"] is False
        assert body["reason"] == reason
        assert body["executed"] is False
        assert body["spend"] == "none"
        assert body["offer_install"] is False
        assert body["harness_order"] == ["openbot", "inspect_react"]
        assert body["in_live_order"] is False
        assert body["version"] == version
        assert "://" not in saved.text
        assert "latest" not in saved.text
    assert not (tmp_path / "harnesses").exists()
    listed = client.get("/v1/harness-configs").json()
    by_adapter = {item["adapter"]: item for item in listed["catalog"]}
    assert by_adapter["workbuddy"]["install_urls"] == list(WORKBUDDY_INSTALL_URLS)
    assert by_adapter["openclaw"]["install_url"] == OPENCLAW_INSTALL_URL
    assert by_adapter["deepseek_harness"]["install_url"] == DSH_INSTALL_URL
    assert by_adapter["qwen_work"]["install_url"] is None
    assert by_adapter["qwen_work"]["runnable"] is False
    assert by_adapter["qwen_work"]["closed_reason"] == OFFICE_CLOSED_REASON
    assert by_adapter["doubao_work"]["install_channel"] == "desktop"
    assert by_adapter["doubao_work"]["install_urls"] == list(DOUBAO_WORK_INSTALL_URLS)
    assert by_adapter["doubao_work"]["runnable"] is False
    assert by_adapter["doubao_work"]["closed_reason"] == OFFICE_CLOSED_REASON
    assert by_adapter["doubao_work"]["in_live_order"] is False
    assert by_adapter["workbuddy"]["runnable"] is True


def test_matching_local_pin_is_admitted_without_running_an_installer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "dsh"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("pip")

    monkeypatch.setattr("ohbe.library.install_distributions", refuse)
    monkeypatch.setattr("ohbe.click_install.run_npm", refuse)
    client = _client(tmp_path, {"DSH_VERSION": DSH_PROFILE_VERSION, "DSH_BIN": str(binary)})
    saved = client.post("/v1/harness-library/install", json=_install("deepseek_harness"))
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["reason"] == "admitted"
    assert body["version"] == DSH_PROFILE_VERSION
    assert body["registration"] == "plan"
    assert body["in_live_order"] is False
    assert body["harness_order"] == ["openbot", "inspect_react"]
    text = (tmp_path / "harnesses" / "profiles" / "deepseek-harness-pin.yaml").read_text(encoding="utf-8")
    assert "live_run: rejected" in text
    assert 'channel: "npm"' in text
    assert DSH_PROFILE_VERSION in text
    assert "://" not in text
    assert "sk-" not in text.lower()
    assert "latest" not in text
    assert DSH_INSTALL_URL not in text

    mismatch = _client(tmp_path / "mismatch", {"DSH_VERSION": "latest", "DSH_BIN": str(binary)})

    def refuse_npm(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("npm")

    monkeypatch.setattr("ohbe.click_install.npm_executable", lambda: None)
    monkeypatch.setattr("ohbe.click_install.run_npm", refuse_npm)
    rejected = mismatch.post("/v1/harness-library/install", json=_install("deepseek_harness"))
    assert rejected.json()["reason"] == "npm_missing"
    assert rejected.json()["version"] == DSH_PROFILE_VERSION
    assert "latest" not in rejected.text


def test_matching_openclaw_pin_records_the_embedded_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "openclaw"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("npm")

    monkeypatch.setattr("ohbe.library.install_distributions", refuse)
    monkeypatch.setattr("ohbe.click_install.run_npm", refuse)
    client = _client(tmp_path, {"OPENCLAW_VERSION": OPENCLAW_NPM_VERSION, "OPENCLAW_BIN": str(binary)})
    saved = client.post("/v1/harness-library/install", json=_install("openclaw"))
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["reason"] == "admitted"
    assert body["version"] == OPENCLAW_NPM_VERSION
    assert body["registration"] == "plan"
    assert body["in_live_order"] is False
    assert body["harness_order"] == ["openbot", "inspect_react"]
    assert body["spend"] == "none"
    text = (tmp_path / "harnesses" / "profiles" / "openclaw-plan.yaml").read_text(encoding="utf-8")
    assert "live_run: rejected" in text
    assert 'execution-path: "embedded-agent"' in text
    assert 'product-layer: "orchestration-channel"' in text
    assert 'plugin-bridge: "refused"' in text
    assert 'runtime: "openclaw"' in text
    assert 'profile: "agent-exec"' in text
    assert OPENCLAW_NPM_VERSION in text
    assert "://" not in text
    assert "sk-" not in text.lower()
    assert "latest" not in text
    assert OPENCLAW_INSTALL_URL not in text


def test_matching_workbuddy_pin_records_the_bundled_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "codebuddy"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("desktop installer")

    monkeypatch.setattr("ohbe.library.install_distributions", refuse)
    monkeypatch.setattr("ohbe.click_install.download_file", refuse)
    client = _client(tmp_path, {"WORKBUDDY_VERSION": WORKBUDDY_VERSION, "WORKBUDDY_BIN": str(binary)})
    saved = client.post("/v1/harness-library/install", json=_install("workbuddy"))
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["reason"] == "admitted"
    assert body["version"] == WORKBUDDY_VERSION
    assert body["registration"] == "plan"
    assert body["in_live_order"] is False
    assert body["harness_order"] == ["openbot", "inspect_react"]
    assert body["spend"] == "none"
    text = (tmp_path / "harnesses" / "profiles" / "workbuddy-pin.yaml").read_text(encoding="utf-8")
    assert "live_run: rejected" in text
    assert 'channel: "desktop"' in text
    assert 'cli: "codebuddy"' in text
    assert 'desktop-install: "unsupported"' in text
    assert 'execution-path: "bundled-codebuddy-cli"' in text
    assert 'product-layer: "desktop-app"' in text
    assert 'model-route: "openai-chat-completions"' in text
    assert 'base-url-override: "refused"' in text
    assert 'dsh-model-source: "refused"' in text
    assert 'permissions: "not-skipped"' in text
    assert 'profile: "headless-print"' in text
    assert WORKBUDDY_VERSION in text
    assert "://" not in text
    assert "sk-" not in text.lower()
    assert "latest" not in text
    for url in WORKBUDDY_INSTALL_URLS:
        assert url not in text

    mismatch = _client(tmp_path / "mismatch", {"WORKBUDDY_VERSION": "latest", "WORKBUDDY_BIN": str(binary)})
    monkeypatch.setattr("ohbe.click_install.current_platform", lambda: ("Linux", "x86_64"))
    rejected = mismatch.post("/v1/harness-library/install", json=_install("workbuddy"))
    assert rejected.json()["reason"] == "os_unsupported"
    assert rejected.json()["version"] == WORKBUDDY_VERSION
    assert "latest" not in rejected.text


def test_remote_competitors_need_auth_then_inventory_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "correct-horse-battery"
    calls: list[str] = []
    monkeypatch.setattr("ohbe.remote_auth.authenticate", lambda *_args, **_kwargs: calls.append("auth"))
    client = _client(tmp_path)
    missing = client.post(
        "/v1/harness-library/install",
        json=_install("qwen_work", target="remote", host="10.0.0.8", port=22),
    )
    assert missing.json()["reason"] == "auth_required"
    assert not (tmp_path / "harnesses").exists()
    authed = client.post(
        "/v1/harness-library/install",
        json=_install(
            "workbuddy",
            target="remote",
            host="10.0.0.8",
            port=22,
            username="ops",
            auth_method="password",
            secret=secret,
        ),
    )
    body = authed.json()
    assert body["reason"] == "inventory_unsupported"
    assert body["ok"] is False
    assert body["network"] == "probed"
    assert body["version"] == WORKBUDDY_VERSION
    assert body["harness_order"] == ["openbot", "inspect_react"]
    assert secret not in authed.text
    assert calls == ["auth"]
    assert not (tmp_path / "harnesses").exists()
    denied = client.post(
        "/v1/harness-library/install",
        json=_install(
            "openclaw",
            target="remote",
            host="api.openai.com",
            port=22,
            username="ops",
            auth_method="password",
            secret=secret,
        ),
    )
    assert denied.json()["reason"] == "rejected_host"
    assert secret not in denied.text
