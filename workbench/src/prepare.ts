export type PrepareTab = "pools" | "harnesses";

export function prepareTabFromSearch(raw: string | null): PrepareTab {
  return raw === "harnesses" ? "harnesses" : "pools";
}
