"""The public HTTP surface a future OHBE SDK binds.

The engine stays in ``ohbe``. These routes are the contract. A later Python
or JavaScript client should call them instead of reimplementing scoring,
egress, or pricing.
"""

from __future__ import annotations

from typing import TypedDict


class SurfaceRoute(TypedDict):
    method: str
    path: str
    sdk: str
    engine: str


SURFACE_ID = "ohbe.http.v1"
ENGINE_ID = "ohbe"

SURFACE_ROUTES: tuple[SurfaceRoute, ...] = (
    {
        "method": "GET",
        "path": "/v1",
        "sdk": "OhbeClient.surface()",
        "engine": "ohbe.api.surface",
    },
    {
        "method": "GET",
        "path": "/health",
        "sdk": "OhbeClient.health()",
        "engine": "ohbe.__version__",
    },
    {
        "method": "GET",
        "path": "/v1/health",
        "sdk": "OhbeClient.health()",
        "engine": "ohbe.__version__",
    },
    {
        "method": "GET",
        "path": "/v1/status",
        "sdk": "OhbeClient.status()",
        "engine": "ohbe.__version__",
    },
    {
        "method": "GET",
        "path": "/v1/doctor",
        "sdk": "OhbeClient.doctor()",
        "engine": "ohbe.api.doctor",
    },
    {
        "method": "GET",
        "path": "/v1/experiments",
        "sdk": "OhbeClient.experiments.list()",
        "engine": "ohbe.audit fixture catalog",
    },
    {
        "method": "GET",
        "path": "/v1/experiments/{experiment_id}",
        "sdk": "OhbeClient.experiments.get(id)",
        "engine": "ohbe.audit fixture catalog",
    },
    {
        "method": "GET",
        "path": "/v1/pools",
        "sdk": "OhbeClient.pools.list()",
        "engine": "ohbe.audit.fixtures.pilot_pool",
    },
    {
        "method": "GET",
        "path": "/v1/pools/{pool_id}",
        "sdk": "OhbeClient.pools.get(id)",
        "engine": "ohbe.audit.fixtures.pilot_pool",
    },
    {
        "method": "GET",
        "path": "/v1/results",
        "sdk": "OhbeClient.results.list()",
        "engine": "results/fixture-pilot/report.json and results/live/<id>/report.json",
    },
    {
        "method": "GET",
        "path": "/v1/results/{result_id}",
        "sdk": "OhbeClient.results.get(id)",
        "engine": "results/fixture-pilot/report.json and results/live/<id>/report.json",
    },
    {
        "method": "POST",
        "path": "/v1/dry-run",
        "sdk": "OhbeClient.validate()",
        "engine": "ohbe.environment.policy",
    },
    {
        "method": "GET",
        "path": "/v1/catalog/models",
        "sdk": "OhbeClient.catalog.models()",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "GET",
        "path": "/v1/catalog/harnesses",
        "sdk": "OhbeClient.catalog.harnesses()",
        "engine": "ohbe.api.harnesses",
    },
    {
        "method": "POST",
        "path": "/v1/catalog/harnesses",
        "sdk": "OhbeClient.catalog.harnesses.select()",
        "engine": "ohbe.api.harnesses",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/comparison-slots",
        "sdk": "OhbeClient.rounds.comparisonSlots()",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/comparison-slots",
        "sdk": "OhbeClient.rounds.comparisonSlots.replace()",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/plans",
        "sdk": "OhbeClient.rounds.plans.list()",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/plans/{plan_id}",
        "sdk": "OhbeClient.rounds.plans.get(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/plans/{plan_id}/slots",
        "sdk": "OhbeClient.rounds.plans.slots(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "PUT",
        "path": "/v1/rounds/plans/{plan_id}/slots",
        "sdk": "OhbeClient.rounds.plans.slots.replace(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/plans",
        "sdk": "OhbeClient.rounds.plans.create()",
        "engine": "ohbe.core.round",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/plans/{plan_id}/suite",
        "sdk": "OhbeClient.rounds.plans.suite(id)",
        "engine": "ohbe.core.round",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/plans/{plan_id}/confirm",
        "sdk": "OhbeClient.rounds.plans.confirm(id)",
        "engine": "ohbe.core.round",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/plans/{plan_id}/start",
        "sdk": "OhbeClient.rounds.plans.start(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/plans/{plan_id}/fork",
        "sdk": "OhbeClient.rounds.plans.fork(id)",
        "engine": "ohbe.core.round",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/suites",
        "sdk": "OhbeClient.rounds.suites.list()",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/suites/{suite_id}",
        "sdk": "OhbeClient.rounds.suites.get(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/reports",
        "sdk": "OhbeClient.rounds.reports.list()",
        "engine": "ohbe.core.round",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/reports/{report_id}",
        "sdk": "OhbeClient.rounds.reports.get(id)",
        "engine": "ohbe.core.round",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/jobs/{job_id}",
        "sdk": "OhbeClient.rounds.jobs.get(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/jobs/{job_id}/events",
        "sdk": "OhbeClient.rounds.jobs.events(id)",
        "engine": "ohbe.core.round",
    },
    {
        "method": "POST",
        "path": "/v1/rounds/jobs/{job_id}/tick",
        "sdk": "OhbeClient.rounds.jobs.tick(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/rounds/jobs/{job_id}/stream",
        "sdk": "OhbeClient.rounds.jobs.stream(id)",
        "engine": "ohbe.api.rounds",
    },
    {
        "method": "GET",
        "path": "/v1/analysis/model-fit",
        "sdk": "OhbeClient.analysis.modelFit()",
        "engine": "ohbe.core.round",
    },
    {
        "method": "GET",
        "path": "/v1/pool-drafts",
        "sdk": "OhbeClient.poolDrafts.list()",
        "engine": "ohbe.pools.template",
    },
    {
        "method": "GET",
        "path": "/v1/pool-drafts/{filename}",
        "sdk": "OhbeClient.poolDrafts.get(filename)",
        "engine": "ohbe.pools.template",
    },
    {
        "method": "POST",
        "path": "/v1/pool-drafts/validate",
        "sdk": "OhbeClient.poolDrafts.validate()",
        "engine": "ohbe.pools.template",
    },
    {
        "method": "POST",
        "path": "/v1/pool-drafts",
        "sdk": "OhbeClient.poolDrafts.save()",
        "engine": "ohbe.pools.template",
    },
    {
        "method": "GET",
        "path": "/v1/model-catalog",
        "sdk": "OhbeClient.modelCatalog.list()",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "GET",
        "path": "/v1/model-catalog/{filename}",
        "sdk": "OhbeClient.modelCatalog.get(filename)",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "POST",
        "path": "/v1/model-catalog/validate",
        "sdk": "OhbeClient.modelCatalog.validate()",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "POST",
        "path": "/v1/model-catalog",
        "sdk": "OhbeClient.modelCatalog.save()",
        "engine": "ohbe.api.intake",
    },
    {
        "method": "GET",
        "path": "/v1/harness-configs",
        "sdk": "OhbeClient.harnessConfigs.list()",
        "engine": "ohbe.core.harness",
    },
    {
        "method": "GET",
        "path": "/v1/harness-configs/{profile_id}",
        "sdk": "OhbeClient.harnessConfigs.get(id)",
        "engine": "ohbe.core.harness",
    },
    {
        "method": "POST",
        "path": "/v1/harness-configs/validate",
        "sdk": "OhbeClient.harnessConfigs.validate()",
        "engine": "ohbe.core.harness",
    },
    {
        "method": "POST",
        "path": "/v1/harness-configs",
        "sdk": "OhbeClient.harnessConfigs.save()",
        "engine": "ohbe.core.harness",
    },
    {
        "method": "POST",
        "path": "/v1/library/probe",
        "sdk": "OhbeClient.library.probe()",
        "engine": "ohbe.library",
    },
    {
        "method": "POST",
        "path": "/v1/harness-library/check",
        "sdk": "OhbeClient.harnessLibrary.check()",
        "engine": "ohbe.library",
    },
    {
        "method": "POST",
        "path": "/v1/harness-library/install",
        "sdk": "OhbeClient.harnessLibrary.install()",
        "engine": "ohbe.library",
    },
    {
        "method": "POST",
        "path": "/v1/harness-library/scan",
        "sdk": "OhbeClient.harnessLibrary.scan()",
        "engine": "ohbe.library",
    },
    {
        "method": "POST",
        "path": "/v1/model-library/admit",
        "sdk": "OhbeClient.modelLibrary.admit()",
        "engine": "ohbe.library",
    },
    {
        "method": "POST",
        "path": "/v1/model-library/scan",
        "sdk": "OhbeClient.modelLibrary.scan()",
        "engine": "ohbe.library",
    },
)

# Exact value required before a request may even ask for live spend.
# Any other string, including "true", stays locked.
LIVE_SPEND_ENV = "OHBE_ALLOW_LIVE_SPEND"
LIVE_SPEND_UNLOCK_VALUE = "1"
