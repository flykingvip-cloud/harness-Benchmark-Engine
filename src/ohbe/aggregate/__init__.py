"""Paired-run aggregation.

Metrics are recomputed from executions and traces. Importing this package
does not import Inspect AI or LiteLLM and does not claim routing optimality.
"""

from ohbe.aggregate.report import (
    OBSERVED_NOTE,
    AggregationError,
    HarnessMetrics,
    PairedProtocol,
    PairedReport,
    ScoredRun,
    aggregate_paired,
    dump_json,
    dump_jsonl,
    execution_documents,
    report_document,
)

__all__ = [
    "OBSERVED_NOTE",
    "AggregationError",
    "HarnessMetrics",
    "PairedProtocol",
    "PairedReport",
    "ScoredRun",
    "aggregate_paired",
    "dump_json",
    "dump_jsonl",
    "execution_documents",
    "report_document",
]
