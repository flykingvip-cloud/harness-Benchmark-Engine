import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { show } from "./format.ts";
import { messages } from "./i18n/messages.ts";
import {
  defaultResultId,
  resolveResultsSelection,
  resultDetailPath,
  resultsHandoffPath,
  selectedResultId,
  type ListedResult,
} from "./results.ts";

const listed: ListedResult[] = [
  { result_id: "fixture-pilot", source_kind: "fixture" },
  { result_id: "live-pilot-smoke", source_kind: "live" },
];

test("a live artifact stays listed and can be opened while the fixture stays the default", () => {
  assert.equal(defaultResultId(listed), "fixture-pilot");
  assert.equal(selectedResultId(listed, null), "fixture-pilot");
  assert.equal(selectedResultId(listed, "live-pilot-smoke"), "live-pilot-smoke");
  assert.equal(resultDetailPath("live-pilot-smoke"), "/v1/results/live-pilot-smoke");
  const live = listed.filter((item) => item.source_kind === "live");
  assert.deepEqual(
    live.map((item) => item.result_id),
    ["live-pilot-smoke"],
  );
  assert.equal(resultDetailPath(live[0].result_id), "/v1/results/live-pilot-smoke");
});

test("a live-only list opens that live artifact", () => {
  const liveOnly: ListedResult[] = [{ result_id: "nested-smoke", source_kind: "live" }];
  assert.equal(selectedResultId(liveOnly, null), "nested-smoke");
  assert.equal(resultDetailPath("nested-smoke"), "/v1/results/nested-smoke");
  assert.equal(selectedResultId(listed, "missing"), "fixture-pilot");
});

test("missing usage stays null on the report table", () => {
  assert.equal(show(null), "null");
  assert.notEqual(show(null), "0");
});

test("monitor handoff URL carries plan and report_id when both are known", () => {
  assert.equal(
    resultsHandoffPath({ planId: "fixture-cross-model", reportId: "fixture-cross-model-report" }),
    "/results?plan=fixture-cross-model&report_id=fixture-cross-model-report",
  );
  assert.equal(resultsHandoffPath({ planId: "fixture-paired", reportId: null }), "/results?plan=fixture-paired");
  assert.equal(resultsHandoffPath({ planId: null, reportId: "fixture-pilot" }), "/results?report_id=fixture-pilot");
  assert.equal(resultsHandoffPath({ planId: null, reportId: null }), "/results");
  assert.match(resultsHandoffPath({ planId: "a/b", reportId: "c d" }), /plan=a%2Fb/);
  assert.match(resultsHandoffPath({ planId: "a/b", reportId: "c d" }), /report_id=c\+d|report_id=c%20d/);
});

test("a handoff selects the requested report or stays empty — never an unrelated default", () => {
  assert.equal(
    resolveResultsSelection({
      results: listed,
      picked: null,
      requestedReportId: "live-pilot-smoke",
      handoffActive: true,
    }),
    "live-pilot-smoke",
  );
  assert.equal(
    resolveResultsSelection({
      results: listed,
      picked: null,
      requestedReportId: "fixture-cross-model-report",
      handoffActive: true,
    }),
    null,
  );
  assert.equal(
    resolveResultsSelection({
      results: listed,
      picked: null,
      requestedReportId: null,
      handoffActive: true,
    }),
    null,
  );
  assert.equal(
    resolveResultsSelection({
      results: listed,
      picked: "live-pilot-smoke",
      requestedReportId: "missing-report",
      handoffActive: true,
    }),
    "live-pilot-smoke",
  );
  assert.equal(
    resolveResultsSelection({
      results: listed,
      picked: null,
      requestedReportId: null,
      handoffActive: false,
    }),
    "fixture-pilot",
  );
});

test("the results page lists both kinds and does not present oracle or a ranking", () => {
  const page = readFileSync(resolve(dirname(fileURLToPath(import.meta.url)), "pages/Results.tsx"), "utf8");
  assert.match(page, /resolveResultsSelection/);
  assert.match(page, /resultDetailPath/);
  assert.match(page, /useSearchParams/);
  assert.match(page, /report_id/);
  assert.match(page, /params\.get\("plan"\)/);
  assert.match(page, /RoundPlanDetail/);
  assert.match(page, /data-testid="result-index"/);
  assert.match(page, /data-source-kind=\{item\.source_kind\}/);
  assert.match(page, /show\(harness\.total_tokens\)/);
  assert.match(page, /show\(harness\.total_model_cost\)/);
  assert.doesNotMatch(page, /total_tokens \?\? 0|total_tokens \|\| 0|total_model_cost \?\? 0/);
  assert.doesNotMatch(page, /live:\s*true/);
  assert.doesNotMatch(page, /oracle score|ranking delivered|Oracle 已交付|排行榜/);
  for (const locale of ["zh", "en"] as const) {
    assert.match(messages[locale]["results.note"], /null/);
    assert.match(messages[locale]["results.note"], /排名|ranking/);
    assert.match(messages[locale]["results.note"], /Oracle/);
    assert.match(messages[locale]["chart.intervalCaption"], /排名|ranking/);
    assert.match(messages[locale]["chart.barCaption"], /零|zero/);
  }
  assert.equal(messages.zh["results.kind.fixture"], "夹具");
  assert.equal(messages.en["results.kind.fixture"], "fixture");
  assert.equal(messages.zh["results.kind.live"], "live");
  assert.equal(messages.en["results.kind.live"], "live");
});
