import type { HarnessResult, ResultDetail, TaskResult } from "./api";

export const DIMENSIONS = ["validity", "effectiveness", "process", "cost", "diagnosability"] as const;

export type DimensionId = (typeof DIMENSIONS)[number];

export interface DimensionFacts {
  validity: { valid: number; invalid: number; taskFailures: number; coverage: number | null };
  effectiveness: { meanSuccess: string | null; delta: string | null; pairedTasks: number; coverage: number | null };
  process: { complete: number; total: number; coverage: number | null };
  cost: { present: number; missing: number; invalid: number; priced: number; coverage: number | null };
  diagnosability: { gatesPassed: number; gatesTotal: number; categories: Array<[string, number]>; coverage: number | null };
}

export function dimensionFacts(detail: ResultDetail): DimensionFacts {
  const rows = detail.tasks.flatMap((task) => task.harnesses);
  const valid = sum(rows, (row) => row.valid_count);
  const invalid = sum(rows, (row) => row.invalid_count);
  const taskFailures = sum(rows, (row) => row.task_failure_count);
  const usage = countUsage(rows);
  const priced = rows.filter((row) => typeof row.cost_per_success === "string" && row.cost_per_success.length > 0).length;
  const complete = rows.filter((row) => row.complete === true).length;
  const rates = pairedRates(detail.tasks);
  const gatesTotal = detail.release_gates.length;
  const gatesPassed = detail.release_gates.filter((gate) => gate.passed).length;
  const categories = categoryCounts(rows);

  return {
    validity: {
      valid,
      invalid,
      taskFailures,
      coverage: ratio(valid, valid + invalid),
    },
    effectiveness: {
      meanSuccess: rates.mean,
      delta: rates.delta,
      pairedTasks: rates.pairedTasks,
      coverage: rates.coverage,
    },
    process: {
      complete,
      total: rows.length,
      coverage: ratio(complete, rows.length),
    },
    cost: {
      present: usage.present,
      missing: usage.missing,
      invalid: usage.invalid,
      priced,
      coverage: ratio(usage.present, usage.present + usage.missing + usage.invalid),
    },
    diagnosability: {
      gatesPassed,
      gatesTotal,
      categories,
      coverage: ratio(gatesPassed, gatesTotal),
    },
  };
}

function pairedRates(tasks: TaskResult[]): { mean: string | null; delta: string | null; pairedTasks: number; coverage: number | null } {
  const all: number[] = [];
  const deltas: number[] = [];
  for (const task of tasks) {
    const rates = task.harnesses.map((row) => parseRate(row.success_rate)).filter((rate): rate is number => rate !== null);
    all.push(...rates);
    if (rates.length >= 2) {
      deltas.push(rates[0] - rates[1]);
    }
  }
  const mean = all.length > 0 ? all.reduce((total, rate) => total + rate, 0) / all.length : null;
  const delta = deltas.length > 0 ? deltas.reduce((total, rate) => total + rate, 0) / deltas.length : null;
  return {
    mean: mean === null ? null : mean.toFixed(6),
    delta: delta === null ? null : delta.toFixed(6),
    pairedTasks: deltas.length,
    coverage: mean,
  };
}

function countUsage(rows: HarnessResult[]): { present: number; missing: number; invalid: number } {
  const counts = { present: 0, missing: 0, invalid: 0 };
  for (const row of rows) {
    if (row.usage_status === "present" || row.usage_status === "missing" || row.usage_status === "invalid") {
      counts[row.usage_status] += 1;
    }
  }
  return counts;
}

function categoryCounts(rows: HarnessResult[]): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const categories = row.invalid_by_category ?? {};
    for (const [name, count] of Object.entries(categories)) {
      if (!Number.isInteger(count) || count < 0) {
        continue;
      }
      counts.set(name, (counts.get(name) ?? 0) + count);
    }
  }
  return [...counts.entries()].sort(([left], [right]) => left.localeCompare(right));
}

function sum(rows: HarnessResult[], read: (row: HarnessResult) => number): number {
  return rows.reduce((total, row) => {
    const value = read(row);
    return total + (Number.isFinite(value) ? value : 0);
  }, 0);
}

function ratio(part: number, total: number): number | null {
  if (total <= 0) {
    return null;
  }
  return part / total;
}

function parseRate(value: string | null): number | null {
  if (value === null || !/^-?\d+(\.\d+)?$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
