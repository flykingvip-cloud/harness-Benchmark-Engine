export interface ListedResult {
  result_id: string;
  source_kind: "fixture" | "live";
}

export function defaultResultId(results: readonly ListedResult[]): string | null {
  return results.find((item) => item.source_kind === "fixture")?.result_id ?? results[0]?.result_id ?? null;
}

export function selectedResultId(results: readonly ListedResult[], picked: string | null): string | null {
  if (picked !== null && results.some((item) => item.result_id === picked)) {
    return picked;
  }
  return defaultResultId(results);
}

/** Stable monitor → report handoff. Query names match API fields (`plan`, `report_id`). */
export function resultsHandoffPath(input: { planId?: string | null; reportId?: string | null }): string {
  const query = new URLSearchParams();
  if (input.planId) {
    query.set("plan", input.planId);
  }
  if (input.reportId) {
    query.set("report_id", input.reportId);
  }
  const encoded = query.toString();
  return encoded ? `/results?${encoded}` : "/results";
}

/**
 * Resolve which result to open.
 * A handoff (`plan` / `report_id` query) never falls back to an unrelated default:
 * the requested report must be in the index, or the selection stays null (honest empty).
 */
export function resolveResultsSelection(input: {
  results: readonly ListedResult[];
  picked: string | null;
  requestedReportId: string | null;
  handoffActive: boolean;
}): string | null {
  const { results, picked, requestedReportId, handoffActive } = input;
  if (picked !== null && results.some((item) => item.result_id === picked)) {
    return picked;
  }
  if (requestedReportId !== null && results.some((item) => item.result_id === requestedReportId)) {
    return requestedReportId;
  }
  if (handoffActive) {
    return null;
  }
  return defaultResultId(results);
}

export function resultDetailPath(resultId: string): string {
  return `/v1/results/${encodeURIComponent(resultId)}`;
}
