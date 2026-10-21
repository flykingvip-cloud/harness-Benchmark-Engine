import assert from "node:assert/strict";
import test from "node:test";
import {
  HARNESS_CATALOG,
  HARNESS_ORDER,
  HARNESS_PRESETS,
  STRENGTHS,
  blankModel,
  canSaveCatalog,
  canSaveDraft,
  canSaveHarness,
  catalogIssues,
  catalogRequestBody,
  emptyCatalogDraft,
  emptyHarnessDraft,
  emptyPoolDraft,
  harnessClosed,
  harnessDraftIssues,
  harnessIdFor,
  harnessRegistration,
  harnessRequestBody,
  litellmPrefix,
  OSS_MODEL_NAME_CANDIDATES,
  remoteAuthReady,
  revisionAdmitted,
  targetReady,
  cloudAddIssues,
  localAddIssues,
  localScanBody,
  localIdentity,
  localVllmIssues,
  poolDraftIssues,
  poolRequestBody,
  withProvider,
} from "./forms.ts";

test("live harness order stays the two preinstalled adapters", () => {
  assert.deepEqual(HARNESS_ORDER, ["openbot", "inspect_react"]);
  assert.equal(harnessRegistration("openbot").unlocksLiveHarness, false);
  assert.equal(harnessRegistration("inspect_react").status, "registered");
  assert.equal(harnessRegistration("inspect_react").devOnly, false);
  assert.equal(harnessRegistration("openbot").liveRun, "rejected");
  assert.equal(HARNESS_PRESETS.inspect_react.version, "0.3.277");
  assert.equal(HARNESS_PRESETS.openbot.profile_id, "openbot-benchmark");
  assert.deepEqual(
    HARNESS_CATALOG.map((entry) => entry.adapter),
    [
      "openbot",
      "inspect_react",
      "workbuddy",
      "openclaw",
      "deepseek_harness",
      "doubao_work",
      "qwen_work",
      "langgraph",
      "crewai",
      "autogen",
      "semantic_kernel",
      "claude_code",
    ],
  );
  assert.equal(HARNESS_PRESETS.deepseek_harness.version, "0.2.1-alpha.2");
  assert.equal(HARNESS_PRESETS.deepseek_harness.install_url, "https://registry.npmjs.org/@deepseek-ai/dsh/-/dsh-0.2.1-alpha.2.tgz");
  assert.equal(HARNESS_PRESETS.openclaw.version, "2026.10.1");
  assert.equal(HARNESS_PRESETS.openclaw.install_channel, "npm");
  assert.equal(HARNESS_PRESETS.workbuddy.version, "5.7.7");
  assert.equal(HARNESS_PRESETS.workbuddy.install_urls.length, 3);
  assert.equal(HARNESS_PRESETS.qwen_work.version, "1.2.5");
  assert.equal(HARNESS_PRESETS.qwen_work.install_url, "");
  assert.equal(HARNESS_PRESETS.qwen_work.runnable, false);
  assert.equal(HARNESS_PRESETS.qwen_work.closed_reason, "no-litellm-only-headless");
  assert.equal(HARNESS_PRESETS.doubao_work.version, "2.31.10");
  assert.equal(HARNESS_PRESETS.doubao_work.install_channel, "desktop");
  assert.equal(HARNESS_PRESETS.doubao_work.install_urls.length, 2);
  assert.equal(HARNESS_PRESETS.doubao_work.runnable, false);
  assert.equal(harnessClosed("qwen_work"), true);
  assert.equal(harnessClosed("doubao_work"), true);
  assert.equal(harnessClosed("workbuddy"), false);
  assert.equal(harnessRegistration("deepseek_harness").status, "plan");
  assert.equal(harnessRegistration("deepseek_harness").inLiveOrder, false);
  assert.equal(harnessRegistration("deepseek_harness").unlocksLiveHarness, false);
  assert.equal(harnessRegistration("deepseek_harness").liveRun, "rejected");
  assert.equal(harnessRegistration("openclaw").status, "plan");
  assert.equal(harnessRegistration("openclaw").inLiveOrder, false);
  assert.equal(harnessRegistration("openclaw").unlocksLiveHarness, false);
  assert.equal(harnessRegistration("workbuddy").inLiveOrder, false);
  assert.equal(harnessRegistration("doubao_work").unlocksLiveHarness, false);
  assert.equal(harnessRegistration("qwen_work").liveRun, "rejected");
  for (const entry of HARNESS_CATALOG) {
    assert.equal(entry.install_url.toLowerCase().includes("latest"), false);
    assert.equal(entry.install_urls.some((url) => url.toLowerCase().includes("latest")), false);
  }
  assert.equal(targetReady("local", null), true);
  assert.equal(targetReady("remote", null), false);
  assert.equal(targetReady("remote", true), true);
  assert.equal(remoteAuthReady({ username: "", authMethod: "ssh_key", secret: "", secretEnv: "" }), false);
  assert.equal(remoteAuthReady({ username: "ops", authMethod: "ssh_key", secret: "", secretEnv: "OHBE_SSH_KEY" }), true);
  assert.equal(remoteAuthReady({ username: "ops", authMethod: "password", secret: "correct-horse", secretEnv: "" }), true);
  assert.equal(remoteAuthReady({ username: "ops;rm", authMethod: "password", secret: "correct-horse", secretEnv: "" }), false);
  assert.equal(revisionAdmitted("7b"), true);
  assert.equal(revisionAdmitted("latest"), false);
  assert.equal(revisionAdmitted("unpinned"), false);
});

test("pool prefixes follow vllm and ollama only", () => {
  assert.equal(litellmPrefix("vllm"), "hosted_vllm/");
  assert.equal(litellmPrefix("ollama"), "ollama/");
  const swapped = withProvider(blankModel("vllm"), "ollama");
  assert.equal(swapped.provider, "ollama");
  assert.equal(swapped.model_id, "ollama/model");
  assert.equal(swapped.litellm_model, "ollama/model");
  assert.deepEqual(poolDraftIssues(emptyPoolDraft()), []);
});

test("pool draft rejects cloud hosts, secrets, urls, and unknown providers", () => {
  const draft = emptyPoolDraft();
  draft.models[0].litellm_model = "hosted_vllm/lab.openai.com";
  assert.ok(poolDraftIssues(draft).includes("cloud_host"));
  draft.models[0].litellm_model = "hosted_vllm/lab";
  draft.models[0].name = "sk-live";
  assert.ok(poolDraftIssues(draft).includes("secret"));
  draft.models[0].name = "lab";
  draft.models[0].revision = "http://gpu/v1";
  assert.ok(poolDraftIssues(draft).includes("url"));
  draft.models[0].revision = "unpinned";
  draft.models[0].model_id = "openai/gpt";
  assert.ok(poolDraftIssues(draft).includes("model_id"));
});

test("save stays closed until the validated body still matches", () => {
  const draft = emptyPoolDraft();
  const fingerprint = JSON.stringify(poolRequestBody(draft));
  assert.equal(canSaveDraft(null, draft, true), false);
  assert.equal(canSaveDraft(fingerprint, draft, false), false);
  assert.equal(canSaveDraft(fingerprint, draft, true), true);
  draft.models[0].name = "renamed";
  assert.equal(canSaveDraft(fingerprint, draft, true), false);
});

test("adding a harness keeps the preinstalled identity", () => {
  const openbot = emptyHarnessDraft();
  assert.equal(harnessIdFor(openbot), "openbot");
  assert.deepEqual(harnessDraftIssues(openbot), []);
  assert.equal("draft_adapter_name" in harnessRequestBody(openbot), false);
  const inspect = { ...HARNESS_PRESETS.inspect_react };
  assert.equal(harnessIdFor(inspect), "inspect-react");
  const fingerprint = JSON.stringify(harnessRequestBody(openbot));
  assert.equal(canSaveHarness(fingerprint, openbot, true), true);
  assert.equal(canSaveHarness(fingerprint, openbot, false), false);
});

test("add-local name suggestions are open-source slugs under vllm or ollama", () => {
  assert.deepEqual(
    OSS_MODEL_NAME_CANDIDATES.map((group) => group.family),
    ["Qwen", "DeepSeek", "MiniMax", "GLM", "Kimi"],
  );
  const names = OSS_MODEL_NAME_CANDIDATES.flatMap((group) => group.names);
  for (const slug of ["qwen3", "deepseek-r1", "glm-4", "kimi-k2", "minimax-m1"]) {
    assert.ok(names.includes(slug), slug);
  }
  const blocked = [
    "openai",
    "anthropic",
    "moonshot",
    "dashscope",
    "zhipu",
    "qwen-plus",
    "qwen-max",
    "qwen-turbo",
    "deepseek-chat",
    "deepseek-reasoner",
    "glm-4-plus",
    "glm-4-flash",
    "moonshot-v1",
    "abab6.5",
  ];
  for (const slug of names) {
    assert.equal(blocked.includes(slug), false, slug);
    assert.equal(slug.includes("/"), false, slug);
    for (const provider of ["vllm", "ollama"] as const) {
      assert.deepEqual(localAddIssues({ provider, name: slug, revision: "7b" }), []);
      assert.ok(localAddIssues({ provider, name: slug, revision: "latest" }).includes("revision"));
      assert.ok(localAddIssues({ provider, name: slug, revision: "unpinned" }).includes("revision"));
    }
  }
  assert.deepEqual(localAddIssues({ provider: "vllm", name: "lab-custom", revision: "2026-10-01" }), []);
  assert.ok(localAddIssues({ provider: "vllm", name: "qwen3", revision: "" }).includes("revision"));
  assert.ok(localAddIssues({ provider: "deepseek" as "vllm", name: "deepseek-r1", revision: "7b" }).includes("provider"));
  assert.ok(localAddIssues({ provider: "moonshot" as "ollama", name: "kimi-k2", revision: "7b" }).includes("provider"));
});

test("local and cloud add actions require the intake triad", () => {
  const local = { provider: "ollama" as const, name: "qwen2.5", revision: "7b" };
  assert.deepEqual(localAddIssues(local), []);
  assert.deepEqual(localIdentity(local), { model_id: "ollama/qwen2.5", litellm_model: "ollama/qwen2.5" });
  assert.ok(localAddIssues({ ...local, revision: "latest" }).includes("revision"));
  assert.ok(localAddIssues({ ...local, name: "" }).includes("name"));
  const cloud = { ...local, provider: "vllm" as const, name: "lab", apiKey: "sk-intake-secret", gatewayUrl: "http://ohbe-gateway:4000" };
  assert.deepEqual(cloudAddIssues(cloud), []);
  assert.equal(localIdentity(cloud).litellm_model, "hosted_vllm/lab");
  assert.deepEqual(cloudAddIssues({ ...cloud, revision: "" }), []);
  assert.ok(cloudAddIssues({ ...cloud, revision: "latest" }).includes("revision"));
  assert.ok(cloudAddIssues({ ...cloud, apiKey: "short" }).includes("api_key"));
  assert.ok(cloudAddIssues({ ...cloud, gatewayUrl: "http://user:pw@ohbe-gateway:4000" }).includes("gateway_url"));
  assert.ok(cloudAddIssues({ ...cloud, gatewayUrl: "" }).includes("gateway_url"));
});

test("local discovery scan does not send a pre-filled identity", () => {
  const body = localScanBody({ baseUrl: " http://127.0.0.1:8001 ", apiKey: "" });
  assert.deepEqual(body, {
    target: "local",
    host: "",
    port: null,
    vllm_base_url: "http://127.0.0.1:8001",
    api_key: "",
  });
  for (const key of ["provider", "name", "revision", "model_id", "litellm_model"]) {
    assert.equal(Object.hasOwn(body, key), false, key);
  }
});

test("local vllm discovery fields stay optional and reject a provider host", () => {
  assert.deepEqual(localVllmIssues({ baseUrl: "", apiKey: "" }), []);
  assert.deepEqual(localVllmIssues({ baseUrl: "http://127.0.0.1:8000", apiKey: "" }), []);
  assert.deepEqual(localVllmIssues({ baseUrl: "http://127.0.0.1:8000/v1", apiKey: "sk-vllm-local" }), []);
  assert.ok(localVllmIssues({ baseUrl: "https://api.openai.com/v1", apiKey: "" }).includes("vllm_base_url"));
  assert.ok(localVllmIssues({ baseUrl: "http://127.0.0.1:8000/v1/chat/completions", apiKey: "" }).includes("vllm_base_url"));
  assert.ok(localVllmIssues({ baseUrl: "http://user:pw@127.0.0.1:8000", apiKey: "" }).includes("vllm_base_url"));
  assert.ok(localVllmIssues({ baseUrl: "http://127.0.0.1:8000", apiKey: "short" }).includes("api_key"));
});

test("catalog intake keeps rich metadata off the pool body and rejects secrets", () => {
  const entry = emptyCatalogDraft();
  assert.equal(catalogIssues(entry).length, 0);
  assert.ok(STRENGTHS.includes("reasoning"));
  const body = catalogRequestBody(entry);
  assert.equal(body.placement, "local");
  assert.deepEqual(body.strengths, ["reasoning"]);
  const fingerprint = JSON.stringify(body);
  assert.equal(canSaveCatalog(fingerprint, entry, true), true);
  entry.name = "sk-live";
  assert.ok(catalogIssues(entry).includes("secret"));
  assert.equal(canSaveCatalog(fingerprint, entry, true), false);
});
