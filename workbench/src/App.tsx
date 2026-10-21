import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { LanguageToggle, useI18n } from "./i18n/I18nProvider";
import { DoctorPage } from "./pages/Doctor";
import { ExperimentsPage } from "./pages/Experiments";
import { OverviewPage } from "./pages/Overview";
import { MonitorPage } from "./pages/Monitor";
import { PreparePage } from "./pages/Prepare";
import { ResultsPage } from "./pages/Results";
import { RoundsPage } from "./pages/Rounds";

export function App() {
  const { locale, t } = useI18n();
  const { pathname } = useLocation();
  const crumbs = [
    { to: "/", label: t("nav.overview"), end: true },
    { to: "/prepare", label: t("nav.prepare"), end: false },
    { to: "/experiments", label: t("nav.fixtureCatalog"), end: false },
    { to: "/rounds", label: t("nav.rounds"), end: false },
    { to: "/monitor", label: t("nav.monitor"), end: false },
    { to: "/results", label: t("nav.results"), end: false },
    { to: "/doctor", label: t("nav.doctor"), end: false },
  ];
  const current = crumbs.find((link) => (link.end ? pathname === link.to : pathname.startsWith(link.to))) ?? crumbs[0];

  return (
    <div className="shell" data-locale={locale}>
      <header className="topbar">
        <p className="topbar-crumb">
          <span className="topbar-mark">OHBE</span>
          <span className="crumb-sep" aria-hidden="true">
            /
          </span>
          <span className="crumb-current">{current.label}</span>
        </p>
        <LanguageToggle />
      </header>
      <aside className="nav">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">
            OH
          </span>
          <div>
            <p className="brand">OHBE</p>
            <p className="brand-sub">{t("brand.sub")}</p>
          </div>
        </div>
        <nav className="nav-links">
          <NavLink to="/" end className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.overview")}
          </NavLink>
          <NavLink to="/prepare" className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.prepare")}
          </NavLink>
          <NavLink to="/experiments" className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.fixtureCatalog")}
          </NavLink>
          <p className="nav-group">{t("nav.group.path")}</p>
          <NavLink to="/rounds" className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.rounds")}
          </NavLink>
          <NavLink to="/monitor" className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.monitor")}
          </NavLink>
          <NavLink to="/results" className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.results")}
          </NavLink>
          <p className="nav-group">{t("nav.group.ops")}</p>
          <NavLink to="/doctor" className={({ isActive }) => (isActive ? "active" : "")}>
            {t("nav.doctor")}
          </NavLink>
        </nav>
        <p className="nav-note">{t("nav.note")}</p>
      </aside>
      <main>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/prepare" element={<PreparePage />} />
          <Route path="/pools" element={<Navigate to="/prepare?tab=pools" replace />} />
          <Route path="/harnesses" element={<Navigate to="/prepare?tab=harnesses" replace />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
          <Route path="/results" element={<ResultsPage />} />
          <Route path="/rounds" element={<RoundsPage />} />
          <Route path="/monitor" element={<MonitorPage />} />
          <Route path="/doctor" element={<DoctorPage />} />
        </Routes>
      </main>
    </div>
  );
}
