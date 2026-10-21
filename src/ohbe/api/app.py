"""FastAPI application for the OHBE ``/v1`` surface.

Routes read the engine, the fixture catalog, and live report files already on disk.
They do not call the LiteLLM
SDK, Inspect, or a model provider. Intake may GET a scoped listing path on an
operator gateway only after the egress policy matches. A present API key is
sent only as a Bearer token on ``GET /v1/models``. That probe does not place
a completion and does not unlock live spend. Startup loads valid
``models/catalog`` YAML into the intake catalog and does not open a socket.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from ohbe import __version__
from ohbe.api.admitted_pool import ADMITTED_POOL_ID, build_admitted_pool
from ohbe.api.catalog import Catalog, CatalogUnavailable, default_root
from ohbe.api.doctor import collect_doctor, live_spend_state
from ohbe.api.forms import (
    list_harness_profiles,
    list_pool_files,
    read_harness_profile,
    read_pool_file,
    save_harness_profile,
    save_pool_draft,
    validate_harness_profile,
    validate_pool_draft,
)
from ohbe.api.harnesses import HarnessDetector, HarnessInventory, HostHarnessDetector
from ohbe.api.intake import (
    IntakeService,
    ProbeTransport,
    list_catalog_entries,
    read_catalog_entry,
    save_catalog_entry,
    validate_catalog_entry,
)
from ohbe.api.library import (
    admit_model,
    check_harness,
    install_harness,
    probe_remote,
    scan_harnesses,
    scan_models,
)
from ohbe.api.rounds import RoundStore
from ohbe.api.schemas import (
    CatalogModel,
    CatalogModelList,
    CloudIntakeRequest,
    ComparisonSlotsRequest,
    ComparisonSlotsResponse,
    CreateRoundPlanRequest,
    DoctorResponse,
    DryRunRequest,
    DryRunResponse,
    ErrorBody,
    ExperimentList,
    ExperimentSummary,
    ForkPlanRequest,
    CatalogEntryRequest,
    CatalogEntryView,
    ConfirmSuiteRequest,
    CatalogList,
    CatalogSaved,
    CatalogValidation,
    HandshakeRequest,
    HandshakeResponse,
    HarnessCheckRequest,
    HarnessInstallRequest,
    HarnessInventoryResponse,
    HarnessProfileList,
    HarnessProfileRequest,
    HarnessProfileSaved,
    HarnessProfileValidation,
    HarnessScanRequest,
    HarnessSelectRequest,
    LibraryActionResult,
    HealthResponse,
    JobView,
    LiveFlagRequest,
    PlanSlotsResponse,
    ModelAdmitRequest,
    ModelFitResponse,
    ModelScanRequest,
    ProbeRequest,
    PoolDraftRequest,
    PoolDraftSaved,
    PoolDraftValidation,
    PoolFileDetail,
    PoolFileList,
    PoolList,
    PoolSummary,
    ResultDetail,
    ResultList,
    RoundPlanDetail,
    RoundPlanList,
    RoundReportDetail,
    RoundReportList,
    RoundStartResponse,
    ServerScanRequest,
    ServerScanResponse,
    StatusResponse,
    SuiteUpsertRequest,
    SuiteView,
    SuiteViewList,
    SurfaceResponse,
    SurfaceRouteModel,
)
from ohbe.api.surface import ENGINE_ID, LIVE_SPEND_ENV, SURFACE_ID, SURFACE_ROUTES
from ohbe.api.validate import ValidationRejected, validate_dry_run
from ohbe.core.base import SCHEMA_VERSION

_LOCAL_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


def create_app(
    root: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    probe: ProbeTransport | None = None,
    harness_detector: HarnessDetector | None = None,
) -> FastAPI:
    """Build the workbench API. ``root`` defaults to the checkout or ``OHBE_ROOT``."""

    catalog = Catalog(Path(root) if root is not None else default_root())
    detector = harness_detector if harness_detector is not None else HostHarnessDetector(environ=environ)
    harnesses = HarnessInventory(detector)
    rounds = RoundStore(catalog, harnesses)
    intake = IntakeService(probe=probe)
    intake.hydrate(catalog.root)
    app = FastAPI(
        title="OHBE API",
        version=__version__,
        summary="Public HTTP surface of the OHBE engine. Validation does not spend.",
        docs_url="/v1/docs",
        openapi_url="/v1/openapi.json",
    )
    app.state.catalog = catalog
    app.state.rounds = rounds
    app.state.intake = intake
    app.state.harnesses = harnesses
    app.state.environ = environ
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_LOCAL_ORIGINS),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(CatalogUnavailable)
    def _catalog(_request: Request, exc: CatalogUnavailable) -> JSONResponse:
        return _error(503, "catalog_unavailable", str(exc))

    @app.exception_handler(ValidationRejected)
    def _rejected(_request: Request, exc: ValidationRejected) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, gateway_host=exc.gateway_host)

    @app.exception_handler(RequestValidationError)
    def _invalid(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "request body is not valid")

    @app.get("/v1", response_model=SurfaceResponse)
    def surface() -> SurfaceResponse:
        return SurfaceResponse(
            surface=SURFACE_ID,
            engine=ENGINE_ID,
            note=(
                "These routes are the public surface of the ohbe engine. "
                "A future SDK binds them. This process does not place model calls."
            ),
            routes=[SurfaceRouteModel.model_validate(dict(item)) for item in SURFACE_ROUTES],
        )

    def _health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="ohbe.api",
            ohbe_version=__version__,
            schema_version=SCHEMA_VERSION,
            live_spend=live_spend_state(_environ(app)),
            spend_execution="disabled",
            cloud_litellm="fail_closed",
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return _health()

    @app.get("/v1/health", response_model=HealthResponse)
    def health_v1() -> HealthResponse:
        return _health()

    @app.get("/v1/status", response_model=StatusResponse)
    def status() -> StatusResponse:
        return StatusResponse(
            service="ohbe.api",
            surface=SURFACE_ID,
            engine=ENGINE_ID,
            ohbe_version=__version__,
            schema_version=SCHEMA_VERSION,
            live_spend=live_spend_state(_environ(app)),
            spend_execution="disabled",
            cloud_litellm="fail_closed",
        )

    @app.get("/v1/doctor", response_model=DoctorResponse)
    def doctor() -> DoctorResponse:
        return collect_doctor(catalog, _environ(app))

    @app.get("/v1/experiments", response_model=ExperimentList)
    def experiments() -> ExperimentList:
        return ExperimentList(experiments=catalog.experiments())

    @app.get("/v1/experiments/{experiment_id}", response_model=ExperimentSummary)
    def experiment(experiment_id: str) -> ExperimentSummary:
        try:
            return catalog.experiment(experiment_id)
        except KeyError:
            raise ValidationRejected(404, "experiment_not_found", f"Unknown experiment: {experiment_id}") from None

    @app.get("/v1/pools", response_model=PoolList)
    def pools() -> PoolList:
        return PoolList(pools=catalog.pools())

    @app.get("/v1/pools/{pool_id}", response_model=PoolSummary)
    def pool(pool_id: str) -> PoolSummary:
        try:
            return catalog.pool(pool_id)
        except KeyError:
            raise ValidationRejected(404, "pool_not_found", f"Unknown model pool: {pool_id}") from None

    @app.get("/v1/pool-drafts", response_model=PoolFileList)
    def pool_drafts() -> PoolFileList:
        return list_pool_files(catalog.root, _environ(app))

    @app.get("/v1/pool-drafts/{filename}", response_model=PoolFileDetail)
    def pool_draft_file(filename: str) -> PoolFileDetail:
        return read_pool_file(catalog.root, filename, _environ(app))

    @app.post("/v1/pool-drafts/validate", response_model=PoolDraftValidation)
    def pool_draft_validate(body: PoolDraftRequest) -> PoolDraftValidation:
        return validate_pool_draft(body, _environ(app))

    @app.post("/v1/pool-drafts", response_model=PoolDraftSaved)
    def pool_draft_save(body: PoolDraftRequest) -> PoolDraftSaved:
        return save_pool_draft(catalog.root, body, _environ(app))

    @app.get("/v1/model-catalog", response_model=CatalogList)
    def model_catalog() -> CatalogList:
        return list_catalog_entries(catalog.root, _environ(app))

    @app.get("/v1/model-catalog/{filename}", response_model=CatalogEntryView)
    def model_catalog_file(filename: str) -> CatalogEntryView:
        return read_catalog_entry(catalog.root, filename, _environ(app))

    @app.post("/v1/model-catalog/validate", response_model=CatalogValidation)
    def model_catalog_validate(body: CatalogEntryRequest) -> CatalogValidation:
        return validate_catalog_entry(body, _environ(app))

    @app.post("/v1/model-catalog", response_model=CatalogSaved)
    def model_catalog_save(body: CatalogEntryRequest) -> CatalogSaved:
        saved = save_catalog_entry(catalog.root, body, _environ(app))
        intake.adopt_catalog_entry(saved.entry)
        return saved

    @app.get("/v1/harness-configs", response_model=HarnessProfileList)
    def harness_configs() -> HarnessProfileList:
        return list_harness_profiles(catalog.root, _environ(app))

    @app.get("/v1/harness-configs/{profile_id}", response_model=HarnessProfileSaved)
    def harness_config(profile_id: str) -> HarnessProfileSaved:
        return read_harness_profile(catalog.root, profile_id, _environ(app))

    @app.post("/v1/harness-configs/validate", response_model=HarnessProfileValidation)
    def harness_config_validate(body: HarnessProfileRequest) -> HarnessProfileValidation:
        return validate_harness_profile(body, _environ(app))

    @app.post("/v1/harness-configs", response_model=HarnessProfileSaved)
    def harness_config_save(body: HarnessProfileRequest) -> HarnessProfileSaved:
        return save_harness_profile(catalog.root, body, _environ(app))

    @app.post("/v1/library/probe", response_model=LibraryActionResult)
    def library_probe(body: ProbeRequest) -> LibraryActionResult:
        return probe_remote(body, _environ(app))

    @app.post("/v1/harness-library/check", response_model=LibraryActionResult)
    def harness_library_check(body: HarnessCheckRequest) -> LibraryActionResult:
        return check_harness(body, _environ(app))

    @app.post("/v1/harness-library/install", response_model=LibraryActionResult)
    def harness_library_install(body: HarnessInstallRequest) -> LibraryActionResult:
        return install_harness(catalog.root, body, _environ(app))

    @app.post("/v1/harness-library/scan", response_model=LibraryActionResult)
    def harness_library_scan(body: HarnessScanRequest) -> LibraryActionResult:
        return scan_harnesses(catalog.root, body, _environ(app))

    @app.post("/v1/model-library/admit", response_model=LibraryActionResult)
    def model_library_admit(body: ModelAdmitRequest) -> LibraryActionResult:
        return admit_model(catalog.root, body, _environ(app), intake)

    @app.post("/v1/model-library/scan", response_model=LibraryActionResult)
    def model_library_scan(body: ModelScanRequest) -> LibraryActionResult:
        return scan_models(catalog.root, body, _environ(app), intake)

    @app.get("/v1/results", response_model=ResultList)
    def results() -> ResultList:
        return ResultList(results=catalog.results())

    @app.get("/v1/results/{result_id}", response_model=ResultDetail)
    def result(result_id: str) -> ResultDetail:
        try:
            return catalog.result(result_id)
        except KeyError:
            raise ValidationRejected(404, "result_not_found", f"Unknown result: {result_id}") from None

    @app.post("/v1/dry-run", response_model=DryRunResponse)
    def dry_run(body: DryRunRequest) -> DryRunResponse:
        return validate_dry_run(body, catalog, _environ(app))

    @app.post("/v1/intake/cloud", response_model=CatalogModel)
    def intake_cloud(body: CloudIntakeRequest) -> CatalogModel:
        return intake.cloud(body)

    @app.post("/v1/intake/server/scan", response_model=ServerScanResponse)
    def intake_server_scan(body: ServerScanRequest) -> ServerScanResponse:
        return intake.scan(body)

    @app.post("/v1/intake/server/handshake", response_model=HandshakeResponse)
    def intake_server_handshake(body: HandshakeRequest) -> HandshakeResponse:
        return intake.handshake(body)

    @app.get("/v1/catalog/models", response_model=CatalogModelList)
    def catalog_models() -> CatalogModelList:
        return intake.catalog()

    @app.get("/v1/intake/pending", response_model=CatalogModelList)
    def intake_pending() -> CatalogModelList:
        return intake.pending()

    @app.get("/v1/catalog/harnesses", response_model=HarnessInventoryResponse)
    def catalog_harnesses() -> HarnessInventoryResponse:
        return harnesses.view()

    @app.post("/v1/catalog/harnesses", response_model=HarnessInventoryResponse)
    def select_catalog_harnesses(body: HarnessSelectRequest) -> HarnessInventoryResponse:
        return harnesses.select(body)

    @app.get("/v1/rounds/comparison-slots", response_model=ComparisonSlotsResponse)
    def comparison_slots() -> ComparisonSlotsResponse:
        return intake.comparison_slots()

    @app.post("/v1/rounds/comparison-slots", response_model=ComparisonSlotsResponse)
    def replace_comparison_slots(body: ComparisonSlotsRequest) -> ComparisonSlotsResponse:
        del body
        raise ValidationRejected(
            409,
            "slots_unscoped",
            "Comparison slots belong to a plan. Use PUT /v1/rounds/plans/{plan_id}/slots.",
        )

    @app.get("/v1/rounds/plans", response_model=RoundPlanList)
    def round_plans() -> RoundPlanList:
        return rounds.list_plans()

    @app.get("/v1/rounds/plans/{plan_id}", response_model=RoundPlanDetail)
    def round_plan(plan_id: str) -> RoundPlanDetail:
        return rounds.plan_detail(plan_id)

    @app.get("/v1/rounds/plans/{plan_id}/slots", response_model=PlanSlotsResponse)
    def round_plan_slots(plan_id: str) -> PlanSlotsResponse:
        return rounds.slot_view(plan_id)

    @app.put("/v1/rounds/plans/{plan_id}/slots", response_model=PlanSlotsResponse)
    def replace_round_plan_slots(plan_id: str, body: ComparisonSlotsRequest) -> PlanSlotsResponse:
        intake.validate_slot_ids(body.model_ids)
        return rounds.replace_plan_slots(plan_id, body.model_ids)

    @app.post("/v1/rounds/plans", response_model=RoundPlanDetail)
    def create_round_plan(body: CreateRoundPlanRequest) -> RoundPlanDetail:
        _reject_locked_live(body.live, _environ(app))
        requested = list(body.model_ids) if body.model_ids is not None else []
        intake.validate_slot_ids(requested)
        admitted_pool = None
        if requested:
            admitted_pool = build_admitted_pool(catalog.root, intake, requested)
        elif body.pool_id == ADMITTED_POOL_ID:
            raise ValidationRejected(
                409,
                "slots_required",
                "Set comparison slots from the admitted catalog before generating a plan.",
            )
        return rounds.create_plan(body, _environ(app), admitted_pool=admitted_pool, slot_ids=requested)

    @app.post("/v1/rounds/plans/{plan_id}/suite", response_model=SuiteView)
    def upsert_suite(plan_id: str, body: SuiteUpsertRequest) -> SuiteView:
        return rounds.upsert_suite(plan_id, body, _environ(app))

    @app.post("/v1/rounds/plans/{plan_id}/confirm", response_model=SuiteView)
    def confirm_round_suite(plan_id: str, body: ConfirmSuiteRequest) -> SuiteView:
        _reject_locked_live(body.live, _environ(app))
        stored = rounds.stored_slot_ids(plan_id)
        if body.model_ids is not None and list(body.model_ids) != stored:
            raise ValidationRejected(
                409,
                "slot_mismatch",
                "Confirm reads this plan's stored slots.",
            )
        admitted = intake.admitted_model_ids()
        for model_id in stored:
            if model_id not in admitted:
                raise ValidationRejected(404, "not_in_catalog", f"Model is not in the catalog: {model_id}")
        return rounds.confirm(
            plan_id,
            LiveFlagRequest(live=body.live),
            _environ(app),
            comparison_model_ids=stored,
        )

    @app.post("/v1/rounds/plans/{plan_id}/start", response_model=RoundStartResponse)
    def start_round(plan_id: str, body: LiveFlagRequest) -> RoundStartResponse:
        return rounds.start(plan_id, body, _environ(app))

    @app.post("/v1/rounds/plans/{plan_id}/fork", response_model=RoundPlanDetail)
    def fork_round(plan_id: str, body: ForkPlanRequest) -> RoundPlanDetail:
        return rounds.fork(plan_id, body, _environ(app))

    @app.get("/v1/rounds/suites", response_model=SuiteViewList)
    def round_suites() -> SuiteViewList:
        return rounds.list_suites()

    @app.get("/v1/rounds/suites/{suite_id}", response_model=SuiteView)
    def round_suite(suite_id: str) -> SuiteView:
        return rounds.suite(suite_id)

    @app.get("/v1/rounds/reports", response_model=RoundReportList)
    def round_reports() -> RoundReportList:
        return rounds.list_reports()

    @app.get("/v1/rounds/reports/{report_id}", response_model=RoundReportDetail)
    def round_report(report_id: str) -> RoundReportDetail:
        return rounds.report(report_id)

    @app.get("/v1/rounds/jobs/{job_id}", response_model=JobView)
    def round_job(job_id: str) -> JobView:
        return rounds.job_view(job_id)

    @app.get("/v1/rounds/jobs/{job_id}/events", response_model=JobView)
    def round_job_events(job_id: str) -> JobView:
        return rounds.job_view(job_id)

    @app.post("/v1/rounds/jobs/{job_id}/tick", response_model=RoundStartResponse)
    def tick_round_job(job_id: str, body: LiveFlagRequest) -> RoundStartResponse:
        return rounds.tick(job_id, body, _environ(app))

    @app.get("/v1/rounds/jobs/{job_id}/stream")
    def stream_round_job(job_id: str) -> StreamingResponse:
        view = rounds.job_view(job_id)

        def replay():
            for event in view.events:
                yield f"event: observation\ndata: {event.model_dump_json()}\n\n"
            yield 'event: end\ndata: {"label":"Dry-run","spend":"none","executed":false,"network":"not_contacted","fixture_pool_id":"pool-pilot"}\n\n'

        return StreamingResponse(replay(), media_type="text/event-stream")

    @app.get("/v1/analysis/model-fit", response_model=ModelFitResponse)
    def model_fit() -> ModelFitResponse:
        return rounds.model_fit()

    return app


def _reject_locked_live(live: bool, environ: Mapping[str, str]) -> None:
    if live and live_spend_state(environ) != "env_set":
        raise ValidationRejected(
            403,
            "live_spend_locked",
            f"Live spend requires JSON live=true and {LIVE_SPEND_ENV}=1. The request was not executed.",
        )


def _environ(app: FastAPI) -> Mapping[str, str]:
    pinned = app.state.environ
    if pinned is not None:
        return pinned
    return os.environ


def _error(status_code: int, code: str, message: str, *, gateway_host: str | None = None) -> JSONResponse:
    body = ErrorBody(error=code, message=message, gateway_host=gateway_host)
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


app = create_app()
