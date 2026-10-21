import { useMemo, useState } from "react";
import { routes, type CatalogList, type CatalogModelList } from "../api";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { recommendSlots, slotIdsForConfirm, unifiedSlotCandidates, type SlotCandidate } from "../slots";
import { useLoad } from "../useLoad";

export function ComparisonSlots({
  embedded = false,
  frozen = false,
  modelIds,
  onChange,
}: {
  embedded?: boolean;
  frozen?: boolean;
  modelIds: readonly string[];
  onChange?: (modelIds: string[]) => void;
}) {
  const { t } = useI18n();
  const catalog = useLoad<CatalogModelList>(routes.catalogModels);
  const files = useLoad<CatalogList>(routes.modelCatalog);
  const [pendingSlot, setPendingSlot] = useState("");
  const candidates = useMemo(
    () => unifiedSlotCandidates(catalog.data?.models ?? [], files.data?.entries ?? []),
    [catalog.data, files.data],
  );
  const displayedIds = modelIds;
  const locked = frozen || !onChange;

  function rowFor(id: string): SlotCandidate {
    return (
      candidates.find((item) => item.id === id) ?? {
        id,
        name: id,
        provider: "",
        placement: "",
        modality: "",
        strengths: [],
        source: "catalog",
        litellmModel: "",
        revision: "",
      }
    );
  }

  function commit(next: string[]) {
    if (locked || !onChange) {
      return;
    }
    onChange(slotIdsForConfirm(next));
  }

  const rows = displayedIds.map(rowFor);
  const available = candidates.filter((item) => !displayedIds.includes(item.id));

  return (
    <section className="block" id="comparison-slots" data-testid="slot-fill">
      {embedded ? null : <h2 className="section-title">{t("rounds.slots")}</h2>}
      <ErrorBanner message={catalog.error} code={catalog.errorCode} />
      <ErrorBanner message={files.error} code={files.errorCode} />
      {catalog.loading || files.loading ? <p className="muted">{t("rounds.slotsLoading")}</p> : null}
      {catalog.data && candidates.length === 0 ? <p className="muted">{t("rounds.slotsEmpty")}</p> : null}
      <div className="row">
        <button
          type="button"
          className="secondary"
          disabled={locked || candidates.length === 0}
          onClick={() => commit(recommendSlots(candidates, 3).map((item) => item.id))}
        >
          {t("rounds.recommend")}
        </button>
        <select value={pendingSlot} disabled={locked} onChange={(event) => setPendingSlot(event.target.value)}>
          <option value="">{t("rounds.addSlot")}</option>
          {available.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={locked || pendingSlot === ""}
          onClick={() => {
            if (displayedIds.includes(pendingSlot)) {
              setPendingSlot("");
              return;
            }
            commit([...displayedIds, pendingSlot]);
            setPendingSlot("");
          }}
        >
          {t("rounds.addSlot")}
        </button>
      </div>
      {rows.length === 0 && candidates.length > 0 && !frozen ? <p className="muted">{t("rounds.slotsNone")}</p> : null}
      {rows.length > 0 ? (
        <div className="compare-wrap" data-testid="slot-board">
          <table>
            <thead>
              <tr>
                <th />
                {rows.map((slot) => (
                  <th key={slot.id}>
                    <div>
                      <code>{slot.id}</code>
                    </div>
                    <div>{slot.name}</div>
                    <button
                      type="button"
                      className="secondary"
                      disabled={locked}
                      onClick={() => commit(displayedIds.filter((item) => item !== slot.id))}
                    >
                      {t("rounds.removeSlot")}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr>
                <th>{t("pools.col.provider")}</th>
                {rows.map((slot) => (
                  <td key={slot.id}>{slot.provider}</td>
                ))}
              </tr>
              <tr>
                <th>{t("pools.placement")}</th>
                {rows.map((slot) => (
                  <td key={slot.id}>{slot.placement}</td>
                ))}
              </tr>
              <tr>
                <th>{t("pools.modality")}</th>
                {rows.map((slot) => (
                  <td key={slot.id}>{slot.modality}</td>
                ))}
              </tr>
              <tr>
                <th>{t("pools.strengths")}</th>
                {rows.map((slot) => (
                  <td key={slot.id}>{slot.strengths.length > 0 ? slot.strengths.join(", ") : t("dim.na")}</td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
