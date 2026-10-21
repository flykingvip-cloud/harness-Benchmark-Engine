"""v0.1 report: same fixture input, same report bytes."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ohbe.adapters.pilot import PILOT_OPENBOT_COMMIT
from ohbe.audit import build_pilot_corpus, report_generator_input, run_pilot_audit
from ohbe.cli import main
from ohbe.core.integrity import canonical_json
from ohbe.cost.table import load_pricing_table
from ohbe.report import DEFERRED, REPORT_KIND, load_report_input, render_report
from ohbe.report.render import OBSERVED_RULE, RELEASE_GATES

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "results" / "fixture-pilot"
TABLE = ROOT / "pricing" / "ohbe-offline-2026-10-09.yaml"
TABLE_HASH = "67391b6b0bc78d14f802c5cdc7f2a300878ca5070de28f485ef57f382b1ed92b"
NAMES = ("report.json", "metrics.jsonl", "report.md", "report.html")


def _bundle(table, state: Path):
    corpus = build_pilot_corpus(table, state)
    return report_generator_input(*run_pilot_audit(corpus))


def test_checked_in_report_matches_the_renderer() -> None:
    document = load_report_input(SAMPLE / "report-input.json")
    first = render_report(document)
    second = render_report(document)
    assert first == second
    assert (SAMPLE / "report.json").read_text(encoding="utf-8") == first.report_json
    assert (SAMPLE / "metrics.jsonl").read_text(encoding="utf-8") == first.metrics_jsonl
    assert (SAMPLE / "report.md").read_text(encoding="utf-8") == first.report_md
    assert (SAMPLE / "report.html").read_text(encoding="utf-8") == first.report_html


def test_fixture_corpus_matches_the_checked_in_input(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    relative = _bundle(load_pricing_table("pricing/ohbe-offline-2026-10-09.yaml"), tmp_path / "rel")
    absolute = _bundle(load_pricing_table(TABLE), tmp_path / "abs")
    committed = (SAMPLE / "report-input.json").read_text(encoding="utf-8")
    assert committed == canonical_json(relative) + "\n"
    assert committed == canonical_json(absolute) + "\n"
    assert "file://" not in committed
    assert "/workspace" not in committed
    assert relative["metrics"][0]["pricing_content_hash"] == TABLE_HASH


def test_report_command_regenerates_the_sample(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out"
    code = main(["report", "--input", str(SAMPLE / "report-input.json"), "--out", str(out)])
    assert code == 0
    assert f"ok out={out}" in capsys.readouterr().out
    for name in NAMES:
        assert (out / name).read_bytes() == (SAMPLE / name).read_bytes()


def test_report_command_rejects_a_bad_input(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"kind": "nope"}\n', encoding="utf-8")
    code = main(["report", "--input", str(bad), "--out", str(tmp_path / "out")])
    assert code == 2
    assert "report input kind" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_report_states_observed_fixture_results_only() -> None:
    data = json.loads((SAMPLE / "report.json").read_text(encoding="utf-8"))
    text = (SAMPLE / "report.md").read_text(encoding="utf-8")
    html = (SAMPLE / "report.html").read_text(encoding="utf-8")
    raw_input = (SAMPLE / "report-input.json").read_text(encoding="utf-8")
    assert data["kind"] == REPORT_KIND
    assert data["label"] == "fixture"
    assert data["spend"] == "not_live"
    assert data["routing_optimality"] is None
    assert data["model_independence"] is None
    assert data["schema_version"] == "0.1"
    assert data["ohbe_version"] == "0.1.0"
    digest = hashlib.sha256(canonical_json(json.loads(raw_input)).encode("utf-8")).hexdigest()
    assert data["input_sha256"] == digest
    repro = data["reproducibility"]
    assert repro["policy_versions"] == ["ohbe-egress-0.1"]
    assert repro["git_commits"] == ["c0ffee"]
    assert repro["pricing"] == [
        {"content_hash": TABLE_HASH, "pricing_id": "ohbe-offline", "version": "2026-10-09"}
    ]
    assert f"openbot@{PILOT_OPENBOT_COMMIT}" in repro["harness_versions"]
    assert "inspect-react@0.3.277" in repro["harness_versions"]
    assert [gate["gate_id"] for gate in data["release_gates"]] == list(RELEASE_GATES)
    assert all(gate["passed"] is True for gate in data["release_gates"])
    by_task = {item["task_id"]: item for item in data["tasks"]}
    echo = by_task["ohbe-pilot-echo"]["harnesses"]
    exact = by_task["ohbe-pilot-exact"]["harnesses"]
    match = by_task["ohbe-pilot-match"]["harnesses"]
    assert all(row["total_model_cost"] is None and row["prompt_tokens"] is None for row in echo)
    assert all(row["success_rate"] == "1.000000" for row in echo)
    assert all(row["total_model_cost"] == "0.0009" and row["success_rate"] == "0.500000" for row in exact)
    assert all(row["task_failure_count"] == 1 for row in exact)
    assert all(
        row["total_model_cost"] == "0.00045" and row["invalid_count"] == 1 and row["task_failure_count"] == 0
        for row in match
    )
    assert OBSERVED_RULE in text
    assert "fixture/demo" in text
    assert "It is not zero." in text
    assert "That order is not a ranking." in text
    for item in DEFERRED:
        assert item in text
        assert item in html
    for gate_id in RELEASE_GATES:
        assert gate_id in text
    lowered = text.lower()
    assert "the optimal harness" not in lowered
    assert "models are independent" not in lowered
    assert "production spend" in text
    rows = [json.loads(line) for line in (SAMPLE / "metrics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 6
    assert rows[0]["task_id"] == "ohbe-pilot-echo"
    assert "<script" not in html


def test_audit_pilot_default_pricing_path_matches_the_sample(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    output = tmp_path / "audit.json"
    report_input = tmp_path / "report-input.json"
    code = main(
        [
            "audit-pilot",
            "--output",
            str(output),
            "--report-input",
            str(report_input),
        ]
    )
    assert code == 0
    assert report_input.read_text(encoding="utf-8") == (SAMPLE / "report-input.json").read_text(encoding="utf-8")


def test_importing_report_does_not_import_inspect() -> None:
    script = (
        "import sys\n"
        "import ohbe.report\n"
        "banned = [name for name in sys.modules if name.split('.', 1)[0] in {'litellm', 'inspect_ai'}]\n"
        "raise SystemExit(0 if not banned else 1)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], check=False, cwd=ROOT)
    assert result.returncode == 0
