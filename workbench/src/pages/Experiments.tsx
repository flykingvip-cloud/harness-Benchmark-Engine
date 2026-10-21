import { routes, type ExperimentSummary } from "../api";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { statusText } from "../i18n/messages";
import { useLoad } from "../useLoad";

interface ExperimentList {
  experiments: ExperimentSummary[];
}

export function ExperimentsPage() {
  const { t } = useI18n();
  return (
    <section data-testid="prepare-experiments">
      <header className="page-head">
        <h1>{t("experiments.title")}</h1>
        <p className="note">{t("experiments.prepareLead")}</p>
      </header>
      <ExperimentCatalog />
    </section>
  );
}

export function ExperimentCatalog() {
  const { t } = useI18n();
  const state = useLoad<ExperimentList>(routes.experiments);
  const experiments = state.data?.experiments ?? [];
  return (
    <div data-testid="fixture-catalog">
      <h2 className="section-title">{t("experiments.title")}</h2>
      <ErrorBanner message={state.error} code={state.errorCode} />
      {state.loading ? <p className="muted">{t("experiments.loading")}</p> : null}
      {state.data && experiments.length === 0 ? <p className="muted">{t("experiments.empty")}</p> : null}
      {experiments.map((item) => (
        <article className="block" key={item.experiment_id}>
          <div className="row spread">
            <h2>{item.name}</h2>
            <div className="row">
              <span className="pill">{statusText(t, item.spend)}</span>
              <span className="pill">{statusText(t, item.label)}</span>
              <span className={item.audit_passed ? "pill ok" : "pill bad"}>{item.audit_passed ? t("audit.passed") : t("audit.failed")}</span>
            </div>
          </div>
          <p>{item.note}</p>
          <dl>
            <div>
              <dt>{t("experiments.id")}</dt>
              <dd>
                <code>{item.experiment_id}</code>
              </dd>
            </div>
            <div>
              <dt>{t("experiments.pool")}</dt>
              <dd>
                <code>{item.pool_id}</code>
              </dd>
            </div>
            <div>
              <dt>{t("experiments.poolHash")}</dt>
              <dd>
                <code>{item.pool_hash}</code>
              </dd>
            </div>
            <div>
              <dt>{t("experiments.source")}</dt>
              <dd>{item.source}</dd>
            </div>
          </dl>
          <ul className="tags">
            {item.task_ids.map((task) => (
              <li key={task}>
                <code>{task}</code>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}
