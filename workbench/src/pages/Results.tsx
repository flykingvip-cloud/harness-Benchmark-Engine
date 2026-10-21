import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getJson, routes, type ModelFitResponse, type ResultIndex, type RoundPlanDetail } from "../api";
import { FrameworkCharts } from "../Charts";
import { DimensionBoard } from "../DimensionBoard";
import { show } from "../format";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { statusText, type MessageKey } from "../i18n/messages";
import { resolveResultsSelection, resultDetailPath } from "../results";
import { describeFailure, useLoad } from "../useLoad";
import { useResultDetail } from "../useResult";

const KIND: Record<ResultIndex["source_kind"], MessageKey> = {
  fixture: "results.kind.fixture",
  live: "results.kind.live",
};

interface ResultList {
  results: ResultIndex[];
}

export function ResultsPage() {
  const { t } = useI18n();
  const [params] = useSearchParams();
  const queryPlan = params.get("plan");
  const queryReportId = params.get("report_id");
  const handoffActive = queryPlan !== null || queryReportId !== null;

  const index = useLoad<ResultList>(routes.results);
  const results = index.data?.results ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const [planReportId, setPlanReportId] = useState<string | null>(null);
  const [planResolveError, setPlanResolveError] = useState<{ message?: string; code: string } | null>(null);
  const [planResolving, setPlanResolving] = useState(false);

  useEffect(() => {
    if (queryReportId !== null || queryPlan === null) {
      setPlanReportId(null);
      setPlanResolveError(null);
      setPlanResolving(false);
      return;
    }
    let cancelled = false;
    setPlanResolving(true);
    setPlanResolveError(null);
    getJson<RoundPlanDetail>(`${routes.roundPlans}/${encodeURIComponent(queryPlan)}`).then(
      (detail) => {
        if (!cancelled) {
          setPlanReportId(detail.report_id);
          setPlanResolving(false);
        }
      },
      (reason: unknown) => {
        if (!cancelled) {
          setPlanReportId(null);
          setPlanResolveError(describeFailure(reason));
          setPlanResolving(false);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [queryPlan, queryReportId]);

  const requestedReportId = queryReportId ?? planReportId;
  const selected = resolveResultsSelection({
    results,
    picked,
    requestedReportId,
    handoffActive,
  });
  const opened = useResultDetail(selected);
  const fit = useLoad<ModelFitResponse>(routes.modelFit);
  const detail = opened.detail;
  const liveOpen = detail?.source_kind === "live";
  const handoffUnresolved =
    handoffActive && !planResolving && selected === null && picked === null && Boolean(index.data);

  return (
    <section>
      <header className="page-head">
        <h1>{t("results.title")}</h1>
        <p>
          <Link className="text-link" to="/rounds">
            {t("results.back")}
          </Link>
        </p>
      </header>
      <ErrorBanner message={index.error} code={index.errorCode} />
      {planResolveError ? <ErrorBanner message={planResolveError.message} code={planResolveError.code} /> : null}
      <p className="muted">{t("results.note")}</p>
      {index.loading || planResolving ? <p className="muted">{t("results.loading")}</p> : null}
      {index.data && results.length === 0 ? <p className="muted">{t("results.empty")}</p> : null}
      {handoffUnresolved && results.length > 0 ? <p className="muted">{t("results.empty")}</p> : null}
      {results.length > 0 ? (
        <>
          <h2 className="section-title">{t("results.list")}</h2>
          <ul className="result-list" data-testid="result-index">
            {results.map((item) => (
              <li key={item.result_id}>
                <button
                  type="button"
                  className={item.result_id === selected ? "secondary on" : "secondary"}
                  data-testid={`result-open-${item.result_id}`}
                  data-source-kind={item.source_kind}
                  aria-pressed={item.result_id === selected}
                  onClick={() => setPicked(item.result_id)}
                >
                  <code>{item.result_id}</code>
                  <span className="result-meta">
                    <span className="pill">{t(KIND[item.source_kind])}</span>
                    <span className="muted">{statusText(t, item.spend)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {opened.detailError ? <ErrorBanner message={opened.detailError.message} code={opened.detailError.code} /> : null}
      {opened.detailLoading ? <p className="muted">{t("results.loading")}</p> : null}
      {detail ? (
        <div data-testid="result-detail" data-source-kind={detail.source_kind} data-result-path={resultDetailPath(detail.result_id)}>
          <p className="banner">
            {t(KIND[detail.source_kind])} · {statusText(t, detail.label)} · {statusText(t, detail.spend)} · {detail.kind}
            {detail.audit_passed ? ` · ${t("audit.passed")}` : ` · ${t("audit.failed")}`}
          </p>
          {detail.observed_rule ? <p className="muted">{detail.observed_rule}</p> : null}
          <p className="muted">{t("results.source", { source: detail.source })}</p>
          <DimensionBoard detail={detail} variant="full" />
          <FrameworkCharts
            detail={detail}
            metrics={liveOpen ? null : (fit.data?.model_metrics ?? null)}
            mode={liveOpen ? null : (fit.data?.mode ?? null)}
            harnessId={liveOpen ? null : (fit.data?.harness_id ?? null)}
            variant="full"
          />
          {detail.tasks.map((task) => (
            <article className="block" key={task.task_id}>
              <h2>
                <code>{task.task_id}</code>
              </h2>
              <div className="table-frame">
                <table>
                  <thead>
                    <tr>
                      <th>{t("results.col.harness")}</th>
                      <th>{t("results.col.success")}</th>
                      <th>{t("results.col.valid")}</th>
                      <th>{t("results.col.taskFail")}</th>
                      <th>{t("results.col.cost")}</th>
                      <th>{t("results.col.usage")}</th>
                      <th>{t("results.col.tokens")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {task.harnesses.map((harness) => (
                      <tr key={harness.harness_id}>
                        <td>{harness.harness_id}</td>
                        <td>{show(harness.success_rate)}</td>
                        <td>
                          {harness.valid_count} / {harness.invalid_count}
                        </td>
                        <td>{harness.task_failure_count}</td>
                        <td>
                          {show(harness.total_model_cost)}
                          <span className="muted"> {statusText(t, harness.cost_status)}</span>
                        </td>
                        <td>{statusText(t, harness.usage_status)}</td>
                        <td>{show(harness.total_tokens)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </article>
          ))}
          <h2 className="section-title">{t("results.gates")}</h2>
          <ul className="checks">
            {detail.release_gates.map((gate) => (
              <li key={gate.gate_id} className={gate.passed ? "pass" : "fail"}>
                <code>{gate.gate_id}</code>
                <span>{gate.summary}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
