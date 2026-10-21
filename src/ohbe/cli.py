"""Minimal OHBE command line.

v0.1 exposes help, the package version, JSONL trace verification, the Docker
egress release gate, the fixture pilot audit, the report renderer, and a live
pilot runner, and an offline private-host doctor. `benchmark run` starts
harnesses only after live prerequisites are present. `benchmark doctor`
does not open a socket and does not invent token counts or prices.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from ohbe import __version__
from ohbe.audit import audit_document, build_pilot_corpus, report_generator_input, run_pilot_audit
from ohbe.aggregate import dump_json
from ohbe.core.base import SCHEMA_VERSION
from ohbe.core.trace import TraceLog
from ohbe.cost.table import PricingError, load_pricing_table
from ohbe.live import LivePilotError, load_live_pilot, resolve_live_context, run_live_pilot
from ohbe.pools.doctor import DoctorError, format_doctor_report, run_doctor
from ohbe.report import ReportError, load_report_input, write_report
from ohbe.environment.docker_runner import (
    GATEWAY_ALIAS,
    GATEWAY_PORT,
    DockerEgressRunner,
    EgressBypassError,
    docker_available,
)
from ohbe.environment.policy import controlled_track_policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description=(
            "OHBE (OpenBot Harness Benchmark Engine). "
            "v0.1 exposes schema, trace integrity, egress isolation, the fixture pilot audit, "
            "the report, a live pilot runner that refuses to start without credentials, "
            "and an offline doctor for a private LiteLLM host."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"ohbe {__version__} (schema {SCHEMA_VERSION})",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("version", help="Print the package version and schema version")
    verify = commands.add_parser(
        "verify-trace",
        help="Verify an append-only JSONL execution trace",
    )
    verify.add_argument("path", help="Path to the JSONL trace")
    commands.add_parser(
        "check-egress",
        help="Prove direct model-provider requests fail under the Docker egress policy",
    )
    audit = commands.add_parser(
        "audit-pilot",
        help="Audit fixture paired executions and write a JSON gate report",
    )
    audit.add_argument("--output", required=True, help="Path for the audit JSON artifact")
    audit.add_argument(
        "--report-input",
        help="Optional path for ohbe.report_input JSON (benchmark report reads it)",
    )
    audit.add_argument(
        "--pricing",
        default="pricing/ohbe-offline-2026-10-09.yaml",
        help="Versioned offline pricing YAML",
    )
    publish = commands.add_parser(
        "report",
        help="Render a v0.1 report from an ohbe.report_input JSON file",
    )
    publish.add_argument("--input", required=True, help="Path to ohbe.report_input JSON")
    publish.add_argument("--out", required=True, help="Directory for report.json, metrics.jsonl, report.md, and report.html")
    live = commands.add_parser(
        "run",
        help="Run a live-pilot experiment when credentials and pins are set",
    )
    live.add_argument("--experiment", required=True, help="Path to an ohbe.live_pilot YAML manifest")
    live.add_argument(
        "--out",
        default="results/live",
        help="Directory for results/<experiment_id> (default: results/live)",
    )
    doctor = commands.add_parser(
        "doctor",
        help="Validate a private LiteLLM host, pool template, and smoke experiment offline",
    )
    doctor.add_argument(
        "--pool",
        default="models/pools/pool-gpu-farm.example.yaml",
        help="Model pool template YAML (default: models/pools/pool-gpu-farm.example.yaml)",
    )
    doctor.add_argument(
        "--experiment",
        default="experiments/live-pilot-smoke.yaml",
        help="Live-pilot manifest (default: experiments/live-pilot-smoke.yaml)",
    )
    doctor.add_argument(
        "--checklist",
        default="docs/private-deploy-checklist.md",
        help="Checklist whose next smoke commands are printed",
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "version":
        print(f"ohbe {__version__}")
        print(f"schema {SCHEMA_VERSION}")
        return 0
    if args.command == "verify-trace":
        report = TraceLog(args.path).verify()
        if report.ok:
            print(f"ok records={report.record_count} head={report.head_hash}")
            return 0
        print(report.errors[0] if report.errors else "trace integrity check failed", file=sys.stderr)
        return 1
    if args.command == "check-egress":
        return _check_egress()
    if args.command == "audit-pilot":
        return _audit_pilot(args.output, args.report_input, args.pricing)
    if args.command == "report":
        return _publish_report(args.input, args.out)
    if args.command == "run":
        return _run_live(args.experiment, args.out)
    if args.command == "doctor":
        return _doctor(args.pool, args.experiment, args.checklist)
    parser.print_help()
    return 2


def _check_egress() -> int:
    if not docker_available():
        print("Docker daemon is not available", file=sys.stderr)
        return 2
    policy = controlled_track_policy(gateway_host=GATEWAY_ALIAS, gateway_port=GATEWAY_PORT)
    try:
        report = DockerEgressRunner(policy).run_probe()
    except EgressBypassError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports runner failures
        print(str(exc), file=sys.stderr)
        return 1
    print(
        "ok "
        f"policy={report.environment.egress_policy_version} "
        f"image={report.environment.image_digest}"
    )
    return 0


def _audit_pilot(output: str, report_input: str | None, pricing: str) -> int:
    path = Path(pricing)
    if not path.is_file():
        print(f"pricing file not found: {pricing}", file=sys.stderr)
        return 2
    try:
        table = load_pricing_table(path)
    except (OSError, PricingError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="ohbe-pilot-") as temporary:
        corpus = build_pilot_corpus(table, Path(temporary))
        report, metrics = run_pilot_audit(corpus)
    document = audit_document(report)
    dump_json(output, document)
    if report_input is not None:
        dump_json(report_input, report_generator_input(report, metrics))
    failed = [gate["gate_id"] for gate in document["gates"] if gate["passed"] is not True]
    if failed:
        print("fail " + ",".join(str(gate_id) for gate_id in failed), file=sys.stderr)
        return 1
    print(f"ok gates={len(document['gates'])} output={output}")
    return 0


def _run_live(experiment: str, out_dir: str) -> int:
    try:
        spec = load_live_pilot(experiment)
        context = resolve_live_context(os.environ, spec)
        result = run_live_pilot(spec, context=context, out_dir=out_dir)
    except LivePilotError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not result.published or not result.passed:
        print(result.message, file=sys.stderr)
        return 1
    print(result.message)
    return 0


def _doctor(pool: str, experiment: str, checklist: str) -> int:
    try:
        report = run_doctor(pool_path=pool, experiment_path=experiment, checklist_path=checklist)
    except DoctorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = format_doctor_report(report)
    if report.ok:
        print(text, end="")
        return 0
    print(text, end="", file=sys.stderr)
    return 1


def _publish_report(source: str, out_dir: str) -> int:
    try:
        document = load_report_input(source)
        write_report(document, out_dir)
    except ReportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"ok out={out_dir}")
    return 0


def cli() -> None:
    raise SystemExit(main())
