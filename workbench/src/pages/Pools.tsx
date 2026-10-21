import { useEffect, useRef, useState } from "react";
import { ApiError, postJson, routes, type CatalogList, type LibraryActionResult } from "../api";
import { cloudAddIssues, localIdentity, localScanBody, localVllmIssues, OSS_MODEL_NAME_CANDIDATES, type PoolProvider } from "../forms";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { ActionNote } from "../library";
import { describeFailure, useLoad } from "../useLoad";

type ModelPanel = "local" | "cloud" | null;

export function PoolsPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { t } = useI18n();
  const [panel, setPanel] = useState<ModelPanel>(null);
  const [nonce, setNonce] = useState(0);
  const catalog = useLoad<CatalogList>(`${routes.modelCatalog}?t=${nonce}`);
  const [provider, setProvider] = useState<PoolProvider>("vllm");
  const [name, setName] = useState("");
  const [revision, setRevision] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [gatewayUrl, setGatewayUrl] = useState("");
  const [vllmBaseUrl, setVllmBaseUrl] = useState("");
  const [vllmApiKey, setVllmApiKey] = useState("");
  const [result, setResult] = useState<LibraryActionResult | null>(null);
  const [pending, setPending] = useState<"admit" | "scan" | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const request = useRef(0);
  const entries = (catalog.data?.entries ?? []).filter((item) => item.ok);
  const localDraft = { provider, name, revision };
  const cloudIssues = cloudAddIssues({ ...localDraft, apiKey, gatewayUrl });
  const vllmIssues = localVllmIssues({ baseUrl: vllmBaseUrl, apiKey: vllmApiKey });
  const canScan = vllmIssues.length === 0 && pending === null;
  const canAdmit = cloudIssues.length === 0 && pending === null;

  function toggle(next: Exclude<ModelPanel, null>) {
    request.current += 1;
    setPanel((current) => (current === next ? null : next));
    setResult(null);
    setFailure(null);
    setPending(null);
    setApiKey("");
    setVllmApiKey("");
  }

  async function admit() {
    if (!canAdmit) {
      return;
    }
    const gen = ++request.current;
    setPending("admit");
    setFailure(null);
    try {
      const identity = localIdentity(localDraft);
      const saved = await postJson<LibraryActionResult>(routes.modelLibraryAdmit, {
        target: "local",
        host: "",
        port: null,
        model_id: identity.model_id,
        provider,
        name,
        revision,
        litellm_model: identity.litellm_model,
        modality: "text",
        strengths: ["reasoning"],
        placement: "gateway",
        api_key: apiKey,
        gateway_url: gatewayUrl.trim(),
      });
      if (request.current !== gen) {
        return;
      }
      setResult(saved);
      if (saved.ok) {
        setApiKey("");
        setNonce((value) => value + 1);
      }
    } catch (error) {
      if (request.current === gen) {
        setFailure(asApiError(error));
      }
    } finally {
      if (request.current === gen) {
        setPending(null);
      }
    }
  }

  async function scan() {
    if (vllmIssues.length > 0) {
      return;
    }
    const gen = ++request.current;
    setPending("scan");
    setFailure(null);
    try {
      const saved = await postJson<LibraryActionResult>(
        routes.modelLibraryScan,
        localScanBody({ baseUrl: vllmBaseUrl, apiKey: vllmApiKey }),
      );
      if (request.current !== gen) {
        return;
      }
      setResult(saved);
      if (saved.admitted.length > 0) {
        setNonce((value) => value + 1);
      }
    } catch (error) {
      if (request.current === gen) {
        setFailure(asApiError(error));
      }
    } finally {
      if (request.current === gen) {
        setPending(null);
      }
    }
  }

  useEffect(() => {
    if (panel !== "local") {
      return;
    }
    void scan();
  }, [panel]);

  const actions = (
        <div className="row">
          <button type="button" className={panel === "local" ? "" : "secondary"} onClick={() => toggle("local")}>
            {t("pools.addLocal")}
          </button>
          <button type="button" className={panel === "cloud" ? "" : "secondary"} onClick={() => toggle("cloud")}>
            {t("pools.addCloud")}
          </button>
        </div>
  );

  const body = (
    <>
      <header className={embedded ? "page-head bar prepare-tab-actions" : "page-head bar"}>
        {embedded ? null : <h1>{t("pools.title")}</h1>}
        {actions}
      </header>
      <ErrorBanner message={failure?.message ?? catalog.error} code={failure?.code ?? catalog.errorCode} />
      {catalog.loading ? <p className="muted">{t("library.loading")}</p> : null}
      {!catalog.loading && entries.length === 0 ? <p className="library-empty">{t("library.emptyModels")}</p> : null}
      {entries.length > 0 ? (
      <ul className="library-list" data-testid="pool-catalog">
        {entries.map((item) => (
          <li key={item.filename} className="library-row">
            <div>
              <strong>{item.name}</strong>
              <span className="muted">
                {" "}
                {item.provider} · {item.revision}
              </span>
            </div>
            <span className="pill">{item.placement === "gateway" ? t("pools.placement.gateway") : t("pools.placement.local")}</span>
          </li>
        ))}
      </ul>
      ) : null}
      {panel ? (
        <div className="library-panel">
          <div className="row spread">
            <h2>{panel === "cloud" ? t("pools.addCloud") : t("pools.addLocal")}</h2>
            <button type="button" className="secondary" onClick={() => setPanel(null)}>
              {t("library.close")}
            </button>
          </div>
          {panel === "local" ? (
            <>
              <div className="row">
                <button type="button" data-testid="pool-scan" disabled={!canScan} onClick={() => void scan()}>
                  {pending === "scan" ? t("library.scanning") : t("pools.scanMachine")}
                </button>
              </div>
              {vllmIssues.length > 0 ? <p className="banner bad">{t("pools.vllmRejected")}</p> : null}
              <details className="advanced" data-testid="pool-advanced">
                <summary>{t("pools.advanced")}</summary>
                <div className="form-grid">
                  <label>
                    {t("pools.vllmBaseUrl")}
                    <input
                      value={vllmBaseUrl}
                      spellCheck={false}
                      onChange={(event) => setVllmBaseUrl(event.target.value)}
                    />
                  </label>
                  <label>
                    {t("pools.vllmApiKey")}
                    <input
                      type="password"
                      value={vllmApiKey}
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) => setVllmApiKey(event.target.value)}
                    />
                  </label>
                </div>
              </details>
              {result ? (
                <div data-testid="pool-scan-results">
                  <p className="scan-label">{t("pools.scanResults")}</p>
                  {result.admitted.length === 0 ? (
                    <p className="library-empty">{t("pools.scanEmpty")}</p>
                  ) : (
                    <ul className="library-list">
                      {result.admitted.map((modelId) => (
                        <li key={modelId} className="library-row">
                          <strong>{modelId}</strong>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : null}
            </>
          ) : (
            <>
              <div className="form-grid" data-testid="pool-cloud-form">
                <label>
                  {t("library.provider")}
                  <select value={provider} onChange={(event) => setProvider(event.target.value === "ollama" ? "ollama" : "vllm")}>
                    <option value="vllm">vllm</option>
                    <option value="ollama">ollama</option>
                  </select>
                </label>
                <label>
                  {t("library.modelName")}
                  <input
                    value={name}
                    list="oss-model-names"
                    spellCheck={false}
                    onChange={(event) => setName(event.target.value)}
                  />
                  <datalist id="oss-model-names">
                    {OSS_MODEL_NAME_CANDIDATES.flatMap((group) =>
                      group.names.map((slug) => (
                        <option key={slug} value={slug}>
                          {group.family}
                        </option>
                      )),
                    )}
                  </datalist>
                </label>
                <label>
                  {t("library.revision")}
                  <input value={revision} spellCheck={false} onChange={(event) => setRevision(event.target.value)} />
                </label>
                <label>
                  {t("pools.apiKey")}
                  <input
                    type="password"
                    value={apiKey}
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(event) => setApiKey(event.target.value)}
                  />
                </label>
                <label>
                  {t("pools.gatewayUrl")}
                  <input value={gatewayUrl} spellCheck={false} onChange={(event) => setGatewayUrl(event.target.value)} />
                </label>
              </div>
              <div className="row">
                <button type="button" disabled={!canAdmit} onClick={() => void admit()}>
                  {pending === "admit" ? t("library.admitting") : t("library.admit")}
                </button>
              </div>
            </>
          )}
          <details className="explain" data-testid="pool-notes" key={panel}>
            <summary>{t("pools.notes")}</summary>
            {panel === "local" ? (
              <>
                <p>{t("pools.localLead")}</p>
                <p>{t("pools.vllmHint")}</p>
              </>
            ) : (
              <>
                <p>{t("pools.cloudLead")}</p>
                <p>{t("pools.localNameHint")}</p>
                <p>{t("pools.revisionHint")}</p>
                <p>{t("pools.fieldHint")}</p>
              </>
            )}
          </details>
          <ActionNote result={result} />
        </div>
      ) : null}
    </>
  );

  if (embedded) {
    return <div className="prepare-tab-panel" data-testid="prepare-pools-panel">{body}</div>;
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
