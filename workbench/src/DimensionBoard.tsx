import type { ResultDetail } from "./api";
import { DIMENSIONS, dimensionFacts, type DimensionFacts, type DimensionId } from "./dimensions";
import { useI18n } from "./i18n/I18nProvider";
import type { MessageKey } from "./i18n/messages";

const NAME: Record<DimensionId, MessageKey> = {
  validity: "dim.validity",
  effectiveness: "dim.effectiveness",
  process: "dim.process",
  cost: "dim.cost",
  diagnosability: "dim.diagnosability",
};

export function DimensionBoard({ detail, variant }: { detail: ResultDetail; variant: "full" | "compact" }) {
  const { t } = useI18n();
  const facts = dimensionFacts(detail);
  const na = t("dim.na");
  return (
    <section className={variant === "full" ? "dim-board" : "dim-board compact"} aria-label={t("dim.heading")}>
      <h2>{t("dim.heading")}</h2>
      <div className="dim-cards">
        {DIMENSIONS.map((id) => (
          <article key={id}>
            <h3>{t(NAME[id])}</h3>
            <p className="metric">{metric(id, facts, na)}</p>
            <p className="dim-detail">{detailLine(id, facts, t, na)}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function metric(id: DimensionId, facts: DimensionFacts, na: string): string {
  if (id === "validity") {
    const total = facts.validity.valid + facts.validity.invalid;
    return total === 0 ? na : `${facts.validity.valid}/${total}`;
  }
  if (id === "effectiveness") {
    return facts.effectiveness.meanSuccess ?? na;
  }
  if (id === "process") {
    return facts.process.total === 0 ? na : `${facts.process.complete}/${facts.process.total}`;
  }
  if (id === "cost") {
    const total = facts.cost.present + facts.cost.missing + facts.cost.invalid;
    return total === 0 ? na : `${facts.cost.present}/${total}`;
  }
  return facts.diagnosability.gatesTotal === 0 ? na : `${facts.diagnosability.gatesPassed}/${facts.diagnosability.gatesTotal}`;
}

function detailLine(
  id: DimensionId,
  facts: DimensionFacts,
  t: (key: MessageKey, vars?: Record<string, string | number>) => string,
  na: string,
): string {
  if (id === "validity") {
    return t("dim.validity.detail", { invalid: facts.validity.invalid, failures: facts.validity.taskFailures });
  }
  if (id === "effectiveness") {
    return t("dim.effectiveness.detail", {
      mean: facts.effectiveness.meanSuccess ?? na,
      delta: facts.effectiveness.delta ?? na,
    });
  }
  if (id === "process") {
    return t("dim.process.detail", { complete: facts.process.complete, total: facts.process.total });
  }
  if (id === "cost") {
    return t("dim.cost.detail", {
      present: facts.cost.present,
      missing: facts.cost.missing,
      invalid: facts.cost.invalid,
      priced: facts.cost.priced,
    });
  }
  const categories = facts.diagnosability.categories.map(([name, count]) => `${name} × ${count}`).join(", ");
  return t("dim.diagnosability.detail", {
    passed: facts.diagnosability.gatesPassed,
    total: facts.diagnosability.gatesTotal,
    categories: categories || t("dim.categoriesNone"),
  });
}
