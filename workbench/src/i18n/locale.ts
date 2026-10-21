export const LOCALE_STORAGE_KEY = "ohbe.workbench.locale";

export type Locale = "zh" | "en";

export function parseLocale(value: string | null | undefined): Locale | null {
  if (value === "zh" || value === "en") {
    return value;
  }
  return null;
}

/** Chinese when any browser language starts with `zh`; otherwise English. */
export function browserLocale(languages: readonly string[]): Locale {
  const prefersZh = languages.some((language) => language.toLowerCase().startsWith("zh"));
  return prefersZh ? "zh" : "en";
}

/** A saved `zh` or `en` overrides the browser. Anything else falls through. */
export function resolveLocale(stored: string | null | undefined, languages: readonly string[]): Locale {
  return parseLocale(stored) ?? browserLocale(languages);
}

export function readStoredLocale(storage: Pick<Storage, "getItem"> | null): string | null {
  if (!storage) {
    return null;
  }
  try {
    return storage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function writeStoredLocale(storage: Pick<Storage, "setItem"> | null, locale: Locale): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Private mode and blocked storage still switch the current view.
  }
}
