"""Closed adapters for 千问办公 and 豆包工作.

Neither product has a LiteLLM-only headless path. These tests prove the
adapters refuse before a process starts, and that a saved profile does not
store a URL, a key, or the floating token latest.
"""

from __future__ import annotations

import socket
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ohbe.adapters.base import AdapterError
from ohbe.adapters.doubao_work.adapter import DoubaoWorkAdapter
from ohbe.adapters.doubao_work.profile import (
    DOUBAO_RUN_REFUSAL,
    DoubaoWorkProfileError,
    normalize_doubao_version,
)
from ohbe.adapters.pilot import pilot_requests
from ohbe.adapters.qwen_work.adapter import QwenWorkAdapter
from ohbe.adapters.qwen_work.profile import (
    QWEN_RUN_REFUSAL,
    QwenWorkProfileError,
    normalize_qwen_version,
)
from ohbe.api.app import create_app
from ohbe.live.spec import HARNESS_ORDER
from ohbe.preinstall import (
    DOUBAO_WORK_INSTALL_URLS,
    DOUBAO_WORK_VERSION,
    OFFICE_CLOSED_REASON,
    QWEN_WORK_VERSION,
)
from tests.samples import NOW, sample_environment, sample_fingerprint, sample_pool


def _client(tmp_path: Path, environ: dict[str, str] | None = None) -> TestClient:
    return TestClient(create_app(tmp_path, environ=environ or {}))


def _request(tmp_path: Path):
    openbot, _inspect = pilot_requests(
        environment=sample_environment(),
        pool=sample_pool(),
        fingerprint=sample_fingerprint(),
        state_root=tmp_path,
        started_at=NOW,
        finished_at=NOW,
    )
    return openbot


def _executable(path: Path) -> None:
    path.write_text("#!/bin/sh\necho ran > \"$PWD/ran.txt\"\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_both_adapters_fail_closed_before_a_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("process or socket")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    request = _request(tmp_path)
    marker = tmp_path / "ran.txt"
    _executable(tmp_path / "qwen-bin")
    _executable(tmp_path / "doubao-bin")

    with pytest.raises(AdapterError, match="qwen_work is not runnable") as qwen_error:
        QwenWorkAdapter().run(request)
    with pytest.raises(AdapterError, match="doubao_work is not runnable") as doubao_error:
        DoubaoWorkAdapter().run(request)

    assert str(qwen_error.value) == QWEN_RUN_REFUSAL
    assert "/release/latest/" in QWEN_RUN_REFUSAL
    assert "://" not in QWEN_RUN_REFUSAL
    assert str(doubao_error.value) == DOUBAO_RUN_REFUSAL
    assert "://" not in DOUBAO_RUN_REFUSAL
    assert "sk-" not in (QWEN_RUN_REFUSAL + DOUBAO_RUN_REFUSAL).lower()
    assert not marker.exists()
    assert "qwen_work" not in HARNESS_ORDER
    assert "doubao_work" not in HARNESS_ORDER
    assert HARNESS_ORDER == ("openbot", "inspect_react")


def test_version_tokens_reject_latest() -> None:
    assert normalize_qwen_version(QWEN_WORK_VERSION) == QWEN_WORK_VERSION
    assert normalize_doubao_version(DOUBAO_WORK_VERSION) == DOUBAO_WORK_VERSION
    with pytest.raises(QwenWorkProfileError):
        normalize_qwen_version("latest")
    with pytest.raises(DoubaoWorkProfileError):
        normalize_doubao_version("latest")
    with pytest.raises(DoubaoWorkProfileError):
        normalize_doubao_version("doubao-work-version-unavailable")


def test_saved_profiles_record_the_refusal_and_omit_urls(tmp_path: Path) -> None:
    client = _client(tmp_path)
    bodies = (
        ("qwen-work-pin", "qwen_work", "qwen-work", QWEN_WORK_VERSION),
        ("doubao-work-pin", "doubao_work", "doubao-work", DOUBAO_WORK_VERSION),
    )
    for profile_id, adapter, name, version in bodies:
        saved = client.post(
            "/v1/harness-configs",
            json={
                "profile_id": profile_id,
                "adapter": adapter,
                "name": name,
                "version": version,
                "timeout_seconds": 30,
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["live_run"] == "rejected"
        assert body["registration"] == "plan"
        assert body["parameters"]["runnable"] is False
        assert body["parameters"]["closed-reason"] == OFFICE_CLOSED_REASON
        assert body["parameters"]["execution-path"] == "unavailable"
        assert body["parameters"]["model-route"] == "refused"
        assert body["parameters"]["provider"] == "refused"
        assert body["parameters"]["desktop-install"] == "unsupported"
        text = (tmp_path / "harnesses" / "profiles" / f"{profile_id}.yaml").read_text(encoding="utf-8")
        assert "live_run: rejected" in text
        assert "runnable: false" in text
        assert "://" not in text
        assert "sk-" not in text.lower()
        assert "latest" not in text.lower()
        for url in DOUBAO_WORK_INSTALL_URLS:
            assert url not in text
    rejected = client.post(
        "/v1/harness-configs",
        json={
            "profile_id": "qwen-work-pin",
            "adapter": "qwen_work",
            "name": "qwen-work",
            "version": "latest",
            "timeout_seconds": 30,
        },
    )
    assert rejected.status_code == 422


def test_matching_local_pin_admits_a_closed_profile_and_does_not_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "office-bin"
    _executable(binary)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("installer")

    monkeypatch.setattr("ohbe.library.install_distributions", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    client = _client(
        tmp_path,
        {"QWEN_WORK_VERSION": QWEN_WORK_VERSION, "QWEN_WORK_BIN": str(binary)},
    )
    saved = client.post(
        "/v1/harness-library/install",
        json={
            "adapter": "qwen_work",
            "target": "local",
            "host": "",
            "port": None,
            "install_deps": True,
            "timeout_seconds": 30,
        },
    )
    assert saved.status_code == 200
    body = saved.json()
    assert body["ok"] is True
    assert body["reason"] == "admitted"
    assert body["executed"] is False
    assert body["spend"] == "none"
    assert body["version"] == QWEN_WORK_VERSION
    assert body["in_live_order"] is False
    assert body["harness_order"] == ["openbot", "inspect_react"]
    text = (tmp_path / "harnesses" / "profiles" / "qwen-work-pin.yaml").read_text(encoding="utf-8")
    assert "runnable: false" in text
    assert 'install-pin: "unversioned-download-not-stored"' in text
    assert "://" not in text
    assert "latest" not in text.lower()
    assert not (tmp_path / "ran.txt").exists()

    doubao_root = tmp_path / "doubao-home"
    doubao_root.mkdir()
    doubao = _client(
        doubao_root,
        {"DOUBAO_WORK_VERSION": DOUBAO_WORK_VERSION, "DOUBAO_WORK_BIN": str(binary)},
    )
    admitted = doubao.post(
        "/v1/harness-library/install",
        json={
            "adapter": "doubao_work",
            "target": "local",
            "host": "",
            "port": None,
            "install_deps": True,
            "timeout_seconds": 30,
        },
    )
    assert admitted.status_code == 200
    assert admitted.json()["reason"] == "admitted"
    assert admitted.json()["version"] == DOUBAO_WORK_VERSION
    doubao_text = (doubao_root / "harnesses" / "profiles" / "doubao-work-pin.yaml").read_text(encoding="utf-8")
    assert 'install-pin: "windows-cdn-not-executed"' in doubao_text
    assert "runnable: false" in doubao_text
    assert "://" not in doubao_text
    assert "sk-" not in doubao_text.lower()
    for url in DOUBAO_WORK_INSTALL_URLS:
        assert url not in doubao_text
        assert url not in admitted.text

    monkeypatch.setattr("ohbe.click_install.current_platform", lambda: ("Linux", "x86_64"))
    monkeypatch.setattr("ohbe.click_install.download_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("download")))
    mismatch = _client(tmp_path / "mismatch", {"DOUBAO_WORK_VERSION": "latest", "DOUBAO_WORK_BIN": str(binary)})
    rejected = mismatch.post(
        "/v1/harness-library/install",
        json={
            "adapter": "doubao_work",
            "target": "local",
            "host": "",
            "port": None,
            "install_deps": True,
            "timeout_seconds": 30,
        },
    )
    assert rejected.json()["reason"] == "os_unsupported"
    assert rejected.json()["version"] == DOUBAO_WORK_VERSION
    assert not (tmp_path / "mismatch" / "harnesses").exists()
