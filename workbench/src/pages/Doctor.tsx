import { useState } from "react";
import { ApiError, postJson, routes, type DoctorResponse, type DryRunResponse } from "../api";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { statusText, translateId } from "../i18n/messages";
import { describeFailure, useLoad } from "../useLoad";

const localGateway = "http://ohbe-gateway:4000";
const cloudGateway = "https://api.litellm.ai/v1";

export function DoctorPage() {
  const { t } = useI18n();
  const [nonce, setNonce] = useState(0);
  const doctor = useLoad<DoctorResponse>(`${routes.doctor}?t=${nonce}`);
  const [gatewayUrl, setGatewayUrl] = useState(localGateway);
  const [live, setLive] = useState(false);
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<DryRunResponse | null>(null);
  const [failure, setFailure] = useState<ApiError | null>(null);

  async function submit(url: string, requestLive: boolean) {
    setGatewayUrl(url);
    setLive(requestLive);
    setPending(true);
    setOutcome(null);
    setFailure(null);
    try {
      // Identifiers stay English. The UI locale does not change this body.
      const body = await postJson<DryRunResponse>(routes.dryRun, {
        gateway_url: url,
        experiment_id: "fixture-pilot",
        pool_id: "pool-pilot",
        live: requestLive,
      });
      setOutcome(body);
    } catch (error) {
      if (error instanceof ApiError) {
        setFailure(error);
      } else {
        const described = describeFailure(error);
        const code = described.code === "request_failed" ? "validationFailed" : described.code;
        setFailure(new ApiError(0, code, described.message ?? ""));
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section>
      <header className="page-head">
        <div className="row spread">
          <h1>{t("doctor.title")}</h1>
          <button type="button" className="secondary" onClick={() => setNonce((value) => value + 1)}>
            {t("doctor.recheck")}
          </button>
        </div>
      </header>
      <ErrorBanner message={doctor.error} code={doctor.errorCode} />
      {doctor.loading ? <p className="muted">{t("doctor.loading")}</p> : null}
      {doctor.data ? (
        <>
          <p className={doctor.data.ok ? "banner ok" : "banner bad"}>
            {t("doctor.banner", {
              status: doctor.data.ok ? t("doctor.pass") : t("doctor.fail"),
              spend: statusText(t, doctor.data.live_spend),
              execution: statusText(t, doctor.data.spend_execution),
            })}
          </p>
          <ul className="checks">
            {doctor.data.checks.map((check) => (
              <li key={check.id} className={check.passed ? "pass" : "fail"}>
                <strong>{translateId(t, "check", check.id) ?? check.id}</strong>
                <span>{check.summary}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <form
        className="block"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(gatewayUrl, live);
        }}
      >
        <h2>{t("doctor.formTitle")}</h2>
        <p className="banner" data-testid="dry-run-label">
          {t("doctor.dryRunLabel")}
        </p>
        <label>
          {t("doctor.gateway")}
          <input value={gatewayUrl} onChange={(event) => setGatewayUrl(event.target.value)} spellCheck={false} />
        </label>
        <label className="check-line">
          <input type="checkbox" checked={live} onChange={(event) => setLive(event.target.checked)} />
          {t("doctor.live")}
        </label>
        <div className="row">
          <button type="submit" disabled={pending}>
            {pending ? t("doctor.pending") : t("doctor.submit")}
          </button>
          <button type="button" className="secondary" onClick={() => void submit(localGateway, false)} disabled={pending}>
            {t("doctor.local")}
          </button>
          <button type="button" className="danger" onClick={() => void submit(cloudGateway, true)} disabled={pending}>
            {t("doctor.cloud")}
          </button>
        </div>
      </form>
      {outcome ? (
        <article className="block">
          <h2>{t("doctor.passed")}</h2>
          <p className="banner">{outcome.label}</p>
          <p>
            {t("doctor.outcome", {
              host: outcome.gateway_host,
              port: outcome.gateway_port,
              mode: statusText(t, outcome.mode),
              spend: statusText(t, outcome.spend),
              network: statusText(t, outcome.network),
              executed: statusText(t, String(outcome.executed)),
            })}
          </p>
          <ul className="checks">
            {outcome.checks.map((check) => (
              <li key={check.id} className={check.passed ? "pass" : "fail"}>
                <strong>{translateId(t, "check", check.id) ?? check.id}</strong>
                <span>{check.summary}</span>
              </li>
            ))}
          </ul>
        </article>
      ) : null}
      {failure ? (
        <article className="block">
          <h2>{translateId(t, "error", failure.code) ?? failure.code}</h2>
          {failure.status > 0 ? (
            <p className="banner bad">
              HTTP {failure.status} · <code>{failure.code}</code>
            </p>
          ) : null}
          {failure.message ? <p>{failure.message}</p> : null}
        </article>
      ) : null}
    </section>
  );
}
