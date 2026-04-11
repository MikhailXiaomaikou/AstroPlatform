import { Component, lazy, Suspense, useState, useEffect, type ErrorInfo, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { I18nProvider, useI18n, ALL_LANGS, LANG_NAMES, type Lang } from "./i18n";
import api from "./api/client";
import "./App.css";

class ErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("React ErrorBoundary caught:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: "2rem", color: "var(--color-text)" }}>
          <h2>Something went wrong</h2>
          <pre style={{ color: "var(--color-red)", whiteSpace: "pre-wrap", marginTop: "1rem" }}>
            {this.state.error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="btn-primary"
            style={{ marginTop: "1rem" }}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const DataBrowser = lazy(() => import("./pages/DataBrowser/DataBrowser"));
const PipelineCanvas = lazy(() => import("./pages/Pipeline/PipelineCanvas"));
const AuthPage = lazy(() => import("./pages/Auth/AuthPage"));
const ADQLPage = lazy(() => import("./pages/ADQL/ADQLPage"));
const WorkspacePage = lazy(() => import("./pages/Workspace/WorkspacePage"));
const TeamPage = lazy(() => import("./pages/Team/TeamPage"));
const HelpPage = lazy(() => import("./pages/Help/HelpPage"));
const ChatPage = lazy(() => import("./pages/Chat/ChatPage"));
const SettingsPage = lazy(() => import("./pages/Settings/SettingsPage"));
const AlertDashboard = lazy(() => import("./pages/AlertDashboard/AlertDashboard"));
const AnomalyExplorer = lazy(() => import("./pages/AnomalyExplorer/AnomalyExplorer"));

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("astro_theme");
    return saved === "light" ? "light" : "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("astro_theme", theme);
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  return { theme, toggle };
}

function NavBar() {
  const { theme, toggle } = useTheme();
  const { user, logout } = useAuth();
  const { lang, setLang, t } = useI18n();

  return (
    <nav className="top-nav">
      <NavLink to="/" className="logo">Standard Astro</NavLink>
      <NavLink to="/">{t("nav.data_browser")}</NavLink>
      <NavLink to="/pipeline">{t("nav.pipeline")}</NavLink>
      <NavLink to="/workspace">{t("nav.workspace")}</NavLink>
      <NavLink to="/adql">{t("nav.adql")}</NavLink>
      <NavLink to="/team">{t("nav.team")}</NavLink>
      <NavLink to="/chat">{t("nav.ai_assistant")}</NavLink>
      <NavLink to="/help">Help</NavLink>
      <NavLink to="/alerts">{t("nav.alerts")}</NavLink>
      <NavLink to="/anomalies">{t("nav.anomalies")}</NavLink>
      <NavLink to="/settings">{t("nav.settings")}</NavLink>
      <div className="nav-spacer" />
      <button
        className="theme-toggle"
        onClick={toggle}
        title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
        aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      >
        {theme === "dark" ? "\u2600\uFE0F" : "\uD83C\uDF19"}
      </button>
      <select
        className="lang-select"
        value={lang}
        onChange={(e) => setLang(e.target.value as Lang)}
        title="Language"
      >
        {ALL_LANGS.map((l) => (
          <option key={l} value={l}>{LANG_NAMES[l]}</option>
        ))}
      </select>
      {user ? (
        <div className="nav-user">
          {user.avatar_url ? (
            <img src={user.avatar_url} alt="" className="nav-avatar" referrerPolicy="no-referrer" />
          ) : (
            <span className="nav-avatar nav-avatar-placeholder">
              {(user.display_name || user.username || user.email)[0].toUpperCase()}
            </span>
          )}
          <span className="nav-user-name" title={user.username || user.email}>
            {user.display_name || user.username || user.email.split("@")[0]}
          </span>
          <button className="nav-logout" onClick={logout} title={t("nav.sign_out")}>
            {t("nav.sign_out")}
          </button>
        </div>
      ) : (
        <NavLink to="/auth" className="nav-auth-link">{t("nav.sign_in")}</NavLink>
      )}
      <span className="tier-badge tier-beta">beta</span>
    </nav>
  );
}

function BackendBanner() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (sessionStorage.getItem("astro_backend_checked")) return;
    let cancelled = false;

    // Show banner only if health check takes longer than 8 seconds
    const showTimer = setTimeout(() => {
      if (!cancelled) setShow(true);
    }, 8000);

    api.get("/health", { timeout: 30000 }).then(() => {
      if (!cancelled) {
        setShow(false);
        clearTimeout(showTimer);
        sessionStorage.setItem("astro_backend_checked", "1");
      }
    }).catch(() => {
      // Backend unreachable — show banner briefly then dismiss
      if (!cancelled) {
        setShow(true);
        setTimeout(() => {
          setShow(false);
          sessionStorage.setItem("astro_backend_checked", "1");
        }, 5000);
      }
    });

    return () => {
      cancelled = true;
      clearTimeout(showTimer);
    };
  }, []);

  if (!show) return null;
  return (
    <div className="backend-banner">
      Connecting to backend server...
    </div>
  );
}

function App() {
  return (
    <I18nProvider>
    <AuthProvider>
      <BrowserRouter>
        <BackendBanner />
        <NavBar />
        <main className="main-content">
          <ErrorBoundary>
            <Suspense fallback={<div className="fits-loading">Loading...</div>}>
              <Routes>
                <Route path="/" element={<DataBrowser />} />
                <Route path="/pipeline" element={<PipelineCanvas />} />
                <Route path="/workspace" element={<WorkspacePage />} />
                <Route path="/adql" element={<ADQLPage />} />
                <Route path="/team" element={<TeamPage />} />
                <Route path="/help" element={<HelpPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/alerts" element={<AlertDashboard />} />
                <Route path="/anomalies" element={<AnomalyExplorer />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/auth" element={<AuthPage />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </BrowserRouter>
    </AuthProvider>
    </I18nProvider>
  );
}

export default App;
