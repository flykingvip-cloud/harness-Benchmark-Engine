import { useState } from "react";
import { ApiError, postJson, routes, type CatalogList, type LibraryActionResult } from "../api";
import { cloudAddIssues, localAddIssues, localIdentity, localVllmIssues, OSS_MODEL_NAME_CANDIDATES, type PoolProvider } from "../forms";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { ActionNote, TargetFields, useLibraryGate } from "../library";
import { describeFailure, useLoad } from "../useLoad";

type ModelPanel = "local" | "cloud" | null;

export function PoolsPage({ embedded = false }: { embedded?: boolean } = {}) {
  const { t } = useI18n();
  const gate = useLibraryGate();
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
  const entries = (catalog.data?.entries ?? []).filter((item) => item.ok);
  const localDraft = { provider, name, revision };
  const localIssues = localAddIssues(localDraft);
  const cloudIssues = cloudAddIssues({ ...localDraft, apiKey, gatewayUrl });
  const vllmIssues = provider === "vllm" ? localVllmIssues({ baseUrl: vllmBaseUrl, apiKey: vllmApiKey }) : [];
  const canScan = gate.ready && localIssues.length === 0 && vllmIssues.length === 0 && pending === null;
  const canAdmit = gate.ready && cloudIssues.length === 0 && pending === null;

  function toggle(next: Exclude<ModelPanel, null>) {
    setPanel((current) => (current === next ? null : next));
    setResult(null);
    setFailure(null);
    setApiKey("");
    setVllmApiKey("");
  }

  async function admit() {
    if (!canAdmit) {
      return;
    }
    setPending("admit");
    setFailure(null);
    try {
      const identity = localIdentity(localDraft);
      const saved = await postJson<LibraryActionResult>(routes.modelLibraryAdmit, {
        ...gate.body,
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
      setResult(saved);
      if (saved.ok) {
        setApiKey("");
        setNonce((value) => value + 1);
      }
    } catch (error) {
      setFailure(asApiError(error));
    } finally {
      setPending(null);
    }
  }

  async function scan() {
    if (!canScan) {
      return;
    }
    setPending("scan");
    setFailure(null);
    try {
      const identity = localIdentity(localDraft);
      const saved = await postJson<LibraryActionResult>(routes.modelLibraryScan, {
        ...gate.body,
        provider,
        name,
        revision,
        model_id: identity.model_id,
        litellm_model: identity.litellm_model,
        ...(provider === "vllm" ? { vllm_base_url: vllmBaseUrl.trim(), api_key: vllmApiKey } : {}),
      });
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
      <ul className="library-list">
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
          <p className="muted">{panel === "cloud" ? t("pools.cloudLead") : t("pools.localLead")}</p>
          <TargetFields gate={gate} />
          <div className="form-grid">
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
                list={panel === "local" ? "oss-model-names" : undefined}
                spellCheck={false}
                onChange={(event) => setName(event.target.value)}
              />
              {panel === "local" ? (
                <datalist id="oss-model-names">
                  {OSS_MODEL_NAME_CANDIDATES.flatMap((group) =>
                    group.names.map((slug) => (
                      <option key={slug} value={slug}>
                        {group.family}
                      </option>
                    )),
                  )}
                </datalist>
              ) : null}
            </label>
            <label>
              {t("library.revision")}
              <input value={revision} spellCheck={false} onChange={(event) => setRevision(event.target.value)} />
            </label>
            {panel === "cloud" ? (
              <>
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
              </>
            ) : null}
            {panel === "local" && provider === "vllm" ? (
              <>
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
              </>
            ) : null}
          </div>
          {panel === "local" ? <p className="muted">{t("pools.localNameHint")}</p> : null}
          {panel === "local" && provider === "vllm" ? <p className="muted">{t("pools.vllmHint")}</p> : null}
          <p className="muted">{t("pools.fieldHint")}</p>
          <div className="row">
            {panel === "cloud" ? (
              <button type="button" disabled={!canAdmit} onClick={() => void admit()}>
                {pending === "admit" ? t("library.admitting") : t("library.admit")}
              </button>
            ) : (
              <button type="button" className="secondary" disabled={!canScan} onClick={() => void scan()}>
                {pending === "scan" ? t("library.scanning") : t("library.scan")}
              </button>
            )}
          </div>
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
