import assert from "node:assert/strict";
import test from "node:test";
import type { FixedHarnessModelMetrics, HarnessResult, ResultDetail } from "./api.ts";
import { crossModelBars, harnessDimensionBars, pairedIntervals } from "./chartData.ts";
import { DIMENSIONS } from "./dimensions.ts";

function row(overrides: Partial<HarnessResult> = {}): HarnessResult {
  return {
    harness_id: "openbot",
    success_rate: "1.000000",
    success_rate_low: "0.342372",
    success_rate_high: "1.000000",
    uncertainty: "wilson95",
    success_count: 1,
    valid_count: 1,
    invalid_count: 0,
    task_failure_count: 0,
    total_model_cost: null,
    cost_status: "invalid",
    usage_status: "missing",
    total_tokens: null,
    label: "fixture",
    cost_per_success: null,
    complete: true,
    invalid_by_category: {},
    ...overrides,
  };
}

function detail(tasks: ResultDetail["tasks"]): ResultDetail {
  return {
    result_id: "fixture-pilot",
    kind: "ohbe.report",
    label: "fixture",
    spend: "not_live",
    audit_passed: true,
    observed_rule: "observed",
    tasks,
    release_gates: [],
    source: "results/fixture-pilot/report.json",
    source_kind: "fixture",
  };
}

const fixture = detail([
  {
    task_id: "ohbe-pilot-echo",
    task_content_hash: "a",
    note: "",
    harnesses: [row({ harness_id: "openbot" }), row({ harness_id: "inspect-react" })],
  },
  {
    task_id: "ohbe-pilot-exact",
    task_content_hash: "b",
    note: "",
    harnesses: [
      row({
        harness_id: "openbot",
        success_rate: "0.500000",
        success_rate_low: "0.094529",
        success_rate_high: "0.905471",
        valid_count: 2,
        usage_status: "present",
        cost_status: "computed",
        total_model_cost: "0.0009",
      }),
      row({
        harness_id: "inspect-react",
        success_rate: "0.500000",
        success_rate_low: "0.094529",
        success_rate_high: "0.905471",
        valid_count: 2,
        usage_status: "present",
      }),
    ],
  },
  {
    task_id: "ohbe-pilot-match",
    task_content_hash: "c",
    note: "",
    harnesses: [
      row({
        harness_id: "openbot",
        success_rate: "1.000000",
        success_rate_low: "0.206543",
        success_rate_high: null,
        valid_count: 1,
        invalid_count: 1,
        usage_status: "present",
        invalid_by_category: { timeout: 1 },
      }),
      row({
        harness_id: "inspect-react",
        success_rate: null,
        success_rate_low: "0.206543",
        success_rate_high: "1.000000",
        valid_count: 1,
        invalid_count: 1,
        usage_status: "invalid",
        invalid_by_category: { timeout: 1 },
      }),
    ],
  },
]);

test("paired intervals stay in report order and skip a null rate", () => {
  const points = pairedIntervals(fixture);
  assert.deepEqual(
    points.map((point) => [point.taskId, point.harnessId, point.rate]),
    [
      ["ohbe-pilot-echo", "openbot", 1],
      ["ohbe-pilot-echo", "inspect-react", 1],
      ["ohbe-pilot-exact", "openbot", 0.5],
      ["ohbe-pilot-exact", "inspect-react", 0.5],
      ["ohbe-pilot-match", "openbot", 1],
    ],
  );
  assert.equal(points[0]?.low, 0.342372);
  assert.equal(points[0]?.high, 1);
  assert.equal(points[4]?.low, null);
  assert.equal(points[4]?.high, null);
  assert.equal(points.some((point) => point.rate === 0 && point.harnessId === "inspect-react"), false);
});

test("a rate outside the unit interval and a reversed bound are not drawn as data", () => {
  const points = pairedIntervals(
    detail([
      {
        task_id: "only",
        task_content_hash: "a",
        note: "",
        harnesses: [
          row({ success_rate: "1.200000", success_rate_low: "0.1", success_rate_high: "1" }),
          row({ success_rate: "0.400000", success_rate_low: "0.900000", success_rate_high: "0.100000" }),
        ],
      },
    ]),
  );
  assert.equal(points.length, 1);
  assert.equal(points[0]?.rate, 0.4);
  assert.equal(points[0]?.low, null);
  assert.equal(points[0]?.high, null);
});

test("dimension bars keep missing usage out of a zero-cost reading", () => {
  const bars = harnessDimensionBars(fixture);
  assert.deepEqual(
    bars.map((item) => item.id),
    [...DIMENSIONS],
  );
  const cost = bars.find((item) => item.id === "cost");
  const effectiveness = bars.find((item) => item.id === "effectiveness");
  const diagnosability = bars.find((item) => item.id === "diagnosability");
  assert.deepEqual(
    cost?.marks.map((mark) => [mark.seriesId, mark.label, mark.ratio]),
    [
      ["openbot", "2/3", 2 / 3],
      ["inspect-react", "1/3", 1 / 3],
    ],
  );
  assert.equal(effectiveness?.marks[0]?.label, "0.83");
  assert.equal(effectiveness?.marks[1]?.ratio, 0.75);
  assert.deepEqual(
    diagnosability?.marks.map((mark) => mark.label),
    ["1/1", "1/1"],
  );
  const validity = bars.find((item) => item.id === "validity");
  assert.equal(validity?.marks[0]?.label, "4/5");
});

test("an empty invalid set does not become a perfect diagnosability bar", () => {
  const bars = harnessDimensionBars(
    detail([
      {
        task_id: "only",
        task_content_hash: "a",
        note: "",
        harnesses: [row({ success_rate: null, invalid_count: 0, invalid_by_category: {} })],
      },
    ]),
  );
  const diagnosability = bars.find((item) => item.id === "diagnosability");
  const effectiveness = bars.find((item) => item.id === "effectiveness");
  assert.equal(diagnosability?.marks[0]?.ratio, null);
  assert.equal(diagnosability?.marks[0]?.label, "—");
  assert.equal(effectiveness?.marks[0]?.ratio, null);
  assert.equal(effectiveness?.marks[0]?.label, "—");
});

test("cross-model bars keep payload order and do not plot a null rate as zero", () => {
  const metrics: FixedHarnessModelMetrics[] = [
    metric("openai/gpt-test-mini", "match--hard", "0.5"),
    metric("openai/gpt-test", "echo--easy", "1"),
    metric("openai/gpt-test-mini", "echo--easy", null),
  ];
  const bars = crossModelBars(metrics);
  assert.deepEqual(
    bars.map((item) => item.taskClass),
    ["match--hard", "echo--easy"],
  );
  assert.deepEqual(
    bars[1]?.marks.map((mark) => [mark.seriesId, mark.label, mark.ratio]),
    [
      ["openai/gpt-test-mini", "—", null],
      ["openai/gpt-test", "1.00", 1],
    ],
  );
});

function metric(modelId: string, taskClass: string, success: string | null): FixedHarnessModelMetrics {
  return {
    model_id: modelId,
    harness_id: "openbot",
    task_class: taskClass,
    success_rate: success,
    success_count: 1,
    valid_count: 2,
    total_model_cost: null,
    cost_per_success: null,
    steps: null,
    usage_status: success === null ? "missing" : "present",
  };
}
