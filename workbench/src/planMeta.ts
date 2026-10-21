import type { RoundMode, RoundPlanSummary, SuiteTask } from "./api";

export const SCENARIO_TAGS = [
  "office_assistant",
  "multi_step_tools",
  "long_context",
  "cost_sensitive",
  "reliability",
] as const;

export const INDUSTRIES = [
  "finance",
  "manufacturing",
  "retail",
  "government",
  "medical",
  "general",
] as const;

export const PURPOSES = ["customer_delivery", "regression", "fixture_demo"] as const;

export type ScenarioTag = (typeof SCENARIO_TAGS)[number];
export type IndustryId = (typeof INDUSTRIES)[number];
export type PurposeId = (typeof PURPOSES)[number];
export type ObjectKind = "harness" | "model";

export interface PlanClassification {
  scenarioTags: string[];
  industry: string;
  purpose: string;
  customer: string;
  requirements: string;
  referenceHarnessIds: string[];
}

export interface LocalTaskDraft {
  task_id: string;
  capability: string;
  difficulty: "easy" | "medium" | "hard";
  domain: string | null;
  prompt: string;
  repetitions: number;
}

export function objectKindFromMode(mode: RoundMode): ObjectKind {
  return mode === "cross_model" ? "model" : "harness";
}

export function modeFromObjectKind(kind: ObjectKind): RoundMode {
  return kind === "model" ? "cross_model" : "paired_harness";
}

export function defaultClassification(purpose: PurposeId = "customer_delivery"): PlanClassification {
  return {
    scenarioTags: [],
    industry: "general",
    purpose,
    customer: "",
    requirements: "",
    referenceHarnessIds: [],
  };
}

export function classificationFromSummary(plan: RoundPlanSummary): PlanClassification {
  return {
    scenarioTags: [...(plan.scenario_tags ?? [])],
    industry: plan.industry || "general",
    purpose: plan.purpose || (plan.origin === "fixture" ? "fixture_demo" : "customer_delivery"),
    customer: plan.customer ?? "",
    requirements: "",
    referenceHarnessIds: [],
  };
}

export function filterPlans(
  plans: readonly RoundPlanSummary[],
  opts: {
    objectKind: ObjectKind;
    scenario: string | "all";
    industry: string | "all";
    purpose: string | "all";
    query: string;
  },
): RoundPlanSummary[] {
  const q = opts.query.trim().toLowerCase();
  return plans.filter((plan) => {
    if (objectKindFromMode(plan.mode) !== opts.objectKind) {
      return false;
    }
    const tags = plan.scenario_tags ?? [];
    const industry = plan.industry || "general";
    const purpose = plan.purpose || (plan.origin === "fixture" ? "fixture_demo" : "customer_delivery");
    if (opts.scenario !== "all" && !tags.includes(opts.scenario)) {
      return false;
    }
    if (opts.industry !== "all" && industry !== opts.industry) {
      return false;
    }
    if (opts.purpose !== "all" && purpose !== opts.purpose) {
      return false;
    }
    if (!q) {
      return true;
    }
    const hay = `${plan.name} ${plan.plan_id} ${plan.customer ?? ""} ${plan.harness_id}`.toLowerCase();
    return hay.includes(q);
  });
}

const SCENARIO_CAPABILITY: Record<string, string> = {
  office_assistant: "office_appointment",
  multi_step_tools: "tool_chain",
  long_context: "long_context",
  cost_sensitive: "cost_qa",
  reliability: "fail_recover",
};

/** Heuristic draft: no LLM, no spend. Writes editable task rows from scenario + requirements. */
export function heuristicTaskDraft(input: {
  scenarioTags: readonly string[];
  industry: string;
  requirements: string;
  timeoutSeconds: number;
}): { capabilities: string[]; domains: string[]; tasks: LocalTaskDraft[]; easy: number; medium: number; hard: number } {
  const tags = input.scenarioTags.length > 0 ? [...input.scenarioTags] : ["office_assistant"];
  const capabilities = tags.map((tag) => SCENARIO_CAPABILITY[tag] ?? (tag.replace(/[^a-z0-9_]/gi, "_").slice(0, 32) || "scenario"));
  const domains = input.industry && input.industry !== "general" ? [input.industry] : [];
  const blurb = input.requirements.trim() || "Generated from scenario tags. Editable. Does not execute.";
  const tasks: LocalTaskDraft[] = capabilities.map((capability, index) => {
    const difficulty: LocalTaskDraft["difficulty"] = index === 0 ? "easy" : index === 1 ? "medium" : "hard";
    const domain = domains[0] ?? null;
    const task_id = domain ? `${capability}--${difficulty}--${domain}` : `${capability}--${difficulty}`;
    return {
      task_id,
      capability,
      difficulty,
      domain,
      prompt: `${blurb} · ${capability} · ${difficulty}${domain ? ` · ${domain}` : ""} · timeout ${input.timeoutSeconds}s`,
      repetitions: 1,
    };
  });
  return {
    capabilities,
    domains,
    tasks,
    easy: 1,
    medium: capabilities.length > 1 ? 1 : 0,
    hard: capabilities.length > 2 ? 1 : 0,
  };
}

export function localTasksFromSuite(tasks: readonly SuiteTask[]): LocalTaskDraft[] {
  return tasks.map((task) => ({
    task_id: task.task_id,
    capability: task.capability,
    difficulty: task.difficulty,
    domain: task.domain,
    prompt: task.prompt,
    repetitions: task.repetitions,
  }));
}

export function observationSlotPreview(input: {
  mode: RoundMode;
  subject: string;
  referenceHarnessIds: readonly string[];
  modelIds: readonly string[];
}): { kind: "subject" | "reference" | "model"; id: string }[] {
  if (input.mode === "paired_harness") {
    const rows: { kind: "subject" | "reference" | "model"; id: string }[] = [];
    if (input.subject) {
      rows.push({ kind: "subject", id: input.subject });
    }
    for (const id of input.referenceHarnessIds) {
      if (id && id !== input.subject) {
        rows.push({ kind: "reference", id });
      }
    }
    return rows;
  }
  return input.modelIds.filter(Boolean).map((id) => ({ kind: "model" as const, id }));
}

export function stageSummaryKey(lifecycle: string): string {
  if (lifecycle === "draft_plan") {
    return "rounds.stage.draft";
  }
  if (lifecycle === "confirmed_suite") {
    return "rounds.stage.confirmed";
  }
  if (lifecycle === "running") {
    return "rounds.stage.running";
  }
  if (lifecycle === "round_report") {
    return "rounds.stage.report";
  }
  if (lifecycle === "fork_plan") {
    return "rounds.stage.fork";
  }
  return "rounds.stage.draft";
}
