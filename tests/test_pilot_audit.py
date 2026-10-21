"""Fixture pilot audit: fairness gates, not live model spend."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ohbe.adapters.pilot import pilot_suite
from ohbe.audit import (
    LIMITATIONS,
    REPORT_INPUT_KIND,
    audit_document,
    build_pilot_corpus,
    report_generator_input,
    run_pilot_audit,
)
from ohbe.cli import main
from ohbe.core.integrity import canonical_json
from ohbe.cost.table import load_pricing_table
from ohbe.eval.objective import select_objective

TABLE = Path(__file__).resolve().parents[1] / "pricing" / "ohbe-offline-2026-10-09.yaml"


def _corpus(tmp_path: Path, **overrides: object):
    table = load_pricing_table(TABLE)
    return build_pilot_corpus(table, tmp_path / "traces", **overrides)  # type: ignore[arg-type]


def test_pilot_suite_covers_three_objective_scorers() -> None:
    tasks = pilot_suite()
    assert [task.task_id for task in tasks] == [
        "ohbe-pilot-echo",
        "ohbe-pilot-exact",
        "ohbe-pilot-match",
    ]
    assert [select_objective(task) for task in tasks] == [
        ("contains", "pong"),
        ("exact", "ok"),
        ("match", "ready"),
    ]


def test_fixture_audit_passes_and_is_stable(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path)
    report, metrics = run_pilot_audit(corpus)
    assert report.passed is True
    assert [gate.gate_id for gate in report.gates] == [
        "same_task_input",
        "same_initial_environment",
        "same_model_pool",
        "same_protocol",
        "missing_usage_not_zero",
        "invalid_not_task_failure",
        "versions_recorded",
        "evaluator_reliability",
        "gateway_bypass_denied",
        "no_routing_claim",
    ]
    assert all(gate.passed for gate in report.gates)
    by_task = {item["task_id"]: item for item in metrics}
    echo = by_task["ohbe-pilot-echo"]
    exact = by_task["ohbe-pilot-exact"]
    match = by_task["ohbe-pilot-match"]
    assert all(row["total_model_cost"] is None for row in echo["harnesses"])
    assert all(row["prompt_tokens"] is None for row in echo["harnesses"])
    assert all(row["cost_status"] == "invalid" for row in echo["harnesses"])
    assert all(row["total_model_cost"] == "0.0009" for row in exact["harnesses"])
    assert all(row["task_failure_count"] == 1 for row in exact["harnesses"])
    assert all(row["success_rate"] == "0.500000" for row in exact["harnesses"])
    assert all(row["total_model_cost"] == "0.00045" for row in match["harnesses"])
    assert all(row["task_failure_count"] == 0 for row in match["harnesses"])
    assert all(row["invalid_count"] == 1 for row in match["harnesses"])
    assert all(row["valid_count"] == 1 for row in match["harnesses"])
    assert all(row["success_rate"] == "1.000000" for row in match["harnesses"])
    missing = next(gate for gate in report.gates if gate.gate_id == "missing_usage_not_zero")
    assert "cost_amount=None" in " ".join(missing.evidence)
    bypass = next(gate for gate in report.gates if gate.gate_id == "gateway_bypass_denied")
    assert any("test_docker_egress.py" in row for row in bypass.evidence)
    assert "Tool-surface parity" in LIMITATIONS[0]
    assert "Oracle scoring" in LIMITATIONS[1]
    document = audit_document(report)
    again, again_metrics = run_pilot_audit(corpus)
    assert canonical_json(document) == canonical_json(audit_document(again))
    bundled = report_generator_input(report, metrics)
    assert bundled["kind"] == REPORT_INPUT_KIND
    assert bundled["audit"]["passed"] is True
    assert len(bundled["metrics"]) == 3
    assert canonical_json(bundled) == canonical_json(report_generator_input(again, again_metrics))


def test_divergent_task_input_fails_the_fairness_gate(tmp_path: Path) -> None:
    corpus = _corpus(
        tmp_path,
        input_overrides={("ohbe-pilot-echo", "inspect-react", "success"): {"prompt": "other"}},
    )
    report, _metrics = run_pilot_audit(corpus)
    assert report.passed is False
    gate = next(item for item in report.gates if item.gate_id == "same_task_input")
    assert gate.passed is False
    assert any(row.startswith("problem:") for row in gate.evidence)


def test_audit_pilot_command_writes_json(tmp_path: Path) -> None:
    output = tmp_path / "audit.json"
    report_input = tmp_path / "report-input.json"
    code = main(
        [
            "audit-pilot",
            "--output",
            str(output),
            "--report-input",
            str(report_input),
            "--pricing",
            str(TABLE),
        ]
    )
    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    bundled = json.loads(report_input.read_text(encoding="utf-8"))
    assert document["kind"] == "ohbe.pilot_audit"
    assert document["passed"] is True
    assert bundled["kind"] == REPORT_INPUT_KIND
    assert bundled["audit"]["passed"] is True
    assert len(bundled["metrics"]) == 3


def test_importing_audit_does_not_import_inspect() -> None:
    script = (
        "import sys\n"
        "import ohbe.audit\n"
        "banned = [name for name in sys.modules if name.split('.', 1)[0] in {'litellm', 'inspect_ai'}]\n"
        "raise SystemExit(0 if not banned else 1)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], check=False, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0
