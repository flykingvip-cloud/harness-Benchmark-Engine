import type { ObservationEvent } from "./api";

/** Plans the monitor can load: confirmed, running, or sealed. Drafts stay on the evaluation page. */
const MONITOR_LIFECYCLES = new Set(["confirmed_suite", "running", "round_report"]);

export function isMonitorable(lifecycle: string): boolean {
  return MONITOR_LIFECYCLES.has(lifecycle);
}

export type SuiteTaskStatus = "queued" | "current" | "done";

export function suiteTaskViews<T extends { task_id: string }>(
  tasks: readonly T[],
  currentIndex: number,
  started: boolean,
): (T & { index: number; status: SuiteTaskStatus })[] {
  return tasks.map((task, offset) => ({
    ...task,
    index: offset + 1,
    status: !started ? "queued" : offset < currentIndex ? "done" : offset === currentIndex ? "current" : "queued",
  }));
}

/** Composer text for the task being switched to. Label is already translated. */
export function taskComposerText(label: string, taskId: string, prompt: string): string {
  const head = `${label} ${taskId}`.trim();
  const body = prompt.trim();
  return body ? `${head}\n${body}` : head;
}

export type PaneOutcome = "pending" | "result" | "skip";
export type StubKind = "focus_replay" | "unmeasured" | "failed_skip";
export type LocalRole = "dispatch" | "result" | "skip";

export interface LocalTurn {
  id: string;
  paneId: string;
  taskId: string;
  role: LocalRole;
  message: string;
  recordedAt: string;
  /** Present on auto/manual result or skip turns so the dialogue can badge honestly. */
  stubKind?: StubKind | "manual_skip";
}

export function nextTurnId(existing: readonly { id: string }[], paneId: string, taskId: string, role: string): string {
  const prefix = `${role}:${taskId}:${paneId}:`;
  const ids = new Set(existing.map((turn) => turn.id));
  let n = 1;
  while (ids.has(`${prefix}${n}`)) {
    n += 1;
  }
  return `${prefix}${n}`;
}

/** One dispatch turn per pane. Paired panes are harnesses; cross-model panes are models. */
export function fanOutTurns(
  existing: readonly LocalTurn[],
  panes: readonly { paneId: string }[],
  taskId: string,
  message: string,
  recordedAt: string,
): LocalTurn[] {
  const created: LocalTurn[] = [];
  for (const pane of panes) {
    created.push({
      id: nextTurnId([...existing, ...created], pane.paneId, taskId, "dispatch"),
      paneId: pane.paneId,
      taskId,
      role: "dispatch",
      message,
      recordedAt,
    });
  }
  return created;
}

const PANE_FAILURE = /\b(fail(?:ed|ure)?|error|exception|timeout|skipped)\b/i;

export function isPaneFailure(message: string): boolean {
  return PANE_FAILURE.test(message);
}


export interface PaneJudgement {
  outcome: PaneOutcome;
  kind: StubKind | null;
}

/**
 * A pane is ready when it has a stub result or a failure skip.
 * The server stub only replays the focus task. Later tasks become an honest
 * unmeasured result after a tick, and only once they have been fanned out.
 * A missing step stays pending. It is not a skip and it is not zero.
 */
export function paneOutcome(input: {
  paneId: string;
  taskId: string;
  focusTaskId: string | null;
  events: readonly { pane_id: string; kind: string; task_id: string; message: string }[];
  local: readonly { paneId: string; taskId: string; role: string }[];
  allowUnmeasured: boolean;
}): PaneJudgement {
  const settled = input.local.find(
    (turn) => turn.paneId === input.paneId && turn.taskId === input.taskId && (turn.role === "result" || turn.role === "skip"),
  );
  if (settled) {
    return { outcome: settled.role === "skip" ? "skip" : "result", kind: null };
  }
  if (input.focusTaskId !== null && input.taskId === input.focusTaskId) {
    const steps = input.events.filter(
      (event) => event.kind === "step" && event.pane_id === input.paneId && event.task_id === input.taskId,
    );
    if (steps.length === 0) {
      return { outcome: "pending", kind: null };
    }
    if (steps.some((event) => isPaneFailure(event.message))) {
      return { outcome: "skip", kind: "failed_skip" };
    }
    return { outcome: "result", kind: "focus_replay" };
  }
  const dispatched = input.local.some(
    (turn) => turn.paneId === input.paneId && turn.taskId === input.taskId && turn.role === "dispatch",
  );
  if (dispatched && input.allowUnmeasured) {
    return { outcome: "result", kind: "unmeasured" };
  }
  return { outcome: "pending", kind: null };
}

/** Auto-advance only when every pane is a result or a skip. Skip does not block. */
export function shouldAutoAdvance(input: {
  auto: boolean;
  paused: boolean;
  taskIndex: number;
  taskCount: number;
  outcomes: readonly PaneOutcome[];
}): boolean {
  if (!input.auto || input.paused) {
    return false;
  }
  if (input.taskCount <= 0 || input.taskIndex < 0 || input.taskIndex >= input.taskCount - 1) {
    return false;
  }
  if (input.outcomes.length === 0) {
    return false;
  }
  return input.outcomes.every((item) => item === "result" || item === "skip");
}

export interface DialogueLine {
  key: string;
  role: "dispatch" | "process" | "result" | "skip" | "pending";
  message: string;
  recordedAt: string;
  kind: string;
  stubKind?: StubKind | "manual_skip" | null;
  outcome?: "success" | "skip" | "unmeasured" | "pending" | null;
}

/** Dispatch, then the server process log for the focus task, then the stub result. */
export function executionLines(
  paneId: string,
  events: readonly ObservationEvent[],
  local: readonly LocalTurn[],
  focusTaskId: string | null,
): DialogueLine[] {
  const mine = local.filter((turn) => turn.paneId === paneId);
  const taskIds: string[] = [];
  for (const turn of mine) {
    if (!taskIds.includes(turn.taskId)) {
      taskIds.push(turn.taskId);
    }
  }
  const server = serverLines(paneId, events);
  const lines: DialogueLine[] = [];
  let usedServer = false;
  for (const taskId of taskIds) {
    for (const turn of mine) {
      if (turn.taskId === taskId && turn.role === "dispatch") {
        lines.push(localLine(turn));
      }
    }
    if (focusTaskId !== null && taskId === focusTaskId) {
      usedServer = true;
      lines.push(...server);
    }
    const settled = mine.filter((turn) => turn.taskId === taskId && turn.role !== "dispatch");
    if (settled.length > 0) {
      for (const turn of settled) {
        lines.push(localLine(turn));
      }
    } else if (mine.some((turn) => turn.taskId === taskId && turn.role === "dispatch")) {
      // Honest placeholder: dispatched but not settled. Missing is not zero.
      lines.push({
        key: `pending:${taskId}:${paneId}`,
        role: "pending",
        message: "",
        recordedAt: mine.find((turn) => turn.taskId === taskId && turn.role === "dispatch")!.recordedAt,
        kind: "pending",
        stubKind: null,
        outcome: "pending",
      });
    }
  }
  if (!usedServer && server.length > 0) {
    return [...server, ...lines];
  }
  return lines;
}

function serverLines(paneId: string, events: readonly ObservationEvent[]): DialogueLine[] {
  return events
    .filter((event) => event.pane_id === "dispatch" || event.pane_id === paneId)
    .slice()
    .sort((left, right) => left.seq - right.seq)
    .map((event) => ({
      key: `server-${event.seq}`,
      role: event.kind === "step" ? "process" : "dispatch",
      message: event.message,
      recordedAt: event.recorded_at,
      kind: event.kind,
    }));
}

function localLine(turn: LocalTurn): DialogueLine {
  const role = turn.role === "dispatch" ? "dispatch" : turn.role === "skip" ? "skip" : "result";
  let outcome: DialogueLine["outcome"] = null;
  if (turn.role === "skip") {
    outcome = "skip";
  } else if (turn.role === "result") {
    outcome = turn.stubKind === "unmeasured" ? "unmeasured" : "success";
  }
  return {
    key: turn.id,
    role,
    message: turn.message,
    recordedAt: turn.recordedAt,
    kind: turn.role,
    stubKind: turn.stubKind ?? null,
    outcome,
  };
}

/** Default and selectable auto-advance gaps. Rhythm is client-only; it does not call the API. */
export const AUTO_ADVANCE_INTERVALS_MS = [1000, 2000, 3000] as const;
export type AutoAdvanceIntervalMs = (typeof AUTO_ADVANCE_INTERVALS_MS)[number];
export const DEFAULT_AUTO_ADVANCE_MS: AutoAdvanceIntervalMs = 2000;

export function normalizeAutoInterval(ms: number): AutoAdvanceIntervalMs {
  if ((AUTO_ADVANCE_INTERVALS_MS as readonly number[]).includes(ms)) {
    return ms as AutoAdvanceIntervalMs;
  }
  return DEFAULT_AUTO_ADVANCE_MS;
}

export type AutoRhythmState =
  | { kind: "off" }
  | { kind: "paused" }
  | { kind: "waiting" }
  | { kind: "countdown"; remainingMs: number; nextIndex: number }
  | { kind: "complete" };

/** Visible auto-switch rhythm for the task bar. Pause stops countdown. */
export function autoRhythm(input: {
  auto: boolean;
  paused: boolean;
  readyToAdvance: boolean;
  taskIndex: number;
  taskCount: number;
  remainingMs: number | null;
}): AutoRhythmState {
  if (!input.auto) {
    return { kind: "off" };
  }
  if (input.paused) {
    return { kind: "paused" };
  }
  if (input.taskCount <= 0 || input.taskIndex >= input.taskCount - 1) {
    return { kind: "complete" };
  }
  if (!input.readyToAdvance) {
    return { kind: "waiting" };
  }
  const remaining = input.remainingMs ?? 0;
  return { kind: "countdown", remainingMs: Math.max(0, remaining), nextIndex: input.taskIndex + 1 };
}

/** Manual skip turns for every pane still pending on this task. Does not invent metrics. */
export function manualSkipTurns(
  existing: readonly LocalTurn[],
  panes: readonly { paneId: string }[],
  taskId: string,
  messageFor: (paneId: string) => string,
  recordedAt: string,
): LocalTurn[] {
  const created: LocalTurn[] = [];
  for (const pane of panes) {
    const settled = existing.some(
      (turn) => turn.paneId === pane.paneId && turn.taskId === taskId && (turn.role === "result" || turn.role === "skip"),
    );
    if (settled) {
      continue;
    }
    const withNew = [...existing, ...created];
    created.push({
      id: nextTurnId(withNew, pane.paneId, taskId, "skip"),
      paneId: pane.paneId,
      taskId,
      role: "skip",
      message: messageFor(pane.paneId),
      recordedAt,
      stubKind: "manual_skip",
    });
  }
  return created;
}

/** True when every pane already has a result or skip for the task (including kinds not yet stored). */
export function panesReadyForAdvance(outcomes: readonly PaneOutcome[]): boolean {
  return outcomes.length > 0 && outcomes.every((item) => item === "result" || item === "skip");
}
