import { createContext, useCallback, useContext, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { readStoredLocale, resolveLocale, writeStoredLocale, type Locale } from "./locale";
import { isMessageKey, messages, type MessageKey } from "./messages";

type Vars = Record<string, string | number>;

interface I18nValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, vars?: Vars) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

function formatMessage(template: string, vars?: Vars): string {
  if (!vars) {
    return template;
  }
  return template.replace(/\{\{(\w+)\}\}/g, (match, name: string) => {
    const value = vars[name];
    return value === undefined ? match : String(value);
  });
}

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function browserLanguages(): readonly string[] {
  if (typeof navigator === "undefined") {
    return [];
  }
  if (navigator.languages.length > 0) {
    return navigator.languages;
  }
  return [navigator.language];
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => resolveLocale(readStoredLocale(safeLocalStorage()), browserLanguages()));

  const setLocale = useCallback((next: Locale) => {
    writeStoredLocale(safeLocalStorage(), next);
    setLocaleState(next);
  }, []);

  useLayoutEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
    document.title = messages[locale]["doc.title"];
  }, [locale]);

  const value = useMemo<I18nValue>(() => {
    return {
      locale,
      setLocale,
      t: (key, vars) => formatMessage(messages[locale][key], vars),
    };
  }, [locale, setLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const value = useContext(I18nContext);
  if (!value) {
    throw new Error("useI18n must be used within I18nProvider");
  }
  return value;
}

export function LanguageToggle() {
  const { locale, setLocale, t } = useI18n();
  return (
    <div className="lang-toggle" role="group" aria-label={t("lang.label")}>
      <button type="button" aria-pressed={locale === "zh"} onClick={() => setLocale("zh")}>
        {t("lang.zh")}
      </button>
      <span className="lang-sep" aria-hidden="true">
        |
      </span>
      <button type="button" aria-pressed={locale === "en"} onClick={() => setLocale("en")}>
        {t("lang.en")}
      </button>
    </div>
  );
}

export function ErrorBanner({ message, code }: { message?: string; code?: string }) {
  const { t } = useI18n();
  if (!message && !code) {
    return null;
  }
  const label = code ? translateError(t, code) : null;
  const primary = label ?? message ?? code ?? t("error.request_failed");
  return (
    <p className="banner bad" role="alert">
      {primary}
      {label && message ? <span className="error-detail">{message}</span> : null}
    </p>
  );
}

function translateError(t: I18nValue["t"], code: string): string | null {
  const key = `error.${code}`;
  if (!isMessageKey(key)) {
    return null;
  }
  return t(key);
}
