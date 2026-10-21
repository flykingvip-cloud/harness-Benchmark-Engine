import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { messages } from "./i18n/messages.ts";
import {
  bestColumnIds,
  columnsFromMetrics,
  columnsFromPanes,
  eventsThrough,
  activeDirectoryPane,
  directoryPanes,
  pairedHarnessIds,
  formatStamp,
  observationPanes,
  observationTurns,
  paneRecorder,
  phaseRank,
  sameSlotIds,
  sessionPathIndex,
  showFixtureFit,
  splitTokens,
  visibleDimensions,
} from "./rounds.ts";
import type { ObservationEvent } from "./api.ts";

function event(partial: Partial<ObservationEvent> & Pick<ObservationEvent, "kind" | "pane_id">): ObservationEvent {
  return {
    job_id: "job",
    seq: 0,
    model_id: partial.pane_id === "dispatch" ? null : partial.pane_id,
    harness_id: "openbot",
    task_id: "echo--easy",
    message: partial.kind,
    recorded_at: "2026-10-09T12:00:00.000000Z",
    ...partial,
  };
}

const timeline = [
  event({ kind: "queued", pane_id: "dispatch", seq: 0 }),
  event({ kind: "dispatching", pane_id: "dispatch", seq: 1 }),
  event({ kind: "step", pane_id: "openai/gpt-test", seq: 2 }),
  event({ kind: "step", pane_id: "openai/gpt-test-mini", seq: 3 }),
  event({ kind: "sealed", pane_id: "dispatch", seq: 4 }),
];

test("dispatcher phases reveal events in order", () => {
  assert.equal(phaseRank("step"), phaseRank("observing"));
  assert.deepEqual(
    eventsThrough(timeline, "queued").map((item) => item.kind),
    ["queued"],
  );
  assert.deepEqual(
    eventsThrough(timeline, "observing").map((item) => item.kind),
    ["queued", "dispatching", "step", "step"],
  );
  assert.equal(eventsThrough(timeline, "sealed").length, timeline.length);
  assert.deepEqual(eventsThrough(timeline, "missing"), []);
});

test("observation panes are one per model under the same harness and task", () => {
  const panes = observationPanes(eventsThrough(timeline, "sealed"));
  assert.deepEqual(
    panes.map((pane) => pane.paneId),
    ["openai/gpt-test", "openai/gpt-test-mini"],
  );
  assert.equal(new Set(panes.map((pane) => pane.harnessId)).size, 1);
  assert.equal(new Set(panes.map((pane) => pane.taskId)).size, 1);
  assert.equal(panes[0]?.harnessId, "openbot");
});

test("paired directory plans one pane per multi reference from labels", () => {
  const panes = directoryPanes(
    {
      mode: "paired_harness",
      subjectHarnessId: "openbot",
      referenceHarnessId: "inspect-react",
      referenceHarnessIds: ["inspect-react", "openclaw", "workbuddy"],
      modelIds: ["openai/gpt-test"],
      taskId: "echo--easy",
      slotIds: [],
    },
    [],
  );
  assert.deepEqual(
    panes.map((pane) => pane.paneId),
    ["openbot", "inspect-react", "openclaw", "workbuddy"],
  );
  assert.equal(panes.every((pane) => pane.origin === "planned"), true);
  assert.equal(panes.every((pane) => pane.modelId === "openai/gpt-test"), true);
  assert.deepEqual(
    pairedHarnessIds({
      subjectHarnessId: "openbot",
      referenceHarnessId: "inspect-react",
      referenceHarnessIds: [],
    }),
    ["openbot", "inspect-react"],
  );
});

test("the observation directory is harness rows or model rows and keeps pane identity", () => {
  const pairedInput = {
    mode: "paired_harness" as const,
    subjectHarnessId: "openbot",
    referenceHarnessId: "inspect-react",
    modelIds: ["openai/gpt-test", "openai/gpt-test-mini"],
    taskId: "echo--easy",
    slotIds: ["vllm/a"],
  };
  const pairedEvents = [
    event({ kind: "queued", pane_id: "dispatch", seq: 0, model_id: null, message: "Dispatcher queued. No model call is placed." }),
    event({ kind: "dispatching", pane_id: "dispatch", seq: 1, model_id: null, message: "Panes assigned. Spend stays none." }),
    event({
      kind: "step",
      pane_id: "openbot",
      seq: 2,
      model_id: "openai/gpt-test",
      harness_id: "openbot",
      message: "Fixture step for pane openbot. Spend none.",
    }),
    event({
      kind: "step",
      pane_id: "inspect-react",
      seq: 3,
      model_id: "openai/gpt-test",
      harness_id: "inspect-react",
      message: "latency: 12ms",
    }),
    event({
      kind: "sealed",
      pane_id: "dispatch",
      seq: 4,
      model_id: null,
      message: "Round sealed offline.",
      recorded_at: "2026-10-09T12:00:05.000000Z",
    }),
  ];
  const waiting = directoryPanes(pairedInput, eventsThrough(pairedEvents, "dispatching"));
  assert.deepEqual(
    waiting.map((pane) => pane.paneId),
    ["openbot", "inspect-react"],
  );
  assert.equal(waiting[0]?.origin, "planned");
  assert.equal(waiting[0]?.modelId, "openai/gpt-test");
  assert.equal(waiting[0]?.lines.length, 0);
  assert.equal(waiting.every((pane) => pane.paneId !== "vllm/a"), true);

  const revealed = directoryPanes(pairedInput, eventsThrough(pairedEvents, "observing"));
  assert.deepEqual(
    revealed.map((pane) => [pane.paneId, pane.origin, pane.lines.length]),
    [
      ["openbot", "event", 1],
      ["inspect-react", "event", 1],
    ],
  );
  assert.equal(new Set(revealed.map((pane) => pane.modelId)).size, 1);

  const openbot = revealed[0];
  assert.ok(openbot);
  const earlyRecord = paneRecorder(openbot, eventsThrough(pairedEvents, "dispatching"), "dispatching");
  assert.equal(earlyRecord.phase, "dispatching");
  assert.equal(earlyRecord.latency, null);
  assert.equal(earlyRecord.cost, null);
  assert.equal(earlyRecord.spend, "none");
  assert.equal(earlyRecord.executed, false);
  assert.equal(formatStamp(earlyRecord.firstAt), "2026-10-09 12:00:00");

  const laterRecord = paneRecorder(openbot, eventsThrough(pairedEvents, "sealed"), "sealed");
  assert.equal(laterRecord.cost, null);
  assert.equal(formatStamp(laterRecord.lastAt), "2026-10-09 12:00:05");
  assert.notEqual(laterRecord.lastAt, earlyRecord.lastAt);
  assert.notEqual(laterRecord.phase, earlyRecord.phase);

  const partner = revealed[1];
  assert.ok(partner);
  assert.equal(paneRecorder(partner, eventsThrough(pairedEvents, "observing"), "observing").latency, "12ms");
  assert.deepEqual(
    observationTurns(openbot.paneId, eventsThrough(pairedEvents, "observing")).map((turn) => turn.role),
    ["dispatch", "dispatch", "pane"],
  );
  assert.equal(activeDirectoryPane("inspect-react", revealed)?.paneId, "inspect-react");
  assert.equal(activeDirectoryPane("missing", revealed)?.paneId, "openbot");
  assert.equal(activeDirectoryPane("x", []), null);

  const slotsOnly = directoryPanes(
    {
      mode: "cross_model",
      subjectHarnessId: "openbot",
      referenceHarnessId: null,
      modelIds: [],
      taskId: null,
      slotIds: ["vllm/a", "ollama/b"],
    },
    [],
  );
  assert.deepEqual(
    slotsOnly.map((pane) => [pane.paneId, pane.origin]),
    [
      ["vllm/a", "slot"],
      ["ollama/b", "slot"],
    ],
  );
  const fixtureModels = directoryPanes(
    {
      mode: "cross_model",
      subjectHarnessId: "openbot",
      referenceHarnessId: null,
      modelIds: ["openai/gpt-test", "openai/gpt-test-mini"],
      taskId: "echo--easy",
      slotIds: ["vllm/a", "ollama/b"],
    },
    [],
  );
  assert.deepEqual(
    fixtureModels.map((pane) => pane.paneId),
    ["openai/gpt-test", "openai/gpt-test-mini"],
  );
  assert.equal(fixtureModels[0]?.origin, "planned");
  assert.equal(fixtureModels[0]?.harnessId, "openbot");

  const priced = paneRecorder(
    { paneId: "openai/gpt-test", modelId: "openai/gpt-test", harnessId: "openbot", taskId: "echo--easy" },
    [event({ kind: "step", pane_id: "openai/gpt-test", seq: 2, message: "cost: 0.0009" })],
    "observing",
  );
  assert.equal(priced.cost, "0.0009");
  assert.equal(priced.spend, "none");
  assert.equal(formatStamp(null), null);
  assert.equal(formatStamp(""), null);
});

test("five-dimension compare ignores missing values and can hide identical rows", () => {
  const columns = columnsFromMetrics([
    {
      model_id: "vllm/a",
      harness_id: "openbot",
      task_class: "echo",
      success_rate: "0.5",
      valid_count: 2,
      cost_per_success: null,
      steps: 4,
      usage_status: "present",
    },
    {
      model_id: "ollama/b",
      harness_id: "openbot",
      task_class: "echo",
      success_rate: "0.9",
      valid_count: 2,
      cost_per_success: "0.2",
      steps: 4,
      usage_status: "missing",
    },
  ]);
  assert.equal(columns[0]?.values.cost.score, null);
  assert.equal(columns[0]?.values.cost.label, "—");
  assert.equal(bestColumnIds(columns, "cost").size, 0);
  assert.deepEqual([...bestColumnIds(columns, "effectiveness")], [`openbot:ollama/b:echo`]);
  assert.equal(bestColumnIds(columns, "validity").size, 0);
  assert.deepEqual([...bestColumnIds(columns, "diagnosability")], ["openbot:vllm/a:echo"]);
  assert.ok(visibleDimensions(columns, true).includes("effectiveness"));
  assert.equal(visibleDimensions(columns, true).includes("validity"), false);
  assert.equal(visibleDimensions(columns, true).includes("process"), false);
  const empty = columnsFromPanes([{ paneId: "pane-a", modelId: "vllm/a" }]);
  assert.equal(empty[0]?.values.cost.label, "—");
  assert.equal(bestColumnIds(empty, "cost").size, 0);
});

test("taxonomy tokens keep first-seen order", () => {
  assert.deepEqual(splitTokens(" echo, exact，echo , match "), ["echo", "exact", "match"]);
  assert.deepEqual(splitTokens(""), []);
});

test("candidate plans are named in both languages and the editor starts closed", () => {
  assert.equal(messages.zh["rounds.plans"], "候选方案");
  assert.equal(messages.en["rounds.plans"], "Candidate plans");
  assert.equal(messages.zh["rounds.addPlan"], "新建方案");
  assert.equal(messages.en["rounds.addPlan"], "New plan");
  assert.equal(messages.zh["rounds.tab.harness"], "评测 Harness");
  assert.equal(messages.en["rounds.tab.model"], "Evaluate models");
  assert.equal(messages.zh["rounds.filter.industry"], "行业");
  assert.equal(messages.zh["rounds.industry.finance"], "金融");
  assert.doesNotMatch(messages.zh["rounds.plans"], /已有方案/);
  const source = readFileSync(new URL("./pages/Rounds.tsx", import.meta.url), "utf8");
  const headerAt = source.indexOf('data-testid="candidate-plans"');
  const editorAt = source.indexOf('data-testid="plan-editor"');
  assert.ok(headerAt >= 0 && editorAt > headerAt);
  const header = source.slice(Math.max(0, headerAt - 80), editorAt);
  assert.match(header, /data-testid="object-tabs"/);
  assert.match(header, /className="object-tab"/);
  assert.match(header, /rounds\.addPlan/);
  assert.match(header, /data-testid="add-plan"/);
  assert.match(header, /data-testid="plan-filters"/);
  assert.match(source, /useState<string \| null>\(null\)/);
  assert.match(source, /useState<PlanSession \| null>\(null\)/);
  assert.match(source, /showSession && session \?/);
  assert.match(source, /data-testid="plan-session"/);
  assert.match(source, /data-plan-id=\{session\.planId \?\? ""\}/);
  assert.match(source, /key=\{session\.planId \?\? "composer"\}/);
  assert.match(source, /data-testid="candidate-plan"/);
  assert.match(source, /data-testid="section-tasks"/);
  assert.match(source, /data-testid="section-compare"/);
  assert.doesNotMatch(source, /data-testid="observation-slot-preview"/);
  assert.match(source, /data-testid="generate-draft"/);
  const identityAt = source.indexOf('data-testid="section-identity"');
  assert.ok(identityAt > editorAt);
  assert.match(source, /data-testid="identity-fields"/);
  assert.match(source, /data-testid="identity-scenario"/);
  assert.match(source, /data-testid="identity-industry"/);
  assert.match(source, /data-testid="identity-mode"/);
  assert.doesNotMatch(source, /role="radiogroup"/);
  assert.doesNotMatch(source, /mode-card/);
  assert.doesNotMatch(source, /mode-grid/);
  assert.match(source, /data-testid="confirmed-handoff"/);
  assert.match(source, /data-testid="frozen-summary"/);
  assert.match(source, /\/monitor\?plan=/);
  assert.doesNotMatch(source, /showFixtureFit/);
  assert.doesNotMatch(source, /id="observation"/);
  assert.doesNotMatch(source, /id="report"/);
  assert.doesNotMatch(source, /id="prepare"/);
  assert.doesNotMatch(source, /data-testid="tester-path"/);
  assert.doesNotMatch(source, /ExperimentCatalog/);
  assert.doesNotMatch(source, /rounds\.start/);
  assert.doesNotMatch(source, /rounds\.tick/);
  assert.match(readFileSync(new URL("./rounds.ts", import.meta.url), "utf8"), /fixture-cross-model-report/);
  assert.doesNotMatch(source, /routes\.comparisonSlots/);
  assert.doesNotMatch(source, /routes\.modelFit/);
  assert.doesNotMatch(source, /已有方案/);
});

test("paired compare is subject+reference selects without slot chrome", () => {
  assert.equal(messages.zh["rounds.reference.placeholder"], "选择参照（可多选）");
  assert.equal(messages.en["rounds.reference.placeholder"], "Select reference (multi)");
  assert.equal(messages.zh["rounds.reference.selected"], "已选参照：{{ids}}");
  assert.equal(messages.en["rounds.reference.empty"], "No references selected");
  const source = readFileSync(new URL("./pages/Rounds.tsx", import.meta.url), "utf8");
  const compare = source.slice(source.indexOf('data-testid="section-compare"'), source.indexOf('data-testid="section-summary"'));
  assert.match(compare, /data-testid="compare-fields"/);
  assert.match(compare, /data-testid="compare-subject"/);
  assert.match(compare, /data-testid="compare-reference"/);
  assert.match(compare, /data-testid="reference-selection"/);
  assert.match(compare, /toggleToken\(session\.classification\.referenceHarnessIds/);
  assert.doesNotMatch(compare, /chip-row/);
  assert.doesNotMatch(compare, /filter-row/);
  assert.doesNotMatch(compare, /observation-slot-preview/);
  assert.doesNotMatch(compare, /rounds\.observationPreview/);
  // Slot join UI stays for cross_model only (fixedHarness branch).
  const paired = compare.slice(0, compare.indexOf("rounds.fixedHarness"));
  assert.doesNotMatch(paired, /ComparisonSlots/);
  assert.doesNotMatch(paired, /rounds\.slotsLead/);
  assert.doesNotMatch(paired, /rounds\.recommend/);
  assert.doesNotMatch(paired, /rounds\.addSlot/);
  assert.doesNotMatch(paired, /data-testid="plan-pool"/);
  assert.match(compare, /ComparisonSlots/);
  assert.match(compare, /rounds\.slotsLead/);
});

test("create identity uses compact selects instead of chips and mode cards", () => {
  assert.equal(messages.zh["rounds.scenario.placeholder"], "选择场景（可多选）");
  assert.equal(messages.en["rounds.scenario.placeholder"], "Select scenario (multi)");
  const source = readFileSync(new URL("./pages/Rounds.tsx", import.meta.url), "utf8");
  const identity = source.slice(source.indexOf('data-testid="section-identity"'), source.indexOf('data-testid="section-requirements"'));
  assert.match(identity, /data-testid="identity-scenario"/);
  assert.match(identity, /data-testid="identity-industry"/);
  assert.match(identity, /data-testid="identity-mode"/);
  assert.match(identity, /toggleToken\(session\.classification\.scenarioTags/);
  assert.doesNotMatch(identity, /chip-row/);
  assert.doesNotMatch(identity, /mode-card/);
  assert.doesNotMatch(identity, /filter-row/);
  assert.match(identity, /rounds\.mode\.pairedNote/);
  assert.match(identity, /rounds\.mode\.crossNote/);
});

test("the path bar and fixture fit follow the active plan only", () => {
  assert.equal(sessionPathIndex(null), 1);
  assert.equal(sessionPathIndex({ job: null, report: null }), 1);
  assert.equal(sessionPathIndex({ job: { job_id: "job-a" }, report: null }), 2);
  assert.equal(sessionPathIndex({ job: { job_id: "job-a" }, report: { report_id: "report-a" } }), 3);
  assert.equal(showFixtureFit("fixture-cross-model-report"), true);
  assert.equal(showFixtureFit("fixture-paired"), false);
  assert.equal(showFixtureFit(null), false);
  assert.equal(sameSlotIds(["a"], ["a"]), true);
  assert.equal(sameSlotIds(["a"], ["b"]), false);
});

test("round copy compares models under one harness in both languages", () => {
  for (const locale of ["zh", "en"] as const) {
    const fit = messages[locale]["rounds.fitLead"];
    const panes = messages[locale]["rounds.panesLead"];
    assert.match(fit, /Harness|harness/);
    assert.match(panes, /Harness|harness/);
    assert.match(fit, /竞技场|leaderboard/);
    assert.match(messages[locale]["rounds.mode.crossNote"], /排行榜|leaderboard/);
    assert.match(messages[locale]["chart.intervalCaption"], /排名|ranking/);
  }
  assert.match(messages.zh["rounds.fitLead"], /固定 Harness/);
  assert.match(messages.en["rounds.fitLead"], /one fixed harness/);
  assert.equal(messages.zh["nav.rounds"], "评测/方案");
  assert.equal(messages.en["nav.rounds"], "Evaluation");
  assert.equal(messages.zh["rounds.title"], "评测/方案");
  assert.equal(messages.en["rounds.title"], "Evaluation");
  assert.equal(messages.zh["rounds.slots"], "对比槽");
  assert.match(messages.en["rounds.panes"], /Observation/);
  assert.equal(Object.hasOwn(messages.zh, "rounds.start"), false);
  assert.equal(Object.hasOwn(messages.en, "rounds.start"), false);
  assert.equal(Object.hasOwn(messages.zh, "rounds.tick"), false);
  assert.equal(Object.hasOwn(messages.en, "rounds.tick"), false);
  assert.equal(Object.hasOwn(messages.zh, "rounds.dispatcher"), false);
  assert.equal(Object.hasOwn(messages.en, "rounds.dispatcher"), false);
  assert.equal(messages.zh["rounds.confirmedTitle"], "套件已确认 · 槽位已冻结");
  assert.equal(messages.en["rounds.confirmedTitle"], "Suite confirmed · slots frozen");
  assert.match(messages.zh["rounds.confirmedHint"], /监测/);
  assert.match(messages.zh["rounds.confirmedHint"], /报告/);
  assert.match(messages.zh["rounds.lead"], /不下发/);
  assert.match(messages.zh["rounds.dispatchLead"], /不解锁花费/);
  assert.match(messages.en["rounds.dispatchLead"], /does not unlock spend/);
  assert.match(messages.zh["rounds.panesLead"], /不是会话管理器/);
  assert.match(messages.en["rounds.panesLead"], /not a session manager/);
  assert.match(messages.zh["rounds.spendBanner"], /spend:none/);
  assert.match(messages.zh["rounds.spendBanner"], /executed:false/);
  assert.match(messages.en["rounds.placeholderNote"], /Missing is not zero/);
  assert.equal(messages.zh["rounds.turn.dispatch"], "调度");
  assert.equal(messages.zh["rounds.turn.pane"], "过程");
  const shell = readFileSync(new URL("./ObservationShell.tsx", import.meta.url), "utf8");
  const source = readFileSync(new URL("./pages/Rounds.tsx", import.meta.url), "utf8");
  const monitor = readFileSync(new URL("./pages/Monitor.tsx", import.meta.url), "utf8");
  assert.match(shell, /data-testid="observation-shell"/);
  assert.match(shell, /data-testid="observation-directory"/);
  assert.match(shell, /data-testid="observation-recorder"/);
  assert.match(shell, /data-testid="observation-stream"/);
  assert.match(shell, /data-testid="spend-none-banner"/);
  assert.match(shell, /data-testid="dispatch-round"/);
  assert.match(shell, /directoryPanes/);
  assert.match(monitor, /data-testid="dispatch-suite"/);
  assert.match(monitor, /ObservationShell/);
  assert.match(monitor, /live: false/);
  assert.match(monitor, /data-testid="suite-details"/);
  assert.match(monitor, /data-testid="next-task"/);
  assert.match(monitor, /data-testid="auto-switch"/);
  assert.match(shell, /mode === "cross_model"/);
  assert.match(monitor, /mode=\{active\?\.mode/);
  assert.doesNotMatch(source, /ObservationShell/);
  assert.match(source, /data-testid="go-monitor"/);
  assert.doesNotMatch(source, /data-testid="dimension-compare"/);
  assert.match(source, /live: false/);
  assert.doesNotMatch(shell, /live:\s*true/);
  assert.doesNotMatch(monitor, /live:\s*true/);
  assert.doesNotMatch(source, /OHBE_ALLOW_LIVE_SPEND/);
  assert.doesNotMatch(monitor, /OHBE_ALLOW_LIVE_SPEND/);
  assert.equal(messages.zh["rounds.openReport"], "打开报告");
  assert.equal(messages.en["rounds.openReport"], "Open report");
  assert.match(messages.zh["rounds.path.planHint"], /确认后冻结/);
  assert.match(messages.en["rounds.path.planHint"], /freeze on confirm/);
  assert.match(messages.zh["rounds.path.observeHint"], /缺失不是零/);
  assert.match(messages.en["rounds.path.observeHint"], /Missing is not zero/);
  assert.match(messages.zh["rounds.slotsLead"], /冻结/);
  assert.match(messages.en["rounds.compareLead"], /not zero/);
  assert.match(messages.zh["rounds.stopBudget"], /不解锁花费/);
  assert.match(messages.en["rounds.stopBudget"], /does not unlock spend/);
  assert.match(messages.zh["rounds.slotsEmpty"], /模型池/);
  assert.match(messages.en["rounds.slotsEmpty"], /Model Pool/);
  assert.doesNotMatch(messages.zh["rounds.slotEmpty"], /入库/);
  assert.doesNotMatch(messages.en["rounds.slotsEmpty"], /Model Intake/);
});

test("fixture model-fit blurbs are short catalog copy in both languages", () => {
  const keys = [
    "rounds.fitBlurb.validity",
    "rounds.fitBlurb.effectiveness",
    "rounds.fitBlurb.process",
    "rounds.fitBlurb.cost",
    "rounds.fitBlurb.diagnosability",
    "rounds.fitFindings",
    "rounds.fitFrontier",
  ] as const;
  const chinese = /[\u4e00-\u9fff]/;
  for (const key of keys) {
    const zh = messages.zh[key];
    const en = messages.en[key];
    assert.match(zh, chinese, key);
    assert.equal(chinese.test(en), false, key);
    assert.ok(zh.length <= 140, key);
    assert.ok(en.length <= 280, key);
  }
  assert.match(messages.zh["rounds.fitBlurb.validity"], /ohbe-egress-0\.1/);
  assert.match(messages.zh["rounds.fitBlurb.validity"], /未记录出口违规/);
  assert.match(messages.en["rounds.fitBlurb.validity"], /No egress violation/);
  assert.match(messages.zh["rounds.fitBlurb.effectiveness"], /不是配对 Harness 差值/);
  assert.match(messages.en["rounds.fitBlurb.effectiveness"], /Not a paired harness delta/);
  assert.match(messages.zh["rounds.fitBlurb.process"], /不是实况工具轨迹/);
  assert.match(messages.en["rounds.fitBlurb.process"], /not a live tool trace/);
  assert.match(messages.zh["rounds.fitBlurb.cost"], /0\.0009/);
  assert.match(messages.zh["rounds.fitBlurb.cost"], /0\.00045/);
  assert.match(messages.zh["rounds.fitBlurb.cost"], /不按零计价/);
  assert.match(messages.en["rounds.fitBlurb.cost"], /not priced as zero/);
  assert.match(messages.zh["rounds.fitBlurb.diagnosability"], /routing_optimality/);
  assert.match(messages.en["rounds.fitFindings"], /not a live measurement/);
  assert.match(messages.zh["rounds.fitFindings"], /不是实况测量/);
  assert.match(messages.zh["rounds.fitFindings"], /echo--easy/);
  assert.match(messages.en["rounds.fitFrontier"], /not a model-arena leaderboard or harness ranking/);
  assert.match(messages.zh["rounds.fitFrontier"], /不是模型竞技场排行榜，也不是 Harness 排名/);
  assert.doesNotMatch(messages.zh["rounds.fitFindings"], /unlock|解锁花费/);
  assert.doesNotMatch(messages.en["rounds.fitFrontier"], /unlock spend/);
});
