import type { ObservationEvent, RoundMode } from "./api";

export const PHASES = ["queued", "dispatching", "observing", "sealed"] as const;

export type Phase = (typeof PHASES)[number];

export function phaseRank(kindOrPhase: string): number {
  const phase = kindOrPhase === "step" ? "observing" : kindOrPhase;
  return PHASES.indexOf(phase as Phase);
}

/** Events at or before a dispatcher phase. Step rows belong to observing. */
export function eventsThrough<T extends { kind: string }>(events: readonly T[], phase: string): T[] {
  const limit = phaseRank(phase);
  if (limit < 0) {
    return [];
  }
  return events.filter((event) => {
    const rank = phaseRank(event.kind);
    return rank >= 0 && rank <= limit;
  });
}

export interface ObservationPane {
  paneId: string;
  modelId: string | null;
  harnessId: string;
  taskId: string;
  lines: ObservationEvent[];
}

/** One pane per step stream. Dispatcher rows stay on the status ladder. */
export function observationPanes(events: readonly ObservationEvent[]): ObservationPane[] {
  const panes = new Map<string, ObservationPane>();
  for (const event of events) {
    if (event.kind !== "step") {
      continue;
    }
    const current = panes.get(event.pane_id);
    if (current) {
      current.lines.push(event);
      continue;
    }
    panes.set(event.pane_id, {
      paneId: event.pane_id,
      modelId: event.model_id,
      harnessId: event.harness_id,
      taskId: event.task_id,
      lines: [event],
    });
  }
  return [...panes.values()];
}

export type DirectoryOrigin = "event" | "planned" | "slot";

export interface DirectoryPane extends ObservationPane {
  /** event: step stream. planned: stub pane_id before steps. slot: comparison slot before a dry-run job. */
  origin: DirectoryOrigin;
}

export interface DirectoryInput {
  mode: RoundMode;
  subjectHarnessId: string | null;
  /** Primary sealed reference (RoundPlan.reference_harness_ref). Kept for callers that only know the sealed field. */
  referenceHarnessId: string | null;
  /** Full reference list from PlanLabels.reference_harness_ids (may include primary). */
  referenceHarnessIds?: readonly string[];
  /** Fixture-pool model ids from the dry-run job. Empty before start. */
  modelIds: readonly string[];
  taskId: string | null;
  /** Comparison slot ids. Cross-model directory uses them only before a job. */
  slotIds: readonly string[];
}

/**
 * Left-hand directory for one PlanSession.
 * Paired mode is one row per harness. Cross-model is one row per model.
 * Step events keep pane_id identity. Before that, rows preview the same ids the stub will use.
 */
export function directoryPanes(input: DirectoryInput, events: readonly ObservationEvent[]): DirectoryPane[] {
  const observed = observationPanes(events);
  if (observed.length > 0) {
    return observed.map((pane) => ({ ...pane, origin: "event" }));
  }
  const taskId = input.taskId ?? "";
  if (input.mode === "paired_harness") {
    const harnessIds = pairedHarnessIds(input);
    const shared = input.modelIds[0] ?? null;
    return harnessIds.map((harnessId) => ({
      paneId: harnessId,
      modelId: shared,
      harnessId,
      taskId,
      lines: [],
      origin: "planned",
    }));
  }
  if (input.modelIds.length > 0) {
    const harnessId = input.subjectHarnessId ?? "";
    return input.modelIds.map((modelId) => ({
      paneId: modelId,
      modelId,
      harnessId,
      taskId,
      lines: [],
      origin: "planned",
    }));
  }
  const harnessId = input.subjectHarnessId ?? "";
  return input.slotIds.map((modelId) => ({
    paneId: modelId,
    modelId,
    harnessId,
    taskId,
    lines: [],
    origin: "slot",
  }));
}

export function activeDirectoryPane<T extends { paneId: string }>(current: string | null, panes: readonly T[]): T | null {
  if (panes.length === 0) {
    return null;
  }
  if (current) {
    const found = panes.find((pane) => pane.paneId === current);
    if (found) {
      return found;
    }
  }
  return panes[0];
}

export interface ObservationTurn {
  seq: number;
  role: "dispatch" | "pane";
  kind: string;
  message: string;
  recordedAt: string;
  paneId: string;
}

/** Dispatcher rows plus this pane's steps, in sequence. Server messages stay as recorded. */
export function observationTurns(paneId: string, events: readonly ObservationEvent[]): ObservationTurn[] {
  return relatedEvents(paneId, events).map((event) => ({
    seq: event.seq,
    role: event.kind === "step" ? "pane" : "dispatch",
    kind: event.kind,
    message: event.message,
    recordedAt: event.recorded_at,
    paneId: event.pane_id,
  }));
}

export interface PaneRecorder {
  phase: string | null;
  firstAt: string | null;
  lastAt: string | null;
  latency: string | null;
  cost: string | null;
  taskId: string;
  modelId: string | null;
  harnessId: string;
  spend: "none";
  executed: false;
}

const LATENCY_VALUE = /\b(?:latency|elapsed)\s*[:=]\s*(\d+(?:\.\d+)?\s*ms)\b/i;
const COST_VALUE = /\bcost\s*[:=]\s*(\d+(?:\.\d+)?)\b/i;

/** Phase and ids for the selected pane. Latency and cost stay empty until an event states them. */
export function paneRecorder(
  pane: Pick<DirectoryPane, "paneId" | "modelId" | "harnessId" | "taskId">,
  events: readonly ObservationEvent[],
  phase: string | null,
): PaneRecorder {
  const related = relatedEvents(pane.paneId, events);
  const stamps = related.map((event) => event.recorded_at).filter((value) => value.trim() !== "");
  const taskFromEvent = related.find((event) => event.task_id.trim() !== "")?.task_id ?? "";
  return {
    phase,
    firstAt: stamps[0] ?? null,
    lastAt: stamps.length > 0 ? stamps[stamps.length - 1] : null,
    latency: latestMarked(related, LATENCY_VALUE),
    cost: latestMarked(related, COST_VALUE),
    taskId: pane.taskId || taskFromEvent,
    modelId: pane.modelId,
    harnessId: pane.harnessId,
    spend: "none",
    executed: false,
  };
}

/** Compact an ISO timestamp for the recorder. Unknown shapes stay as recorded. */
export function formatStamp(value: string | null | undefined): string | null {
  if (!value || value.trim() === "") {
    return null;
  }
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/.exec(value);
  if (!match) {
    return value;
  }
  return `${match[1]} ${match[2]}`;
}

function relatedEvents(paneId: string, events: readonly ObservationEvent[]): ObservationEvent[] {
  return events
    .filter((event) => event.pane_id === "dispatch" || event.pane_id === paneId)
    .slice()
    .sort((left, right) => left.seq - right.seq);
}

function latestMarked(events: readonly { message: string }[], pattern: RegExp): string | null {
  let found: string | null = null;
  for (const event of events) {
    const match = pattern.exec(event.message);
    if (match?.[1]) {
      found = match[1];
    }
  }
  return found;
}

/** Subject plus every reference id (labels list preferred; sealed primary as fallback). */
export function pairedHarnessIds(input: {
  subjectHarnessId: string | null;
  referenceHarnessId: string | null;
  referenceHarnessIds?: readonly string[];
}): string[] {
  const fromLabels = (input.referenceHarnessIds ?? []).filter(Boolean);
  const refs = fromLabels.length > 0 ? fromLabels : input.referenceHarnessId ? [input.referenceHarnessId] : [];
  return uniqueIds([input.subjectHarnessId, ...refs]);
}

function uniqueIds(ids: readonly (string | null)[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const id of ids) {
    if (!id || seen.has(id)) {
      continue;
    }
    seen.add(id);
    result.push(id);
  }
  return result;
}

export const COMPARE_DIMS = ["validity", "effectiveness", "process", "cost", "diagnosability"] as const;

export type CompareDim = (typeof COMPARE_DIMS)[number];

export interface CompareCell {
  score: number | null;
  label: string;
}

export interface CompareColumn {
  id: string;
  label: string;
  values: Record<CompareDim, CompareCell>;
}

const HIGHER_IS_BETTER: Record<CompareDim, boolean> = {
  validity: true,
  effectiveness: true,
  process: true,
  cost: false,
  diagnosability: true,
};

export function columnsFromMetrics(
  metrics: readonly {
    model_id: string;
    harness_id: string;
    task_class: string;
    success_rate: string | null;
    valid_count: number;
    cost_per_success: string | null;
    steps: number | null;
    usage_status: string;
  }[],
): CompareColumn[] {
  return metrics.map((row) => ({
    id: `${row.harness_id}:${row.model_id}:${row.task_class}`,
    label: row.model_id,
    values: {
      validity: cell(finiteNumber(row.valid_count), numberLabel(row.valid_count)),
      effectiveness: cell(decimalOrNull(row.success_rate), textOrDash(row.success_rate)),
      process: cell(row.steps === null ? null : finiteNumber(row.steps), row.steps === null ? "—" : numberLabel(row.steps)),
      cost: cell(decimalOrNull(row.cost_per_success), textOrDash(row.cost_per_success)),
      diagnosability: cell(usageScore(row.usage_status), usageLabel(row.usage_status)),
    },
  }));
}

export function columnsFromPanes(panes: readonly { paneId: string; modelId: string | null }[]): CompareColumn[] {
  return panes.map((pane) => ({
    id: pane.paneId,
    label: pane.modelId ?? pane.paneId,
    values: {
      validity: emptyCell(),
      effectiveness: emptyCell(),
      process: emptyCell(),
      cost: emptyCell(),
      diagnosability: emptyCell(),
    },
  }));
}

/** Highlight only when two comparable numbers differ. A missing cell is not zero and not best. */
export function bestColumnIds(columns: readonly CompareColumn[], dim: CompareDim): Set<string> {
  const present = columns.filter((column) => column.values[dim].score !== null);
  const distinct = new Set(present.map((column) => column.values[dim].score));
  if (present.length < 2 || distinct.size < 2) {
    return new Set();
  }
  const scores = present.map((column) => column.values[dim].score as number);
  const best = HIGHER_IS_BETTER[dim] ? Math.max(...scores) : Math.min(...scores);
  return new Set(present.filter((column) => column.values[dim].score === best).map((column) => column.id));
}

export function visibleDimensions(columns: readonly CompareColumn[], hideIdentical: boolean): CompareDim[] {
  if (!hideIdentical || columns.length < 2) {
    return [...COMPARE_DIMS];
  }
  return COMPARE_DIMS.filter((dim) => {
    const first = columns[0]?.values[dim].score;
    return columns.some((column) => column.values[dim].score !== first);
  });
}

function emptyCell(): CompareCell {
  return { score: null, label: "—" };
}

function cell(score: number | null, label: string): CompareCell {
  return { score, label };
}

function finiteNumber(value: number | null): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value;
}

function decimalOrNull(value: string | null | undefined): number | null {
  if (value == null) {
    return null;
  }
  const trimmed = value.trim();
  if (!trimmed || trimmed === "—" || trimmed === "-") {
    return null;
  }
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

function textOrDash(value: string | null | undefined): string {
  if (value == null || value.trim() === "" || value.trim() === "—") {
    return "—";
  }
  return value;
}

function numberLabel(value: number): string {
  return String(value);
}

function usageScore(status: string): number | null {
  if (status === "present") {
    return 2;
  }
  if (status === "missing") {
    return 1;
  }
  if (status === "invalid") {
    return 0;
  }
  return null;
}

function usageLabel(status: string): string {
  if (status === "present" || status === "missing" || status === "invalid") {
    return status;
  }
  return "—";
}

export const FIXTURE_FIT_REPORT_ID = "fixture-cross-model-report";

/** Path bar: 0 prepare, 1 plan, 2 observation, 3 report. No session stays on the plan step. */
export function sessionPathIndex(session: { job: unknown; report: unknown } | null): number {
  if (!session) {
    return 1;
  }
  if (session.report) {
    return 3;
  }
  if (session.job) {
    return 2;
  }
  return 1;
}

export function showFixtureFit(reportId: string | null | undefined): boolean {
  return reportId === FIXTURE_FIT_REPORT_ID;
}

export function sameSlotIds(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

export function splitTokens(value: string): string[] {
  const seen = new Set<string>();
  const tokens: string[] = [];
  for (const part of value.split(/[,，]/)) {
    const token = part.trim();
    if (!token || seen.has(token)) {
      continue;
    }
    seen.add(token);
    tokens.push(token);
  }
  return tokens;
}
