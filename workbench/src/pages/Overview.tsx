import { Link } from "react-router-dom";
import { routes, type DoctorResponse, type ModelFitResponse, type StatusResponse, type SurfaceResponse } from "../api";
import { FrameworkCharts } from "../Charts";
import { DimensionBoard } from "../DimensionBoard";
import { ErrorBanner, useI18n } from "../i18n/I18nProvider";
import { statusText } from "../i18n/messages";
import { useFirstResult } from "../useResult";
import { useLoad } from "../useLoad";

export function OverviewPage() {
  const { t } = useI18n();
  const status = useLoad<StatusResponse>(routes.status);
  const surface = useLoad<SurfaceResponse>(routes.surface);
  const result = useFirstResult();
  const fit = useLoad<ModelFitResponse>(routes.modelFit);
  const sameError = status.error === surface.error && status.errorCode === surface.errorCode;

  return (
    <section>
      <header className="page-head">
        <h1>{t("overview.title")}</h1>
      </header>
      <ErrorBanner message={status.error} code={status.errorCode} />
      {surface.error && !sameError ? <ErrorBanner message={surface.error} code={surface.errorCode} /> : null}
      {status.loading || surface.loading ? <p className="muted">{t("overview.loading")}</p> : null}
      {status.data ? (
        <div className="cards">
          <article>
            <h2>{t("overview.engine")}</h2>
            <p className="metric">{status.data.ohbe_version}</p>
            <p className="muted">{t("overview.schema", { version: status.data.schema_version })}</p>
          </article>
          <article>
            <h2>{t("overview.liveSpend")}</h2>
            <p className="metric">{statusText(t, status.data.live_spend)}</p>
            <p className="muted">{t("overview.execution", { value: statusText(t, status.data.spend_execution) })}</p>
          </article>
          <article>
            <h2>{t("overview.cloud")}</h2>
            <p className="metric">{t("overview.cloudDenied")}</p>
            <p className="muted">{status.data.cloud_litellm}</p>
          </article>
        </div>
      ) : null}
      <DoctorSummary />
      {result.detail ? (
        <>
          <DimensionBoard detail={result.detail} variant="compact" />
          <FrameworkCharts
            detail={result.detail}
            metrics={fit.data?.model_metrics ?? null}
            mode={fit.data?.mode ?? null}
            harnessId={fit.data?.harness_id ?? null}
            variant="compact"
          />
        </>
      ) : null}
      {result.detailError && status.data ? <ErrorBanner message={result.detailError.message} code={result.detailError.code} /> : null}
      {surface.data ? (
        <>
          {surface.data.routes.length === 0 ? <p className="muted">{t("overview.empty")}</p> : null}
          {surface.data.routes.length > 0 ? (
            <div className="table-frame">
              <table>
              <thead>
                <tr>
                  <th>{t("overview.col.method")}</th>
                  <th>{t("overview.col.path")}</th>
                  <th>{t("overview.col.sdk")}</th>
                  <th>{t("overview.col.engine")}</th>
                </tr>
              </thead>
              <tbody>
                {surface.data.routes.map((route) => (
                  <tr key={`${route.method} ${route.path}`}>
                    <td>{route.method}</td>
                    <td>
                      <code>{route.path}</code>
                    </td>
                    <td>
                      <code>{route.sdk}</code>
                    </td>
                    <td>{route.engine}</td>
                  </tr>
                ))}
              </tbody>
              </table>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function DoctorSummary() {
  const { t } = useI18n();
  const doctor = useLoad<DoctorResponse>(routes.doctor);

  return (
    <article className="block doctor-summary">
      <div className="row spread">
        <h2>{t("overview.doctorTitle")}</h2>
        <Link className="text-link" to="/doctor">
          {t("overview.doctorOpen")}
        </Link>
      </div>
      <ErrorBanner message={doctor.error} code={doctor.errorCode} />
      {doctor.loading ? <p className="muted">{t("doctor.loading")}</p> : null}
      {doctor.data ? (
        <p className={doctor.data.ok ? "banner ok" : "banner bad"}>
          {t("doctor.banner", {
            status: doctor.data.ok ? t("doctor.pass") : t("doctor.fail"),
            spend: statusText(t, doctor.data.live_spend),
            execution: statusText(t, doctor.data.spend_execution),
          })}
        </p>
      ) : null}
    </article>
  );
}
