"""Pilot fairness audit.

Importing this package does not import Inspect AI or LiteLLM and does not
claim tool-surface parity or routing optimality.
"""

from ohbe.audit.fixtures import PilotCorpus, build_pilot_corpus
from ohbe.audit.gates import LIMITATIONS, AuditGate, AuditReport, audit_document, run_pilot_audit
from ohbe.audit.report_input import REPORT_INPUT_KIND, report_generator_input

__all__ = [
    "LIMITATIONS",
    "REPORT_INPUT_KIND",
    "AuditGate",
    "AuditReport",
    "PilotCorpus",
    "audit_document",
    "build_pilot_corpus",
    "report_generator_input",
    "run_pilot_audit",
]
