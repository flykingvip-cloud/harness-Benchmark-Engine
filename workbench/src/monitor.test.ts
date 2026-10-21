import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import type { ObservationEvent } from "./api.ts";
import {
  AUTO_ADVANCE_INTERVALS_MS,
  DEFAULT_AUTO_ADVANCE_MS,
  autoRhythm,
  executionLines,
  fanOutTurns,
  isMonitorable,
  isPaneFailure,
  manualSkipTurns,
  normalizeAutoInterval,
  paneOutcome,
  panesReadyForAdvance,
  shouldAutoAdvance,
  suiteTaskViews,
  taskComposerText,
  type LocalTurn,
} from "./monitor.ts";
import { resultsHandoffPath } from "./results.ts";
import { directoryPanes } from "./rounds.ts";

function step(paneId: string, message: string, taskId = "suite.task_01"): ObservationEvent {
  return {
    job_id: "job",
    seq: 2,
    pane_id: paneId,
    model_id: paneId,
    harness_id: "openbot",
    task_id: taskId,
    kind: "step",
    message,
    recorded_at: "2026-10-12T10:07:00.000Z",
  };
}

test("monitor lists confirmed, running, and sealed plans only", () => {
  assert.equal(isMonitorable("confirmed_suite"), true);
  assert.equal(isMonitorable("running"), true);
  assert.equal(isMonitorable("round_report"), true);
  assert.equal(isMonitorable("draft_plan"), false);
  assert.equal(isMonitorable("fork_plan"), false);
});

test("suite tasks stay in suite order and start collapsed-ready", () => {
  const tasks = [{ task_id: "suite.task_01" }, { task_id: "suite.task_02" }, { task_id: "suite.task_03" }];
  const waiting = suiteTaskViews(tasks, 0, false);
  assert.deepEqual(
    waiting.map((task) => [task.index, task.task_id, task.status]),
    [
      [1, "suite.task_01", "queued"],
      [2, "suite.task_02", "queued"],
      [3, "suite.task_03", "queued"],
    ],
  );
  const current = suiteTaskViews(tasks, 1, true);
  assert.deepEqual(
    current.map((task) => task.status),
    ["done", "current", "queued"],
  );
});

test("task switch fills the composer and fans out to every paired harness pane", () => {
  const panes = directoryPanes(
    {
      mode: "paired_harness",
      subjectHarnessId: "openbot",
      referenceHarnessId: "inspect-react",
      referenceHarnessIds: ["inspect-react", "openclaw"],
      modelIds: ["openai/gpt-test"],
      taskId: "suite.task_02",
      slotIds: [],
    },
    [],
  );
  const text = taskComposerText("任务2", "suite.task_02", "Say the suite task.");
  assert.match(text, /^任务2 suite\.task_02\nSay the suite task\.$/);
  const turns = fanOutTurns([], panes, "suite.task_02", text, "2026-10-12T10:08:00.000Z");
  assert.deepEqual(
    turns.map((turn) => [turn.paneId, turn.role, turn.taskId]),
    [
      ["openbot", "dispatch", "suite.task_02"],
      ["inspect-react", "dispatch", "suite.task_02"],
      ["openclaw", "dispatch", "suite.task_02"],
    ],
  );
  assert.equal(turns[0]?.message, text);
  assert.equal(new Set(turns.map((turn) => turn.id)).size, 3);
});

test("cross_model fans the same task out to model panes under one harness", () => {
  const panes = directoryPanes(
    {
      mode: "cross_model",
      subjectHarnessId: "openbot",
      referenceHarnessId: null,
      modelIds: ["openai/gpt-test", "openai/gpt-test-mini"],
      taskId: "suite.task_02",
      slotIds: ["vllm/a", "ollama/b"],
    },
    [],
  );
  assert.equal(panes.every((pane) => pane.harnessId === "openbot"), true);
  const turns = fanOutTurns([], panes, "suite.task_02", "body", "2026-10-12T10:08:00.000Z");
  assert.deepEqual(
    turns.map((turn) => turn.paneId),
    ["openai/gpt-test", "openai/gpt-test-mini"],
  );
  assert.equal(turns.every((turn) => turn.role === "dispatch"), true);
});

test("auto-advance waits for every pane stub and a failure skip does not block", () => {
  const focus = "suite.task_01";
  const events = [step("openbot", "Fixture step for pane openbot. Spend none.", focus), step("inspect-react", "error: timeout", focus)];
  const openbot = paneOutcome({
    paneId: "openbot",
    taskId: focus,
    focusTaskId: focus,
    events,
    local: [],
    allowUnmeasured: false,
  });
  const failed = paneOutcome({
    paneId: "inspect-react",
    taskId: focus,
    focusTaskId: focus,
    events,
    local: [],
    allowUnmeasured: false,
  });
  const missing = paneOutcome({
    paneId: "other",
    taskId: focus,
    focusTaskId: focus,
    events,
    local: [],
    allowUnmeasured: false,
  });
  assert.equal(openbot.outcome, "result");
  assert.equal(openbot.kind, "focus_replay");
  assert.equal(failed.outcome, "skip");
  assert.equal(failed.kind, "failed_skip");
  assert.equal(missing.outcome, "pending");
  assert.equal(isPaneFailure("Fixture step. Spend none."), false);
  assert.equal(
    shouldAutoAdvance({
      auto: true,
      paused: false,
      taskIndex: 0,
      taskCount: 3,
      outcomes: [openbot.outcome, failed.outcome],
    }),
    true,
  );
  assert.equal(
    shouldAutoAdvance({
      auto: true,
      paused: false,
      taskIndex: 0,
      taskCount: 3,
      outcomes: [openbot.outcome, missing.outcome],
    }),
    false,
  );
  assert.equal(
    shouldAutoAdvance({
      auto: true,
      paused: true,
      taskIndex: 0,
      taskCount: 3,
      outcomes: ["result", "skip"],
    }),
    false,
  );
  assert.equal(
    shouldAutoAdvance({
      auto: false,
      paused: false,
      taskIndex: 0,
      taskCount: 3,
      outcomes: ["result", "result"],
    }),
    false,
  );
  assert.equal(
    shouldAutoAdvance({
      auto: true,
      paused: false,
      taskIndex: 2,
      taskCount: 3,
      outcomes: ["result"],
    }),
    false,
  );
});

test("a later task stays unmeasured until a tick, and the result is a stub summary", () => {
  const local: LocalTurn[] = [
    {
      id: "dispatch:suite.task_02:openbot:1",
      paneId: "openbot",
      taskId: "suite.task_02",
      role: "dispatch",
      message: "filled",
      recordedAt: "2026-10-12T10:08:00.000Z",
    },
  ];
  const waiting = paneOutcome({
    paneId: "openbot",
    taskId: "suite.task_02",
    focusTaskId: "suite.task_01",
    events: [step("openbot", "Fixture step", "suite.task_01")],
    local,
    allowUnmeasured: false,
  });
  assert.equal(waiting.outcome, "pending");
  const ready = paneOutcome({
    paneId: "openbot",
    taskId: "suite.task_02",
    focusTaskId: "suite.task_01",
    events: [step("openbot", "Fixture step", "suite.task_01")],
    local,
    allowUnmeasured: true,
  });
  assert.deepEqual(ready, { outcome: "result", kind: "unmeasured" });
  const lines = executionLines(
    "openbot",
    [step("openbot", "Fixture step for pane openbot. Spend none.", "suite.task_01")],
    [
      ...fanOutTurns([], [{ paneId: "openbot" }], "suite.task_01", "task one", "2026-10-12T10:01:00.000Z"),
      {
        id: "result:suite.task_01:openbot:1",
        paneId: "openbot",
        taskId: "suite.task_01",
        role: "result",
        message: "焦点任务桩回放。未测量全套件。",
        recordedAt: "2026-10-12T10:09:00.000Z",
      },
    ],
    "suite.task_01",
  );
  assert.deepEqual(
    lines.map((line) => line.role),
    ["dispatch", "process", "result"],
  );
  assert.match(lines[2]?.message ?? "", /未测量全套件/);
  assert.doesNotMatch(lines.map((line) => line.message).join("\n"), /validity|effectiveness|diagnosability/);
});

test("sealed monitor handoff builds /results with plan and report_id", () => {
  assert.equal(
    resultsHandoffPath({ planId: "fixture-cross-model", reportId: "fixture-cross-model-report" }),
    "/results?plan=fixture-cross-model&report_id=fixture-cross-model-report",
  );
  assert.equal(resultsHandoffPath({ planId: "fixture-paired", reportId: null }), "/results?plan=fixture-paired");
});

test("the monitor page links sealed rounds to the report handoff URL", () => {
  const page = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "pages/Monitor.tsx"), "utf8");
  assert.match(page, /resultsHandoffPath/);
  assert.match(page, /data-testid="monitor-report-handoff"/);
  assert.match(page, /planId:\s*active\.planId/);
  assert.match(page, /reportId:\s*active\.reportId/);
  assert.doesNotMatch(page, /DimensionBoard/);
  assert.doesNotMatch(page, /live:\s*true/);
  assert.doesNotMatch(page, /OHBE_ALLOW_LIVE_SPEND/);
});

test("auto rhythm exposes waiting, countdown, pause, and interval choices", () => {
  assert.deepEqual(AUTO_ADVANCE_INTERVALS_MS, [1000, 2000, 3000]);
  assert.equal(DEFAULT_AUTO_ADVANCE_MS, 2000);
  assert.equal(normalizeAutoInterval(2000), 2000);
  assert.equal(normalizeAutoInterval(999), 2000);
  assert.deepEqual(
    autoRhythm({ auto: false, paused: false, readyToAdvance: true, taskIndex: 0, taskCount: 3, remainingMs: 1000 }),
    { kind: "off" },
  );
  assert.deepEqual(
    autoRhythm({ auto: true, paused: true, readyToAdvance: true, taskIndex: 0, taskCount: 3, remainingMs: 1000 }),
    { kind: "paused" },
  );
  assert.deepEqual(
    autoRhythm({ auto: true, paused: false, readyToAdvance: false, taskIndex: 0, taskCount: 3, remainingMs: null }),
    { kind: "waiting" },
  );
  assert.deepEqual(
    autoRhythm({ auto: true, paused: false, readyToAdvance: true, taskIndex: 0, taskCount: 3, remainingMs: 1500 }),
    { kind: "countdown", remainingMs: 1500, nextIndex: 1 },
  );
  assert.deepEqual(
    autoRhythm({ auto: true, paused: false, readyToAdvance: true, taskIndex: 2, taskCount: 3, remainingMs: 500 }),
    { kind: "complete" },
  );
});

test("manual skip settles pending panes without inventing metrics", () => {
  const local: LocalTurn[] = [
    {
      id: "dispatch:suite.task_01:openbot:1",
      paneId: "openbot",
      taskId: "suite.task_01",
      role: "dispatch",
      message: "filled",
      recordedAt: "2026-10-12T10:08:00.000Z",
    },
    {
      id: "result:suite.task_01:inspect-react:1",
      paneId: "inspect-react",
      taskId: "suite.task_01",
      role: "result",
      message: "ok",
      recordedAt: "2026-10-12T10:08:01.000Z",
      stubKind: "focus_replay",
    },
  ];
  const skips = manualSkipTurns(
    local,
    [{ paneId: "openbot" }, { paneId: "inspect-react" }],
    "suite.task_01",
    (paneId) => `skip ${paneId}`,
    "2026-10-12T10:08:02.000Z",
  );
  assert.equal(skips.length, 1);
  assert.equal(skips[0]?.paneId, "openbot");
  assert.equal(skips[0]?.role, "skip");
  assert.equal(skips[0]?.stubKind, "manual_skip");
  assert.match(skips[0]?.message ?? "", /skip openbot/);
  assert.doesNotMatch(skips[0]?.message ?? "", /\b0\b|validity|cost_amount/);
  assert.equal(panesReadyForAdvance(["result", "skip"]), true);
  assert.equal(panesReadyForAdvance(["result", "pending"]), false);
});

test("execution dialogue keeps a pending placeholder until a result or skip lands", () => {
  const lines = executionLines(
    "openbot",
    [],
    fanOutTurns([], [{ paneId: "openbot" }], "suite.task_02", "task two", "2026-10-12T10:08:00.000Z"),
    "suite.task_01",
  );
  assert.deepEqual(
    lines.map((line) => [line.role, line.outcome]),
    [
      ["dispatch", null],
      ["pending", "pending"],
    ],
  );
});

test("monitor page exposes rhythm controls and keeps live spend locked", () => {
  const page = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "pages/Monitor.tsx"), "utf8");
  assert.match(page, /data-testid="auto-interval"/);
  assert.match(page, /data-testid="skip-task"/);
  assert.match(page, /data-testid="monitor-rhythm"/);
  assert.match(page, /manualSkipTurns/);
  assert.match(page, /autoRhythm/);
  assert.doesNotMatch(page, /live:\s*true/);
  assert.doesNotMatch(page, /OHBE_ALLOW_LIVE_SPEND/);
});

