"""Request and response models for the ``/v1`` surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ohbe.core.round import (
    DimensionSummary,
    DispatchJob,
    FixedHarnessModelMetrics,
    ModelFitRecommendation,
    ObservationEvent,
    RoundPlan,
    RoundReport,
    TaskSuite,
)
from ohbe.preinstall import HarnessAdapterName


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Check(APIModel):
    id: str
    passed: bool
    summary: str
    evidence: list[str] = Field(default_factory=list)


class SurfaceRouteModel(APIModel):
    method: str
    path: str
    sdk: str
    engine: str


class SurfaceResponse(APIModel):
    surface: str
    engine: str
    note: str
    routes: list[SurfaceRouteModel]


class StatusResponse(APIModel):
    service: str
    surface: str
    engine: str
    ohbe_version: str
    schema_version: str
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"]
    cloud_litellm: Literal["fail_closed"]


class HealthResponse(APIModel):
    """Liveness probe. It does not read the fixture catalog."""

    status: Literal["ok"]
    service: str
    ohbe_version: str
    schema_version: str
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"]
    cloud_litellm: Literal["fail_closed"]


class DoctorResponse(APIModel):
    ok: bool
    surface: str
    ohbe_version: str
    schema_version: str
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"]
    checks: list[Check]


class ExperimentSummary(APIModel):
    experiment_id: str
    name: str
    label: Literal["fixture"]
    spend: Literal["not_live"]
    task_ids: list[str]
    pool_id: str
    pool_hash: str
    audit_passed: bool
    source: str
    note: str


class ExperimentList(APIModel):
    experiments: list[ExperimentSummary]


class PoolModel(APIModel):
    model_id: str
    provider: str
    name: str
    revision: str | None


class PoolSummary(APIModel):
    pool_id: str
    content_hash: str
    models: list[PoolModel]
    hash_matches_report: bool
    source: str
    note: str


class PoolList(APIModel):
    pools: list[PoolSummary]


class HarnessResult(APIModel):
    harness_id: str
    success_rate: str | None
    success_rate_low: str | None = None
    success_rate_high: str | None = None
    uncertainty: str | None = None
    success_count: int
    valid_count: int
    invalid_count: int
    task_failure_count: int
    total_model_cost: str | None
    cost_status: str
    usage_status: str
    total_tokens: int | None
    label: str
    cost_per_success: str | None = None
    complete: bool = False
    invalid_by_category: dict[str, int] = Field(default_factory=dict)


class TaskResult(APIModel):
    task_id: str
    task_content_hash: str
    note: str
    harnesses: list[HarnessResult]


class ReleaseGate(APIModel):
    gate_id: str
    passed: bool
    summary: str


class ResultIndex(APIModel):
    result_id: str
    kind: str
    label: str
    spend: str
    audit_passed: bool
    task_count: int
    source: str
    source_kind: Literal["fixture", "live"]


class ResultList(APIModel):
    results: list[ResultIndex]


class ResultDetail(APIModel):
    result_id: str
    kind: str
    label: str
    spend: str
    audit_passed: bool
    observed_rule: str
    tasks: list[TaskResult]
    release_gates: list[ReleaseGate]
    source: str
    source_kind: Literal["fixture", "live"]


class DryRunRequest(APIModel):
    gateway_url: str = Field(min_length=1)
    experiment_id: str | None = None
    pool_id: str | None = None
    live: bool = Field(default=False, strict=True)


class DryRunResponse(APIModel):
    ok: bool
    mode: Literal["dry_run"]
    label: Literal["Dry-run"] = "Dry-run"
    executed: Literal[False]
    spend: Literal["none"]
    network: Literal["not_contacted"]
    live_requested: bool
    gateway_host: str
    gateway_port: int
    checks: list[Check]


class ErrorBody(APIModel):
    error: str
    message: str
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    executed: Literal[False] = False
    gateway_host: str | None = None


CampaignLifecycle = Literal["draft_plan", "confirmed_suite", "running", "round_report", "fork_plan"]
RoundMode = Literal["paired_harness", "cross_model"]


class HarnessDraft(APIModel):
    harness_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)


class DifficultyDraft(APIModel):
    difficulty: Literal["easy", "medium", "hard"]
    weight: int = Field(strict=True, ge=1)



class PlanLabels(APIModel):
    """Tester-facing classification for list filters and create identity.

    Stored beside the sealed RoundPlan (like slot snapshots). Does not affect
    plan_hash. Fixture rows default to purpose=fixture_demo.
    """

    scenario_tags: list[str] = Field(default_factory=list)
    industry: str = "general"
    purpose: str = "customer_delivery"
    customer: str | None = None
    requirements: str | None = None
    reference_harness_ids: list[str] = Field(default_factory=list)


class CreateRoundPlanRequest(APIModel):
    """Draft a round plan.

    ``model_ids`` on this request are the new plan's slot snapshot. A non-empty
    list of catalog members binds ``pool-admitted``. Omitting the field or
    sending an empty list uses the fixture pool, unless ``pool_id`` is
    ``pool-admitted`` (that still requires slots). This route does not read
    the residual global slot list.
    """

    name: str = Field(min_length=1, max_length=200)
    seed: int = Field(strict=True)
    rounds: int = Field(default=2, strict=True, ge=1, le=50)
    mode: RoundMode = "paired_harness"
    harness: HarnessDraft
    reference_harness: HarnessDraft | None = None
    pool_id: str = Field(min_length=1)
    model_ids: list[str] | None = None
    difficulty_mix: list[DifficultyDraft] = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    difficulties: list[Literal["easy", "medium", "hard"]] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=30, strict=True, ge=1)
    max_total_tokens: int | None = Field(default=None, strict=True, ge=1)
    max_cost: str | None = None
    stop_max_rounds: int | None = Field(default=None, strict=True, ge=1)
    max_invalid_rate: str | None = None
    stop_on_budget: bool = Field(default=True, strict=True)
    labels: PlanLabels | None = None
    live: bool = Field(default=False, strict=True)


class SuiteTaskDraft(APIModel):
    task_id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    difficulty: Literal["easy", "medium", "hard"]
    domain: str | None = None
    prompt: str = Field(min_length=1)
    repetitions: int = Field(strict=True, ge=1)


class SuiteUpsertRequest(APIModel):
    tasks: list[SuiteTaskDraft] | None = None
    live: bool = Field(default=False, strict=True)


class LiveFlagRequest(APIModel):
    live: bool = Field(default=False, strict=True)


class ConfirmSuiteRequest(APIModel):
    """Freeze a suite and this plan's stored slot snapshot.

    ``model_ids``, when sent, must match the snapshot already stored on the
    plan. Omitting the field freezes that snapshot. Re-confirm does not
    rewrite the snapshot or ``suite_hash``. This route does not read the
    residual global slot list and does not spend.
    """

    live: bool = Field(default=False, strict=True)
    model_ids: list[str] | None = None


class ForkPlanRequest(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    live: bool = Field(default=False, strict=True)


class RoundPlanSummary(APIModel):
    plan_id: str
    name: str
    mode: RoundMode
    lifecycle: CampaignLifecycle
    harness_id: str
    seed: int
    rounds: int
    origin: Literal["fixture", "session"]
    industry: str = "general"
    purpose: str = "customer_delivery"
    scenario_tags: list[str] = Field(default_factory=list)
    customer: str | None = None
    spend: Literal["not_live"] = "not_live"
    live_spend: Literal["locked"] = "locked"


class RoundPlanList(APIModel):
    plans: list[RoundPlanSummary]
    note: str


class RoundPlanDetail(APIModel):
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_requested: bool = False
    lifecycle: CampaignLifecycle
    origin: Literal["fixture", "session"]
    plan: RoundPlan
    suite_id: str | None = None
    job_id: str | None = None
    report_id: str | None = None
    comparison_model_ids: list[str] = Field(default_factory=list)
    slots_status: Literal["draft", "frozen"]
    pool_kind: Literal["admitted", "fixture"] = "fixture"
    pool_model_ids: list[str] = Field(default_factory=list)
    labels: PlanLabels = Field(default_factory=PlanLabels)



class SuiteView(APIModel):
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_requested: bool = False
    lifecycle: CampaignLifecycle
    suite: TaskSuite
    comparison_model_ids: list[str] = Field(default_factory=list)
    slots_status: Literal["draft", "frozen"]


class SuiteViewList(APIModel):
    suites: list[TaskSuite]
    spend: Literal["not_live"] = "not_live"


class RoundReportDetail(APIModel):
    lifecycle: CampaignLifecycle
    report: RoundReport
    spend: Literal["not_live"] = "not_live"


class RoundReportList(APIModel):
    reports: list[RoundReport]
    spend: Literal["not_live"] = "not_live"


class JobView(APIModel):
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    label: Literal["Dry-run"] = "Dry-run"
    fixture_pool_id: Literal["pool-pilot"] = "pool-pilot"
    job: DispatchJob
    events: list[ObservationEvent]


class RoundStartResponse(APIModel):
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    label: Literal["Dry-run"] = "Dry-run"
    fixture_pool_id: Literal["pool-pilot"] = "pool-pilot"
    live_requested: bool
    checks: list[Check]
    job: DispatchJob
    events: list[ObservationEvent]
    report_id: str | None = None
    lifecycle: CampaignLifecycle


class PoolModelDraft(APIModel):
    model_id: str = Field(min_length=1, max_length=200)
    provider: Literal["vllm", "ollama"]
    name: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    litellm_model: str = Field(min_length=1, max_length=200)


class PoolDraftRequest(APIModel):
    """Structured pool editor payload.

    Gateway env names, ``fail_closed``, and the forbid-list are applied by the
    server. A client cannot send a URL, a key, or a cloud provider.
    """

    pool_id: str = Field(min_length=1, max_length=64)
    models: list[PoolModelDraft] = Field(min_length=1, max_length=32)


class PoolDraftValidation(APIModel):
    ok: bool
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    checks: list[Check]
    pool_id: str | None = None
    content_hash: str | None = None
    destination: str | None = None


class PoolDraftSaved(APIModel):
    ok: Literal[True] = True
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    checks: list[Check]
    pool_id: str
    content_hash: str
    source: str


class PoolFileModel(APIModel):
    model_id: str
    provider: str
    name: str
    revision: str
    litellm_model: str


class PoolFileSummary(APIModel):
    filename: str
    pool_id: str
    source: str
    read_only: bool
    ok: bool
    note: str
    content_hash: str | None = None
    models: list[PoolFileModel] = Field(default_factory=list)


class PoolFileDetail(PoolFileSummary):
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"


class PoolFileList(APIModel):
    pools: list[PoolFileSummary]
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"


class CatalogEntryRequest(APIModel):
    """Register one model in the OHBE catalog.

    Modality, strengths, and placement are catalog fields. They are not pool
    metadata. Provider stays vllm or ollama through LiteLLM.
    """

    model_id: str = Field(min_length=1, max_length=120)
    provider: Literal["vllm", "ollama"]
    name: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    litellm_model: str = Field(min_length=1, max_length=200)
    modality: Literal["text", "code", "vision", "multimodal"]
    strengths: list[
        Literal[
            "reasoning",
            "code",
            "tool_use",
            "long_context",
            "instruction",
            "multilingual",
            "low_latency",
            "low_cost",
        ]
    ] = Field(min_length=1, max_length=8)
    placement: Literal["local", "gateway"]


class CatalogValidation(APIModel):
    ok: bool
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    checks: list[Check]
    model_id: str | None = None
    filename: str | None = None
    destination: str | None = None


class CatalogEntryView(APIModel):
    filename: str
    model_id: str
    provider: str
    name: str
    revision: str
    litellm_model: str
    modality: str
    strengths: list[str] = Field(default_factory=list)
    placement: str
    source: str
    ok: bool
    note: str
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"] = "locked"
    spend_execution: Literal["disabled"] = "disabled"


class CatalogSaved(APIModel):
    ok: Literal[True] = True
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    checks: list[Check]
    model_id: str
    filename: str
    source: str
    entry: CatalogEntryView


class CatalogList(APIModel):
    entries: list[CatalogEntryView]
    note: str
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"


class HarnessProfileRequest(APIModel):
    """Register a catalog preinstall. An unknown harness name is rejected.

    The catalog is selectable preinstall plus version detection. Live runs
    still follow ``HARNESS_ORDER``. Saving a profile does not spend and does
    not add a plan-only name to that tuple.
    """

    profile_id: str = Field(min_length=1, max_length=64)
    adapter: HarnessAdapterName
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=80)
    timeout_seconds: int = Field(strict=True, ge=1, le=86400)
    draft_adapter_name: str | None = Field(default=None, max_length=64)


class HarnessCatalogEntry(APIModel):
    """One selectable preinstall and its local version detection.

    ``in_live_order`` reports whether this name is already in ``HARNESS_ORDER``.
    It is not a request to expand that tuple.
    """

    adapter: str
    harness_id: str
    name: str
    profile_id: str
    version: str
    detected_version: str | None = None
    version_state: Literal["pin", "detected", "unavailable", "differs"]
    version_kind: Literal["git_sha", "exact", "package"]
    registration: Literal["adapter", "plan"]
    in_live_order: bool
    package: str | None = None
    install_channel: Literal["pip", "npm", "desktop", "unpublished"] = "pip"
    install_url: str | None = None
    install_urls: list[str] = Field(default_factory=list)
    runnable: bool = False
    closed_reason: str = ""


class HarnessRefView(APIModel):
    harness_id: str
    name: str
    version: str
    config_hash: str


class HarnessProfileValidation(APIModel):
    ok: bool
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    live_run: Literal["rejected"] = "rejected"
    checks: list[Check]
    profile_id: str | None = None
    adapter: str | None = None
    status: Literal["registered", "draft", "plan"] | None = None
    registration: Literal["adapter", "plan"] | None = None
    dev_only: bool | None = None
    harness_id: str | None = None
    name: str | None = None
    version: str | None = None
    parameters: dict[str, Any] | None = None
    config_hash: str | None = None
    ref: HarnessRefView | None = None
    destination: str | None = None


class HarnessProfileSaved(APIModel):
    ok: Literal[True] = True
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    live_run: Literal["rejected"] = "rejected"
    checks: list[Check]
    profile_id: str
    adapter: str
    status: Literal["registered", "draft", "plan"]
    registration: Literal["adapter", "plan"]
    dev_only: bool
    harness_id: str
    name: str
    version: str
    parameters: dict[str, Any]
    config_hash: str
    ref: HarnessRefView
    source: str


class HarnessProfileIndex(APIModel):
    profile_id: str
    adapter: str
    status: Literal["registered", "draft", "plan", "invalid"]
    registration: Literal["adapter", "plan"] | None = None
    dev_only: bool
    live_run: Literal["rejected"] = "rejected"
    harness_id: str
    name: str
    version: str
    config_hash: str | None = None
    source: str
    ok: bool
    note: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    ref: HarnessRefView | None = None


class HarnessProfileList(APIModel):
    harness_order: list[str]
    catalog: list[HarnessCatalogEntry]
    profiles: list[HarnessProfileIndex]
    note: str
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted"] = "not_contacted"
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"


class RemoteAuthInput(APIModel):
    """SSH material for a harness remote target.

    ``secret`` is the key or password. ``secret_env`` is the name of an
    environment variable that holds it. When ``secret_env`` is set, that
    value is used and the inline secret is ignored. Neither value is written
    to pool YAML, catalog YAML, a harness profile, or git.
    """

    username: str = ""
    auth_method: Literal["ssh_key", "password", ""] = ""
    secret: str = Field(default="", max_length=65536, repr=False)
    secret_env: str = ""


class LibraryTarget(RemoteAuthInput):
    target: Literal["local", "remote"]
    host: str = ""
    port: int | None = None


class ProbeRequest(RemoteAuthInput):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(strict=True, ge=1, le=65535)


class HarnessCheckRequest(LibraryTarget):
    adapter: HarnessAdapterName


class HarnessInstallRequest(HarnessCheckRequest):
    install_deps: bool = False
    timeout_seconds: int = Field(default=600, strict=True, ge=1, le=86400)


class HarnessScanRequest(LibraryTarget):
    pass


class ModelAdmitRequest(CatalogEntryRequest):
    """Admit one model through 添加云端.

    The server stores ``placement: gateway``. ``api_key`` is used for the
    connectivity probe and is not written into pool or catalog YAML.

    ``revision`` may be empty. A concrete value is kept. An empty value is
    taken from the gateway ``GET /v1/models`` listing after the probe. A
    floating tag such as ``latest`` is still rejected before any socket.
    """

    revision: str = Field(default="", max_length=200)
    target: Literal["local", "remote"]
    host: str = ""
    port: int | None = None
    api_key: str = Field(min_length=1, max_length=512)
    gateway_url: str = Field(min_length=1, max_length=300)


class ModelScanRequest(LibraryTarget):
    """添加本地. Identity fields are required when the form sends them.

    Omitted identity keeps discovery-only callers working. A partial or invalid
    identity is rejected before any model is written.

    ``vllm_base_url`` and ``api_key`` are optional. They select a vLLM
    ``GET /v1/models`` during local discovery. The token is not returned and
    is not written into pool or catalog YAML. Empty values fall back to
    ``OHBE_VLLM_BASE_URL`` and ``OHBE_VLLM_API_KEY``.
    """

    provider: Literal["vllm", "ollama"] | None = None
    name: str = ""
    revision: str = ""
    model_id: str = ""
    litellm_model: str = ""
    vllm_base_url: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=512, repr=False)


class LibraryActionResult(APIModel):
    """One library action. ``harness_order`` is echoed and not expanded."""

    ok: bool
    executed: Literal[False] = False
    spend: Literal["none"] = "none"
    network: Literal["not_contacted", "probed"]
    live_spend: Literal["locked", "env_set"]
    spend_execution: Literal["disabled"] = "disabled"
    live_run: Literal["rejected"] = "rejected"
    target: Literal["local", "remote"]
    reachable: bool | None = None
    reason: Literal[
        "ready",
        "unreachable",
        "rejected_host",
        "deps_missing",
        "version_rejected",
        "fields_rejected",
        "policy_mismatch",
        "connectivity_failed",
        "admitted",
        "empty",
        "auth_required",
        "auth_failed",
        "auth_unavailable",
        "inventory_unsupported",
        "install_unsupported",
        "installer_unpinned",
        "npm_missing",
        "os_unsupported",
        "desktop_registered",
        "install_failed",
    ]
    offer_install: bool = False
    missing: list[str] = Field(default_factory=list)
    admitted: list[str] = Field(default_factory=list)
    harness_order: list[str] = Field(default_factory=list)
    registration: Literal["adapter", "plan"] | None = None
    in_live_order: bool | None = None
    version: str | None = None
    source: str | None = None


class ModelFitResponse(APIModel):
    mode: Literal["cross_model"] = "cross_model"
    harness_id: str
    suite_id: str
    suite_hash: str
    findings: str
    frontier_note: str
    recommendations: list[ModelFitRecommendation]
    model_metrics: list[FixedHarnessModelMetrics]
    dimensions: list[DimensionSummary]
    spend: Literal["not_live"] = "not_live"
    label: Literal["fixture"] = "fixture"
    routing_optimality: None = None
    model_independence: None = None
    note: str
    source: str


IntakePath = Literal["cloud_api_key", "server_scan"]
IntakeAdmission = Literal["admitted", "pending"]
IntakeRoute = Literal["litellm_gateway", "rejected"]
IntakeBackend = Literal["remote", "vllm", "ollama", "other"]
IntakeConnectivity = Literal["ok", "failed", "not_run"]
IntakeHandshake = Literal["confirmed", "not_applicable"]
IntakeNetwork = Literal["not_contacted", "probed", "unreachable"]
IntakeScope = Literal["connectivity", "handshake", "discovery"]
CloudProvider = Literal[
    "openai",
    "anthropic",
    "google",
    "mistral",
    "groq",
    "cohere",
    "together",
    "deepseek",
    "fireworks",
    "perplexity",
    "other",
]


class CatalogModel(APIModel):
    """One intake attempt. ``accepted`` is true only when both gates pass.

    The gates are connectivity-or-handshake, and a LiteLLM gateway policy
    match. A pending row is saved and is not a catalog member.
    """

    record_id: str
    model_id: str
    display_name: str
    provider: str
    intake_path: IntakePath
    admission: IntakeAdmission
    accepted: bool
    catalog_member: bool
    connectivity_or_handshake: bool
    connectivity: IntakeConnectivity
    handshake: IntakeHandshake
    policy_match: bool
    policy_checks: list[Check]
    route: IntakeRoute
    gateway_host: str | None
    gateway_port: int | None
    backend: IntakeBackend
    reason: str | None
    credential: Literal["api_key_accepted", "not_applicable"]
    credential_fingerprint: str | None = None
    spend: Literal["none"] = "none"
    benchmark_unlocked: Literal[False] = False
    executed: Literal[False] = False
    network: IntakeNetwork
    scope: IntakeScope

    @model_validator(mode="after")
    def _both_gates(self) -> CatalogModel:
        both = self.connectivity_or_handshake and self.policy_match
        if self.accepted is not both:
            raise ValueError("catalog admission requires connectivity or handshake and a gateway policy match")
        if self.accepted:
            if self.admission != "admitted" or not self.catalog_member:
                raise ValueError("an accepted model must be an admitted catalog member")
            if self.connectivity != "ok":
                raise ValueError("an accepted model needs a successful connectivity probe")
            if self.intake_path == "server_scan" and self.handshake != "confirmed":
                raise ValueError("server-scan admission requires a confirmed handshake")
        elif self.admission != "pending" or self.catalog_member:
            raise ValueError("a refused model stays pending and out of the catalog")
        return self


class CatalogModelList(APIModel):
    models: list[CatalogModel]
    spend: Literal["none"] = "none"
    note: str


class CloudIntakeRequest(APIModel):
    display_name: str = Field(min_length=1, max_length=80)
    model_id: str = Field(min_length=1, max_length=160)
    litellm_model: str = Field(min_length=1, max_length=200)
    provider: CloudProvider
    api_key: str = Field(min_length=1, max_length=512)
    gateway_url: str = Field(min_length=1, max_length=300)
    probe: Literal["health"]
    live: bool = Field(default=False, strict=True)


class DiscoveredModel(APIModel):
    model_id: str
    display_name: str
    provider: str
    litellm_model: str
    backend: Literal["vllm", "ollama", "other"]
    admissible: bool
    policy_match: bool


class ServerScanRequest(APIModel):
    gateway_url: str = Field(min_length=1, max_length=300)
    probe: Literal["list"]
    live: bool = Field(default=False, strict=True)


class ServerScanResponse(APIModel):
    """A listing. Nothing in it is a catalog member until handshake."""

    scan_id: str
    admission: Literal["pending", "listed"]
    catalog_member: Literal[False] = False
    accepted: Literal[False] = False
    policy_match: bool
    policy_checks: list[Check]
    connectivity: IntakeConnectivity
    connectivity_or_handshake: Literal[False] = False
    route: IntakeRoute
    gateway_host: str | None
    gateway_port: int | None
    models: list[DiscoveredModel]
    reason: str | None
    spend: Literal["none"] = "none"
    benchmark_unlocked: Literal[False] = False
    executed: Literal[False] = False
    network: IntakeNetwork
    scope: Literal["discovery"] = "discovery"
    note: str


class HandshakeRequest(APIModel):
    scan_id: str = Field(min_length=1)
    model_ids: list[str] = Field(min_length=1)
    live: bool = Field(default=False, strict=True)


class HandshakeResponse(APIModel):
    scan_id: str
    results: list[CatalogModel]
    spend: Literal["none"] = "none"
    benchmark_unlocked: Literal[False] = False
    executed: Literal[False] = False
    network: Literal["not_contacted"] = "not_contacted"
    note: str


class HarnessInventoryItem(APIModel):
    """One registered harness on this host.

    ``presence`` is ``preinstalled`` only when auto-detect finds an allowed
    version. ``plans_whitelisted`` is that case, or an operator selection of an
    allowed version. ``live_whitelisted`` is the plans gate plus membership in
    ``HARNESS_ORDER``.
    """

    harness_id: str
    name: str
    live_name: str | None
    allowed_versions: list[str]
    detected_version: str | None
    presence: Literal["absent", "preinstalled", "version_rejected"]
    selected_version: str | None
    whitelist_version: str | None
    plans_whitelisted: bool
    live_whitelisted: bool


class HarnessInventoryResponse(APIModel):
    harnesses: list[HarnessInventoryItem]
    plans_whitelist: list[str]
    live_whitelist: list[str]
    live_order: list[str]
    spend: Literal["none"] = "none"
    note: str


class HarnessSelection(APIModel):
    harness_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class HarnessSelectRequest(APIModel):
    selections: list[HarnessSelection] = Field(default_factory=list)


class ComparisonSlotsRequest(APIModel):
    model_ids: list[str] = Field(default_factory=list)


class ComparisonSlotsResponse(APIModel):
    model_ids: list[str]
    source: Literal["round_comparison_slots"] = "round_comparison_slots"
    spend: Literal["none"] = "none"
    note: str


class PlanSlotsResponse(APIModel):
    """One plan's ordered slot snapshot. ``frozen`` follows a confirmed suite."""

    plan_id: str
    model_ids: list[str]
    status: Literal["draft", "frozen"]
    source: Literal["plan_slots"] = "plan_slots"
    spend: Literal["none"] = "none"
