export interface SlotCandidate {
  id: string;
  name: string;
  provider: string;
  placement: string;
  modality: string;
  strengths: string[];
  source: "catalog" | "fixture";
  litellmModel: string;
  revision: string;
}

/** Prefer a mix of provider and placement, then entries that list strengths. Ids break ties. */
export function recommendSlots(candidates: readonly SlotCandidate[], limit = 3): SlotCandidate[] {
  const pool = [...candidates].sort((left, right) => left.id.localeCompare(right.id) || left.source.localeCompare(right.source));
  const picked: SlotCandidate[] = [];
  const providers = new Set<string>();
  const placements = new Set<string>();
  while (picked.length < limit && picked.length < pool.length) {
    let bestIndex = -1;
    let bestScore = -1;
    pool.forEach((item, index) => {
      if (picked.some((chosen) => chosen.id === item.id)) {
        return;
      }
      let score = 0;
      if (!providers.has(item.provider)) {
        score += 2;
      }
      if (!placements.has(item.placement)) {
        score += 2;
      }
      if (item.strengths.length > 0) {
        score += 1;
      }
      if (score > bestScore) {
        bestScore = score;
        bestIndex = index;
      }
    });
    if (bestIndex < 0) {
      break;
    }
    const chosen = pool[bestIndex];
    if (!chosen) {
      break;
    }
    picked.push(chosen);
    providers.add(chosen.provider);
    placements.add(chosen.placement);
  }
  return picked;
}

export function slotFrozen(status: string | null | undefined): boolean {
  return status === "confirmed";
}

export interface AdmittedModelRef {
  model_id: string;
  display_name: string;
  provider: string;
}

export interface CatalogFileRef {
  ok: boolean;
  model_id: string;
  name: string;
  provider: string;
  placement: string;
  modality: string;
  strengths: string[];
  litellm_model: string;
  revision: string;
}

/** Membership comes from the intake catalog. Disk files only add display fields. */
export function unifiedSlotCandidates(
  admitted: readonly AdmittedModelRef[],
  files: readonly CatalogFileRef[],
): SlotCandidate[] {
  const byId = new Map<string, CatalogFileRef>();
  for (const file of files) {
    if (file.ok && file.model_id && !byId.has(file.model_id)) {
      byId.set(file.model_id, file);
    }
  }
  const seen = new Set<string>();
  const merged: SlotCandidate[] = [];
  for (const model of admitted) {
    if (!model.model_id || seen.has(model.model_id)) {
      continue;
    }
    seen.add(model.model_id);
    const file = byId.get(model.model_id);
    merged.push({
      id: model.model_id,
      name: file?.name || model.display_name || model.model_id,
      provider: file?.provider || model.provider,
      placement: file?.placement ?? "",
      modality: file?.modality ?? "",
      strengths: file ? [...file.strengths] : [],
      source: "catalog",
      litellmModel: file?.litellm_model ?? "",
      revision: file?.revision ?? "",
    });
  }
  return merged;
}

/** Confirm writes this list. It is the comparison-slot ids, not a second board. */
export function slotIdsForConfirm(modelIds: readonly string[]): string[] {
  return [...modelIds];
}

export const ADMITTED_POOL_ID = "pool-admitted";

/** Generate binds this pool. Empty slots are not a fallback to the fixture pool. */
export function admittedPoolId(modelIds: readonly string[]): string | null {
  return modelIds.length > 0 ? ADMITTED_POOL_ID : null;
}
