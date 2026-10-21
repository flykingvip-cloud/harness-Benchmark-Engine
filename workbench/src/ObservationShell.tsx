import { useState } from "react";
import type { ObservationEvent, RoundMode } from "./api";
import { useI18n } from "./i18n/I18nProvider";
import { statusText, type Translate } from "./i18n/messages";
import { executionLines, type DialogueLine, type LocalTurn } from "./monitor";
import {
  activeDirectoryPane,
  directoryPanes,
  formatStamp,
  observationTurns,
  paneRecorder,
  type DirectoryPane,
} from "./rounds";

interface ObservationShellProps {
  mode: RoundMode;
  subjectHarnessId: string | null;
  referenceHarnessId: string | null;
  /** Full multi-ref list from PlanLabels; maps into paired directory panes. */
  referenceHarnessIds?: readonly string[];
  modelIds: readonly string[];
  taskId: string | null;
  slotIds: readonly string[];
  events: readonly ObservationEvent[];
  phase: string | null;
  fixturePoolId: string | null;
  suiteId: string | null;
  jobId: string | null;
  pending: boolean;
  displayTaskId?: string | null;
  execution?: {
    local: readonly LocalTurn[];
    focusTaskId: string | null;
    composer: string;
    onComposer: (value: string) => void;
    onSend: () => void;
    onDispatch: () => void;
    disabled: boolean;
  } | null;
}

export function ObservationShell({
  mode,
  subjectHarnessId,
  referenceHarnessId,
  referenceHarnessIds = [],
  modelIds,
  taskId,
  slotIds,
  events,
  phase,
  fixturePoolId,
  suiteId,
  jobId,
  pending,
  displayTaskId,
  execution,
}: ObservationShellProps) {
  const { t } = useI18n();
  const [currentId, setCurrentId] = useState<string | null>(null);
  const panes = directoryPanes(
    { mode, subjectHarnessId, referenceHarnessId, referenceHarnessIds, modelIds, taskId, slotIds },
    events,
  );
  const active = activeDirectoryPane(currentId, panes);
  const recorder = active ? paneRecorder(active, events, phase) : null;
  const lines: readonly DialogueLine[] = active
    ? execution
      ? executionLines(active.paneId, events, execution.local, execution.focusTaskId)
      : observationTurns(active.paneId, events).map((turn) => ({
          key: `server-${turn.seq}`,
          role: turn.role === "dispatch" ? "dispatch" : "process",
          message: turn.message,
          recordedAt: turn.recordedAt,
          kind: turn.kind,
        }))
    : [];
  const placeholder = t("rounds.placeholder");
  const recordedTask = displayTaskId ?? recorder?.taskId;
  const showSlots = mode === "cross_model" && slotIds.length > 0;

  return (
    <div className="observe-shell" data-testid="observation-shell">
      <section className="observe-directory" aria-label={t("rounds.directory")}>
        <header className="observe-dir-head">
          <h3>
            {t("rounds.directory")}
            <span className="observe-count">{panes.length}</span>
          </h3>
          <p className="muted">{mode === "paired_harness" ? t("rounds.directoryPaired") : t("rounds.directoryModels")}</p>
        </header>
        {showSlots ? (
          <p className="observe-slot-note" data-testid="shell-slots">
            <span>{t("rounds.shellSlots")}</span>
            {slotIds.map((id) => (
              <code key={id}>{id}</code>
            ))}
            <span className="muted">{t("rounds.shellSlotsNote")}</span>
          </p>
        ) : null}
        {panes.length === 0 ? (
          <p className="muted observe-empty" data-testid="observation-directory">
            {t("rounds.panesEmpty")}
          </p>
        ) : (
          <ul className="observe-dir-list" data-testid="observation-directory">
            {panes.map((pane) => {
              const selected = active?.paneId === pane.paneId;
              return (
                <li key={pane.paneId}>
                  <button
                    type="button"
                    className="observe-row"
                    data-testid="pane-row"
                    data-pane-id={pane.paneId}
                    data-origin={pane.origin}
                    title={paneTitle(mode, pane)}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => setCurrentId(pane.paneId)}
                  >
                    <span className="observe-mark" aria-hidden="true">
                      {markOf(paneTitle(mode, pane))}
                    </span>
                    <span className="observe-row-copy">
                      <strong>{paneTitle(mode, pane)}</strong>
                      <span className="muted">{paneSubtitle(mode, pane, placeholder)}</span>
                      <span className="observe-row-meta">
                        {pane.origin === "slot" ? <span className="pill">{t("rounds.slotPreview")}</span> : null}
                        {pane.origin === "planned" ? <span className="pill">{t("rounds.plannedPane")}</span> : null}
                        {pane.lines.length > 0 ? (
                          <time dateTime={pane.lines[pane.lines.length - 1]?.recorded_at}>
                            {formatStamp(pane.lines[pane.lines.length - 1]?.recorded_at) ?? placeholder}
                          </time>
                        ) : null}
                      </span>
                    </span>
                    <span className="observe-chevron" aria-hidden="true" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="observe-detail" aria-label={t("rounds.recorderTitle")}>
        <header className="observe-detail-head">
          <div>
            <h3>{active ? paneTitle(mode, active) : t("rounds.panes")}</h3>
            <p className="muted">
              {phase ? <span className="pill ok">{statusText(t, phase)}</span> : <span className="pill">{placeholder}</span>}
              {fixturePoolId ? <code>{fixturePoolId}</code> : null}
              {jobId ? <code>{jobId}</code> : null}
            </p>
          </div>
        </header>

        {jobId ? (
          <p className="note observe-fanout">
            {t("rounds.fanout")}
            {suiteId ? <code>{suiteId}</code> : null}
          </p>
        ) : (
          <p className="note observe-fanout">{t("rounds.dispatchLead")}</p>
        )}

        <div data-testid="observation-recorder">
          <h4>{t("rounds.recorderTitle")}</h4>
          <dl className="observe-params">
            <div>
              <dt>{t("rounds.recorder.phase")}</dt>
              <dd>{recorder?.phase ? statusText(t, recorder.phase) : placeholder}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.first")}</dt>
              <dd>{formatStamp(recorder?.firstAt) ?? placeholder}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.last")}</dt>
              <dd>{formatStamp(recorder?.lastAt) ?? placeholder}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.latency")}</dt>
              <dd>{recorder?.latency ?? placeholder}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.cost")}</dt>
              <dd>{recorder?.cost ?? placeholder}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.task")}</dt>
              <dd>{textOr(recordedTask, placeholder)}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.model")}</dt>
              <dd>{textOr(recorder?.modelId, placeholder)}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.harness")}</dt>
              <dd>{textOr(recorder?.harnessId, placeholder)}</dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.spend")}</dt>
              <dd>
                <code>spend:none</code>
              </dd>
            </div>
            <div>
              <dt>{t("rounds.recorder.executed")}</dt>
              <dd>
                <code>executed:false</code>
              </dd>
            </div>
          </dl>
          <p className="muted observe-placeholder-note">{t("rounds.placeholderNote")}</p>
          <p className="banner" data-testid="spend-none-banner">
            {t("rounds.spendBanner")}
          </p>
        </div>

        <div className="observe-stream" data-testid={execution ? "execution-dialogue" : "observation-stream"}>
          <h4>
            {execution ? t("monitor.dialogueTitle") : t("rounds.streamTitle")}
            <span className="observe-count">{lines.length}</span>
          </h4>
          {lines.length === 0 ? <p className="muted">{execution ? t("monitor.dialogueEmpty") : t("rounds.streamEmpty")}</p> : null}
          <ol className="observe-log" aria-live="polite" data-testid="observation-stream">
            {lines.map((turn) => {
              const outcome = turn.outcome ?? outcomeFromRole(turn.role, turn.stubKind);
              const body =
                turn.role === "pending" && turn.message.trim() === ""
                  ? t("monitor.result.pending")
                  : turn.message;
              return (
                <li
                  key={turn.key}
                  className={`observe-turn ${turnClass(turn.role, outcome)}`}
                  data-role={turn.role}
                  data-outcome={outcome ?? ""}
                  data-stub-kind={turn.stubKind ?? ""}
                  data-testid="dialogue-turn"
                >
                  <header>
                    <span className="observe-role">
                      {roleLabel(t, turn.role)}
                      <span className={`pill ${outcomePill(outcome)}`}>{outcomeLabel(t, outcome, turn.kind)}</span>
                    </span>
                    <time dateTime={turn.recordedAt}>{formatStamp(turn.recordedAt) ?? placeholder}</time>
                  </header>
                  <p>{body}</p>
                </li>
              );
            })}
          </ol>
          {execution ? (
            <form
              className="observe-composer"
              onSubmit={(event) => {
                event.preventDefault();
                execution.onSend();
              }}
            >
              <textarea
                data-testid="execution-composer"
                value={execution.composer}
                placeholder={t("monitor.composerPlaceholder")}
                disabled={pending}
                onChange={(event) => execution.onComposer(event.target.value)}
              />
              <button type="submit" data-testid="execution-send" disabled={execution.disabled || pending}>
                {t("monitor.send")}
              </button>
              <button
                type="button"
                className="secondary"
                data-testid="dispatch-round"
                disabled={execution.disabled || pending}
                onClick={execution.onDispatch}
              >
                {t("monitor.dispatchLine")}
              </button>
            </form>
          ) : null}
        </div>
      </section>
    </div>
  );
}

function turnClass(role: DialogueLine["role"], outcome: DialogueLine["outcome"]): string {
  if (role === "dispatch") {
    return "dispatch";
  }
  if (role === "skip" || outcome === "skip") {
    return "skip";
  }
  if (role === "pending" || outcome === "pending") {
    return "pending";
  }
  if (outcome === "unmeasured") {
    return "result unmeasured";
  }
  if (role === "result" || outcome === "success") {
    return "result success";
  }
  return "process";
}

function outcomeFromRole(
  role: DialogueLine["role"],
  stubKind: DialogueLine["stubKind"],
): DialogueLine["outcome"] {
  if (role === "pending") {
    return "pending";
  }
  if (role === "skip") {
    return "skip";
  }
  if (role === "result") {
    return stubKind === "unmeasured" ? "unmeasured" : "success";
  }
  return null;
}

function outcomePill(outcome: DialogueLine["outcome"]): string {
  if (outcome === "success") {
    return "ok";
  }
  if (outcome === "skip") {
    return "warn";
  }
  if (outcome === "unmeasured" || outcome === "pending") {
    return "";
  }
  return "";
}

function outcomeLabel(
  t: Translate,
  outcome: DialogueLine["outcome"],
  kind: string,
): string {
  if (outcome === "success") {
    return t("monitor.turn.success");
  }
  if (outcome === "skip") {
    return t("monitor.turn.skip");
  }
  if (outcome === "unmeasured") {
    return t("monitor.turn.unmeasured");
  }
  if (outcome === "pending") {
    return t("monitor.turn.pending");
  }
  return statusText(t, kind);
}

function roleLabel(
  t: Translate,
  role: DialogueLine["role"],
): string {
  if (role === "dispatch") {
    return t("rounds.turn.dispatch");
  }
  if (role === "result") {
    return t("monitor.turn.result");
  }
  if (role === "skip") {
    return t("monitor.turn.skip");
  }
  if (role === "pending") {
    return t("monitor.turn.pending");
  }
  return t("rounds.turn.pane");
}

function paneTitle(mode: RoundMode, pane: DirectoryPane): string {
  if (mode === "paired_harness") {
    return pane.harnessId || pane.paneId;
  }
  return pane.modelId || pane.paneId;
}

function paneSubtitle(mode: RoundMode, pane: DirectoryPane, placeholder: string): string {
  if (mode === "paired_harness") {
    return pane.modelId || placeholder;
  }
  return pane.harnessId || placeholder;
}

function markOf(title: string): string {
  const trimmed = title.trim();
  if (!trimmed) {
    return "·";
  }
  return trimmed.slice(0, 1).toUpperCase();
}

function textOr(value: string | null | undefined, placeholder: string): string {
  if (value == null || value.trim() === "") {
    return placeholder;
  }
  return value;
}
