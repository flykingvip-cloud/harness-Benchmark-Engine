import type { FixedHarnessModelMetrics, HarnessResult, ResultDetail } from "./api";
import type { DimensionId } from "./dimensions";

const DIMENSIONS: readonly DimensionId[] = ["validity", "effectiveness", "process", "cost", "diagnosability"];

const DECIMAL = /^-?\d+(\.\d+)?$/;

export interface IntervalPoint {
  taskId: string;
  harnessId: string;
  rate: number;
  low: number | null;
  high: number | null;
}

export interface BarMark {
  seriesId: string;
  ratio: number | null;
  label: string;
}

export interface DimensionBars {
  id: DimensionId;
  marks: BarMark[];
}

export interface CrossBars {
  taskClass: string;
  marks: BarMark[];
}

export function parseDecimal(value: string | null | undefined): number | null {
  if (value == null || !DECIMAL.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatRate(value: number): string {
  return value.toFixed(2);
}

/** Paired success rates in report order. A missing rate is omitted, not drawn as zero. */
export function pairedIntervals(detail: ResultDetail): IntervalPoint[] {
  const points: IntervalPoint[] = [];
  for (const task of detail.tasks) {
    for (const harness of task.harnesses) {
      const rate = unit(harness.success_rate);
      if (rate === null) {
        continue;
      }
      const low = unit(harness.success_rate_low);
      const high = unit(harness.success_rate_high);
      const bounded = low !== null && high !== null && low <= rate && rate <= high;
      points.push({
        taskId: task.task_id,
        harnessId: harness.harness_id,
        rate,
        low: bounded ? low : null,
        high: bounded ? high : null,
      });
    }
  }
  return points;
}

/** Five readings per harness. Cost is the share of rows with usage present, not a dollar amount. */
export function harnessDimensionBars(detail: ResultDetail): DimensionBars[] {
  const order: string[] = [];
  const buckets = new Map<string, Bucket>();
  for (const task of detail.tasks) {
    for (const row of task.harnesses) {
      if (!buckets.has(row.harness_id)) {
        order.push(row.harness_id);
        buckets.set(row.harness_id, emptyBucket());
      }
      absorb(buckets.get(row.harness_id)!, row);
    }
  }
  return DIMENSIONS.map((id) => ({
    id,
    marks: order.map((seriesId) => ({ seriesId, ...reading(id, buckets.get(seriesId) ?? emptyBucket()) })),
  }));
}

/** cross_model success under one harness. Task-class order is the payload order, not a rank. */
export function crossModelBars(metrics: FixedHarnessModelMetrics[]): CrossBars[] {
  const classes: string[] = [];
  const models: string[] = [];
  for (const row of metrics) {
    if (!classes.includes(row.task_class)) {
      classes.push(row.task_class);
    }
    if (!models.includes(row.model_id)) {
      models.push(row.model_id);
    }
  }
  return classes.map((taskClass) => ({
    taskClass,
    marks: models.map((seriesId) => {
      const row = metrics.find((item) => item.task_class === taskClass && item.model_id === seriesId);
      const rate = row ? unit(row.success_rate) : null;
      return { seriesId, ratio: rate, label: rate === null ? "—" : formatRate(rate) };
    }),
  }));
}

interface Bucket {
  valid: number;
  invalid: number;
  rates: number[];
  complete: number;
  total: number;
  present: number;
  missing: number;
  usageInvalid: number;
  explained: number;
}

function emptyBucket(): Bucket {
  return {
    valid: 0,
    invalid: 0,
    rates: [],
    complete: 0,
    total: 0,
    present: 0,
    missing: 0,
    usageInvalid: 0,
    explained: 0,
  };
}

function absorb(bucket: Bucket, row: HarnessResult): void {
  bucket.valid += finite(row.valid_count);
  bucket.invalid += finite(row.invalid_count);
  const rate = unit(row.success_rate);
  if (rate !== null) {
    bucket.rates.push(rate);
  }
  bucket.total += 1;
  if (row.complete === true) {
    bucket.complete += 1;
  }
  if (row.usage_status === "present") {
    bucket.present += 1;
  } else if (row.usage_status === "missing") {
    bucket.missing += 1;
  } else if (row.usage_status === "invalid") {
    bucket.usageInvalid += 1;
  }
  for (const count of Object.values(row.invalid_by_category ?? {})) {
    if (Number.isInteger(count) && count >= 0) {
      bucket.explained += count;
    }
  }
}

function reading(id: DimensionId, bucket: Bucket): { ratio: number | null; label: string } {
  if (id === "validity") {
    const total = bucket.valid + bucket.invalid;
    return ratioLabel(total === 0 ? null : bucket.valid / total, total === 0 ? "—" : `${bucket.valid}/${total}`);
  }
  if (id === "effectiveness") {
    if (bucket.rates.length === 0) {
      return ratioLabel(null, "—");
    }
    const mean = bucket.rates.reduce((total, rate) => total + rate, 0) / bucket.rates.length;
    return ratioLabel(mean, formatRate(mean));
  }
  if (id === "process") {
    return ratioLabel(bucket.total === 0 ? null : bucket.complete / bucket.total, bucket.total === 0 ? "—" : `${bucket.complete}/${bucket.total}`);
  }
  if (id === "cost") {
    const total = bucket.present + bucket.missing + bucket.usageInvalid;
    return ratioLabel(total === 0 ? null : bucket.present / total, total === 0 ? "—" : `${bucket.present}/${total}`);
  }
  if (bucket.invalid <= 0) {
    return ratioLabel(null, "—");
  }
  return ratioLabel(Math.min(1, bucket.explained / bucket.invalid), `${bucket.explained}/${bucket.invalid}`);
}

function ratioLabel(ratio: number | null, label: string): { ratio: number | null; label: string } {
  return { ratio, label };
}

function unit(value: string | null | undefined): number | null {
  const parsed = parseDecimal(value);
  if (parsed === null || parsed < 0 || parsed > 1) {
    return null;
  }
  return parsed;
}

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}
