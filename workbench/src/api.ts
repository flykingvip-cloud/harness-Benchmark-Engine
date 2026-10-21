const base = import.meta.env.VITE_OHBE_API_BASE ?? "";

export const routes = {
  surface: "/v1",
  health: "/health",
  healthV1: "/v1/health",
  status: "/v1/status",
  doctor: "/v1/doctor",
  experiments: "/v1/experiments",
  pools: "/v1/pools",
  results: "/v1/results",
  dryRun: "/v1/dry-run",
  poolDrafts: "/v1/pool-drafts",
  poolDraftValidate: "/v1/pool-drafts/validate",
  modelCatalog: "/v1/model-catalog",
  modelCatalogValidate: "/v1/model-catalog/validate",
  harnessConfigs: "/v1/harness-configs",
  harnessConfigValidate: "/v1/harness-configs/validate",
  catalogModels: "/v1/catalog/models",
  catalogHarnesses: "/v1/catalog/harnesses",
  comparisonSlots: "/v1/rounds/comparison-slots",
  libraryProbe: "/v1/library/probe",
  harnessLibraryCheck: "/v1/harness-library/check",
  harnessLibraryInstall: "/v1/harness-library/install",
  harnessLibraryScan: "/v1/harness-library/scan",
  modelLibraryAdmit: "/v1/model-library/admit",
  modelLibraryScan: "/v1/model-library/scan",
  roundPlans: "/v1/rounds/plans",
  roundSuites: "/v1/rounds/suites",
  roundReports: "/v1/rounds/reports",
  roundJobs: "/v1/rounds/jobs",
  modelFit: "/v1/analysis/model-fit",
} as const;

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function getJson<T>(path: string): Promise<T> {
  return request<T>(path);
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${base}${path}`, init);
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const record = asRecord(payload);
    throw new ApiError(
      response.status,
      typeof record?.error === "string" ? record.error : "request_failed",
      typeof record?.message === "string" ? record.message : `HTTP ${response.status}`,
    );
  }
  return payload as T;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value !== null && typeof value === "object") {
    return value as Record<string, unknown>;
  }
  return null;
}

export interface SurfaceRoute {
  method: string;
  path: string;
  sdk: string;
  engine: string;
}

export interface SurfaceResponse {
  surface: string;
  engine: string;
  note: string;
  routes: SurfaceRoute[];
}

export interface StatusResponse {
  service: string;
  surface: string;
  engine: string;
  ohbe_version: string;
  schema_version: string;
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  cloud_litellm: "fail_closed";
}

export interface Check {
  id: string;
  passed: boolean;
  summary: string;
  evidence: string[];
}

export interface DoctorResponse {
  ok: boolean;
  surface: string;
  ohbe_version: string;
  schema_version: string;
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  checks: Check[];
}

export interface ExperimentSummary {
  experiment_id: string;
  name: string;
  label: "fixture";
  spend: "not_live";
  task_ids: string[];
  pool_id: string;
  pool_hash: string;
  audit_passed: boolean;
  source: string;
  note: string;
}

export interface PoolModel {
  model_id: string;
  provider: string;
  name: string;
  revision: string | null;
}

export interface PoolSummary {
  pool_id: string;
  content_hash: string;
  models: PoolModel[];
  hash_matches_report: boolean;
  source: string;
  note: string;
}

export interface ResultIndex {
  result_id: string;
  kind: string;
  label: string;
  spend: string;
  audit_passed: boolean;
  task_count: number;
  source: string;
  source_kind: "fixture" | "live";
}

export interface HarnessResult {
  harness_id: string;
  success_rate: string | null;
  success_rate_low?: string | null;
  success_rate_high?: string | null;
  uncertainty?: string | null;
  success_count: number;
  valid_count: number;
  invalid_count: number;
  task_failure_count: number;
  total_model_cost: string | null;
  cost_status: string;
  usage_status: string;
  total_tokens: number | null;
  label: string;
  cost_per_success?: string | null;
  complete?: boolean;
  invalid_by_category?: Record<string, number>;
}

export interface TaskResult {
  task_id: string;
  task_content_hash: string;
  note: string;
  harnesses: HarnessResult[];
}

export interface ReleaseGate {
  gate_id: string;
  passed: boolean;
  summary: string;
}

export interface ResultDetail {
  result_id: string;
  kind: string;
  label: string;
  spend: string;
  audit_passed: boolean;
  observed_rule: string;
  tasks: TaskResult[];
  release_gates: ReleaseGate[];
  source: string;
  source_kind: "fixture" | "live";
}

export type RoundMode = "paired_harness" | "cross_model";

export interface PlanLabels {
  scenario_tags: string[];
  industry: string;
  purpose: string;
  customer: string | null;
  requirements: string | null;
  reference_harness_ids: string[];
}

export type CampaignLifecycle = "draft_plan" | "confirmed_suite" | "running" | "round_report" | "fork_plan";

export interface RoundPlanSummary {
  plan_id: string;
  name: string;
  mode: RoundMode;
  lifecycle: CampaignLifecycle;
  harness_id: string;
  seed: number;
  rounds: number;
  origin: "fixture" | "session";
  industry?: string;
  purpose?: string;
  scenario_tags?: string[];
  customer?: string | null;
  spend: "not_live";
  live_spend: "locked";
}

export interface RoundPlanList {
  plans: RoundPlanSummary[];
  note: string;
}

export interface HarnessRef {
  harness_id: string;
  name: string;
  version: string;
  config_hash: string;
}

export interface RoundPlan {
  plan_id: string;
  name: string;
  seed: number;
  rounds: number;
  mode: RoundMode;
  harness_ref: HarnessRef;
  reference_harness_ref: HarnessRef | null;
  gateway: "litellm";
  egress_policy_version: string;
  live_spend: "locked";
  forked_from: string | null;
  plan_hash: string;
  model_pool_ref: { pool_id: string; content_hash: string };
  difficulty_mix: { difficulty: "easy" | "medium" | "hard"; weight: number }[];
  taxonomy: { capabilities: string[]; difficulties: string[]; domains: string[] };
  budget: { timeout_seconds: number; max_total_tokens: number | null; max_cost: string | null };
  stop: { max_rounds: number; max_invalid_rate: string | null; stop_on_budget: boolean };
}

export interface RoundPlanDetail {
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_requested: boolean;
  lifecycle: CampaignLifecycle;
  origin: "fixture" | "session";
  plan: RoundPlan;
  suite_id: string | null;
  job_id: string | null;
  report_id: string | null;
  comparison_model_ids: string[];
  slots_status: "draft" | "frozen";
  pool_kind: "admitted" | "fixture";
  pool_model_ids: string[];
  labels?: PlanLabels;
}

export interface SuiteTask {
  task_id: string;
  capability: string;
  difficulty: "easy" | "medium" | "hard";
  domain: string | null;
  prompt: string;
  repetitions: number;
  content_hash: string;
}

export interface TaskSuite {
  suite_id: string;
  plan_id: string;
  plan_hash: string;
  status: "draft" | "confirmed";
  tasks: SuiteTask[];
  confirmed_at: string | null;
  suite_hash: string;
}

export interface SuiteView {
  executed: false;
  spend: "none";
  network: "not_contacted";
  lifecycle: CampaignLifecycle;
  suite: TaskSuite;
  comparison_model_ids: string[];
  slots_status: "draft" | "frozen";
}

export interface ObservationEvent {
  job_id: string;
  seq: number;
  pane_id: string;
  model_id: string | null;
  harness_id: string;
  task_id: string;
  kind: string;
  message: string;
  recorded_at: string;
}

export interface DispatchJob {
  job_id: string;
  plan_id: string;
  suite_id: string;
  suite_hash: string;
  mode: RoundMode;
  harness_id: string;
  model_ids: string[];
  focus_task_id: string;
  reference_harness_id: string | null;
  status: "queued" | "dispatching" | "observing" | "sealed";
  spend: "none";
  executed: false;
  network: "not_contacted";
  live_spend: "locked";
  note: string;
}

export interface JobView {
  executed: false;
  spend: "none";
  network: "not_contacted";
  label: "Dry-run";
  fixture_pool_id: "pool-pilot";
  job: DispatchJob;
  events: ObservationEvent[];
}

export interface DimensionSummary {
  dimension: "validity" | "effectiveness" | "process" | "cost" | "diagnosability";
  reading: string;
  coverage: string | null;
}

export interface FixedHarnessModelMetrics {
  model_id: string;
  harness_id: string;
  task_class: string;
  success_rate: string | null;
  success_count: number;
  valid_count: number;
  total_model_cost: string | null;
  cost_per_success: string | null;
  steps: number | null;
  usage_status: string;
}

export interface ModelFitRecommendation {
  model_id: string;
  task_class: string;
  score_quality: string;
  score_cost: string;
  pareto_flag: boolean;
  rationale: string;
}

export interface RoundReport {
  report_id: string;
  plan_id: string;
  suite_id: string;
  suite_hash: string;
  mode: RoundMode;
  harness_id: string;
  findings: string;
  frontier_note: string;
  dimensions: DimensionSummary[];
  model_metrics: FixedHarnessModelMetrics[];
  recommendations: ModelFitRecommendation[];
  measurement: "fixture_demo" | "not_measured";
  spend: "not_live";
  label: "fixture";
  routing_optimality: null;
  model_independence: null;
}

export interface RoundReportDetail {
  lifecycle: CampaignLifecycle;
  report: RoundReport;
  spend: "not_live";
}

export interface RoundStartResponse {
  executed: false;
  spend: "none";
  network: "not_contacted";
  label: "Dry-run";
  fixture_pool_id: "pool-pilot";
  live_requested: boolean;
  checks: Check[];
  job: DispatchJob;
  events: ObservationEvent[];
  report_id: string | null;
  lifecycle: CampaignLifecycle;
}

export interface ModelFitResponse {
  mode: "cross_model";
  harness_id: string;
  suite_id: string;
  suite_hash: string;
  findings: string;
  frontier_note: string;
  recommendations: ModelFitRecommendation[];
  model_metrics: FixedHarnessModelMetrics[];
  dimensions: DimensionSummary[];
  spend: "not_live";
  label: "fixture";
  routing_optimality: null;
  model_independence: null;
  note: string;
  source: string;
}

export interface PoolDraftValidation {
  ok: boolean;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  checks: Check[];
  pool_id: string | null;
  content_hash: string | null;
  destination: string | null;
}

export interface PoolDraftSaved {
  ok: true;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  checks: Check[];
  pool_id: string;
  content_hash: string;
  source: string;
}

export interface PoolFileModel {
  model_id: string;
  provider: string;
  name: string;
  revision: string;
  litellm_model: string;
}

export interface PoolFileSummary {
  filename: string;
  pool_id: string;
  source: string;
  read_only: boolean;
  ok: boolean;
  note: string;
  content_hash: string | null;
  models: PoolFileModel[];
  executed?: false;
  spend?: "none";
  network?: "not_contacted";
  live_spend?: "locked" | "env_set";
  spend_execution?: "disabled";
}

export interface PoolFileList {
  pools: PoolFileSummary[];
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
}

export interface CatalogEntry {
  filename: string;
  model_id: string;
  provider: string;
  name: string;
  revision: string;
  litellm_model: string;
  modality: string;
  strengths: string[];
  placement: string;
  source: string;
  ok: boolean;
  note: string;
  executed?: false;
  spend?: "none";
  network?: "not_contacted";
  live_spend?: "locked" | "env_set";
  spend_execution?: "disabled";
}

export interface CatalogValidation {
  ok: boolean;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  checks: Check[];
  model_id: string | null;
  filename: string | null;
  destination: string | null;
}

export interface CatalogSaved {
  ok: true;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  checks: Check[];
  model_id: string;
  filename: string;
  source: string;
  entry: CatalogEntry;
}

export interface CatalogList {
  entries: CatalogEntry[];
  note: string;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
}

export interface HarnessRefView {
  harness_id: string;
  name: string;
  version: string;
  config_hash: string;
}

export interface HarnessProfileValidation {
  ok: boolean;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  live_run: "rejected";
  checks: Check[];
  profile_id: string | null;
  adapter: string | null;
  status: "registered" | "draft" | "plan" | null;
  registration: "adapter" | "plan" | null;
  dev_only: boolean | null;
  harness_id: string | null;
  name: string | null;
  version: string | null;
  parameters: Record<string, unknown> | null;
  config_hash: string | null;
  ref: HarnessRefView | null;
  destination: string | null;
}

export interface HarnessProfileSaved {
  ok: true;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  live_run: "rejected";
  checks: Check[];
  profile_id: string;
  adapter: string;
  status: "registered" | "draft" | "plan";
  registration: "adapter" | "plan";
  dev_only: boolean;
  harness_id: string;
  name: string;
  version: string;
  parameters: Record<string, unknown>;
  config_hash: string;
  ref: HarnessRefView;
  source: string;
}

export interface HarnessProfileIndex {
  profile_id: string;
  adapter: string;
  status: "registered" | "draft" | "plan" | "invalid";
  registration: "adapter" | "plan" | null;
  dev_only: boolean;
  live_run: "rejected";
  harness_id: string;
  name: string;
  version: string;
  config_hash: string | null;
  source: string;
  ok: boolean;
  note: string;
  parameters: Record<string, unknown>;
  ref: HarnessRefView | null;
}

export interface HarnessCatalogEntry {
  adapter: string;
  harness_id: string;
  name: string;
  profile_id: string;
  version: string;
  detected_version: string | null;
  version_state: "pin" | "detected" | "unavailable" | "differs";
  version_kind: "git_sha" | "exact" | "package";
  registration: "adapter" | "plan";
  in_live_order: boolean;
  package: string | null;
  install_channel: "pip" | "npm" | "desktop" | "unpublished";
  install_url: string | null;
  install_urls: string[];
  runnable: boolean;
  closed_reason: string;
}

export interface LibraryActionResult {
  ok: boolean;
  executed: false;
  spend: "none";
  network: "not_contacted" | "probed";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
  live_run: "rejected";
  target: "local" | "remote";
  reachable: boolean | null;
  reason:
    | "ready"
    | "unreachable"
    | "rejected_host"
    | "deps_missing"
    | "version_rejected"
    | "fields_rejected"
    | "policy_mismatch"
    | "connectivity_failed"
    | "admitted"
    | "empty"
    | "auth_required"
    | "auth_failed"
    | "auth_unavailable"
    | "inventory_unsupported"
    | "install_unsupported"
    | "installer_unpinned"
    | "npm_missing"
    | "os_unsupported"
    | "desktop_registered"
    | "install_failed";
  offer_install: boolean;
  missing: string[];
  admitted: string[];
  harness_order: string[];
  registration: "adapter" | "plan" | null;
  in_live_order: boolean | null;
  version: string | null;
  source: string | null;
}

export interface HarnessProfileList {
  harness_order: string[];
  catalog: HarnessCatalogEntry[];
  profiles: HarnessProfileIndex[];
  note: string;
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_spend: "locked" | "env_set";
  spend_execution: "disabled";
}

export interface CatalogModel {
  record_id: string;
  model_id: string;
  display_name: string;
  provider: string;
  intake_path: "cloud_api_key" | "server_scan";
  admission: "admitted" | "pending";
  accepted: boolean;
  catalog_member: boolean;
  connectivity_or_handshake: boolean;
  connectivity: "ok" | "failed" | "not_run";
  handshake: "confirmed" | "not_applicable";
  policy_match: boolean;
  policy_checks: Check[];
  route: "litellm_gateway" | "rejected";
  gateway_host: string | null;
  gateway_port: number | null;
  backend: "remote" | "vllm" | "ollama" | "other";
  reason: string | null;
  credential: "api_key_accepted" | "not_applicable";
  credential_fingerprint: string | null;
  spend: "none";
  benchmark_unlocked: false;
  executed: false;
  network: "not_contacted" | "probed" | "unreachable";
  scope: "connectivity" | "handshake" | "discovery";
}

export interface CatalogModelList {
  models: CatalogModel[];
  spend: "none";
  note: string;
}

export interface DiscoveredModel {
  model_id: string;
  display_name: string;
  provider: string;
  litellm_model: string;
  backend: "vllm" | "ollama" | "other";
  admissible: boolean;
  policy_match: boolean;
}

export interface ServerScanResponse {
  scan_id: string;
  admission: "pending" | "listed";
  catalog_member: false;
  accepted: false;
  policy_match: boolean;
  policy_checks: Check[];
  connectivity: "ok" | "failed" | "not_run";
  connectivity_or_handshake: false;
  route: "litellm_gateway" | "rejected";
  gateway_host: string | null;
  gateway_port: number | null;
  models: DiscoveredModel[];
  reason: string | null;
  spend: "none";
  benchmark_unlocked: false;
  executed: false;
  network: "not_contacted" | "probed" | "unreachable";
  scope: "discovery";
  note: string;
}

export interface HandshakeResponse {
  scan_id: string;
  results: CatalogModel[];
  spend: "none";
  benchmark_unlocked: false;
  executed: false;
  network: "not_contacted";
  note: string;
}

export interface HarnessInventoryItem {
  harness_id: string;
  name: string;
  live_name: string | null;
  allowed_versions: string[];
  detected_version: string | null;
  presence: "absent" | "preinstalled" | "version_rejected";
  selected_version: string | null;
  whitelist_version: string | null;
  plans_whitelisted: boolean;
  live_whitelisted: boolean;
}

export interface HarnessInventoryResponse {
  harnesses: HarnessInventoryItem[];
  plans_whitelist: string[];
  live_whitelist: string[];
  live_order: string[];
  spend: "none";
  note: string;
}

export interface ComparisonSlotsResponse {
  model_ids: string[];
  source: "round_comparison_slots";
  spend: "none";
  note: string;
}

export interface PlanSlotsResponse {
  plan_id: string;
  model_ids: string[];
  status: "draft" | "frozen";
  source: "plan_slots";
  spend: "none";
}

export interface DryRunResponse {
  ok: boolean;
  mode: "dry_run";
  label: "Dry-run";
  executed: false;
  spend: "none";
  network: "not_contacted";
  live_requested: boolean;
  gateway_host: string;
  gateway_port: number;
  checks: Check[];
}
