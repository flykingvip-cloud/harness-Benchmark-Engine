import { useSearchParams } from "react-router-dom";
import { useI18n } from "../i18n/I18nProvider";
import { prepareTabFromSearch, type PrepareTab } from "../prepare";
import { HarnessesPage } from "./Harnesses";
import { PoolsPage } from "./Pools";

export function PreparePage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const tab = prepareTabFromSearch(params.get("tab"));

  function select(next: PrepareTab) {
    if (next === "pools") {
      setParams({}, { replace: true });
      return;
    }
    setParams({ tab: next }, { replace: true });
  }

  return (
    <section className="bleed prepare-page" data-testid="prepare-page">
      <header className="page-head">
        <h1>{t("nav.prepare")}</h1>
        <p>{t("prepare.lead")}</p>
      </header>

      <div className="object-tabs" role="tablist" aria-label={t("prepare.tabs")} data-testid="prepare-tabs">
        <button
          type="button"
          role="tab"
          className="object-tab"
          aria-selected={tab === "pools"}
          data-testid="prepare-tab-pools"
          onClick={() => select("pools")}
        >
          {t("nav.pools")}
        </button>
        <button
          type="button"
          role="tab"
          className="object-tab"
          aria-selected={tab === "harnesses"}
          data-testid="prepare-tab-harnesses"
          onClick={() => select("harnesses")}
        >
          {t("nav.harnesses")}
        </button>
      </div>

      {tab === "pools" ? <PoolsPage embedded /> : <HarnessesPage embedded />}
    </section>
  );
}
