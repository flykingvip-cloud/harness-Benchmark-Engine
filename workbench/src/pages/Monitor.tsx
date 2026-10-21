import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ApiError,
  getJson,
  postJson,
  routes,
  type DispatchJob,
  type JobView,
  type RoundMode,
  type RoundPlanDetail,
  type RoundPlanList,
  type RoundStartResponse,
  type SuiteView,
  type TaskSuite,
} from "../api";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import {
  AUTO_ADVANCE_INTERVALS_MS,
  DEFAULT_AUTO_ADVANCE_MS,
  autoRhythm,
  fanOutTurns,
  isMonitorable,
  manualSkipTurns,
  normalizeAutoInterval,
  paneOutcome,
  panesReadyForAdvance,
  shouldAutoAdvance,
  suiteTaskViews,
  taskComposerText,
  type AutoAdvanceIntervalMs,
  type LocalTurn,
  type PaneOutcome,
  type SuiteTaskStatus,
} from "../monitor";
import { ObservationShell } from "../ObservationShell";
import { resultsHandoffPath } from "../results";
import { directoryPanes, type DirectoryPane } from "../rounds";
import { describeFailure, useLoad } from "../useLoad";

interface MonitorSession {
  planId: string;
  name: string;
  mode: RoundMode;
  subjectHarnessId: string;
  referenceHarnessId: string | null;
  /** Labels multi-ref list (falls back to sealed primary). One pane per id in paired mode. */
  referenceHarnessIds: string[];
  slotIds: string[];
  suite: TaskSuite | null;
  job: DispatchJob | null;
  events: JobView["events"];
  phase: string | null;
  fixturePoolId: string | null;
  reportId: string | null;
}

function referenceIdsFrom(detail: RoundPlanDetail): string[] {
  const fromLabels = detail.labels?.reference_harness_ids ?? [];
  if (fromLabels.length > 0) {
    return [...fromLabels];
  }
  const primary = detail.plan.reference_harness_ref?.harness_id;
  return primary ? [primary] : [];
}

function sessionFrom(
  detail: RoundPlanDetail,
  suite: TaskSuite | null,
  job: DispatchJob | null,
  events: JobView["events"],
  fixturePoolId: string | null,
  reportId: string | null,
): MonitorSession {
  return {
    planId: detail.plan.plan_id,
    name: detail.plan.name,
    mode: detail.plan.mode,
    subjectHarnessId: detail.plan.harness_ref.harness_id,
    referenceHarnessId: detail.plan.reference_harness_ref?.harness_id ?? null,
    referenceHarnessIds: referenceIdsFrom(detail),
    slotIds: [...detail.comparison_model_ids],
    suite,
    job,
    events,
    phase: job?.status ?? null,
    fixturePoolId,
    reportId,
  };
}

async function loadMonitorSession(planId: string): Promise<MonitorSession> {
  const detail = await getJson<RoundPlanDetail>(`${routes.roundPlans}/${planId}`);
  let suite: TaskSuite | null = null;
  if (detail.suite_id) {
    const view = await getJson<SuiteView>(`${routes.roundSuites}/${detail.suite_id}`);
    suite = view.suite;
  }
  let job: DispatchJob | null = null;
  let events: JobView["events"] = [];
  let fixturePoolId: string | null = null;
  if (detail.job_id) {
    const view = await getJson<JobView>(`${routes.roundJobs}/${detail.job_id}`);
    job = view.job;
    events = view.events;
    fixturePoolId = view.fixture_pool_id;
  }
  return sessionFrom(detail, suite, job, events, fixturePoolId, detail.report_id);
}

function taskStatusKey(status: SuiteTaskStatus): "monitor.status.queued" | "monitor.status.current" | "monitor.status.done" {
  if (status === "done") {
    return "monitor.status.done";
  }
  if (status === "current") {
    return "monitor.status.current";
  }
  return "monitor.status.queued";
}

function panesFor(session: MonitorSession, taskId: string | null, modelIds: readonly string[], events: MonitorSession["events"]): DirectoryPane[] {
  return directoryPanes(
    {
      mode: session.mode,
      subjectHarnessId: session.subjectHarnessId,
      referenceHarnessId: session.referenceHarnessId,
      referenceHarnessIds: session.referenceHarnessIds,
      modelIds,
      taskId,
      slotIds: session.slotIds,
    },
    events,
  );
}

export function MonitorPage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const plans = useLoad<RoundPlanList>(routes.roundPlans);
  const requested = params.get("plan");
  const [session, setSession] = useState<MonitorSession | null>(null);
  const [readyId, setReadyId] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [cursor, setCursor] = useState(0);
  const [localTurns, setLocalTurns] = useState<LocalTurn[]>([]);
  const [composer, setComposer] = useState("");
  const [auto, setAuto] = useState(false);
  const [paused, setPaused] = useState(false);
  const [autoIntervalMs, setAutoIntervalMs] = useState<AutoAdvanceIntervalMs>(DEFAULT_AUTO_ADVANCE_MS);
  const [countdownMs, setCountdownMs] = useState<number | null>(null);
  const [suiteOpen, setSuiteOpen] = useState(false);
  const [tickAfterDispatch, setTickAfterDispatch] = useState(false);
  const requestSeq = useRef(0);
  const primed = useRef<string | null>(null);
  const autoFrom = useRef<string | null>(null);
  const sessionRef = useRef(session);
  sessionRef.current = session;

  const monitorable = (plans.data?.plans ?? []).filter((plan) => isMonitorable(plan.lifecycle));
  const active = session && readyId === session.planId ? session : null;
  const tasks = active?.suite?.tasks ?? [];
  const currentTask = tasks[cursor] ?? null;
  const started = Boolean(active?.job);
  const panes = active ? panesFor(active, currentTask?.task_id ?? tasks[0]?.task_id ?? null, active.job?.model_ids ?? [], active.events) : [];
  const taskViews = suiteTaskViews(tasks, cursor, started);

  function fail(error: unknown) {
    if (error instanceof ApiError) {
      setFailure(error);
      return;
    }
    const described = describeFailure(error);
    setFailure(new ApiError(0, described.code, described.message ?? ""));
  }

  function resetRun() {
    setCursor(0);
    setLocalTurns([]);
    setComposer("");
    setAuto(false);
    setPaused(false);
    setCountdownMs(null);
    setSuiteOpen(false);
    setTickAfterDispatch(false);
    primed.current = null;
    autoFrom.current = null;
  }

  async function openPlan(planId: string) {
    const seq = ++requestSeq.current;
    resetRun();
    setReadyId(null);
    setSession(null);
    setPending(true);
    setFailure(null);
    try {
      const loaded = await loadMonitorSession(planId);
      if (requestSeq.current !== seq) {
        return;
      }
      if (loaded.suite?.status !== "confirmed") {
        setFailure(new ApiError(0, "suite_not_confirmed", t("monitor.notMonitorable")));
        return;
      }
      setSession(loaded);
      setReadyId(planId);
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

  const attempted = useRef<string | null>(null);
  useEffect(() => {
    if (!requested || plans.loading || pending || readyId === requested || attempted.current === requested) {
      return;
    }
    const row = plans.data?.plans.find((plan) => plan.plan_id === requested);
    if (plans.data && !row) {
      return;
    }
    if (row && !isMonitorable(row.lifecycle)) {
      return;
    }
    attempted.current = requested;
    void openPlan(requested);
  }, [requested, plans.loading, plans.data, readyId, pending]);

  function choosePlan(planId: string) {
    if (!planId || planId === requested) {
      return;
    }
    setParams({ plan: planId });
  }

  function bodyFor(index: number, taskId: string, prompt: string): string {
    return taskComposerText(t("monitor.taskN", { n: index + 1 }), taskId, prompt);
  }

  function fillTask(index: number) {
    const task = tasks[index];
    if (!task || panes.length === 0) {
      return;
    }
    const text = bodyFor(index, task.task_id, task.prompt);
    const message = t("monitor.fanoutBody", { body: text });
    setCursor(index);
    setComposer(text);
    setTickAfterDispatch(false);
    setLocalTurns((current) => {
      if (current.some((turn) => turn.taskId === task.task_id && turn.role === "dispatch")) {
        return current;
      }
      return [...current, ...fanOutTurns(current, panes, task.task_id, message, new Date().toISOString())];
    });
  }

  useEffect(() => {
    if (!active?.suite || active.job) {
      return;
    }
    const task = active.suite.tasks[0];
    if (!task) {
      setComposer("");
      return;
    }
    setComposer(bodyFor(0, task.task_id, task.prompt));
    setCursor(0);
    // Preview only. Fan-out waits until the suite is sent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.planId, active?.job, active?.suite, t]);

  useEffect(() => {
    if (!active?.job || !active.suite || panes.length === 0) {
      return;
    }
    if (primed.current === active.planId) {
      return;
    }
    if (localTurns.length > 0) {
      primed.current = active.planId;
      return;
    }
    const task = active.suite.tasks[0];
    if (!task) {
      return;
    }
    primed.current = active.planId;
    const text = bodyFor(0, task.task_id, task.prompt);
    const message = t("monitor.fanoutBody", { body: text });
    setCursor(0);
    setComposer(text);
    setTickAfterDispatch(false);
    setLocalTurns((current) => {
      if (current.some((turn) => turn.taskId === task.task_id && turn.role === "dispatch")) {
        return current;
      }
      return [...current, ...fanOutTurns(current, panes, task.task_id, message, new Date().toISOString())];
    });
    // Prime once per loaded job so the first task is filled and fanned out.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.planId, active?.job, panes.length, localTurns.length, t]);

  useEffect(() => {
    if (!active?.suite || !currentTask || panes.length === 0) {
      return;
    }
    const focus = active.job?.focus_task_id ?? null;
    const now = new Date().toISOString();
    setLocalTurns((current) => {
      const extra: LocalTurn[] = [];
      for (const pane of panes) {
        const judged = paneOutcome({
          paneId: pane.paneId,
          taskId: currentTask.task_id,
          focusTaskId: focus,
          events: active.events,
          local: current,
          allowUnmeasured: tickAfterDispatch,
        });
        if (!judged.kind) {
          continue;
        }
        const role = judged.outcome === "skip" ? "skip" : "result";
        const message =
          judged.kind === "failed_skip"
            ? t("monitor.result.skip", { taskId: currentTask.task_id, paneId: pane.paneId })
            : judged.kind === "focus_replay"
              ? t("monitor.result.focus", { taskId: currentTask.task_id, paneId: pane.paneId })
              : t("monitor.result.unmeasured", { taskId: currentTask.task_id, paneId: pane.paneId });
        extra.push({
          id: `${role}:${currentTask.task_id}:${pane.paneId}:1`,
          paneId: pane.paneId,
          taskId: currentTask.task_id,
          role,
          message,
          recordedAt: now,
          stubKind: judged.kind ?? undefined,
        });
      }
      if (extra.length === 0) {
        return current;
      }
      return [...current, ...extra];
    });
  }, [active, currentTask, panes, tickAfterDispatch, t]);

  const focusTaskId = active?.job?.focus_task_id ?? null;
  const currentOutcomes: PaneOutcome[] =
    active && currentTask && panes.length > 0
      ? panes.map((pane) => {
          const judged = paneOutcome({
            paneId: pane.paneId,
            taskId: currentTask.task_id,
            focusTaskId: focusTaskId,
            events: active.events,
            local: localTurns,
            allowUnmeasured: tickAfterDispatch,
          });
          // Wait until the result turn is stored. A fresh judgement would be overwritten by the advance.
          return judged.kind ? "pending" : judged.outcome;
        })
      : [];
  const readyToAdvance = panesReadyForAdvance(currentOutcomes);
  const rhythm = autoRhythm({
    auto,
    paused,
    readyToAdvance: Boolean(
      active?.suite &&
        currentTask &&
        shouldAutoAdvance({
          auto,
          paused,
          taskIndex: cursor,
          taskCount: active.suite.tasks.length,
          outcomes: currentOutcomes,
        }),
    ),
    taskIndex: cursor,
    taskCount: active?.suite?.tasks.length ?? 0,
    remainingMs: countdownMs,
  });

  useEffect(() => {
    if (!auto || paused || !readyToAdvance || !active?.suite || !currentTask || pending || panes.length === 0) {
      setCountdownMs(null);
      return;
    }
    if (
      !shouldAutoAdvance({
        auto,
        paused,
        taskIndex: cursor,
        taskCount: active.suite.tasks.length,
        outcomes: currentOutcomes,
      })
    ) {
      setCountdownMs(null);
      return;
    }
    const key = `${active.planId}:${currentTask.task_id}`;
    if (autoFrom.current === key) {
      return;
    }
    const interval = normalizeAutoInterval(autoIntervalMs);
    setCountdownMs(interval);
    const startedAt = Date.now();
    const tick = window.setInterval(() => {
      const left = Math.max(0, interval - (Date.now() - startedAt));
      setCountdownMs(left);
    }, 100);
    const timer = window.setTimeout(() => {
      autoFrom.current = key;
      setCountdownMs(null);
      fillTask(cursor + 1);
    }, interval);
    return () => {
      window.clearTimeout(timer);
      window.clearInterval(tick);
    };
    // fillTask closes over the latest task list. The ref stops a second advance from the same task.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active?.planId, currentTask?.task_id, auto, paused, pending, readyToAdvance, autoIntervalMs, cursor, currentOutcomes.join("|")]);

  function skipCurrentTask() {
    if (!currentTask || panes.length === 0 || paused || pending) {
      return;
    }
    const now = new Date().toISOString();
    setLocalTurns((current) => [
      ...current,
      ...manualSkipTurns(
        current,
        panes,
        currentTask.task_id,
        (paneId) => t("monitor.result.manualSkip", { taskId: currentTask.task_id, paneId }),
        now,
      ),
    ]);
  }

  async function start() {
    const planId = sessionRef.current?.planId;
    const source = planId ? sessionRef.current : null;
    if (!planId || !source?.suite || source.job) {
      return;
    }
    setPending(true);
    setFailure(null);
    try {
      const body = await postJson<RoundStartResponse>(`${routes.roundPlans}/${planId}/start`, { live: false });
      if (sessionRef.current?.planId !== planId) {
        return;
      }
      const next: MonitorSession = {
        ...source,
        job: body.job,
        events: body.events,
        phase: body.job.status,
        fixturePoolId: body.fixture_pool_id,
        reportId: body.report_id,
      };
      const task = source.suite.tasks[0];
      const nextPanes = panesFor(next, task?.task_id ?? null, body.job.model_ids, body.events);
      primed.current = planId;
      setSession(next);
      setReadyId(planId);
      if (task && nextPanes.length > 0) {
        const text = taskComposerText(t("monitor.taskN", { n: 1 }), task.task_id, task.prompt);
        setCursor(0);
        setComposer(text);
        setTickAfterDispatch(false);
        setLocalTurns(fanOutTurns([], nextPanes, task.task_id, t("monitor.fanoutBody", { body: text }), new Date().toISOString()));
      }
    } catch (error) {
      if (sessionRef.current?.planId === planId) {
        fail(error);
      }
    } finally {
      setPending(false);
    }
  }

  async function tick() {
    const source = sessionRef.current;
    const job = source?.job;
    if (!source || !job || paused) {
      return;
    }
    setPending(true);
    setFailure(null);
    try {
      const body = await postJson<RoundStartResponse>(`${routes.roundJobs}/${job.job_id}/tick`, { live: false });
      if (sessionRef.current?.planId !== source.planId) {
        return;
      }
      setSession((current) =>
        current && current.planId === source.planId
          ? {
              ...current,
              job: body.job,
              events: body.events,
              phase: body.job.status,
              fixturePoolId: body.fixture_pool_id,
              reportId: body.report_id,
            }
          : current,
      );
      setTickAfterDispatch(true);
    } catch (error) {
      if (sessionRef.current?.planId === source.planId) {
        fail(error);
      }
    } finally {
      setPending(false);
    }
  }

  function sendComposer() {
    if (!currentTask || panes.length === 0 || composer.trim() === "" || paused) {
      return;
    }
    const message = t("monitor.fanoutBody", { body: composer });
    setLocalTurns((current) => [...current, ...fanOutTurns(current, panes, currentTask.task_id, message, new Date().toISOString())]);
  }

  const suiteConfirmed = active?.suite?.status === "confirmed";
  const canDispatch = Boolean(active && suiteConfirmed && !active.job && !pending);
  const canTick = Boolean(active?.job && active.job.status !== "sealed" && !paused && !pending);
  const canStep = Boolean(started && currentTask && cursor < tasks.length - 1 && !paused && !pending);
  const canSkip = Boolean(
    started && currentTask && !paused && !pending && currentOutcomes.some((item) => item === "pending"),
  );
  const rejected = Boolean(requested && plans.data && !plans.data.plans.some((plan) => plan.plan_id === requested));
  const notReady = Boolean(
    requested && plans.data && plans.data.plans.some((plan) => plan.plan_id === requested && !isMonitorable(plan.lifecycle)),
  );

  return (
    <section data-testid="monitor-page" data-mode={active?.mode ?? ""}>
      <header className="page-head">
        <h1>{t("monitor.title")}</h1>
        <p className="note">{t("monitor.lead")}</p>
      </header>
      <p className="banner" data-testid="monitor-dry-run">
        {t("monitor.dryRun")}
      </p>
      <ErrorBanner message={failure?.message} code={failure?.code} />
      <ErrorBanner message={plans.error} code={plans.errorCode} />

      <div className="monitor-bar" data-testid="monitor-bar">
        <div className="monitor-bar-row">
          <span className="monitor-kicker">T0</span>
          <label className="monitor-plan">
            <span>{t("monitor.plan")}</span>
            <select
              data-testid="monitor-plan"
              value={active?.planId ?? ""}
              disabled={pending || monitorable.length === 0}
              onChange={(event) => choosePlan(event.target.value)}
            >
              <option value="">{t("monitor.pick")}</option>
              {monitorable.map((plan) => (
                <option key={plan.plan_id} value={plan.plan_id}>
                  {plan.name} · {plan.plan_id}
                </option>
              ))}
            </select>
          </label>
          {suiteConfirmed ? <span className="pill ok">{t("status.confirmed_suite")}</span> : null}
          {active ? (
            <span className="pill" data-testid="monitor-mode">
              {active.mode}
            </span>
          ) : null}
          <button
            type="button"
            className="suite-toggle"
            data-testid="suite-details"
            aria-expanded={suiteOpen}
            disabled={tasks.length === 0}
            onClick={() => setSuiteOpen((value) => !value)}
          >
            <span aria-hidden="true">{suiteOpen ? "▾" : "▸"}</span>
            {t("monitor.suiteDetails")}
            <span className="muted">{t("monitor.suiteCount", { count: tasks.length })}</span>
          </button>
          <span className="monitor-spacer" />
          <button
            type="button"
            className="secondary"
            data-testid="monitor-pause"
            aria-pressed={paused}
            disabled={!active?.job || pending}
            onClick={() => setPaused((value) => !value)}
          >
            {paused ? t("monitor.resume") : t("monitor.pause")}
          </button>
          <button type="button" className="monitor-tick" data-testid="monitor-tick" disabled={!canTick} onClick={() => void tick()}>
            {t("monitor.tick")}
          </button>
        </div>
        <div className="monitor-bar-row">
          <button type="button" className="monitor-dispatch" data-testid="dispatch-suite" disabled={!canDispatch} onClick={() => void start()}>
            {t("monitor.dispatchSuite")}
          </button>
          <span className="muted">{t("monitor.fanoutHint")}</span>
        </div>
        {tasks.length > 0 ? (
          <div className={suiteOpen ? "suite-panel" : "suite-panel collapsed"} data-testid="suite-panel">
            {suiteOpen ? (
              <ol className="suite-list" data-testid="suite-tasks">
                {taskViews.map((task) => (
                  <li key={task.task_id} className={task.status} data-task-id={task.task_id}>
                    <span className="suite-id">{t("monitor.taskN", { n: task.index })}</span>
                    <span>
                      <code>{task.task_id}</code>
                      {task.status === "current" ? <span className="muted"> · {t("monitor.status.current")}</span> : null}
                    </span>
                    <span className="muted">{t(taskStatusKey(task.status))}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="muted suite-hint">{t("monitor.suiteCollapsed")}</p>
            )}
          </div>
        ) : null}
        <div className="monitor-taskbar" data-testid="monitor-taskbar">
          <span>
            {t("monitor.currentLead")}{" "}
            <strong>
              {currentTask ? t("monitor.taskProgress", { index: currentTask ? cursor + 1 : 0, total: tasks.length }) : t("rounds.placeholder")}
            </strong>
            {currentTask ? <code>{currentTask.task_id}</code> : null}
          </span>
          <span className="monitor-spacer" />
          <label className={auto ? "monitor-switch on" : "monitor-switch"}>
            <input
              type="checkbox"
              data-testid="auto-switch"
              checked={auto}
              disabled={!started || pending}
              onChange={(event) => {
                setAuto(event.target.checked);
                if (!event.target.checked) {
                  setCountdownMs(null);
                }
              }}
            />
            {t("monitor.auto")}
          </label>
          <label className="monitor-interval" data-testid="auto-interval">
            <span className="muted">{t("monitor.autoInterval")}</span>
            <select
              value={autoIntervalMs}
              disabled={!started || pending || !auto}
              onChange={(event) => setAutoIntervalMs(normalizeAutoInterval(Number(event.target.value)))}
            >
              {AUTO_ADVANCE_INTERVALS_MS.map((ms) => (
                <option key={ms} value={ms}>
                  {t("monitor.autoIntervalSec", { sec: ms / 1000 })}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="secondary" data-testid="skip-task" disabled={!canSkip} onClick={skipCurrentTask}>
            {t("monitor.skipTask")}
          </button>
          <button type="button" className="secondary" data-testid="next-task" disabled={!canStep} onClick={() => fillTask(cursor + 1)}>
            {t("monitor.next")}
          </button>
        </div>
      </div>

      {rhythm.kind === "paused" || paused ? <p className="note" data-testid="monitor-rhythm">{t("monitor.pausedNote")}</p> : null}
      {rhythm.kind === "waiting" ? <p className="note" data-testid="monitor-rhythm">{t("monitor.autoWaiting")}</p> : null}
      {rhythm.kind === "countdown" ? (
        <p className="note" data-testid="monitor-rhythm">
          {t("monitor.autoCountdown", { sec: Math.ceil(rhythm.remainingMs / 1000), n: rhythm.nextIndex + 1 })}
        </p>
      ) : null}
      {rhythm.kind === "complete" && auto ? <p className="muted" data-testid="monitor-rhythm">{t("monitor.autoComplete")}</p> : null}
      {plans.loading || pending ? <p className="muted">{t("monitor.loading")}</p> : null}
      {!plans.loading && monitorable.length === 0 ? <p className="muted">{t("monitor.empty")}</p> : null}
      {rejected ? <p className="callout">{t("monitor.missingPlan")}</p> : null}
      {notReady ? <p className="callout">{t("monitor.notMonitorable")}</p> : null}
      {active && !active.job ? <p className="muted">{t("monitor.noJob")}</p> : null}

      {active ? (
        <ObservationShell
          key={active.planId}
          mode={active.mode}
          subjectHarnessId={active.subjectHarnessId}
          referenceHarnessId={active.referenceHarnessId}
          referenceHarnessIds={active.referenceHarnessIds}
          modelIds={active.job?.model_ids ?? []}
          taskId={currentTask?.task_id ?? null}
          slotIds={active.slotIds}
          events={active.events}
          phase={active.phase}
          fixturePoolId={active.fixturePoolId}
          suiteId={active.job?.suite_id ?? active.suite?.suite_id ?? null}
          jobId={active.job?.job_id ?? null}
          pending={pending}
          displayTaskId={currentTask?.task_id ?? null}
          execution={{
            local: localTurns,
            focusTaskId: active.job?.focus_task_id ?? null,
            composer,
            onComposer: setComposer,
            onSend: sendComposer,
            onDispatch: sendComposer,
            disabled: !started || paused || composer.trim() === "" || panes.length === 0,
          }}
        />
      ) : null}

      {active?.job?.status === "sealed" || active?.reportId ? (
        <p className="note">
          {t("monitor.sealed")}{" "}
          <Link
            className="text-link"
            data-testid="monitor-report-handoff"
            to={resultsHandoffPath({ planId: active.planId, reportId: active.reportId })}
          >
            {t("monitor.reportLink")}
          </Link>
        </p>
      ) : (
        <p className="muted">
          <Link className="text-link" to="/results">
            {t("monitor.reportLink")}
          </Link>
        </p>
      )}
    </section>
  );
}
