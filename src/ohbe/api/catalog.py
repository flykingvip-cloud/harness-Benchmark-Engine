"""Read-only catalog of the fixture experiment, pool, and report.

The fixture record is the checked-in report. Live rows are ``report.json``
files already under ``results/live/<experiment_id>/``. This module does not
call a model, does not invent spend, and does not turn missing usage into zero.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from ohbe.audit.fixtures import pilot_pool, pilot_pricing_ref
from ohbe.cost.table import PricingError, load_pricing_table
from ohbe.api.schemas import (
    ExperimentSummary,
    HarnessResult,
    PoolModel,
    PoolSummary,
    ReleaseGate,
    ResultDetail,
    ResultIndex,
    TaskResult,
)

FIXTURE_EXPERIMENT_ID = "fixture-pilot"
FIXTURE_RESULT_ID = "fixture-pilot"
FIXTURE_NOTE = "Fixture corpus. These executions are not live model spend."
_UNIT_INTERVAL = re.compile(r"^(?:0(?:\.\d+)?|1(?:\.0+)?)$")
_UNCERTAINTY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
PRICING_RELATIVE = Path("pricing") / "ohbe-offline-2026-10-09.yaml"
REPORT_RELATIVE = Path("results") / "fixture-pilot" / "report.json"
LIVE_RESULTS_DIR = Path("results") / "live"
# ``benchmark report --out results/live/<id>/report`` writes the nested file.
# A directory-level ``report.json`` is the import path tests and operators can drop in.
_LIVE_REPORT_RELATIVES = (Path("report.json"), Path("report") / "report.json")
_LIVE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class CatalogUnavailable(RuntimeError):
    """The fixture catalog could not be read from the repository root."""


def default_root() -> Path:
    """Repository root from ``OHBE_ROOT`` or the checkout that contains this package."""

    env = os.environ.get("OHBE_ROOT")
    if env:
        path = Path(env).expanduser().resolve()
        if not path.is_dir():
            raise CatalogUnavailable(f"OHBE_ROOT is not a directory: {env}")
        return path
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "ohbe").is_dir():
            return parent
    raise CatalogUnavailable("OHBE_ROOT is unset and the repository root was not found")


class Catalog:
    """Fixture experiment, model pool, and report for one checkout."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.pricing_path = self.root / PRICING_RELATIVE
        self.report_path = self.root / REPORT_RELATIVE

    def experiments(self) -> list[ExperimentSummary]:
        report, pool = self._report_and_pool()
        return [self._experiment(report, pool)]

    def experiment(self, experiment_id: str) -> ExperimentSummary:
        found = next((item for item in self.experiments() if item.experiment_id == experiment_id), None)
        if found is None:
            raise KeyError(experiment_id)
        return found

    def pools(self) -> list[PoolSummary]:
        report, pool = self._report_and_pool()
        return [self._pool_summary(report, pool)]

    def pool(self, pool_id: str) -> PoolSummary:
        found = next((item for item in self.pools() if item.pool_id == pool_id), None)
        if found is None:
            raise KeyError(pool_id)
        return found

    def results(self) -> list[ResultIndex]:
        """Fixture first, then live reports already on disk.

        A missing fixture report still fails the catalog. One unreadable live
        file is omitted from the list so the fixture row stays available.
        """

        rows = [self._index(self._fixture_report(), FIXTURE_RESULT_ID, REPORT_RELATIVE.as_posix(), "fixture")]
        rows.extend(self._live_indexes())
        return rows

    def result(self, result_id: str) -> ResultDetail:
        if result_id == FIXTURE_RESULT_ID:
            return self._detail(self._fixture_report(), FIXTURE_RESULT_ID, REPORT_RELATIVE.as_posix(), "fixture")
        path = self._live_report_file(result_id)
        if path is None:
            raise KeyError(result_id)
        return self._detail(self._live_report(path), result_id, self._relative(path), "live")

    def _experiment(self, report: dict[str, Any], pool: Any) -> ExperimentSummary:
        task_ids = [str(item.get("task_id")) for item in _list(report.get("tasks"))]
        return ExperimentSummary(
            experiment_id=FIXTURE_EXPERIMENT_ID,
            name="Fixture pilot",
            label="fixture",
            spend="not_live",
            task_ids=task_ids,
            pool_id=pool.pool_id,
            pool_hash=pool.content_hash,
            audit_passed=report.get("audit_passed") is True,
            source=REPORT_RELATIVE.as_posix(),
            note=FIXTURE_NOTE,
        )

    def _pool_summary(self, report: dict[str, Any], pool: Any) -> PoolSummary:
        recorded = _list(_mapping(report.get("reproducibility")).get("model_pool_hashes"))
        matches = pool.content_hash in recorded
        return PoolSummary(
            pool_id=pool.pool_id,
            content_hash=pool.content_hash,
            models=[
                PoolModel(
                    model_id=model.model_id,
                    provider=model.provider,
                    name=model.name,
                    revision=model.revision,
                )
                for model in pool.models
            ],
            hash_matches_report=matches,
            source="ohbe.audit.fixtures.pilot_pool",
            note=FIXTURE_NOTE,
        )

    def _report_and_pool(self) -> tuple[dict[str, Any], Any]:
        return self._report(), self._pool()

    def _fixture_report(self) -> dict[str, Any]:
        document = _read_report(self.report_path, missing=f"fixture report is missing: {REPORT_RELATIVE.as_posix()}")
        if document.get("spend") != "not_live" or document.get("label") != "fixture":
            raise CatalogUnavailable("fixture report is not marked not_live")
        return document

    def _report(self) -> dict[str, Any]:
        return self._fixture_report()

    def _live_indexes(self) -> list[ResultIndex]:
        rows: list[ResultIndex] = []
        for result_id in self._live_ids():
            path = self._live_report_file(result_id)
            if path is None:
                continue
            try:
                document = self._live_report(path)
            except CatalogUnavailable:
                continue
            rows.append(self._index(document, result_id, self._relative(path), "live"))
        return rows

    def _live_ids(self) -> list[str]:
        root = self.root / LIVE_RESULTS_DIR
        if not root.is_dir():
            return []
        names: list[str] = []
        for child in root.iterdir():
            if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                continue
            if child.name == FIXTURE_RESULT_ID or _LIVE_ID.fullmatch(child.name) is None:
                continue
            names.append(child.name)
        return sorted(names)

    def _live_report_file(self, result_id: str) -> Path | None:
        if result_id == FIXTURE_RESULT_ID or _LIVE_ID.fullmatch(result_id) is None:
            return None
        live_root = (self.root / LIVE_RESULTS_DIR).resolve()
        directory = self.root / LIVE_RESULTS_DIR / result_id
        try:
            if not directory.resolve().is_relative_to(live_root):
                return None
        except OSError:
            return None
        for relative in _LIVE_REPORT_RELATIVES:
            candidate = directory / relative
            if not candidate.is_file():
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved.is_file() and resolved.is_relative_to(live_root):
                return candidate
        return None

    def _live_report(self, path: Path) -> dict[str, Any]:
        document = _read_report(path, missing="live report is missing")
        label = document.get("label")
        spend = document.get("spend")
        if not isinstance(label, str) or not label:
            raise CatalogUnavailable("live report label is missing")
        if not isinstance(spend, str) or not spend:
            raise CatalogUnavailable("live report spend is missing")
        return document

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _index(
        self,
        report: dict[str, Any],
        result_id: str,
        source: str,
        source_kind: Literal["fixture", "live"],
    ) -> ResultIndex:
        tasks = report.get("tasks")
        count = len(tasks) if isinstance(tasks, list) else 0
        return ResultIndex(
            result_id=result_id,
            kind=str(report.get("kind")),
            label=str(report.get("label")),
            spend=str(report.get("spend")),
            audit_passed=report.get("audit_passed") is True,
            task_count=count,
            source=source,
            source_kind=source_kind,
        )

    def _detail(
        self,
        report: dict[str, Any],
        result_id: str,
        source: str,
        source_kind: Literal["fixture", "live"],
    ) -> ResultDetail:
        return ResultDetail(
            result_id=result_id,
            kind=str(report.get("kind")),
            label=str(report.get("label")),
            spend=str(report.get("spend")),
            audit_passed=report.get("audit_passed") is True,
            observed_rule=str(report.get("observed_rule") or ""),
            tasks=[_task(item) for item in _list(report.get("tasks"))],
            release_gates=[_gate(item) for item in _list(report.get("release_gates"))],
            source=source,
            source_kind=source_kind,
        )

    def _pool(self) -> Any:
        if not self.pricing_path.is_file():
            raise CatalogUnavailable(f"pricing file is missing: {PRICING_RELATIVE.as_posix()}")
        try:
            table = load_pricing_table(self.pricing_path)
        except (OSError, PricingError) as exc:
            raise CatalogUnavailable("offline pricing table could not be loaded") from exc
        return pilot_pool(pilot_pricing_ref(table))


def _read_report(path: Path, *, missing: str) -> dict[str, Any]:
    if not path.is_file():
        raise CatalogUnavailable(missing)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogUnavailable("report is not JSON") from exc
    if not isinstance(document, dict) or document.get("kind") != "ohbe.report":
        raise CatalogUnavailable("report kind is not ohbe.report")
    return document


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _task(item: Any) -> TaskResult:
    data = _mapping(item)
    return TaskResult(
        task_id=str(data.get("task_id")),
        task_content_hash=str(data.get("task_content_hash")),
        note=str(data.get("note") or ""),
        harnesses=[_harness(entry) for entry in _list(data.get("harnesses"))],
    )


def _harness(item: Any) -> HarnessResult:
    data = _mapping(item)
    return HarnessResult(
        harness_id=str(data.get("harness_id")),
        success_rate=_optional_str(data.get("success_rate")),
        success_rate_low=_unit_interval(data.get("success_rate_low")),
        success_rate_high=_unit_interval(data.get("success_rate_high")),
        uncertainty=_uncertainty(data.get("uncertainty")),
        success_count=_int(data.get("success_count")),
        valid_count=_int(data.get("valid_count")),
        invalid_count=_int(data.get("invalid_count")),
        task_failure_count=_int(data.get("task_failure_count")),
        total_model_cost=_optional_str(data.get("total_model_cost")),
        cost_status=str(data.get("cost_status")),
        usage_status=str(data.get("usage_status")),
        total_tokens=_optional_int(data.get("total_tokens")),
        label=str(data.get("label")),
        cost_per_success=_optional_str(data.get("cost_per_success")),
        complete=data.get("complete") is True,
        invalid_by_category=_category_counts(data.get("invalid_by_category")),
    )


def _gate(item: Any) -> ReleaseGate:
    data = _mapping(item)
    return ReleaseGate(
        gate_id=str(data.get("gate_id")),
        passed=data.get("passed") is True,
        summary=str(data.get("summary") or ""),
    )


def _unit_interval(value: Any) -> str | None:
    if isinstance(value, str) and _UNIT_INTERVAL.fullmatch(value):
        return value
    return None


def _uncertainty(value: Any) -> str | None:
    if isinstance(value, str) and _UNCERTAINTY.fullmatch(value):
        return value
    return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _category_counts(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    counts: dict[str, int] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            counts[key] = item
    return counts


def _int(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
