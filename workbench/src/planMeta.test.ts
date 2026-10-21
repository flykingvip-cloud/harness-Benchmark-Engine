import assert from "node:assert/strict";
import test from "node:test";
import {
  filterPlans,
  heuristicTaskDraft,
  modeFromObjectKind,
  objectKindFromMode,
  observationSlotPreview,
} from "./planMeta.ts";
import type { RoundPlanSummary } from "./api.ts";

function plan(partial: Partial<RoundPlanSummary> & Pick<RoundPlanSummary, "plan_id" | "name" | "mode">): RoundPlanSummary {
  return {
    lifecycle: "draft_plan",
    harness_id: "openbot",
    seed: 7,
    rounds: 1,
    origin: "session",
    spend: "not_live",
    live_spend: "locked",
    industry: "finance",
    purpose: "customer_delivery",
    scenario_tags: ["office_assistant"],
    customer: "Acme",
    ...partial,
  };
}

test("object kind maps to round mode", () => {
  assert.equal(objectKindFromMode("paired_harness"), "harness");
  assert.equal(objectKindFromMode("cross_model"), "model");
  assert.equal(modeFromObjectKind("harness"), "paired_harness");
  assert.equal(modeFromObjectKind("model"), "cross_model");
});

test("list filters by object, industry, scenario, purpose, and query", () => {
  const rows = [
    plan({ plan_id: "a", name: "Office finance", mode: "paired_harness" }),
    plan({
      plan_id: "b",
      name: "Cost frontier",
      mode: "cross_model",
      industry: "general",
      purpose: "regression",
      scenario_tags: ["cost_sensitive"],
      customer: "Beta",
    }),
    plan({
      plan_id: "c",
      name: "Fixture paired",
      mode: "paired_harness",
      origin: "fixture",
      purpose: "fixture_demo",
      industry: "general",
      scenario_tags: ["office_assistant"],
    }),
  ];
  assert.deepEqual(
    filterPlans(rows, { objectKind: "harness", scenario: "all", industry: "all", purpose: "all", query: "" }).map((item) => item.plan_id),
    ["a", "c"],
  );
  assert.deepEqual(
    filterPlans(rows, { objectKind: "model", scenario: "cost_sensitive", industry: "general", purpose: "regression", query: "beta" }).map(
      (item) => item.plan_id,
    ),
    ["b"],
  );
  assert.deepEqual(
    filterPlans(rows, { objectKind: "harness", scenario: "all", industry: "all", purpose: "fixture_demo", query: "" }).map((item) => item.plan_id),
    ["c"],
  );
});

test("heuristic draft fills editable tasks without spend", () => {
  const drafted = heuristicTaskDraft({
    scenarioTags: ["office_assistant", "cost_sensitive"],
    industry: "finance",
    requirements: "Compare shells on office booking",
    timeoutSeconds: 30,
  });
  assert.ok(drafted.tasks.length >= 1);
  assert.equal(drafted.tasks[0]?.capability, "office_appointment");
  assert.match(drafted.tasks[0]?.prompt ?? "", /Compare shells/);
  assert.equal(drafted.domains[0], "finance");
});

test("observation preview lists subject plus multi references or model slots", () => {
  assert.deepEqual(
    observationSlotPreview({
      mode: "paired_harness",
      subject: "openbot",
      referenceHarnessIds: ["inspect-react", "openclaw"],
      modelIds: ["vllm/qwen3"],
    }),
    [
      { kind: "subject", id: "openbot" },
      { kind: "reference", id: "inspect-react" },
      { kind: "reference", id: "openclaw" },
    ],
  );
  assert.deepEqual(
    observationSlotPreview({
      mode: "cross_model",
      subject: "openbot",
      referenceHarnessIds: [],
      modelIds: ["vllm/a", "vllm/b"],
    }),
    [
      { kind: "model", id: "vllm/a" },
      { kind: "model", id: "vllm/b" },
    ],
  );
});
