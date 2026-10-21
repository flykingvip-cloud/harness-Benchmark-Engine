import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { browserLocale, LOCALE_STORAGE_KEY, parseLocale, readStoredLocale, resolveLocale, writeStoredLocale } from "./locale.ts";
import { isMessageKey, messages } from "./messages.ts";

test("zh* browsers default to Chinese", () => {
  assert.equal(browserLocale(["zh-CN"]), "zh");
  assert.equal(browserLocale(["zh-Hans-CN", "en-US"]), "zh");
  assert.equal(browserLocale(["en-US", "zh-TW"]), "zh");
  assert.equal(browserLocale(["ZH-cn"]), "zh");
});

test("other browsers default to English", () => {
  assert.equal(browserLocale(["en-US"]), "en");
  assert.equal(browserLocale(["fr-FR"]), "en");
  assert.equal(browserLocale(["ja"]), "en");
  assert.equal(browserLocale([]), "en");
  assert.equal(browserLocale(["zulu"]), "en");
});

test("a saved locale overrides the browser", () => {
  assert.equal(resolveLocale("en", ["zh-CN", "zh"]), "en");
  assert.equal(resolveLocale("zh", ["en-US"]), "zh");
  assert.equal(resolveLocale(null, ["zh-HK"]), "zh");
  assert.equal(resolveLocale(undefined, ["de"]), "en");
});

test("only zh and en are stored locales", () => {
  assert.equal(parseLocale("zh"), "zh");
  assert.equal(parseLocale("en"), "en");
  assert.equal(parseLocale("zh-CN"), null);
  assert.equal(parseLocale("fr"), null);
  assert.equal(parseLocale(""), null);
  assert.equal(resolveLocale("zh-CN", ["en-US"]), "en");
  assert.equal(resolveLocale("english", ["zh"]), "zh");
});

test("storage read and write use the stable key and ignore failures", () => {
  const saved = new Map<string, string>();
  const storage = {
    getItem: (key: string) => saved.get(key) ?? null,
    setItem: (key: string, value: string) => {
      saved.set(key, value);
    },
  };
  assert.equal(readStoredLocale(storage), null);
  writeStoredLocale(storage, "en");
  assert.equal(saved.get(LOCALE_STORAGE_KEY), "en");
  assert.equal(readStoredLocale(storage), "en");
  assert.equal(resolveLocale(readStoredLocale(storage), ["zh-CN"]), "en");

  const blocked = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  assert.equal(readStoredLocale(blocked), null);
  writeStoredLocale(blocked, "zh");
  assert.equal(readStoredLocale(null), null);
});

test("Chinese and English catalogs have the same keys", () => {
  const zhKeys = Object.keys(messages.zh).sort();
  const enKeys = Object.keys(messages.en).sort();
  assert.deepEqual(zhKeys, enKeys);
  for (const key of zhKeys) {
    assert.equal(isMessageKey(key), true);
    assert.ok(messages.zh[key as keyof typeof messages.zh].length > 0);
    assert.ok(messages.en[key as keyof typeof messages.en].length > 0);
  }
  assert.equal(messages.zh["lang.zh"], "中文");
  assert.equal(messages.en["lang.en"], "English");
  assert.equal(messages.zh["nav.overview"], "总览");
  assert.equal(messages.en["nav.overview"], "Overview");
  assert.equal(messages.en["nav.doctor"], "Doctor");
});

test("visible pages keep Chinese copy in the catalog", () => {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const files = ["App.tsx", "main.tsx", "useLoad.ts", "api.ts", "library.tsx", "rounds.ts", "monitor.ts", "ObservationShell.tsx", "Charts.tsx", "chartData.ts", "DimensionBoard.tsx", "pages/Overview.tsx", "pages/Experiments.tsx", "pages/Prepare.tsx", "pages/Pools.tsx", "pages/Harnesses.tsx", "pages/Results.tsx", "pages/Doctor.tsx", "pages/Rounds.tsx", "pages/Monitor.tsx", "pages/ComparisonSlots.tsx"];
  const chinese = /[\u4e00-\u9fff]/;
  for (const file of files) {
    const text = readFileSync(resolve(root, file), "utf8");
    assert.equal(chinese.test(text), false, file);
  }
});

test("dry-run payload identifiers stay English", () => {
  const source = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "../pages/Doctor.tsx"), "utf8");
  assert.match(source, /experiment_id:\s*"fixture-pilot"/);
  assert.match(source, /pool_id:\s*"pool-pilot"/);
  assert.match(source, /live:\s*requestLive/);
});

test("the model pool and harness library are the only add pages", () => {
  assert.equal(messages.zh["nav.pools"], "模型池");
  assert.equal(messages.en["nav.pools"], "Model Pool");
  assert.equal(messages.zh["pools.title"], "模型池");
  assert.equal(messages.en["pools.title"], "Model Pool");
  assert.equal(messages.zh["nav.harnesses"], "Harness 库");
  assert.equal(messages.en["nav.harnesses"], "Harness Library");
  assert.equal(messages.zh["harness.title"], "Harness 库");
  assert.equal(messages.en["harness.title"], "Harness Library");
  assert.equal(messages.zh["pools.addLocal"], "添加本地");
  assert.equal(messages.en["pools.addLocal"], "Add local");
  assert.equal(messages.zh["pools.addCloud"], "添加云端");
  assert.equal(messages.en["pools.addCloud"], "Add cloud");
  assert.equal(messages.zh["library.scan"], "扫描");
  assert.equal(messages.en["library.scan"], "Scan");
  assert.equal(messages.zh["library.preinstall"], "预装");
  assert.match(messages.zh["rounds.needHarness"], /Harness 库/);

  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const app = readFileSync(resolve(root, "App.tsx"), "utf8");
  const pools = readFileSync(resolve(root, "pages/Pools.tsx"), "utf8");
  const slots = readFileSync(resolve(root, "pages/ComparisonSlots.tsx"), "utf8");
  const actionsStart = pools.indexOf("const actions =");
  const actionsEnd = pools.indexOf(");", actionsStart);
  const actions = pools.slice(actionsStart, actionsEnd);
  assert.match(actions, /pools\.addLocal/);
  assert.match(actions, /pools\.addCloud/);
  assert.doesNotMatch(actions, /library\.scan/);
  assert.match(pools, /embedded \? "page-head bar prepare-tab-actions"/);
  assert.match(pools, /modelLibraryScan/);
  assert.match(pools, /pools\.localLead/);
  assert.match(pools, /pools\.localNameHint/);
  assert.match(pools, /list=\{panel === "local" \? "oss-model-names" : undefined\}/);
  assert.match(pools, /<datalist id="oss-model-names">/);
  assert.match(pools, /OSS_MODEL_NAME_CANDIDATES/);
  assert.match(pools, /<option value="vllm">vllm<\/option>/);
  assert.match(pools, /<option value="ollama">ollama<\/option>/);
  assert.doesNotMatch(pools, /option value="(?:openai|anthropic|deepseek|qwen|moonshot|minimax|glm|kimi)"/);
  assert.match(messages.zh["pools.localNameHint"], /Qwen、DeepSeek、MiniMax、GLM、Kimi/);
  assert.match(messages.zh["pools.localNameHint"], /自行填写/);
  assert.match(messages.zh["pools.localNameHint"], /名称必填/);
  assert.doesNotMatch(messages.zh["pools.localNameHint"], /可选/);
  assert.match(messages.en["pools.localNameHint"], /Qwen, DeepSeek, MiniMax, GLM, and Kimi/);
  assert.match(messages.en["pools.localNameHint"], /type your own/);
  assert.match(messages.en["pools.localNameHint"], /Name is required/);
  assert.equal(messages.zh["library.revision"], "修订");
  assert.equal(messages.en["library.revision"], "Revision");
  assert.match(messages.zh["pools.revisionHint"], /latest/);
  assert.match(messages.zh["pools.revisionHint"], /main/);
  assert.match(messages.en["pools.revisionHint"], /concrete revision/);
  assert.match(messages.zh["pools.fieldHint"], /自动生成/);
  assert.match(messages.zh["pools.fieldHint"], /名称和具体修订/);
  assert.match(messages.zh["pools.fieldHint"], /密钥不进池文件/);
  assert.doesNotMatch(messages.zh["pools.fieldHint"], /必填：model_id/);
  assert.match(messages.en["pools.fieldHint"], /derived from the provider and name/);
  assert.match(messages.en["pools.fieldHint"], /Secrets never enter the pool file/);
  assert.doesNotMatch(messages.en["pools.fieldHint"], /Required: model_id/);
  assert.equal(messages.zh["pools.advanced"], "高级");
  assert.equal(messages.en["pools.advanced"], "Advanced");
  assert.match(pools, /pools\.vllmHint/);
  assert.match(pools, /vllm_base_url/);
  assert.match(pools, /provider === "vllm" \? \{ vllm_base_url: vllmBaseUrl\.trim\(\), api_key: vllmApiKey \}/);
  assert.match(pools, /target: "local"/);
  assert.doesNotMatch(pools, /TargetFields/);
  assert.doesNotMatch(pools, /useLibraryGate/);
  const advancedStart = pools.indexOf('<details className="advanced"');
  const advancedEnd = pools.indexOf("</details>", advancedStart);
  assert.ok(advancedStart > 0 && advancedEnd > advancedStart);
  const advanced = pools.slice(advancedStart, advancedEnd);
  assert.doesNotMatch(advanced, /\sopen[\s=>]/);
  assert.match(advanced, /pools\.vllmBaseUrl/);
  assert.match(advanced, /pools\.vllmApiKey/);
  assert.match(advanced, /pools\.vllmHint/);
  const beforeAdvanced = pools.slice(0, advancedStart);
  assert.match(beforeAdvanced, /library\.provider/);
  assert.match(beforeAdvanced, /library\.modelName/);
  assert.match(beforeAdvanced, /library\.revision/);
  assert.doesNotMatch(beforeAdvanced, /pools\.vllmBaseUrl/);
  assert.match(pools.slice(advancedEnd), /library\.scan/);
  assert.match(messages.zh["pools.vllmHint"], /不解锁 live spend/);
  assert.match(messages.zh["pools.vllmHint"], /OHBE_VLLM_API_KEY/);
  assert.match(messages.zh["pools.vllmHint"], /127\.0\.0\.1:8001/);
  assert.doesNotMatch(messages.zh["pools.vllmHint"], /127\.0\.0\.1:8000/);
  assert.match(messages.en["pools.vllmHint"], /does not unlock live spend/);
  assert.match(messages.en["pools.vllmHint"], /127\.0\.0\.1:8001/);
  assert.match(messages.en["pools.vllmHint"], /not the OHBE workbench API/);
  assert.doesNotMatch(pools, /OHBE_ALLOW_LIVE_SPEND/);
  const preparePage = readFileSync(resolve(root, "pages/Prepare.tsx"), "utf8");
  assert.match(preparePage, /nav\.harnesses/);
  assert.match(preparePage, /nav\.pools/);
  assert.match(app, /path="\/harnesses"/);
  assert.match(app, /path="\/pools"/);
  assert.match(app, /path="\/prepare"/);
  assert.match(app, /Navigate to="\/prepare\?tab=pools"/);
  assert.match(app, /Navigate to="\/prepare\?tab=harnesses"/);
  assert.match(app, /PreparePage/);
  assert.equal(app.includes("intake"), false);
  assert.equal(app.includes("ModelIntake"), false);
  assert.equal(app.includes("HarnessPreinstall"), false);
  assert.doesNotMatch(app, /nav\.modelIntake/);
  assert.doesNotMatch(app, /nav\.harnessPreinstall/);
  assert.doesNotMatch(slots, /comparisonSlots/);
  assert.match(slots, /catalogModels/);
  assert.doesNotMatch(slots, /cloudIntake/);
});

test("tester navigation is overview, prepare, and one evaluation page", () => {
  assert.equal(messages.zh["nav.prepare"], "资源准备");
  assert.equal(messages.en["nav.prepare"], "Resource prep");
  assert.equal(messages.zh["nav.rounds"], "评测/方案");
  assert.equal(messages.en["nav.rounds"], "Evaluation");
  assert.equal(messages.zh["nav.results"], "报告");
  assert.equal(messages.en["nav.results"], "Report");
  assert.match(messages.zh["chart.intervalCaption"], /不是排名/);
  assert.match(messages.en["chart.intervalCaption"], /not a ranking/);
  assert.match(messages.zh["chart.barCaption"], /缺失不是零/);
  assert.match(messages.en["chart.barCaption"], /Missing is not zero/);
  assert.match(messages.en["nav.note"], /No cloud spend/);
  assert.equal(messages.zh["prepare.tabs"], "资源准备分页");
  assert.equal(messages.en["prepare.tabs"], "Resource tabs");
  assert.match(messages.zh["prepare.lead"], /模型池/);
  assert.match(messages.en["prepare.lead"], /Model Pool/);

  const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
  const app = readFileSync(resolve(root, "App.tsx"), "utf8");
  const prepare = readFileSync(resolve(root, "pages/Prepare.tsx"), "utf8");
  const navStart = app.indexOf('<nav className="nav-links">');
  const navEnd = app.indexOf("</nav>", navStart);
  const nav = app.slice(navStart, navEnd);
  assert.match(nav, /to="\/prepare"/);
  assert.match(nav, /nav\.prepare/);
  assert.match(nav, /nav\.fixtureCatalog/);
  assert.match(nav, /to="\/experiments"/);
  assert.doesNotMatch(nav, /nav-sub/);
  assert.doesNotMatch(nav, /prepare\.map/);
  assert.doesNotMatch(nav, /nav\.group\.resources/);
  assert.doesNotMatch(nav, /to="\/pools"/);
  assert.doesNotMatch(nav, /to="\/harnesses"/);
  assert.match(nav, /nav\.overview/);
  assert.match(nav, /nav\.group\.path/);
  assert.match(nav, /nav\.rounds/);
  assert.match(nav, /nav\.monitor/);
  assert.match(nav, /nav\.results/);
  assert.match(nav, /to="\/results"/);
  assert.match(nav, /nav\.group\.ops/);
  assert.match(nav, /nav\.doctor/);
  assert.match(nav, /to="\/doctor"/);
  assert.match(app, /path="\/monitor"/);
  assert.equal(messages.zh["nav.monitor"], "监测");
  assert.equal(messages.en["nav.monitor"], "Monitor");
  assert.equal(messages.zh["nav.group.path"], "主路径");
  assert.equal(messages.en["nav.group.path"], "Main path");
  assert.equal(messages.zh["nav.group.ops"], "运维");
  assert.match(app, /path="\/experiments"/);
  assert.match(app, /ExperimentsPage/);
  assert.match(app, /path="\/prepare"/);
  assert.match(app, /PreparePage/);
  assert.match(app, /Navigate to="\/prepare\?tab=pools"/);
  assert.match(app, /Navigate to="\/prepare\?tab=harnesses"/);
  assert.doesNotMatch(app, /Navigate to="\/rounds" replace/);
  assert.match(app, /path="\/results"/);
  assert.match(app, /path="\/doctor"/);
  assert.match(prepare, /data-testid="prepare-tabs"/);
  assert.match(prepare, /data-testid="prepare-tab-pools"/);
  assert.match(prepare, /data-testid="prepare-tab-harnesses"/);
  assert.match(prepare, /object-tabs/);
  assert.match(prepare, /object-tab/);
  assert.match(prepare, /PoolsPage embedded/);
  assert.match(prepare, /HarnessesPage embedded/);

  const overview = readFileSync(resolve(root, "pages/Overview.tsx"), "utf8");
  const rounds = readFileSync(resolve(root, "pages/Rounds.tsx"), "utf8");
  const results = readFileSync(resolve(root, "pages/Results.tsx"), "utf8");
  const slotPicker = readFileSync(resolve(root, "pages/ComparisonSlots.tsx"), "utf8");
  assert.match(overview, /to="\/doctor"/);
  assert.match(overview, /overview\.doctorOpen/);
  assert.doesNotMatch(rounds, /ExperimentCatalog/);
  assert.doesNotMatch(rounds, /to="\/results"/);
  assert.doesNotMatch(rounds, /rounds\.openReport/);
  assert.doesNotMatch(rounds, /data-testid="tester-path"/);
  assert.doesNotMatch(rounds, /id="prepare"/);
  assert.doesNotMatch(rounds, /id="observation"/);
  assert.doesNotMatch(rounds, /id="report"/);
  assert.doesNotMatch(rounds, /rounds\.start/);
  assert.doesNotMatch(rounds, /rounds\.tick/);
  assert.match(rounds, /data-testid="candidate-plans"/);
  assert.match(rounds, /to="\/prepare\?tab=pools"/);
  assert.match(rounds, /to="\/prepare\?tab=harnesses"/);
  assert.doesNotMatch(rounds, /to="\/pools"/);
  assert.doesNotMatch(rounds, /to="\/harnesses"/);
  assert.match(rounds, /data-testid="go-monitor"/);
  assert.match(rounds, /\/monitor\?plan=/);
  assert.match(rounds, /data-testid="identity-scenario"/);
  assert.match(rounds, /data-testid="identity-industry"/);
  assert.match(rounds, /data-testid="identity-mode"/);
  assert.doesNotMatch(rounds, /mode-card/);
  assert.match(rounds, /rounds\.draftLead/);
  assert.match(rounds, /rounds\.slotsLead/);
  assert.doesNotMatch(slotPicker, /rounds\.slotsLead/);
  assert.match(results, /to="\/rounds"/);
  assert.doesNotMatch(rounds, /live:\s*true/);
});

test("the boot script uses the same storage key and titles", () => {
  const htmlPath = resolve(dirname(fileURLToPath(import.meta.url)), "../../index.html");
  const html = readFileSync(htmlPath, "utf8");
  assert.ok(html.includes(LOCALE_STORAGE_KEY));
  assert.ok(html.includes(messages.zh["doc.title"]));
  assert.ok(html.includes(messages.en["doc.title"]));
});
