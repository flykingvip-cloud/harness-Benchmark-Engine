import assert from "node:assert/strict";
import test from "node:test";
import type { HarnessResult, ResultDetail } from "./api.ts";
import { dimensionFacts } from "./dimensions.ts";

function row(overrides: Partial<HarnessResult> = {}): HarnessResult {
  return {
    harness_id: "openbot",
    success_rate: "1.000000",
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

function detail(tasks: ResultDetail["tasks"], gates = 2): ResultDetail {
  return {
    result_id: "fixture-pilot",
    kind: "ohbe.report",
    label: "fixture",
    spend: "not_live",
    audit_passed: true,
    observed_rule: "observed",
    tasks,
    release_gates: Array.from({ length: gates }, (_, index) => ({
      gate_id: `gate-${index}`,
      passed: index !== gates - 1 ? true : false,
      summary: "summary",
    })),
    source: "results/fixture-pilot/report.json",
    source_kind: "fixture",
  };
}

test("fixture-shaped rows keep missing cost out of the priced count", () => {
  const facts = dimensionFacts(
    detail([
      {
        task_id: "echo",
        task_content_hash: "a",
        note: "",
        harnesses: [
          row({ harness_id: "openbot", success_rate: "1.000000", valid_count: 2, success_count: 2 }),
          row({ harness_id: "inspect-react", success_rate: "1.000000", valid_count: 2, success_count: 2 }),
        ],
      },
      {
        task_id: "exact",
        task_content_hash: "b",
        note: "",
        harnesses: [
          row({
            harness_id: "openbot",
            success_rate: "0.500000",
            valid_count: 2,
            success_count: 1,
            task_failure_count: 1,
            total_model_cost: "0.0009",
            cost_status: "computed",
            usage_status: "present",
            cost_per_success: "0.0009",
          }),
          row({
            harness_id: "inspect-react",
            success_rate: "0.500000",
            valid_count: 2,
            success_count: 1,
            task_failure_count: 1,
            total_model_cost: "0.0009",
            cost_status: "computed",
            usage_status: "present",
            cost_per_success: "0.0009",
          }),
        ],
      },
      {
        task_id: "match",
        task_content_hash: "c",
        note: "",
        harnesses: [
          row({
            harness_id: "openbot",
            success_rate: "1.000000",
            valid_count: 1,
            invalid_count: 1,
            usage_status: "present",
            cost_status: "computed",
            total_model_cost: "0.00045",
            cost_per_success: "0.00045",
            invalid_by_category: { timeout: 1 },
          }),
          row({
            harness_id: "inspect-react",
            success_rate: "1.000000",
            valid_count: 1,
            invalid_count: 1,
            usage_status: "invalid",
            cost_status: "invalid",
            total_model_cost: null,
            cost_per_success: null,
            invalid_by_category: { timeout: 1 },
          }),
        ],
      },
    ]),
  );

  assert.equal(facts.validity.valid, 10);
  assert.equal(facts.validity.invalid, 2);
  assert.equal(facts.validity.taskFailures, 2);
  assert.equal(facts.effectiveness.meanSuccess, "0.833333");
  assert.equal(facts.effectiveness.delta, "0.000000");
  assert.equal(facts.effectiveness.pairedTasks, 3);
  assert.equal(facts.process.complete, 6);
  assert.equal(facts.cost.present, 3);
  assert.equal(facts.cost.missing, 2);
  assert.equal(facts.cost.invalid, 1);
  assert.equal(facts.cost.priced, 3);
  assert.deepEqual(facts.diagnosability.categories, [["timeout", 2]]);
  assert.equal(facts.diagnosability.gatesPassed, 1);
  assert.equal(facts.diagnosability.gatesTotal, 2);
});

test("a null cost is not a priced success and an empty report has no coverage", () => {
  const facts = dimensionFacts(
    detail(
      [
        {
          task_id: "only",
          task_content_hash: "a",
          note: "",
          harnesses: [row({ success_rate: null, complete: false, total_model_cost: null, cost_per_success: null, success_count: 4 })],
        },
      ],
      0,
    ),
  );
  assert.equal(facts.cost.priced, 0);
  assert.equal(facts.effectiveness.meanSuccess, null);
  assert.equal(facts.effectiveness.delta, null);
  assert.equal(facts.effectiveness.coverage, null);
  assert.equal(facts.process.coverage, 0);
  assert.equal(facts.diagnosability.coverage, null);
});
