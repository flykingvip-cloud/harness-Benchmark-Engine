import { useState } from "react";
import { ApiError, postJson, routes, type HarnessProfileList, type LibraryActionResult } from "../api";
import { HARNESS_CATALOG, harnessClosed, type HarnessAdapter } from "../forms";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { isMessageKey } from "../i18n/messages";
import { ActionNote, TargetFields, useLibraryGate, type LibraryPanel } from "../library";
import { describeFailure, useLoad } from "../useLoad";

export function HarnessesPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { t } = useI18n();
  const gate = useLibraryGate({ auth: true });
  const [panel, setPanel] = useState<LibraryPanel>(null);
  const [nonce, setNonce] = useState(0);
  const listed = useLoad<HarnessProfileList>(`${routes.harnessConfigs}?t=${nonce}`);
  const [selected, setSelected] = useState<HarnessAdapter | null>(null);
  const [result, setResult] = useState<LibraryActionResult | null>(null);
  const [pending, setPending] = useState<"install" | "scan" | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const profiles = listed.data?.profiles ?? [];

  function toggle(next: Exclude<LibraryPanel, null>) {
    setPanel((current) => (current === next ? null : next));
    setResult(null);
    setFailure(null);
  }

  function requestBody() {
    if (gate.target === "remote" && gate.auth) {
      return { ...gate.body, ...gate.auth };
    }
    return { ...gate.body };
  }

  async function install() {
    if (!selected || !gate.ready) {
      return;
    }
    setPending("install");
    setFailure(null);
    try {
      const saved = await postJson<LibraryActionResult>(routes.harnessLibraryInstall, {
        ...requestBody(),
        adapter: selected,
        install_deps: true,
        timeout_seconds: 600,
      });
      setResult(saved);
      if (saved.ok) {
        setNonce((value) => value + 1);
      }
    } catch (error) {
      setFailure(asApiError(error));
    } finally {
      setPending(null);
    }
  }

  async function scan() {
    if (!gate.ready) {
      return;
    }
    setPending("scan");
    setFailure(null);
    try {
      const saved = await postJson<LibraryActionResult>(routes.harnessLibraryScan, requestBody());
      setResult(saved);
      if (saved.admitted.length > 0) {
        setNonce((value) => value + 1);
      }
    } catch (error) {
      setFailure(asApiError(error));
    } finally {
      setPending(null);
    }
  }


  function adapterLabel(adapter: string): string {
    const key = `harness.adapter.${adapter}`;
    return isMessageKey(key) ? t(key) : adapter;
  }

  function pinLabel(version: string): string {
    return version.endsWith("-unavailable") ? t("library.pinUnpublished") : version;
  }

  const actions = (
        <div className="row">
          <button type="button" className={panel === "preinstall" ? "" : "secondary"} onClick={() => toggle("preinstall")}>
            {t("library.preinstall")}
          </button>
          <button type="button" className={panel === "scan" ? "" : "secondary"} onClick={() => toggle("scan")}>
            {t("library.scan")}
          </button>
        </div>
  );

  const body = (
    <>
      <header className={embedded ? "page-head bar prepare-tab-actions" : "page-head bar"}>
        {embedded ? null : <h1>{t("harness.title")}</h1>}
        {actions}
      </header>
      <ErrorBanner message={failure?.message ?? listed.error} code={failure?.code ?? listed.errorCode} />
      {listed.loading ? <p className="muted">{t("library.loading")}</p> : null}
      {!listed.loading && profiles.length === 0 ? <p className="library-empty">{t("library.emptyHarness")}</p> : null}
      {profiles.length > 0 ? (
      <ul className="library-list">
        {profiles.map((profile) => {
          const plan = profile.registration === "plan" || profile.status === "plan";
          const closed = profile.parameters["runnable"] === false && typeof profile.parameters["closed-reason"] === "string" && profile.parameters["closed-reason"] !== "";
          return (
            <li key={profile.profile_id} className="library-row">
              <div>
                <strong>{adapterLabel(profile.adapter)}</strong>
                <span className="muted"> {profile.version}</span>
              </div>
              <span className="row">
                {closed ? <span className="pill bad">{t("library.notRunnable")}</span> : null}
                <span className={profile.ok ? (plan ? "pill warn" : "pill ok") : "pill bad"}>
                  {profile.ok ? (plan ? t("library.plan") : t("library.live")) : t("doctor.fail")}
                </span>
                {plan ? <span className="pill warn">{t("library.notLive")}</span> : null}
              </span>
            </li>
          );
        })}
      </ul>
      ) : null}
      {panel ? (
        <div className="library-panel">
          <div className="row spread">
            <h2>{panel === "preinstall" ? t("library.preinstall") : t("library.scan")}</h2>
            <button type="button" className="secondary" onClick={() => setPanel(null)}>
              {t("library.close")}
            </button>
          </div>
          <TargetFields gate={gate} />
          {panel === "preinstall" ? (
            <div className="catalog-picks">
              {HARNESS_CATALOG.map((entry) => (
                <button
                  key={entry.adapter}
                  type="button"
                  className={selected === entry.adapter ? "preset-card on" : "preset-card"}
                  onClick={() => {
                    setSelected(entry.adapter);
                    setResult(null);
                  }}
                >
                  <strong>{adapterLabel(entry.adapter)}</strong>
                  <span className="muted">{pinLabel(entry.version)}</span>
                  {harnessClosed(entry.adapter) ? (
                    <span className="pill bad">{t("library.notRunnable")}</span>
                  ) : (
                    <span className={entry.registration === "plan" ? "pill warn" : "pill ok"}>
                      {entry.registration === "plan" ? t("library.plan") : t("library.live")}
                    </span>
                  )}
                  {entry.adapter === "qwen_work" ? <span className="muted">{t("harness.closed.qwen_work")}</span> : null}
                  {entry.adapter === "doubao_work" ? <span className="muted">{t("harness.closed.doubao_work")}</span> : null}
                  {entry.adapter === "workbuddy" ? <span className="muted">{t("harness.install.workbuddy")}</span> : null}
                  {entry.adapter === "openclaw" || entry.adapter === "deepseek_harness" ? (
                    <span className="muted">{t("harness.install.npm")}</span>
                  ) : null}
                </button>
              ))}
            </div>
          ) : null}
          <div className="row">
            {panel === "preinstall" ? (
              <button type="button" disabled={!selected || !gate.ready || pending !== null} onClick={() => void install()}>
                {pending === "install" ? t("library.installing") : t("library.install")}
              </button>
            ) : (
              <button type="button" disabled={!gate.ready || pending !== null} onClick={() => void scan()}>
                {pending === "scan" ? t("library.scanning") : t("library.scan")}
              </button>
            )}
            {result?.offer_install ? (
              <button type="button" className="secondary" disabled={pending !== null} onClick={() => void install()}>
                {pending === "install" ? t("library.installing") : t("library.installDeps")}
              </button>
            ) : null}
          </div>
          <ActionNote result={result} />
        </div>
      ) : null}
    </>
  );

  if (embedded) {
    return <div className="prepare-tab-panel" data-testid="prepare-harnesses-panel">{body}</div>;
  }
  return <section className="bleed">{body}</section>;

}

function asApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  const described = describeFailure(error);
  return new ApiError(0, described.code, described.message ?? "");
}
