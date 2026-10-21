"""Input document for the v0.1 report.

`ohbe.report` reads this object: the pilot audit plus the metrics JSON for
each task. This module does not render the report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ohbe.audit.gates import AuditReport, audit_document
from ohbe.core.base import SCHEMA_VERSION

REPORT_INPUT_KIND = "ohbe.report_input"
REPORT_INPUT_NOTE = "ohbe.report reads this document. It is not itself the published report."


def report_generator_input(
    report: AuditReport,
    metrics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bundle the audit artifact and the per-task metrics documents."""

    return {
        "audit": audit_document(report),
        "kind": REPORT_INPUT_KIND,
        "metrics": [dict(item) for item in metrics],
        "note": REPORT_INPUT_NOTE,
        "schema_version": SCHEMA_VERSION,
    }
