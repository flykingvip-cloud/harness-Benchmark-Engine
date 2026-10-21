"""v0.1 report renderer.

The input is an `ohbe.report_input` document. Importing this package does not
import Inspect AI or LiteLLM.
"""

from ohbe.report.render import (
    DEFERRED,
    OBSERVED_RULE,
    REPORT_KIND,
    ReportError,
    ReportFiles,
    load_report_input,
    render_report,
    write_report,
)

__all__ = [
    "DEFERRED",
    "OBSERVED_RULE",
    "REPORT_KIND",
    "ReportError",
    "ReportFiles",
    "load_report_input",
    "render_report",
    "write_report",
]
