"""Render a v0.1 report from an `ohbe.report_input` document.

The renderer copies observed metrics and uncertainty. It does not rank
harnesses, infer routing optimality, or treat cost and success as evidence
that models are independent. The same parsed input produces the same bytes.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ohbe.audit.report_input import REPORT_INPUT_KIND
from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.integrity import canonical_json

REPORT_KIND = "ohbe.report"
DEFERRED = (
    "Oracle scoring is a stub and is not computed.",
    "Routing regret is a stub and is not computed.",
    "A multi-harness production matrix beyond this fixture pair is deferred.",
    "A dashboard is deferred.",
    "Tool-surface parity between harnesses is not claimed.",
    "Live production spend is not in this report.",
)
OBSERVED_RULE = (
    "Figures are observed results from the supplied executions, with uncertainty "
    "intervals when the aggregator produced them. A difference in cost or success "
    "rate is not evidence of routing optimality, and it is not evidence that one "
    "model is independent of another."
)
RELEASE_GATES = (
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
)


class ReportError(ValueError):
    """The report input cannot be rendered."""


@dataclass(frozen=True)
class ReportFiles:
    """UTF-8 file bodies. Each body ends with a newline."""

    report_json: str
    metrics_jsonl: str
    report_md: str
    report_html: str


def load_report_input(path: str | Path) -> dict[str, Any]:
    """Read and validate one report-input JSON object."""

    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportError(f"could not read report input: {exc}") from exc
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportError(f"report input is not JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ReportError("report input must be a JSON object")
    validate_report_input(loaded)
    return loaded


def validate_report_input(document: Mapping[str, Any]) -> None:
    """Raise when `document` is not an `ohbe.report_input` bundle."""

    if document.get("kind") != REPORT_INPUT_KIND:
        raise ReportError(f"report input kind must be {REPORT_INPUT_KIND}")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ReportError(f"report input schema_version must be {SCHEMA_VERSION}")
    audit = document.get("audit")
    if not isinstance(audit, dict) or audit.get("kind") != "ohbe.pilot_audit":
        raise ReportError("report input audit must be an ohbe.pilot_audit object")
    if not isinstance(audit.get("gates"), list):
        raise ReportError("audit gates must be a list")
    metrics = document.get("metrics")
    if not isinstance(metrics, list):
        raise ReportError("report input metrics must be a list")
    for index, item in enumerate(metrics):
        if not isinstance(item, dict) or not isinstance(item.get("task_id"), str):
            raise ReportError(f"metrics[{index}] must be an object with task_id")
        if not isinstance(item.get("harnesses"), list):
            raise ReportError(f"metrics[{index}] harnesses must be a list")


def render_report(document: Mapping[str, Any]) -> ReportFiles:
    """Build the JSON, JSONL, Markdown, and HTML bodies."""

    validate_report_input(document)
    model = _model(document)
    report_json = canonical_json(model) + "\n"
    metrics_jsonl = "".join(canonical_json(row) + "\n" for row in _rows(model))
    report_md = _markdown(model)
    report_html = _html(model)
    return ReportFiles(
        report_json=report_json,
        metrics_jsonl=metrics_jsonl,
        report_md=report_md,
        report_html=report_html,
    )


def write_report(document: Mapping[str, Any], out_dir: str | Path) -> ReportFiles:
    """Write `report.json`, `metrics.jsonl`, `report.md`, and `report.html`."""

    files = render_report(document)
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_text(files.report_json, encoding="utf-8")
    (directory / "metrics.jsonl").write_text(files.metrics_jsonl, encoding="utf-8")
    (directory / "report.md").write_text(files.report_md, encoding="utf-8")
    (directory / "report.html").write_text(files.report_html, encoding="utf-8")
    return files


def _model(document: Mapping[str, Any]) -> dict[str, Any]:
    audit = document["audit"]
    metrics = document["metrics"]
    label = _label(audit)
    gates = _checklist(audit)
    return {
        "audit_passed": audit.get("passed") is True,
        "deferred": list(DEFERRED),
        "input_sha256": hashlib.sha256(canonical_json(dict(document)).encode("utf-8")).hexdigest(),
        "kind": REPORT_KIND,
        "label": label,
        "limitations": list(audit.get("limitations") or []),
        "observed_rule": OBSERVED_RULE,
        "ohbe_version": audit.get("ohbe_version"),
        "release_gates": gates,
        "reproducibility": _reproducibility(audit, metrics),
        "routing_optimality": None,
        "model_independence": None,
        "schema_version": SCHEMA_VERSION,
        "spend": "not_live" if label == "fixture" else "as_supplied",
        "tasks": [_task(item, label) for item in metrics],
    }


def _label(audit: Mapping[str, Any]) -> str:
    text = " ".join(str(item) for item in audit.get("limitations") or []).lower()
    if "fixture" in text or "not live" in text:
        return "fixture"
    return "supplied-input"


def _checklist(audit: Mapping[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for gate in audit.get("gates") or []:
        if isinstance(gate, dict) and isinstance(gate.get("gate_id"), str):
            found[gate["gate_id"]] = gate
    rows: list[dict[str, Any]] = []
    for gate_id in RELEASE_GATES:
        gate = found.get(gate_id)
        if gate is None:
            rows.append(
                {
                    "evidence": [],
                    "gate_id": gate_id,
                    "passed": None,
                    "summary": "not present in the audit input",
                }
            )
            continue
        evidence = gate.get("evidence") if isinstance(gate.get("evidence"), list) else []
        rows.append(
            {
                "evidence": [str(item) for item in evidence],
                "gate_id": gate_id,
                "passed": gate.get("passed") is True,
                "summary": str(gate.get("summary") or ""),
            }
        )
    return rows


def _reproducibility(audit: Mapping[str, Any], metrics: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "git_commits": _flat_unique(metrics, "git_commits"),
        "image_digests": _evidence_values(audit, "same_initial_environment", "image:"),
        "ohbe_version": audit.get("ohbe_version"),
        "ohbe_versions_on_executions": _flat_unique(metrics, "ohbe_versions"),
        "policy_versions": _evidence_values(audit, "same_initial_environment", "policy:"),
        "pricing": _pricing(metrics),
        "schema_version": SCHEMA_VERSION,
        "schema_versions_on_metrics": _unique(item.get("schema_version") for item in metrics),
        "timeouts_seconds": _unique(item.get("timeout_seconds") for item in metrics),
        "repetitions": _unique(item.get("repetitions") for item in metrics),
        "max_total_tokens": _unique(item.get("max_total_tokens") for item in metrics),
        "max_cost": _unique(item.get("max_cost") for item in metrics),
        "model_pool_ids": _unique(item.get("model_pool_id") for item in metrics),
        "model_pool_hashes": _unique(item.get("model_pool_hash") for item in metrics),
        "gateway": _evidence_values(audit, "same_initial_environment", "gateway:"),
        "harness_versions": _evidence_values(audit, "versions_recorded", "harness:"),
    }


def _pricing(metrics: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for item in metrics:
        key = (item.get("pricing_id"), item.get("pricing_version"), item.get("pricing_content_hash"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "content_hash": item.get("pricing_content_hash"),
                "pricing_id": item.get("pricing_id"),
                "version": item.get("pricing_version"),
            }
        )
    return rows


def _task(item: Mapping[str, Any], label: str) -> dict[str, Any]:
    harnesses = item.get("harnesses") or []
    return {
        "harnesses": [_harness(item, row, label) for row in harnesses if isinstance(row, dict)],
        "note": item.get("note"),
        "task_content_hash": item.get("task_content_hash"),
        "task_id": item.get("task_id"),
    }


def _harness(task: Mapping[str, Any], row: Mapping[str, Any], label: str) -> dict[str, Any]:
    return {
        "complete": row.get("complete"),
        "cost_per_success": row.get("cost_per_success"),
        "cost_status": row.get("cost_status"),
        "git_commits": list(row.get("git_commits") or []),
        "harness_id": row.get("harness_id"),
        "invalid_by_category": dict(row.get("invalid_by_category") or {}),
        "invalid_count": row.get("invalid_count"),
        "label": label,
        "latency_missing_count": row.get("latency_missing_count"),
        "latency_ms_high": row.get("latency_ms_high"),
        "latency_ms_low": row.get("latency_ms_low"),
        "latency_ms_mean": row.get("latency_ms_mean"),
        "max_cost": task.get("max_cost"),
        "max_total_tokens": task.get("max_total_tokens"),
        "model_pool_hash": task.get("model_pool_hash"),
        "model_pool_id": task.get("model_pool_id"),
        "observed_count": row.get("observed_count"),
        "ohbe_versions": list(row.get("ohbe_versions") or []),
        "pricing_content_hash": task.get("pricing_content_hash"),
        "pricing_id": task.get("pricing_id"),
        "pricing_version": task.get("pricing_version"),
        "prompt_tokens": row.get("prompt_tokens"),
        "completion_tokens": row.get("completion_tokens"),
        "repetitions": task.get("repetitions"),
        "schema_version": task.get("schema_version"),
        "seeds": list(row.get("seeds") or []),
        "success_count": row.get("success_count"),
        "success_rate": row.get("success_rate"),
        "success_rate_high": row.get("success_rate_high"),
        "success_rate_low": row.get("success_rate_low"),
        "task_content_hash": task.get("task_content_hash"),
        "task_failure_count": row.get("task_failure_count"),
        "task_id": task.get("task_id"),
        "timeout_seconds": task.get("timeout_seconds"),
        "total_model_cost": row.get("total_model_cost"),
        "total_tokens": row.get("total_tokens"),
        "uncertainty": row.get("uncertainty"),
        "usage_status": row.get("usage_status"),
        "valid_count": row.get("valid_count"),
    }


def _rows(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in model["tasks"]:
        if isinstance(task, dict):
            for harness in task.get("harnesses") or []:
                if isinstance(harness, dict):
                    rows.append(harness)
    return rows


def _markdown(model: Mapping[str, Any]) -> str:
    label = str(model["label"])
    lines = [
        f"# OHBE v0.1 report ({label})",
        "",
        "This file is a v0.1 scaffolding report. "
        + (
            "It is a fixture/demo. It is not live OpenBot or Inspect production spend."
            if label == "fixture"
            else "Spend figures are only those already present in the input."
        ),
        "",
        OBSERVED_RULE,
        "",
        "Harness rows follow the input order. That order is not a ranking.",
        "",
        f"Audit passed: {_yn(model['audit_passed'])}.",
        f"`routing_optimality` is null. `model_independence` is null.",
        "",
        "## Reproducibility",
        "",
    ]
    repro = model["reproducibility"]
    for key in (
        "schema_version",
        "ohbe_version",
        "ohbe_versions_on_executions",
        "git_commits",
        "policy_versions",
        "gateway",
        "image_digests",
        "harness_versions",
        "pricing",
        "model_pool_ids",
        "model_pool_hashes",
        "timeouts_seconds",
        "repetitions",
        "max_total_tokens",
        "max_cost",
    ):
        lines.append(f"- {key}: `{_plain(repro[key])}`")
    lines.extend(["", f"- input_sha256: `{model['input_sha256']}`", "", "## Release-gate checklist", ""])
    lines.append("| Gate | Passed | Summary |")
    lines.append("| --- | --- | --- |")
    for gate in model["release_gates"]:
        lines.append(
            f"| `{_cell(gate['gate_id'])}` | {_yn(gate['passed'])} | {_cell(gate['summary'])} |"
        )
    lines.extend(["", "Evidence pointers stay in `report.json` under `release_gates`.", "", "## Observed metrics", ""])
    lines.append(
        "| Task | Harness | Valid | Invalid | Successes | Task failures | Success rate | 95% interval | "
        "Total model cost | Cost per success | Usage | Prompt tokens | Latency mean ms |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in _rows(model):
        interval = _interval(row)
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{_cell(row['task_id'])}`",
                    f"`{_cell(row['harness_id'])}`",
                    _cell(row["valid_count"]),
                    _cell(row["invalid_count"]),
                    _cell(row["success_count"]),
                    _cell(row["task_failure_count"]),
                    _cell(row["success_rate"]),
                    _cell(interval),
                    _cell(row["total_model_cost"]),
                    _cell(row["cost_per_success"]),
                    _cell(row["usage_status"]),
                    _cell(row["prompt_tokens"]),
                    _cell(row["latency_ms_mean"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "A null cost or token total was not reported. It is not zero.", "", "## Deferred", ""])
    for item in model["deferred"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Limitations from the audit", ""])
    limitations = model["limitations"] or ["None recorded."]
    for item in limitations:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def _html(model: Mapping[str, Any]) -> str:
    label = html.escape(str(model["label"]))
    banner = (
        "Fixture/demo. Not live OpenBot or Inspect production spend."
        if model["label"] == "fixture"
        else "Spend figures are only those already present in the input."
    )
    repro_rows = []
    for key, value in model["reproducibility"].items():
        repro_rows.append(f"<tr><th>{html.escape(key)}</th><td><code>{html.escape(_plain(value))}</code></td></tr>")
    gate_rows = []
    for gate in model["release_gates"]:
        gate_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(gate['gate_id']))}</code></td>"
            f"<td>{html.escape(_yn(gate['passed']))}</td>"
            f"<td>{html.escape(str(gate['summary']))}</td>"
            "</tr>"
        )
    metric_rows = []
    for row in _rows(model):
        cells = [
            row["task_id"],
            row["harness_id"],
            row["valid_count"],
            row["invalid_count"],
            row["success_count"],
            row["task_failure_count"],
            row["success_rate"],
            _interval(row),
            row["total_model_cost"],
            row["cost_per_success"],
            row["usage_status"],
            row["prompt_tokens"],
            row["latency_ms_mean"],
        ]
        metric_rows.append("<tr>" + "".join(f"<td>{html.escape(_cell(cell))}</td>" for cell in cells) + "</tr>")
    deferred = "".join(f"<li>{html.escape(str(item))}</li>" for item in model["deferred"])
    limitations = model["limitations"] or ["None recorded."]
    limitation_html = "".join(f"<li>{html.escape(str(item))}</li>" for item in limitations)
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>OHBE v0.1 report</title>",
            "<style>",
            "body{font-family:sans-serif;max-width:60rem;margin:2rem auto;line-height:1.4}",
            "table{border-collapse:collapse;width:100%;margin:1rem 0}",
            "th,td{border:1px solid #ccc;padding:0.3rem;text-align:left;vertical-align:top}",
            "code{font-size:0.9em}",
            "</style>",
            "</head>",
            "<body>",
            f"<h1>OHBE v0.1 report ({label})</h1>",
            f"<p>{html.escape(banner)}</p>",
            f"<p>{html.escape(OBSERVED_RULE)}</p>",
            "<p>Harness rows follow the input order. That order is not a ranking.</p>",
            f"<p>Audit passed: {html.escape(_yn(model['audit_passed']))}. "
            "<code>routing_optimality</code> is null. <code>model_independence</code> is null.</p>",
            "<h2>Reproducibility</h2>",
            "<table>" + "".join(repro_rows) + "</table>",
            f"<p>input_sha256: <code>{html.escape(str(model['input_sha256']))}</code></p>",
            "<h2>Release-gate checklist</h2>",
            "<table><tr><th>Gate</th><th>Passed</th><th>Summary</th></tr>"
            + "".join(gate_rows)
            + "</table>",
            "<p>Evidence pointers stay in report.json under release_gates.</p>",
            "<h2>Observed metrics</h2>",
            "<table><tr><th>Task</th><th>Harness</th><th>Valid</th><th>Invalid</th>"
            "<th>Successes</th><th>Task failures</th><th>Success rate</th><th>95% interval</th>"
            "<th>Total model cost</th><th>Cost per success</th><th>Usage</th><th>Prompt tokens</th>"
            "<th>Latency mean ms</th></tr>"
            + "".join(metric_rows)
            + "</table>",
            "<p>A null cost or token total was not reported. It is not zero.</p>",
            "<h2>Deferred</h2>",
            f"<ul>{deferred}</ul>",
            "<h2>Limitations from the audit</h2>",
            f"<ul>{limitation_html}</ul>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _interval(row: Mapping[str, Any]) -> str:
    low = row.get("success_rate_low")
    high = row.get("success_rate_high")
    method = row.get("uncertainty")
    if low is None or high is None:
        return "not estimated"
    return f"[{low}, {high}] {method}"


def _yn(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "absent"


def _cell(value: object) -> str:
    if value is None:
        return "null"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _plain(value: object) -> str:
    if isinstance(value, (dict, list)):
        return canonical_json(value) if isinstance(value, dict) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return "null"
    return str(value)


def _unique(values: Any) -> list[Any]:
    rows: list[Any] = []
    for value in values:
        if value not in rows:
            rows.append(value)
    return rows


def _flat_unique(metrics: Sequence[Mapping[str, Any]], field: str) -> list[Any]:
    found: list[Any] = []
    for item in metrics:
        for harness in item.get("harnesses") or []:
            if not isinstance(harness, dict):
                continue
            for value in harness.get(field) or []:
                if value not in found:
                    found.append(value)
    return found


def _evidence_values(audit: Mapping[str, Any], gate_id: str, prefix: str) -> list[str]:
    found: list[str] = []
    for gate in audit.get("gates") or []:
        if not isinstance(gate, dict) or gate.get("gate_id") != gate_id:
            continue
        for item in gate.get("evidence") or []:
            text = str(item)
            if text.startswith(prefix):
                value = text[len(prefix) :]
                if value not in found:
                    found.append(value)
    return found
