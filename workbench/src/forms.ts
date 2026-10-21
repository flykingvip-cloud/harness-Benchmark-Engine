export const HARNESS_ORDER = ["openbot", "inspect_react"] as const;
export const OPENBOT_VERSION_PLACEHOLDER = "openbot-commit-unavailable";
export const INSPECT_PROFILE_VERSION = "0.3.277";
export const DSH_PROFILE_VERSION = "0.2.1-alpha.2";
export const DSH_INSTALL_URL = "https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-alpha.2.tgz";
export const OPENCLAW_NPM_VERSION = "2026.10.1";
export const OPENCLAW_INSTALL_URL = "https://registry.npmjs.org/openclaw/-/openclaw-2026.10.1.tgz";
export const WORKBUDDY_VERSION = "5.7.7";
export const WORKBUDDY_INSTALL_URLS = [
  "https://acc-1258344699.cos.ap-guangzhou.myqcloud.com/workbuddy/saas/darwin-arm64/WorkBuddy-darwin-arm64-5.7.7.40774747-2e8619da.dmg",
  "https://acc-1258344699.cos.ap-guangzhou.myqcloud.com/workbuddy/saas/darwin-x64/WorkBuddy-darwin-x64-5.7.7.40774747-2e8619da.dmg",
  "https://acc-1258344699.cos.ap-guangzhou.myqcloud.com/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.7.7.40774747-2e8619da.exe",
] as const;
export const QWEN_WORK_VERSION = "1.2.5";
export const DOUBAO_WORK_VERSION = "2.31.10";
export const DOUBAO_WORK_INSTALL_URLS = [
  "https://lf-flow-web-cdn.doubao.com/obj/flow-doubao/doubao_pc/2.31.10/DoubaoWork_installer_2.31.10.exe",
  "https://lf-flow-web-cdn.doubao.com/obj/flow-doubao/doubao_pc/2.31.10/DoubaoWork_Arm64_installer_2.31.10.exe",
] as const;
export const OFFICE_CLOSED_REASON = "no-litellm-only-headless";

export interface HarnessPreset extends HarnessDraftBody {
  harness_id: string;
  registration: "adapter" | "plan";
  version_kind: "git_sha" | "exact" | "package";
  install_channel: "pip" | "npm" | "desktop" | "unpublished";
  install_url: string;
  install_urls: readonly string[];
  runnable: boolean;
  closed_reason: string;
}

const SLUG = /^[a-z0-9][a-z0-9-]{0,63}$/;
const COMMIT = /^[0-9a-f]{40}$/;
export type PoolProvider = "vllm" | "ollama";
export type HarnessAdapter =
  | "openbot"
  | "inspect_react"
  | "workbuddy"
  | "openclaw"
  | "deepseek_harness"
  | "doubao_work"
  | "qwen_work"
  | "langgraph"
  | "crewai"
  | "autogen"
  | "semantic_kernel"
  | "claude_code";
export type Modality = "text" | "code" | "vision" | "multimodal";
export type Placement = "local" | "gateway";
export const STRENGTHS = [
  "reasoning",
  "code",
  "tool_use",
  "long_context",
  "instruction",
  "multilingual",
  "low_latency",
  "low_cost",
] as const;
export const MODALITIES = ["text", "code", "vision", "multimodal"] as const;

export interface PoolModelDraft {
  model_id: string;
  provider: PoolProvider;
  name: string;
  revision: string;
  litellm_model: string;
}

export interface PoolDraftBody {
  pool_id: string;
  models: PoolModelDraft[];
}

export interface HarnessDraftBody {
  profile_id: string;
  adapter: HarnessAdapter;
  name: string;
  version: string;
  timeout_seconds: number;
}

export interface CatalogDraft extends PoolModelDraft {
  modality: Modality;
  strengths: string[];
  placement: Placement;
}

const PIP_PIN = { install_channel: "pip" as const, install_url: "", install_urls: [] as const };
const RUNNABLE = { runnable: true, closed_reason: "" } as const;
const PLAN_ONLY = { runnable: false, closed_reason: "" } as const;
const CLOSED = { runnable: false, closed_reason: OFFICE_CLOSED_REASON } as const;

export const HARNESS_CATALOG: readonly HarnessPreset[] = [
  {
    profile_id: "openbot-benchmark",
    adapter: "openbot",
    name: "openbot-benchmark",
    version: OPENBOT_VERSION_PLACEHOLDER,
    timeout_seconds: 600,
    harness_id: "openbot",
    registration: "adapter",
    version_kind: "git_sha",
    ...PIP_PIN,
    ...RUNNABLE,
  },
  {
    profile_id: "inspect-react-pin",
    adapter: "inspect_react",
    name: "inspect-react",
    version: INSPECT_PROFILE_VERSION,
    timeout_seconds: 600,
    harness_id: "inspect-react",
    registration: "adapter",
    version_kind: "exact",
    ...PIP_PIN,
    ...RUNNABLE,
  },
  {
    profile_id: "workbuddy-pin",
    adapter: "workbuddy",
    name: "workbuddy",
    version: WORKBUDDY_VERSION,
    timeout_seconds: 600,
    harness_id: "workbuddy",
    registration: "plan",
    version_kind: "exact",
    install_channel: "desktop",
    install_url: WORKBUDDY_INSTALL_URLS[2],
    install_urls: WORKBUDDY_INSTALL_URLS,
    ...RUNNABLE,
  },
  {
    profile_id: "openclaw-plan",
    adapter: "openclaw",
    name: "openclaw-plan",
    version: OPENCLAW_NPM_VERSION,
    timeout_seconds: 600,
    harness_id: "openclaw",
    registration: "plan",
    version_kind: "exact",
    install_channel: "npm",
    install_url: OPENCLAW_INSTALL_URL,
    install_urls: [OPENCLAW_INSTALL_URL],
    ...RUNNABLE,
  },
  {
    profile_id: "deepseek-harness-pin",
    adapter: "deepseek_harness",
    name: "deepseek-harness",
    version: DSH_PROFILE_VERSION,
    timeout_seconds: 600,
    harness_id: "deepseek-harness",
    registration: "plan",
    version_kind: "exact",
    install_channel: "npm",
    install_url: DSH_INSTALL_URL,
    install_urls: [DSH_INSTALL_URL],
    ...RUNNABLE,
  },
  {
    profile_id: "doubao-work-pin",
    adapter: "doubao_work",
    name: "doubao-work",
    version: DOUBAO_WORK_VERSION,
    timeout_seconds: 600,
    harness_id: "doubao-work",
    registration: "plan",
    version_kind: "exact",
    install_channel: "desktop",
    install_url: DOUBAO_WORK_INSTALL_URLS[0],
    install_urls: DOUBAO_WORK_INSTALL_URLS,
    ...CLOSED,
  },
  {
    profile_id: "qwen-work-pin",
    adapter: "qwen_work",
    name: "qwen-work",
    version: QWEN_WORK_VERSION,
    timeout_seconds: 600,
    harness_id: "qwen-work",
    registration: "plan",
    version_kind: "exact",
    install_channel: "desktop",
    install_url: "",
    install_urls: [],
    ...CLOSED,
  },
  {
    profile_id: "langgraph-plan",
    adapter: "langgraph",
    name: "langgraph-plan",
    version: "langgraph-version-unavailable",
    timeout_seconds: 600,
    harness_id: "langgraph",
    registration: "plan",
    version_kind: "package",
    ...PIP_PIN,
    ...PLAN_ONLY,
  },
  {
    profile_id: "crewai-plan",
    adapter: "crewai",
    name: "crewai-plan",
    version: "crewai-version-unavailable",
    timeout_seconds: 600,
    harness_id: "crewai",
    registration: "plan",
    version_kind: "package",
    ...PIP_PIN,
    ...PLAN_ONLY,
  },
  {
    profile_id: "autogen-plan",
    adapter: "autogen",
    name: "autogen-plan",
    version: "autogen-version-unavailable",
    timeout_seconds: 600,
    harness_id: "autogen",
    registration: "plan",
    version_kind: "package",
    ...PIP_PIN,
    ...PLAN_ONLY,
  },
  {
    profile_id: "semantic-kernel-plan",
    adapter: "semantic_kernel",
    name: "semantic-kernel-plan",
    version: "semantic-kernel-version-unavailable",
    timeout_seconds: 600,
    harness_id: "semantic-kernel",
    registration: "plan",
    version_kind: "package",
    ...PIP_PIN,
    ...PLAN_ONLY,
  },
  {
    profile_id: "claude-code-plan",
    adapter: "claude_code",
    name: "claude-code-plan",
    version: "claude-code-version-unavailable",
    timeout_seconds: 600,
    harness_id: "claude-code",
    registration: "plan",
    version_kind: "package",
    ...PIP_PIN,
    ...PLAN_ONLY,
  },
];

export const HARNESS_PRESETS: Record<HarnessAdapter, HarnessPreset> = Object.fromEntries(
  HARNESS_CATALOG.map((entry) => [entry.adapter, entry]),
) as Record<HarnessAdapter, HarnessPreset>;

export function litellmPrefix(provider: PoolProvider): string {
  return provider === "vllm" ? "hosted_vllm/" : "ollama/";
}

export function withProvider(model: PoolModelDraft, provider: PoolProvider): PoolModelDraft {
  return {
    ...model,
    provider,
    model_id: swapPrefix(model.model_id, `${model.provider}/`, `${provider}/`),
    litellm_model: swapPrefix(model.litellm_model, litellmPrefix(model.provider), litellmPrefix(provider)),
  };
}

export function blankModel(provider: PoolProvider = "vllm"): PoolModelDraft {
  return {
    provider,
    model_id: `${provider}/model`,
    name: "model",
    revision: "unpinned",
    litellm_model: `${litellmPrefix(provider)}model`,
  };
}

export function emptyPoolDraft(): PoolDraftBody {
  return {
    pool_id: "pool-gpu-farm",
    models: [
      {
        model_id: "vllm/REPLACE_ME-instruct",
        provider: "vllm",
        name: "REPLACE_ME-instruct",
        revision: "unpinned",
        litellm_model: "hosted_vllm/REPLACE_ME-instruct",
      },
    ],
  };
}

export function poolRequestBody(draft: PoolDraftBody): PoolDraftBody {
  return {
    pool_id: draft.pool_id,
    models: draft.models.map((model) => ({
      model_id: model.model_id,
      provider: model.provider,
      name: model.name,
      revision: model.revision,
      litellm_model: model.litellm_model,
    })),
  };
}

export function poolDraftIssues(body: PoolDraftBody): string[] {
  const issues: string[] = [];
  if (!SLUG.test(body.pool_id)) {
    issues.push("pool_id");
  }
  if (body.models.length === 0) {
    issues.push("models");
  }
  const seen = new Set<string>();
  for (const model of body.models) {
    if (model.provider !== "vllm" && model.provider !== "ollama") {
      issues.push("provider");
    }
    const provider: PoolProvider = model.provider === "ollama" ? "ollama" : "vllm";
    if (!model.model_id.startsWith(`${provider}/`) || model.model_id === `${provider}/`) {
      issues.push("model_id");
    }
    if (!model.litellm_model.startsWith(litellmPrefix(provider))) {
      issues.push("litellm_model");
    }
    if (!model.name.trim() || model.name !== model.name.trim()) {
      issues.push("name");
    }
    if (!model.revision.trim() || model.revision !== model.revision.trim()) {
      issues.push("revision");
    }
    if (seen.has(model.model_id)) {
      issues.push("duplicate");
    }
    seen.add(model.model_id);
    for (const value of [body.pool_id, model.model_id, model.name, model.revision, model.litellm_model]) {
      issues.push(...sensitiveIssues(value));
    }
  }
  return issues;
}

export function canSaveDraft(validatedFingerprint: string | null, body: PoolDraftBody, ok: boolean): boolean {
  return ok && validatedFingerprint === JSON.stringify(poolRequestBody(body)) && poolDraftIssues(body).length === 0;
}

export function emptyCatalogDraft(): CatalogDraft {
  return {
    ...blankModel(),
    modality: "text",
    strengths: ["reasoning"],
    placement: "local",
  };
}

export function catalogRequestBody(draft: CatalogDraft): CatalogDraft {
  return {
    model_id: draft.model_id,
    provider: draft.provider,
    name: draft.name,
    revision: draft.revision,
    litellm_model: draft.litellm_model,
    modality: draft.modality,
    strengths: [...draft.strengths],
    placement: draft.placement,
  };
}

export function catalogIssues(body: CatalogDraft): string[] {
  const issues = poolDraftIssues({ pool_id: "pool-catalog-check", models: [body] }).filter((issue) => issue !== "pool_id");
  if (!MODALITIES.includes(body.modality)) {
    issues.push("modality");
  }
  if (body.placement !== "local" && body.placement !== "gateway") {
    issues.push("placement");
  }
  if (body.strengths.length === 0 || new Set(body.strengths).size !== body.strengths.length) {
    issues.push("strengths");
  }
  for (const strength of body.strengths) {
    if (!(STRENGTHS as readonly string[]).includes(strength)) {
      issues.push("strengths");
    }
  }
  return issues;
}

export function canSaveCatalog(validatedFingerprint: string | null, body: CatalogDraft, ok: boolean): boolean {
  return ok && validatedFingerprint === JSON.stringify(catalogRequestBody(body)) && catalogIssues(body).length === 0;
}

export function emptyHarnessDraft(): HarnessDraftBody {
  return { ...HARNESS_PRESETS.openbot };
}

export function harnessRequestBody(draft: HarnessDraftBody): HarnessDraftBody {
  return {
    profile_id: draft.profile_id,
    adapter: draft.adapter,
    name: draft.name,
    version: draft.version,
    timeout_seconds: draft.timeout_seconds,
  };
}

export function harnessRegistration(adapter: HarnessAdapter): {
  liveRun: "rejected";
  status: "registered" | "plan";
  devOnly: false;
  unlocksLiveHarness: false;
  registration: "adapter" | "plan";
  inLiveOrder: boolean;
} {
  const entry = HARNESS_PRESETS[adapter];
  const registration = entry.registration;
  return {
    liveRun: "rejected",
    status: registration === "adapter" ? "registered" : "plan",
    devOnly: false,
    unlocksLiveHarness: false,
    registration,
    inLiveOrder: registration === "adapter",
  };
}

export function harnessIdFor(draft: HarnessDraftBody): string {
  return HARNESS_PRESETS[draft.adapter].harness_id;
}

export function harnessClosed(adapter: HarnessAdapter): boolean {
  return HARNESS_PRESETS[adapter].closed_reason !== "";
}

const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$/;

/** Name suggestions for Add local. Family tokens, not provider ids. */
export const OSS_MODEL_NAME_CANDIDATES: readonly { readonly family: string; readonly names: readonly string[] }[] = [
  { family: "Qwen", names: ["qwen2.5", "qwen3", "qwq"] },
  { family: "DeepSeek", names: ["deepseek-r1", "deepseek-v3"] },
  { family: "MiniMax", names: ["minimax-m1"] },
  { family: "GLM", names: ["glm-4", "glm-4.5"] },
  { family: "Kimi", names: ["kimi-k2"] },
];

export interface LocalAddDraft {
  provider: PoolProvider;
  name: string;
  revision: string;
}

export interface CloudAddDraft extends LocalAddDraft {
  apiKey: string;
  gatewayUrl: string;
}

export function localIdentity(draft: LocalAddDraft): { model_id: string; litellm_model: string } {
  return {
    model_id: `${draft.provider}/${draft.name}`,
    litellm_model: `${litellmPrefix(draft.provider)}${draft.name}`,
  };
}

export function localAddIssues(draft: LocalAddDraft): string[] {
  const issues: string[] = [];
  if (draft.provider !== "vllm" && draft.provider !== "ollama") {
    issues.push("provider");
  }
  if (!MODEL_NAME.test(draft.name)) {
    issues.push("name");
  }
  if (!revisionAdmitted(draft.revision)) {
    issues.push("revision");
  }
  const identity = localIdentity(draft);
  if (!identity.model_id.startsWith(`${draft.provider}/`) || identity.model_id.endsWith("/")) {
    issues.push("model_id");
  }
  const prefix = litellmPrefix(draft.provider === "ollama" ? "ollama" : "vllm");
  if (!identity.litellm_model.startsWith(prefix) || identity.litellm_model === prefix) {
    issues.push("litellm_model");
  }
  for (const value of [draft.name, draft.revision, identity.model_id, identity.litellm_model]) {
    issues.push(...sensitiveIssues(value));
  }
  return issues;
}

const VLLM_HOST_SUFFIXES = ["openai.com", "anthropic.com", "litellm.ai", "litellm.com", "litellm.cloud", "berri.ai"];

function deniedVllmHost(host: string): boolean {
  const name = host.toLowerCase().replace(/\.$/, "");
  return VLLM_HOST_SUFFIXES.some((suffix) => name === suffix || name.endsWith(`.${suffix}`));
}

/** Discovery body. Identity stays omitted so the server lists every passing local row. */
export function localScanBody(input: { baseUrl: string; apiKey: string }): {
  target: "local";
  host: "";
  port: null;
  vllm_base_url: string;
  api_key: string;
} {
  return {
    target: "local",
    host: "",
    port: null,
    vllm_base_url: input.baseUrl.trim(),
    api_key: input.apiKey,
  };
}

/** Optional vLLM listing fields. Empty values stay valid so the server can use env. */
export function localVllmIssues(input: { baseUrl: string; apiKey: string }): string[] {
  const issues: string[] = [];
  const url = input.baseUrl;
  if (url.trim()) {
    let rejected = url !== url.trim() || !/^https?:\/\//.test(url) || url.includes("@") || url.includes("?") || url.includes("#");
    if (!rejected) {
      try {
        const parsed = new URL(url);
        const path = parsed.pathname.replace(/\/+$/, "");
        rejected = (path !== "" && path !== "/v1") || deniedVllmHost(parsed.hostname);
      } catch {
        rejected = true;
      }
    }
    if (rejected) {
      issues.push("vllm_base_url");
    }
  }
  if (input.apiKey && (input.apiKey.length < 8 || input.apiKey.length > 256 || /\s/.test(input.apiKey) || input.apiKey.includes("://"))) {
    issues.push("api_key");
  }
  return issues;
}

export function cloudAddIssues(draft: CloudAddDraft): string[] {
  const revision = draft.revision === "" ? "from-listing" : draft.revision;
  const issues = localAddIssues({ ...draft, revision });
  if (draft.apiKey.length < 8 || draft.apiKey.length > 256 || /\s/.test(draft.apiKey) || draft.apiKey.includes("://")) {
    issues.push("api_key");
  }
  const url = draft.gatewayUrl.trim();
  if (!url || !/^https?:\/\//.test(url) || url.includes("@") || url.includes("?") || url.includes("#")) {
    issues.push("gateway_url");
  }
  return issues;
}

export function revisionAdmitted(revision: string): boolean {
  const text = revision.trim();
  if (!text || text !== revision) {
    return false;
  }
  const lowered = text.toLowerCase();
  if (lowered === "latest" || lowered === "unpinned" || lowered === "main" || lowered === "master") {
    return false;
  }
  if (lowered.includes("sk-") || lowered.includes("api_key") || text.includes("://")) {
    return false;
  }
  if (lowered.includes("openai.com") || lowered.includes("anthropic.com")) {
    return false;
  }
  return true;
}

export function targetReady(target: "local" | "remote", reachable: boolean | null): boolean {
  return target === "local" || reachable === true;
}

const REMOTE_USER = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,31}$/;
const SECRET_ENV = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

export function remoteAuthReady(input: {
  username: string;
  authMethod: "ssh_key" | "password";
  secret: string;
  secretEnv: string;
}): boolean {
  if (!REMOTE_USER.test(input.username)) {
    return false;
  }
  const envOk = input.secretEnv !== "" && SECRET_ENV.test(input.secretEnv);
  if (input.authMethod === "ssh_key") {
    const keyOk = input.secret.includes("PRIVATE KEY") && input.secret.length >= 32 && !input.secret.includes("\u0000");
    return envOk || keyOk;
  }
  const secretOk =
    input.secret.length > 0 &&
    input.secret.length <= 65536 &&
    input.secret.trim() !== "" &&
    !input.secret.includes("\n") &&
    !input.secret.includes("\r") &&
    !input.secret.includes("\u0000");
  return envOk || secretOk;
}

export function harnessDraftIssues(body: HarnessDraftBody): string[] {
  const issues: string[] = [];
  if (!SLUG.test(body.profile_id)) {
    issues.push("profile_id");
  }
  if (!Number.isInteger(body.timeout_seconds) || body.timeout_seconds < 1) {
    issues.push("timeout");
  }
  if (!body.name.trim() || body.name !== body.name.trim()) {
    issues.push("name");
  }
  issues.push(...sensitiveIssues(body.profile_id), ...sensitiveIssues(body.name), ...sensitiveIssues(body.version));
  const preset = HARNESS_PRESETS[body.adapter];
  if (!preset) {
    issues.push("adapter");
  } else if (preset.version_kind === "git_sha") {
    if (body.version !== OPENBOT_VERSION_PLACEHOLDER && !COMMIT.test(body.version)) {
      issues.push("version");
    }
  } else if (body.version !== preset.version) {
    issues.push("version");
  }
  return issues;
}

export function canSaveHarness(validatedFingerprint: string | null, body: HarnessDraftBody, ok: boolean): boolean {
  return ok && validatedFingerprint === JSON.stringify(harnessRequestBody(body)) && harnessDraftIssues(body).length === 0;
}

function swapPrefix(value: string, from: string, to: string): string {
  if (value.startsWith(from)) {
    return to + value.slice(from.length);
  }
  return value;
}

function sensitiveIssues(value: string): string[] {
  const issues: string[] = [];
  const lowered = value.toLowerCase();
  if (lowered.includes("sk-") || lowered.includes("api_key")) {
    issues.push("secret");
  }
  if (value.includes("://")) {
    issues.push("url");
  }
  if (lowered.includes("openai.com") || lowered.includes("anthropic.com")) {
    issues.push("cloud_host");
  }
  return issues;
}
