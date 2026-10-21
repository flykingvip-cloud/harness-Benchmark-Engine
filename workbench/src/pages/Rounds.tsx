import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  getJson,
  postJson,
  putJson,
  routes,
  type CatalogList,
  type CatalogModelList,
  type HarnessInventoryItem,
  type HarnessInventoryResponse,
  type PlanSlotsResponse,
  type RoundMode,
  type RoundPlanDetail,
  type RoundPlanList,
  type RoundPlanSummary,
  type SuiteTask,
  type SuiteView,
  type TaskSuite,
} from "../api";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { labelText, statusText, type Translate } from "../i18n/messages";
import {
  INDUSTRIES,
  PURPOSES,
  SCENARIO_TAGS,
  classificationFromSummary,
  defaultClassification,
  filterPlans,
  heuristicTaskDraft,
  localTasksFromSuite,
  modeFromObjectKind,
  objectKindFromMode,
  observationSlotPreview,
  stageSummaryKey,
  type LocalTaskDraft,
  type ObjectKind,
  type PlanClassification,
} from "../planMeta";
import { sameSlotIds, splitTokens } from "../rounds";
import { admittedPoolId, recommendSlots, slotIdsForConfirm, unifiedSlotCandidates } from "../slots";
import { describeFailure, useLoad } from "../useLoad";
import { ComparisonSlots } from "./ComparisonSlots";

const PARAMETERS: Record<string, Record<string, string>> = {
  openbot: { profile: "litellm-only" },
  "inspect-react": { solver: "react" },
  openclaw: { profile: "litellm-only" },
};

interface PlanSession {
  planId: string | null;
  mode: RoundMode;
  name: string;
  seed: number;
  rounds: number;
  subject: string;
  capabilities: string;
  domains: string;
  easy: number;
  medium: number;
  hard: number;
  timeout: number;
  stopOnBudget: boolean;
  slots: string[];
  slotsStatus: "draft" | "frozen";
  detail: RoundPlanDetail | null;
  suite: TaskSuite | null;
  prompts: Record<string, string>;
  poolKind: "admitted" | "fixture" | null;
  poolModelIds: string[];
  classification: PlanClassification;
  localTasks: LocalTaskDraft[];
}

function whole(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.trunc(value);
}

function draftOf(item: HarnessInventoryItem) {
  return {
    harness_id: item.harness_id,
    name: item.name,
    version: item.whitelist_version ?? "",
    parameters: PARAMETERS[item.harness_id] ?? {},
  };
}

function draftTask(task: SuiteTask | LocalTaskDraft) {
  return {
    task_id: task.task_id,
    capability: task.capability,
    difficulty: task.difficulty,
    domain: task.domain,
    prompt: task.prompt,
    repetitions: task.repetitions,
  };
}

function weightOf(mix: { difficulty: string; weight: number }[], name: string): number {
  return mix.find((item) => item.difficulty === name)?.weight ?? 0;
}

function blankComposer(subjectId: string, mode: RoundMode): PlanSession {
  return {
    planId: null,
    mode,
    name: mode === "cross_model" ? "Model comparison plan" : "Harness comparison plan",
    seed: 7,
    rounds: 1,
    subject: subjectId,
    capabilities: "echo",
    domains: "",
    easy: 1,
    medium: 0,
    hard: 0,
    timeout: 30,
    stopOnBudget: true,
    slots: [],
    slotsStatus: "draft",
    detail: null,
    suite: null,
    prompts: {},
    poolKind: null,
    poolModelIds: [],
    classification: defaultClassification("customer_delivery"),
    localTasks: [],
  };
}

function sessionFromDetail(detail: RoundPlanDetail, suite: TaskSuite | null): PlanSession {
  const plan = detail.plan;
  const labels = detail.labels;
  const classification: PlanClassification = {
    scenarioTags: [...(labels?.scenario_tags ?? [])],
    industry: labels?.industry || "general",
    purpose: labels?.purpose || (detail.origin === "fixture" ? "fixture_demo" : "customer_delivery"),
    customer: labels?.customer ?? "",
    requirements: labels?.requirements ?? "",
    referenceHarnessIds:
      labels?.reference_harness_ids?.length
        ? [...labels.reference_harness_ids]
        : plan.reference_harness_ref
          ? [plan.reference_harness_ref.harness_id]
          : [],
  };
  const localTasks = suite ? localTasksFromSuite(suite.tasks) : [];
  return {
    planId: plan.plan_id,
    mode: plan.mode,
    name: plan.name,
    seed: plan.seed,
    rounds: plan.rounds,
    subject: plan.harness_ref.harness_id,
    capabilities: plan.taxonomy.capabilities.join(", "),
    domains: plan.taxonomy.domains.join(", "),
    easy: weightOf(plan.difficulty_mix, "easy"),
    medium: weightOf(plan.difficulty_mix, "medium"),
    hard: weightOf(plan.difficulty_mix, "hard"),
    timeout: plan.budget.timeout_seconds,
    stopOnBudget: plan.stop.stop_on_budget,
    slots: [...detail.comparison_model_ids],
    slotsStatus: detail.slots_status,
    detail,
    suite,
    prompts: suite ? Object.fromEntries(suite.tasks.map((task) => [task.task_id, task.prompt])) : {},
    poolKind: detail.pool_kind,
    poolModelIds: [...detail.pool_model_ids],
    classification,
    localTasks,
  };
}

async function loadPlanSession(planId: string): Promise<PlanSession> {
  const detail = await getJson<RoundPlanDetail>(`${routes.roundPlans}/${planId}`);
  let suite: TaskSuite | null = null;
  if (detail.suite_id) {
    const view = await getJson<SuiteView>(`${routes.roundSuites}/${detail.suite_id}`);
    suite = view.suite;
  }
  return sessionFromDetail(detail, suite);
}

function toggleToken(list: string[], token: string): string[] {
  return list.includes(token) ? list.filter((item) => item !== token) : [...list, token];
}

async function recommendAdmittedSlotIds(): Promise<string[]> {
  const [catalog, files] = await Promise.all([
    getJson<CatalogModelList>(routes.catalogModels),
    getJson<CatalogList>(routes.modelCatalog),
  ]);
  return recommendSlots(unifiedSlotCandidates(catalog.models ?? [], files.entries ?? []), 3).map((item) => item.id);
}

export function RoundsPage() {
  const { t } = useI18n();
  const [nonce, setNonce] = useState(0);
  const plans = useLoad<RoundPlanList>(`${routes.roundPlans}?t=${nonce}`);
  const [activePlanId, setActivePlanId] = useState<string | null>(null);
  const [readyId, setReadyId] = useState<string | null>(null);
  const [composer, setComposer] = useState<PlanSession | null>(null);
  const [sessions, setSessions] = useState<Record<string, PlanSession>>({});
  const [picking, setPicking] = useState(true);
  const [objectKind, setObjectKind] = useState<ObjectKind>("harness");
  const [scenarioFilter, setScenarioFilter] = useState<string | "all">("all");
  const [industryFilter, setIndustryFilter] = useState<string | "all">("all");
  const [purposeFilter, setPurposeFilter] = useState<string | "all">("all");
  const [query, setQuery] = useState("");
  const inventory = useLoad<HarnessInventoryResponse>(routes.catalogHarnesses);
  const whitelisted = (inventory.data?.harnesses ?? []).filter((item) => item.plans_whitelisted && item.whitelist_version);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const requestSeq = useRef(0);
  const composerRef = useRef(composer);
  const activePlanIdRef = useRef(activePlanId);
  const sessionsRef = useRef(sessions);
  composerRef.current = composer;
  activePlanIdRef.current = activePlanId;
  sessionsRef.current = sessions;

  const session = composer ?? (activePlanId !== null && readyId === activePlanId ? sessions[activePlanId] ?? null : null);
  const showSession = session !== null && !picking;
  const confirmed = session?.suite?.status === "confirmed";

  const filtered = useMemo(() => {
    const rows = plans.data?.plans ?? [];
    return filterPlans(rows, {
      objectKind,
      scenario: scenarioFilter,
      industry: industryFilter,
      purpose: purposeFilter,
      query,
    });
  }, [plans.data, objectKind, scenarioFilter, industryFilter, purposeFilter, query]);

  const counts = useMemo(() => {
    const rows = plans.data?.plans ?? [];
    return {
      harness: rows.filter((plan) => objectKindFromMode(plan.mode) === "harness").length,
      model: rows.filter((plan) => objectKindFromMode(plan.mode) === "model").length,
    };
  }, [plans.data]);

  useEffect(() => {
    const rows = (inventory.data?.harnesses ?? []).filter((item) => item.plans_whitelisted && item.whitelist_version);
    setComposer((current) => {
      if (!current) {
        return current;
      }
      let next = current;
      if (!current.subject || !rows.some((item) => item.harness_id === current.subject)) {
        next = { ...next, subject: rows[0]?.harness_id ?? "" };
      }
      if (
        next.mode === "paired_harness" &&
        next.classification.referenceHarnessIds.length === 0 &&
        rows.length > 0
      ) {
        const partner = rows.find((item) => item.harness_id !== next.subject);
        if (partner) {
          next = {
            ...next,
            classification: { ...next.classification, referenceHarnessIds: [partner.harness_id] },
          };
        }
      }
      return next;
    });
  }, [inventory.data]);

  function fail(error: unknown) {
    if (error instanceof ApiError) {
      setFailure(error);
      return;
    }
    const described = describeFailure(error);
    setFailure(new ApiError(0, described.code, described.message ?? ""));
  }

  function edit(patch: Partial<PlanSession>) {
    if (composerRef.current) {
      setComposer((current) => (current ? { ...current, ...patch } : current));
      return;
    }
    const planId = activePlanIdRef.current;
    if (!planId) {
      return;
    }
    setSessions((current) => {
      const row = current[planId];
      if (!row) {
        return current;
      }
      return { ...current, [planId]: { ...row, ...patch } };
    });
  }

  function editClassification(patch: Partial<PlanClassification>) {
    const source = composerRef.current ?? (activePlanIdRef.current ? sessionsRef.current[activePlanIdRef.current] : null);
    if (!source) {
      return;
    }
    edit({ classification: { ...source.classification, ...patch } });
  }

  function addPlan() {
    requestSeq.current += 1;
    setActivePlanId(null);
    setReadyId(null);
    setPicking(false);
    setFailure(null);
    setComposer(blankComposer(whitelisted[0]?.harness_id ?? "", modeFromObjectKind(objectKind)));
  }

  async function openPlan(planId: string) {
    const seq = ++requestSeq.current;
    setComposer(null);
    setActivePlanId(planId);
    setReadyId(null);
    setPicking(false);
    setPending(true);
    setFailure(null);
    try {
      const loaded = await loadPlanSession(planId);
      if (requestSeq.current !== seq) {
        return;
      }
      setObjectKind(objectKindFromMode(loaded.mode));
      setSessions((current) => ({ ...current, [planId]: loaded }));
      setReadyId(planId);
    } catch (error) {
      if (requestSeq.current !== seq) {
        return;
      }
      fail(error);
    } finally {
      if (requestSeq.current === seq) {
        setPending(false);
      }
    }
  }

  function backToList() {
    setPicking(true);
    setComposer(null);
    setActivePlanId(null);
    setReadyId(null);
    setFailure(null);
  }

  async function updateSlots(next: string[]) {
    const ids = slotIdsForConfirm(next);
    if (composerRef.current) {
      setComposer((current) => (current ? { ...current, slots: ids } : current));
      return;
    }
    const planId = activePlanIdRef.current;
    const row = planId ? sessionsRef.current[planId] : null;
    if (!planId || !row || row.slotsStatus === "frozen" || row.poolKind === "fixture") {
      return;
    }
    setPending(true);
    setFailure(null);
    try {
      const saved = await putJson<PlanSlotsResponse>(`${routes.roundPlans}/${planId}/slots`, { model_ids: ids });
      if (activePlanIdRef.current !== planId || composerRef.current) {
        return;
      }
      setSessions((current) => {
        const existing = current[planId];
        if (!existing) {
          return current;
        }
        return {
          ...current,
          [planId]: {
            ...existing,
            slots: [...saved.model_ids],
            slotsStatus: saved.status,
            detail: existing.detail
              ? { ...existing.detail, comparison_model_ids: [...saved.model_ids], slots_status: saved.status }
              : existing.detail,
          },
        };
      });
    } catch (error) {
      if (activePlanIdRef.current === planId && !composerRef.current) {
        fail(error);
      }
    } finally {
      if (activePlanIdRef.current === planId && !composerRef.current) {
        setPending(false);
      }
    }
  }

  function applyHeuristicDraft() {
    const source = composerRef.current ?? (activePlanIdRef.current ? sessionsRef.current[activePlanIdRef.current] : null);
    if (!source) {
      return;
    }
    const drafted = heuristicTaskDraft({
      scenarioTags: source.classification.scenarioTags,
      industry: source.classification.industry,
      requirements: source.classification.requirements,
      timeoutSeconds: Math.max(1, whole(source.timeout)),
    });
    edit({
      capabilities: drafted.capabilities.join(", "),
      domains: drafted.domains.join(", "),
      easy: drafted.easy,
      medium: drafted.medium,
      hard: drafted.hard,
      localTasks: drafted.tasks,
      prompts: Object.fromEntries(drafted.tasks.map((task) => [task.task_id, task.prompt])),
      rounds: Math.max(1, drafted.tasks.length),
    });
  }

  async function persistGenerated() {
    const source = composerRef.current ?? (activePlanIdRef.current ? sessionsRef.current[activePlanIdRef.current] : null);
    if (!source) {
      return;
    }
    if (source.localTasks.length === 0) {
      applyHeuristicDraft();
    }
    const latest = composerRef.current ?? (activePlanIdRef.current ? sessionsRef.current[activePlanIdRef.current] : null);
    if (!latest || latest.localTasks.length === 0) {
      setFailure(new ApiError(0, "invalid_request", t("rounds.needAxis")));
      return;
    }
    const capabilityTokens = splitTokens(latest.capabilities);
    const mix = [
      { difficulty: "easy" as const, weight: whole(latest.easy) },
      { difficulty: "medium" as const, weight: whole(latest.medium) },
      { difficulty: "hard" as const, weight: whole(latest.hard) },
    ].filter((item) => item.weight > 0);
    if (capabilityTokens.length === 0 || mix.length === 0) {
      setFailure(new ApiError(0, "invalid_request", t("rounds.needAxis")));
      return;
    }
    const subjectItem = whitelisted.find((item) => item.harness_id === latest.subject) ?? null;
    const refs = latest.classification.referenceHarnessIds
      .map((id) => whitelisted.find((item) => item.harness_id === id) ?? null)
      .filter((item): item is HarnessInventoryItem => item !== null && item.harness_id !== latest.subject);
    if (!subjectItem) {
      setFailure(new ApiError(0, "harness_not_whitelisted", t("rounds.needHarness")));
      return;
    }
    if (latest.mode === "paired_harness" && refs.length === 0) {
      setFailure(new ApiError(0, "harness_not_whitelisted", t("rounds.needPartner")));
      return;
    }
    let slotIds = slotIdsForConfirm(latest.slots);
    if (slotIds.length === 0) {
      try {
        slotIds = await recommendAdmittedSlotIds();
      } catch (error) {
        fail(error);
        return;
      }
      if (slotIds.length > 0) {
        edit({ slots: slotIds });
      }
    }
    const poolId = admittedPoolId(slotIds);
    const ids = slotIds;
    if (!poolId) {
      setFailure(new ApiError(0, "slots_required", t("rounds.needSlots")));
      return;
    }
    const seq = ++requestSeq.current;
    setPending(true);
    setFailure(null);
    try {
      let planId = latest.planId;
      let createdDetail: RoundPlanDetail | null = null;
      if (!planId) {
        createdDetail = await postJson<RoundPlanDetail>(routes.roundPlans, {
          name: latest.name,
          seed: whole(latest.seed),
          rounds: Math.max(1, whole(latest.rounds)),
          mode: latest.mode,
          harness: draftOf(subjectItem),
          reference_harness: latest.mode === "paired_harness" && refs[0] ? draftOf(refs[0]) : null,
          pool_id: poolId,
          model_ids: ids,
          difficulty_mix: mix,
          capabilities: capabilityTokens,
          domains: splitTokens(latest.domains),
          timeout_seconds: Math.max(1, whole(latest.timeout)),
          stop_max_rounds: Math.max(1, whole(latest.rounds)),
          stop_on_budget: latest.stopOnBudget,
          labels: {
            scenario_tags: latest.classification.scenarioTags,
            industry: latest.classification.industry,
            purpose: latest.classification.purpose,
            customer: latest.classification.customer || null,
            requirements: latest.classification.requirements || null,
            reference_harness_ids: latest.classification.referenceHarnessIds,
          },
          live: false,
        });
        planId = createdDetail.plan.plan_id;
      }
      const tasks = latest.localTasks.map((task) => ({
        ...draftTask(task),
        prompt: latest.prompts[task.task_id] ?? task.prompt,
      }));
      const drafted = await postJson<SuiteView>(`${routes.roundPlans}/${planId}/suite`, { tasks, live: false });
      const detail = createdDetail ?? (await getJson<RoundPlanDetail>(`${routes.roundPlans}/${planId}`));
      const next = sessionFromDetail(
        { ...detail, suite_id: drafted.suite.suite_id, lifecycle: drafted.lifecycle },
        drafted.suite,
      );
      next.localTasks = localTasksFromSuite(drafted.suite.tasks);
      next.prompts = Object.fromEntries(drafted.suite.tasks.map((task) => [task.task_id, latest.prompts[task.task_id] ?? task.prompt]));
      next.classification = {
        ...next.classification,
        ...latest.classification,
      };
      setSessions((current) => ({ ...current, [planId!]: next }));
      setNonce((value) => value + 1);
      if (requestSeq.current !== seq) {
        return;
      }
      setComposer(null);
      setActivePlanId(planId);
      setReadyId(planId);
      setPicking(false);
    } catch (error) {
      if (requestSeq.current === seq) {
        fail(error);
      }
    } finally {
      if (requestSeq.current === seq) {
        setPending(false);
      }
    }
  }

  async function saveDraft() {
    const planId = activePlanIdRef.current;
    const source = planId ? sessionsRef.current[planId] : composerRef.current;
    if (!source) {
      return;
    }
    if (!source.planId) {
      await persistGenerated();
      return;
    }
    if (!source.suite && source.localTasks.length === 0) {
      return;
    }
    setPending(true);
    setFailure(null);
    try {
      const tasks = (source.localTasks.length > 0 ? source.localTasks : source.suite?.tasks ?? []).map((task) => ({
        ...draftTask(task),
        prompt: source.prompts[task.task_id] ?? task.prompt,
      }));
      const view = await postJson<SuiteView>(`${routes.roundPlans}/${source.planId}/suite`, { tasks, live: false });
      if (activePlanIdRef.current !== source.planId) {
        return;
      }
      setSessions((current) => {
        const row = current[source.planId!];
        if (!row) {
          return current;
        }
        return {
          ...current,
          [source.planId!]: {
            ...row,
            suite: view.suite,
            localTasks: localTasksFromSuite(view.suite.tasks),
            prompts: Object.fromEntries(view.suite.tasks.map((task) => [task.task_id, source.prompts[task.task_id] ?? task.prompt])),
            detail: row.detail ? { ...row.detail, lifecycle: view.lifecycle, suite_id: view.suite.suite_id } : row.detail,
          },
        };
      });
    } catch (error) {
      if (activePlanIdRef.current === source.planId) {
        fail(error);
      }
    } finally {
      setPending(false);
    }
  }

  async function confirm() {
    const planId = activePlanIdRef.current;
    let source = planId ? sessionsRef.current[planId] : null;
    if (!source?.planId) {
      await persistGenerated();
      source = activePlanIdRef.current ? sessionsRef.current[activePlanIdRef.current] : null;
    }
    if (!source?.planId) {
      return;
    }
    const slotIds = source.slots;
    setPending(true);
    setFailure(null);
    try {
      const view = await postJson<SuiteView>(`${routes.roundPlans}/${source.planId}/confirm`, {
        live: false,
        model_ids: slotIdsForConfirm(slotIds),
      });
      if (activePlanIdRef.current !== source.planId) {
        return;
      }
      setSessions((current) => {
        const row = current[source!.planId!];
        if (!row) {
          return current;
        }
        return {
          ...current,
          [source!.planId!]: {
            ...row,
            suite: view.suite,
            localTasks: localTasksFromSuite(view.suite.tasks),
            prompts: Object.fromEntries(view.suite.tasks.map((task) => [task.task_id, task.prompt])),
            slots: [...view.comparison_model_ids],
            slotsStatus: view.slots_status,
            detail: row.detail
              ? {
                  ...row.detail,
                  lifecycle: view.lifecycle,
                  suite_id: view.suite.suite_id,
                  comparison_model_ids: [...view.comparison_model_ids],
                  slots_status: view.slots_status,
                }
              : row.detail,
          },
        };
      });
      setNonce((value) => value + 1);
      setPicking(false);
    } catch (error) {
      if (activePlanIdRef.current === source.planId) {
        fail(error);
      }
    } finally {
      setPending(false);
    }
  }

  const subjectItem = session ? whitelisted.find((item) => item.harness_id === session.subject) ?? null : null;
  const slotIds = session?.slots ?? [];
  const nextPoolId = admittedPoolId(slotIds);
  const slotsLocked = session ? session.slotsStatus === "frozen" || session.poolKind === "fixture" : true;
  const drifted = Boolean(
    session?.detail &&
      session.poolKind === "admitted" &&
      session.slotsStatus === "draft" &&
      !sameSlotIds(session.slots, session.poolModelIds),
  );
  const modeLocked = Boolean(session?.planId);
  const preview = session
    ? observationSlotPreview({
        mode: session.mode,
        subject: session.subject,
        referenceHarnessIds: session.classification.referenceHarnessIds,
        modelIds: session.slots,
      })
    : [];

  return (
    <section className="plan-stack">
      <header className="page-head">
        <div className="plan-head-row">
          <h1>{t("rounds.title")}</h1>
          <p className="prepare-jump">
            <span>{t("rounds.prepareShort")}</span>{" "}
            <Link className="text-link" to="/prepare?tab=pools">
              {t("nav.pools")}
            </Link>
            <span aria-hidden="true"> · </span>
            <Link className="text-link" to="/prepare?tab=harnesses">
              {t("nav.harnesses")}
            </Link>
          </p>
        </div>
        <p>{t("rounds.lead")}</p>
      </header>
      {!inventory.loading && whitelisted.length === 0 ? <p className="callout">{t("rounds.needHarness")}</p> : null}

      <ErrorBanner message={failure?.message} code={failure?.code} />
      <ErrorBanner message={plans.error} code={plans.errorCode} />
      <ErrorBanner message={inventory.error} code={inventory.errorCode} />

      {!showSession ? (
        <div className="block plan-current" data-testid="candidate-plans">
          <div className="object-tabs" role="tablist" aria-label={t("rounds.objectTabs")} data-testid="object-tabs">
            <button
              type="button"
              role="tab"
              className="object-tab"
              aria-selected={objectKind === "harness"}
              data-testid="tab-harness"
              onClick={() => setObjectKind("harness")}
            >
              {t("rounds.tab.harness")}
              <span className="tab-count">{counts.harness}</span>
            </button>
            <button
              type="button"
              role="tab"
              className="object-tab"
              aria-selected={objectKind === "model"}
              data-testid="tab-model"
              onClick={() => setObjectKind("model")}
            >
              {t("rounds.tab.model")}
              <span className="tab-count">{counts.model}</span>
            </button>
          </div>

          <div className="plan-filters" data-testid="plan-filters">
            <div className="filter-row">
              <span className="filter-label">{t("rounds.filter.scenario")}</span>
              <div className="chip-row">
                <button type="button" className={scenarioFilter === "all" ? "chip on" : "chip"} onClick={() => setScenarioFilter("all")}>
                  {t("rounds.filter.all")}
                </button>
                {SCENARIO_TAGS.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    className={scenarioFilter === tag ? "chip on" : "chip"}
                    onClick={() => setScenarioFilter(tag)}
                  >
                    {labelText(t, `rounds.scenario.${tag}`)}
                  </button>
                ))}
              </div>
            </div>
            <div className="filter-row">
              <span className="filter-label">{t("rounds.filter.industry")}</span>
              <div className="chip-row">
                <button type="button" className={industryFilter === "all" ? "chip on" : "chip"} onClick={() => setIndustryFilter("all")}>
                  {t("rounds.filter.all")}
                </button>
                {INDUSTRIES.map((id) => (
                  <button
                    key={id}
                    type="button"
                    className={industryFilter === id ? "chip on" : "chip"}
                    onClick={() => setIndustryFilter(id)}
                  >
                    {labelText(t, `rounds.industry.${id}`)}
                  </button>
                ))}
              </div>
            </div>
            <div className="filter-row">
              <span className="filter-label">{t("rounds.filter.purpose")}</span>
              <div className="chip-row">
                <button type="button" className={purposeFilter === "all" ? "chip on" : "chip"} onClick={() => setPurposeFilter("all")}>
                  {t("rounds.filter.all")}
                </button>
                {PURPOSES.map((id) => (
                  <button
                    key={id}
                    type="button"
                    className={purposeFilter === id ? "chip on" : "chip"}
                    onClick={() => setPurposeFilter(id)}
                  >
                    {labelText(t, `rounds.purpose.${id}`)}
                  </button>
                ))}
              </div>
            </div>
            <div className="filter-actions">
              <input
                className="plan-search"
                value={query}
                placeholder={t("rounds.searchPlaceholder")}
                onChange={(event) => setQuery(event.target.value)}
                data-testid="plan-search"
              />
              <button type="button" data-testid="add-plan" onClick={addPlan} disabled={pending}>
                + {t("rounds.addPlan")}
              </button>
            </div>
          </div>

          {plans.loading ? <p className="muted">{t("rounds.loading")}</p> : null}
          {!plans.loading && filtered.length === 0 ? <p className="muted">{t("rounds.empty")}</p> : null}
          <div className="plan-card-list" data-testid="plan-card-list">
            {filtered.map((plan) => (
              <PlanCard key={plan.plan_id} plan={plan} pending={pending} onOpen={() => void openPlan(plan.plan_id)} t={t} />
            ))}
          </div>
        </div>
      ) : null}

      {showSession && session ? (
        <div data-testid="plan-session" data-plan-id={session.planId ?? ""} key={session.planId ?? "composer"}>
          <div className="section-bar">
            <button type="button" className="secondary" data-testid="back-to-plans" onClick={backToList}>
              ← {t("rounds.backToList")}
            </button>
            <span className="pill">{statusText(t, session.mode)}</span>
            {session.suite ? (
              <span className={session.suite.status === "confirmed" ? "pill ok" : "pill warn"}>{statusText(t, session.suite.status)}</span>
            ) : null}
          </div>

          {confirmed && session.planId ? (
            <>
              <div className="handoff-cta" data-testid="confirmed-handoff">
                <div>
                  <p className="handoff-title">{t("rounds.confirmedTitle")}</p>
                  <p className="handoff-hint">{t("rounds.confirmedHint")}</p>
                </div>
                <Link className="monitor-go monitor-go-cta" data-testid="go-monitor" to={`/monitor?plan=${encodeURIComponent(session.planId)}`}>
                  {t("monitor.go")} →
                </Link>
              </div>
              <div className="block frozen-summary" data-testid="frozen-summary">
                <h2 className="section-title">{t("rounds.summary")}</h2>
                <p className="note">
                  {t(session.mode === "cross_model" ? "rounds.tab.model" : "rounds.tab.harness")} · {labelText(t, `rounds.industry.${session.classification.industry}`)} ·{" "}
                  {session.classification.scenarioTags.map((tag) => labelText(t, `rounds.scenario.${tag}`)).join(" · ") || t("rounds.filter.all")} ·{" "}
                  {session.localTasks.length} {t("rounds.tasksUnit")} · Dry-run
                </p>
              </div>
            </>
          ) : (
            <>
              <div id="plan-editor" data-testid="plan-editor" className="plan-editor-sections">
                <section className="block plan-section" data-testid="section-identity">
                  <h2 className="section-title">{t("rounds.section.identity")}</h2>
                  <div className="form-grid" data-testid="identity-fields">
                    <label>
                      {t("rounds.object")}
                      <input value={t(session.mode === "cross_model" ? "rounds.tab.model" : "rounds.tab.harness")} disabled />
                    </label>
                    <label>
                      {t("rounds.name")}
                      <input value={session.name} onChange={(event) => edit({ name: event.target.value })} />
                    </label>
                    <label>
                      {t("rounds.customer")}
                      <input
                        value={session.classification.customer}
                        onChange={(event) => editClassification({ customer: event.target.value })}
                      />
                    </label>
                    <label>
                      {t("rounds.filter.purpose")}
                      <select
                        value={session.classification.purpose}
                        onChange={(event) => editClassification({ purpose: event.target.value })}
                      >
                        {PURPOSES.map((id) => (
                          <option key={id} value={id}>
                            {labelText(t, `rounds.purpose.${id}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("rounds.filter.scenario")}
                      <select
                        data-testid="identity-scenario"
                        aria-label={t("rounds.filter.scenario")}
                        value=""
                        onChange={(event) => {
                          const tag = event.target.value;
                          if (!tag) {
                            return;
                          }
                          editClassification({ scenarioTags: toggleToken(session.classification.scenarioTags, tag) });
                        }}
                      >
                        <option value="">
                          {session.classification.scenarioTags.length
                            ? session.classification.scenarioTags.map((tag) => labelText(t, `rounds.scenario.${tag}`)).join(" · ")
                            : t("rounds.scenario.placeholder")}
                        </option>
                        {SCENARIO_TAGS.map((tag) => (
                          <option key={tag} value={tag}>
                            {session.classification.scenarioTags.includes(tag) ? "✓ " : ""}
                            {labelText(t, `rounds.scenario.${tag}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("rounds.filter.industry")}
                      <select
                        data-testid="identity-industry"
                        aria-label={t("rounds.filter.industry")}
                        value={session.classification.industry}
                        onChange={(event) => editClassification({ industry: event.target.value })}
                      >
                        {INDUSTRIES.map((id) => (
                          <option key={id} value={id}>
                            {labelText(t, `rounds.industry.${id}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      {t("rounds.col.mode")}
                      <select
                        data-testid="identity-mode"
                        aria-label={t("rounds.col.mode")}
                        value={session.mode}
                        disabled={modeLocked}
                        title={t(session.mode === "cross_model" ? "rounds.mode.crossNote" : "rounds.mode.pairedNote")}
                        onChange={(event) => {
                          const mode = event.target.value as RoundMode;
                          edit({
                            mode,
                            name:
                              session.name ||
                              (mode === "cross_model" ? "Model comparison plan" : "Harness comparison plan"),
                          });
                        }}
                      >
                        <option value="paired_harness" title={t("rounds.mode.pairedNote")}>
                          {t("rounds.mode.paired")}
                        </option>
                        <option value="cross_model" title={t("rounds.mode.crossNote")}>
                          {t("rounds.mode.cross")}
                        </option>
                      </select>
                    </label>
                  </div>
                  {modeLocked ? <p className="note">{t("rounds.modeLocked")}</p> : (
                    <p className="note identity-mode-note">{t(session.mode === "cross_model" ? "rounds.mode.crossNote" : "rounds.mode.pairedNote")}</p>
                  )}
                </section>

                <section className="block plan-section" data-testid="section-requirements">
                  <h2 className="section-title">{t("rounds.section.requirements")}</h2>
                  <label className="stack-label">
                    {t("rounds.requirements")}
                    <textarea
                      className="requirements"
                      rows={4}
                      value={session.classification.requirements}
                      onChange={(event) => editClassification({ requirements: event.target.value })}
                    />
                  </label>
                  <div className="row">
                    <button type="button" onClick={applyHeuristicDraft} disabled={pending} data-testid="generate-draft">
                      {t("rounds.generateDraft")}
                    </button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        editClassification({ scenarioTags: session.classification.scenarioTags.length ? session.classification.scenarioTags : ["office_assistant"] });
                        applyHeuristicDraft();
                      }}
                      disabled={pending}
                    >
                      {t("rounds.useTemplate")}
                    </button>
                  </div>
                  <p className="note">{t("rounds.generateDraftNote")}</p>
                </section>

                <section className="block plan-section" data-testid="section-tasks">
                  <div className="section-bar">
                    <h2 className="section-title">{t("rounds.section.tasks")}</h2>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        const capability = "custom_task";
                        const difficulty = "easy" as const;
                        const task_id = `${capability}--${difficulty}`;
                        const task: LocalTaskDraft = {
                          task_id,
                          capability,
                          difficulty,
                          domain: session.classification.industry !== "general" ? session.classification.industry : null,
                          prompt: session.classification.requirements || "Custom task",
                          repetitions: 1,
                        };
                        edit({
                          localTasks: [...session.localTasks, task],
                          prompts: { ...session.prompts, [task_id]: task.prompt },
                        });
                      }}
                    >
                      + {t("rounds.addTask")}
                    </button>
                  </div>
                  {session.localTasks.length === 0 ? <p className="muted">{t("rounds.suiteEmpty")}</p> : <p className="callout">{t("rounds.draftLead")}</p>}
                  <ul className="task-draft-list">
                    {session.localTasks.map((task, index) => (
                      <li key={task.task_id} data-testid="task-draft-row">
                        <div>
                          <strong>
                            {t("rounds.taskN", { n: String(index + 1) })}: {task.capability} · {task.difficulty} · {session.timeout}s
                          </strong>
                          <textarea
                            className="prompt"
                            value={session.prompts[task.task_id] ?? task.prompt}
                            onChange={(event) => edit({ prompts: { ...session.prompts, [task.task_id]: event.target.value } })}
                          />
                        </div>
                        <button
                          type="button"
                          className="secondary"
                          onClick={() =>
                            edit({
                              localTasks: session.localTasks.filter((row) => row.task_id !== task.task_id),
                            })
                          }
                        >
                          {t("rounds.removeTask")}
                        </button>
                      </li>
                    ))}
                  </ul>
                  <div className="row">
                    <button type="button" className="secondary" onClick={() => void persistGenerated()} disabled={pending || !subjectItem}>
                      {pending ? t("rounds.generating") : t("rounds.generate")}
                    </button>
                  </div>
                </section>

                <section className="block plan-section" data-testid="section-compare">
                  <h2 className="section-title">{t("rounds.section.compare")}</h2>
                  {session.mode === "paired_harness" ? (
                    <>
                      <div className="form-grid" data-testid="compare-fields">
                        <label>
                          {t("rounds.subject")}
                          <select
                            data-testid="compare-subject"
                            aria-label={t("rounds.subject")}
                            value={session.subject}
                            onChange={(event) => edit({ subject: event.target.value })}
                            disabled={whitelisted.length === 0 || modeLocked}
                          >
                            {whitelisted.length === 0 ? <option value="">{t("rounds.needHarness")}</option> : null}
                            {whitelisted.map((item) => (
                              <option key={item.harness_id} value={item.harness_id}>
                                {item.harness_id}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          {t("rounds.referenceMulti")}
                          <select
                            data-testid="compare-reference"
                            aria-label={t("rounds.referenceMulti")}
                            value=""
                            disabled={whitelisted.length === 0 || modeLocked}
                            onChange={(event) => {
                              const id = event.target.value;
                              if (!id) {
                                return;
                              }
                              editClassification({
                                referenceHarnessIds: toggleToken(session.classification.referenceHarnessIds, id),
                              });
                            }}
                          >
                            <option value="">
                              {session.classification.referenceHarnessIds.length
                                ? session.classification.referenceHarnessIds.join(" · ")
                                : t("rounds.reference.placeholder")}
                            </option>
                            {whitelisted
                              .filter((item) => item.harness_id !== session.subject)
                              .map((item) => (
                                <option key={item.harness_id} value={item.harness_id}>
                                  {session.classification.referenceHarnessIds.includes(item.harness_id) ? "✓ " : ""}
                                  {item.harness_id}
                                </option>
                              ))}
                          </select>
                        </label>
                      </div>
                      <p className="note reference-selection" data-testid="reference-selection">
                        {session.classification.referenceHarnessIds.length
                          ? t("rounds.reference.selected", {
                              ids: session.classification.referenceHarnessIds.join(" · "),
                            })
                          : t("rounds.reference.empty")}
                      </p>
                    </>
                  ) : (
                    <>
                      <div className="form-grid">
                        <label>
                          {t("rounds.fixedHarness")}
                          <select
                            value={session.subject}
                            onChange={(event) => edit({ subject: event.target.value })}
                            disabled={whitelisted.length === 0 || modeLocked}
                          >
                            {whitelisted.map((item) => (
                              <option key={item.harness_id} value={item.harness_id}>
                                {item.harness_id}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <p className="note">{t("rounds.modelSlotsNote")}</p>
                      <p className="note" data-testid="plan-pool">
                        {t("rounds.pool")}: {nextPoolId ? <code>{nextPoolId}</code> : t("rounds.needSlots")}
                        {nextPoolId
                          ? slotIds.map((id) => (
                              <code key={id}>{id}</code>
                            ))
                          : null}
                      </p>
                      <p className="note">{t("rounds.slotsLead")}</p>
                      <ComparisonSlots
                        embedded
                        frozen={slotsLocked}
                        modelIds={slotIds}
                        onChange={slotsLocked ? undefined : (ids) => void updateSlots(ids)}
                      />
                    </>
                  )}
                  {drifted ? <p className="callout">{t("rounds.slotsDrift")}</p> : null}
                </section>

                <section className="block plan-section" data-testid="section-summary">
                  <h2 className="section-title">{t("rounds.section.summary")}</h2>
                  <p className="note" data-testid="draft-summary">
                    {t(session.mode === "cross_model" ? "rounds.tab.model" : "rounds.tab.harness")} ·{" "}
                    {labelText(t, `rounds.industry.${session.classification.industry}`)} ·{" "}
                    {session.classification.scenarioTags.map((tag) => labelText(t, `rounds.scenario.${tag}`)).join(" · ") || "—"} ·{" "}
                    {session.localTasks.length} {t("rounds.tasksUnit")} · {preview.length} {t("rounds.slotsUnit")} · Dry-run
                  </p>
                  <div className="row">
                    <button type="button" className="secondary" onClick={() => void saveDraft()} disabled={pending}>
                      {t("rounds.save")}
                    </button>
                    <button type="button" onClick={() => void confirm()} disabled={pending || drifted} data-testid="confirm-plan">
                      {t("rounds.confirmGoMonitor")}
                    </button>
                  </div>
                </section>
              </div>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}

function PlanCard({
  plan,
  pending,
  onOpen,
  t,
}: {
  plan: RoundPlanSummary;
  pending: boolean;
  onOpen: () => void;
  t: Translate;
}) {
  const classification = classificationFromSummary(plan);
  return (
    <article className="plan-card" data-testid="candidate-plan">
      <div className="plan-card-main">
        <h3>{plan.name}</h3>
        <div className="muted">
          <code>{plan.plan_id}</code>
        </div>
        <div className="chip-row compact">
          <span className="chip">{t(objectKindFromMode(plan.mode) === "model" ? "rounds.tab.modelShort" : "rounds.tab.harnessShort")}</span>
          <span className="chip">{labelText(t, `rounds.industry.${classification.industry}`)}</span>
          {classification.scenarioTags.map((tag) => (
            <span key={tag} className="chip">
              {labelText(t, `rounds.scenario.${tag}`)}
            </span>
          ))}
          <span className="chip">{labelText(t, `rounds.purpose.${classification.purpose}`)}</span>
        </div>
        <p className="muted">
          <code>{plan.harness_id}</code> · {labelText(t, stageSummaryKey(plan.lifecycle))} · {plan.rounds} {t("rounds.roundsUnit")}
        </p>
      </div>
      <button type="button" className="plan-open-card" disabled={pending} onClick={onOpen}>
        {t("rounds.open")}
      </button>
    </article>
  );
}
