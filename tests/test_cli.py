"""benchmark --help and trace verification."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ohbe.cli import main
from ohbe.core import TraceEventType, TraceLog
from tests.samples import NOW


def test_benchmark_help_mentions_ohbe(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    output = capsys.readouterr().out
    assert "OHBE" in output
    assert "OpenBot Harness Benchmark Engine" in output


def test_benchmark_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    output = capsys.readouterr().out
    assert "ohbe 0.1.0" in output
    assert "schema 0.1" in output


def test_verify_trace_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "trace.jsonl"
    record = TraceLog(path).append(
        execution_id="exec-1",
        event_type=TraceEventType.EXECUTION_STARTED,
        payload={"marker": "alpha"},
        recorded_at=NOW,
        record_id="rec-0",
    )
    assert main(["verify-trace", str(path)]) == 0
    assert f"ok records=1 head={record.record_hash}" in capsys.readouterr().out

    path.write_text("{}\n", encoding="utf-8")
    assert main(["verify-trace", str(path)]) == 1
    assert capsys.readouterr().err


def test_python_module_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "ohbe", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "OHBE" in result.stdout
    assert "OpenBot Harness Benchmark Engine" in result.stdout

    script = Path(sys.executable).with_name("benchmark")
    if script.exists():
        via_script = subprocess.run([str(script), "--help"], check=False, capture_output=True, text=True)
        assert via_script.returncode == 0
        assert "OHBE" in via_script.stdout
