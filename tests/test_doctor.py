"""benchmark doctor: offline env, pool, and experiment checks."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from ohbe.cli import main
from ohbe.pools.doctor import extract_smoke_commands, run_doctor

ROOT = Path(__file__).resolve().parents[1]
POOL = "models/pools/pool-gpu-farm.example.yaml"
EXPERIMENT = "experiments/live-pilot-smoke.yaml"
CHECKLIST = "docs/private-deploy-checklist.md"
SECRET = "doctor-secret-token"


def _env(**overrides: str) -> dict[str, str]:
    data = {
        "LITELLM_BASE_URL": "http://gpu-litellm:4000/v1",
        "LITELLM_API_KEY": SECRET,
    }
    data.update(overrides)
    return data


def test_checklist_smoke_block_is_the_manual_run() -> None:
    text = (ROOT / CHECKLIST).read_text(encoding="utf-8")
    commands = extract_smoke_commands(text)
    assert "benchmark run" in commands
    assert "experiments/live-pilot-smoke.yaml" in commands
    assert "benchmark report" in commands
    assert "doctor 不会运行" in commands


def test_doctor_renders_litellm_profile_without_the_key_or_network(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(ROOT)
    for name in ("LITELLM_API_KEY", "LITELLM_BASE_URL", "OPENBOT_BIN", "OPENBOT_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://gpu-litellm:4000/v1")
    monkeypatch.setenv("LITELLM_API_KEY", SECRET)

    def _deny(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor opened a socket")

    monkeypatch.setattr(socket, "getaddrinfo", _deny)
    monkeypatch.setattr(socket.socket, "connect", _deny)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live runner started")

    monkeypatch.setattr("ohbe.live.runner.run_live_pilot", _boom)
    code = main(["doctor", "--pool", POOL, "--experiment", EXPERIMENT, "--checklist", CHECKLIST])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    output = captured.out
    assert SECRET not in output
    assert "ok litellm_base_url host=gpu-litellm port=4000" in output
    assert "ok litellm_api_key present" in output
    assert "ok openbot_bin optional" in output
    assert "placeholders=REPLACE_ME" in output
    assert "fail_closed=true" in output
    assert "ok profile litellm-only" in output
    assert "--- profile ---" in output
    assert '"provider": "litellm"' in output
    assert "http://gpu-litellm:4000" in output
    assert "${LITELLM_API_KEY}" in output
    assert "api.openai.com" not in output
    assert "api.anthropic.com" not in output
    assert "vllm/REPLACE_ME-instruct" in output
    assert "--- next ---" in output
    assert "benchmark run" in output
    assert "benchmark doctor 不会运行" in output or "doctor 不会运行" in output
    report = run_doctor(
        pool_path=POOL,
        experiment_path=EXPERIMENT,
        checklist_path=CHECKLIST,
        environ=_env(),
    )
    assert report.ok
    assert report.profile is not None
    assert SECRET not in report.profile


def test_doctor_fails_closed_on_cloud_hosts_and_a_missing_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("OPENBOT_BIN", raising=False)

    def _run(env: dict[str, str]) -> tuple[int, str]:
        for name in ("LITELLM_API_KEY", "LITELLM_BASE_URL"):
            monkeypatch.delenv(name, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        code = main(["doctor"])
        captured = capsys.readouterr()
        return code, captured.err + captured.out

    code, text = _run({"LITELLM_BASE_URL": "http://gpu-litellm:4000/v1"})
    assert code == 1
    assert "fail litellm_api_key" in text
    assert "--- profile ---" not in text

    for url in (
        "http://api.openai.com:443/v1",
        "http://api.anthropic.com:443/v1",
        "http://foo.openai.com:443/v1",
        "http://bar.anthropic.com:443/v1",
        "https://gpu-litellm:4000/v1",
    ):
        code, text = _run({"LITELLM_BASE_URL": url, "LITELLM_API_KEY": SECRET})
        assert code == 1
        assert "fail litellm_base_url" in text
        assert SECRET not in text
        assert "--- profile ---" not in text

    code, text = _run(
        {"LITELLM_BASE_URL": "http://10.1.2.3:4000/v1", "LITELLM_API_KEY": SECRET}
    )
    assert code == 1
    assert "match the experiment" in text
    assert "--- next ---" not in text

    missing = tmp_path / "openbot"
    code, text = _run(
        {
            "LITELLM_BASE_URL": "http://gpu-litellm:4000/v1",
            "LITELLM_API_KEY": SECRET,
            "OPENBOT_BIN": str(missing),
        }
    )
    assert code == 1
    assert "fail openbot_bin" in text

    binary = tmp_path / "openbot-ok"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    code, text = _run(
        {
            "LITELLM_BASE_URL": "http://gpu-litellm:4000/v1",
            "LITELLM_API_KEY": SECRET,
            "OPENBOT_BIN": str(binary),
        }
    )
    assert code == 0
    assert "ok openbot_bin executable" in text
    assert SECRET not in text

    code = main(["doctor", "--pool", str(tmp_path / "missing.yaml")])
    captured = capsys.readouterr()
    assert code == 2
    assert "not found" in captured.err


def test_doctor_source_does_not_call_the_network() -> None:
    source = (ROOT / "src" / "ohbe" / "pools" / "doctor.py").read_text(encoding="utf-8")
    template = (ROOT / "src" / "ohbe" / "pools" / "template.py").read_text(encoding="utf-8")
    for blob in (source, template):
        assert "import socket" not in blob
        assert "subprocess" not in blob
        assert "urlopen" not in blob
