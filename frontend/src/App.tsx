import { Component, lazy, Suspense, useState, useEffect, type ErrorInfo, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { I18nProvider, useI18n, ALL_LANGS, LANG_NAMES, type Lang } from "./i18n";
import api from "./api/client";
import { useTracking } from "./hooks/useTracking";
import CommandPalette from "./components/CommandPalette";
import OnboardingOverlay from "./components/OnboardingOverlay";
import HelpDrawer from "./components/HelpDrawer";
import LandingPage from "./pages/Landing/LandingPage";
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
const SharedSessionPage = lazy(() => import("./pages/SharedSession/SharedSessionPage"));
const ObservationsPage = lazy(() => import("./pages/Observations/ObservationsPage"));
const AccountPage = lazy(() => import("./pages/Account/AccountPage"));
const PapersPage = lazy(() => import("./pages/Papers/PapersPage"));

function useTheme() {
  // Journal edition: default to light. We use a new key (astro_theme_v2) so
  // any prior "dark" value from the Apple-style design is ignored on first
  // load — users who actively prefer dark can toggle it again.
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("astro_theme_v2");
    return saved === "dark" ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("astro_theme_v2", theme);
    // Clear the legacy key so it can't override again if it was "dark".
    if (localStorage.getItem("astro_theme")) {
      localStorage.removeItem("astro_theme");
    }
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "dark" ? "light" : "dark"));

  return { theme, toggle };
}

function LangSwitch() {
  const { lang, setLang } = useI18n();
  const label: Record<Lang, string> = { en: "EN", zh: "中文", fr: "FR", es: "ES" };
  return (
    <div className="lang-switch" role="group" aria-label="Language">
      {ALL_LANGS.map((l) => (
        <button
          key={l}
          type="button"
          className={`lang-btn${lang === l ? " active" : ""}`}
          onClick={() => setLang(l)}
          title={LANG_NAMES[l]}
        >
          {label[l]}
        </button>
      ))}
    </div>
  );
}

function NavBar() {
  const { theme, toggle } = useTheme();
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <header className="journal-masthead">
      {/* Row 1 — brand + 8-tab nav, matches demo masthead-row */}
      <div className="journal-masthead-row">
        <div className="journal-masthead-brand">
          <NavLink to="/" className="journal-masthead-title" aria-label="Standard Astro home">
            Standard · Astro
          </NavLink>
          <div className="journal-masthead-sub">{t("brand.sub")}</div>
        </div>

        <button
          className="nav-hamburger"
          onClick={() => setMenuOpen(!menuOpen)}
          aria-label="Toggle navigation"
          aria-expanded={menuOpen}
        >
          <span /><span /><span />
        </button>

        <nav
          className={`journal-masthead-nav${menuOpen ? " open" : ""}`}
          aria-label="Primary"
        >
          <NavLink to="/"          end onClick={() => setMenuOpen(false)}>{t("nav.home")}</NavLink>
          <NavLink to="/chat"          onClick={() => setMenuOpen(false)}>{t("nav.ai_assistant")}</NavLink>
          <NavLink to="/search"        onClick={() => setMenuOpen(false)}>{t("nav.browse")}</NavLink>
          <NavLink to="/adql"          onClick={() => setMenuOpen(false)}>{t("nav.adql")}</NavLink>
          <NavLink to="/pipeline"      onClick={() => setMenuOpen(false)}>{t("nav.pipeline")}</NavLink>
          <NavLink to="/workspace"     onClick={() => setMenuOpen(false)}>{t("nav.sessions")}</NavLink>
          <NavLink to="/papers"        onClick={() => setMenuOpen(false)}>{t("nav.papers")}</NavLink>
          <NavLink to="/account"       onClick={() => setMenuOpen(false)}>{t("nav.account")}</NavLink>
        </nav>
      </div>

      {/* Row 2 — issue meta + language chips + theme + user controls */}
      <div className="journal-issue-meta">
        <span className="journal-issue-line">{t("issue.line1")}</span>
        <div className="journal-issue-right">
          <LangSwitch />
          <button
            className="journal-icon-btn"
            onClick={toggle}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            {theme === "dark" ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <circle cx="12" cy="12" r="5"/><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
              </svg>
            )}
          </button>
          {user ? (
            <div className="journal-user">
              {user.avatar_url ? (
                <img src={user.avatar_url} alt="" className="journal-avatar" referrerPolicy="no-referrer" />
              ) : (
                <span className="journal-avatar journal-avatar-placeholder">
                  {(user.display_name || user.username || user.email)[0].toUpperCase()}
                </span>
              )}
              <span className="journal-user-name" title={user.username || user.email}>
                {user.display_name || user.username || user.email.split("@")[0]}
              </span>
              <button className="journal-user-logout" onClick={logout}>
                {t("nav.sign_out")}
              </button>
            </div>
          ) : (
            <NavLink to="/auth" className="journal-auth-link">{t("nav.sign_in")}</NavLink>
          )}
          <HelpDrawer />
        </div>
      </div>
    </header>
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

function TrackingBridge() {
  const location = useLocation();
  const { track, setCurrentPage, getEventCount } = useTracking();
  const [startedAt] = useState(() => Date.now());
  const [pageEnteredAt, setPageEnteredAt] = useState(() => Date.now());
  const [lastPath, setLastPath] = useState(location.pathname);

  useEffect(() => {
    const pageName = location.pathname || "/";
    setCurrentPage(pageName);
    track("session.started", {
      referrer: document.referrer || "",
      user_agent_summary: navigator.userAgent.slice(0, 120),
    });

    const handleUnload = () => {
      const totalDuration = Date.now() - startedAt;
      const pageDuration = Date.now() - pageEnteredAt;
      track("session.page_view", { page_name: pageName, time_on_page_ms: pageDuration });
      track("session.ended", { total_duration_ms: totalDuration, events_count: getEventCount() });
    };

    window.addEventListener("beforeunload", handleUnload);
    return () => window.removeEventListener("beforeunload", handleUnload);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const pageName = location.pathname || "/";
    setCurrentPage(pageName);
    if (lastPath !== pageName) {
      track("session.page_view", {
        page_name: lastPath,
        time_on_page_ms: Date.now() - pageEnteredAt,
      });
      setLastPath(pageName);
      setPageEnteredAt(Date.now());
    }
  }, [lastPath, location.pathname, pageEnteredAt, setCurrentPage, track]);

  return null;
}

function JournalFooter() {
  const { t } = useI18n();
  return (
    <footer className="journal-footer">
      <div className="journal-footer-row">
        <div>
          <div className="journal-footer-title">Standard · Astro</div>
          <div className="journal-footer-sub">{t("footer.sub")}</div>
        </div>
        <div className="journal-footer-cols">
          <div>
            <strong>{t("footer.col.platform")}</strong>
            <NavLink to="/help">{t("footer.link.docs")}</NavLink>
            <a href="https://github.com/MikhailXiaomaikou/Standard-Astro" target="_blank" rel="noreferrer">GitHub</a>
            <NavLink to="/observations">{t("nav.observations")}</NavLink>
          </div>
          <div>
            <strong>{t("footer.col.science")}</strong>
            <NavLink to="/search">{t("nav.data_browser")}</NavLink>
            <NavLink to="/adql">{t("nav.adql")}</NavLink>
            <NavLink to="/pipeline">{t("nav.pipeline")}</NavLink>
          </div>
          <div>
            <strong>{t("footer.col.community")}</strong>
            <NavLink to="/team">{t("nav.team")}</NavLink>
            <NavLink to="/chat">{t("nav.ai_assistant")}</NavLink>
            <NavLink to="/account">{t("nav.account")}</NavLink>
          </div>
        </div>
      </div>
      <div className="journal-footer-base">
        <span>© 2026 Standard Astro</span>
        <span>claude-opus-4-7 · Planck18 · Gaia DR3 · PARSEC 3.9</span>
      </div>
    </footer>
  );
}

function App() {
  return (
    <I18nProvider>
    <AuthProvider>
      <BrowserRouter>
        <a href="#main-content" className="skip-to-content">Skip to content</a>
        <TrackingBridge />
        <BackendBanner />
        <NavBar />
        <CommandPalette />
        <OnboardingOverlay />
        <main id="main-content" className="main-content">
          <ErrorBoundary>
            <Suspense fallback={<div className="fits-loading">Loading...</div>}>
              <Routes>
                <Route path="/" element={<LandingPage />} />
                <Route path="/search" element={<DataBrowser />} />
                <Route path="/pipeline" element={<PipelineCanvas />} />
                <Route path="/workspace" element={<WorkspacePage />} />
                <Route path="/adql" element={<ADQLPage />} />
                <Route path="/team" element={<TeamPage />} />
                <Route path="/help" element={<HelpPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/account" element={<AccountPage />} />
                <Route path="/papers" element={<PapersPage />} />
                <Route path="/research" element={<Navigate to="/account" replace />} />
                <Route path="/settings" element={<Navigate to="/account" replace />} />
                <Route path="/shared/:token" element={<SharedSessionPage />} />
                <Route path="/observations" element={<ObservationsPage />} />
                <Route path="/alerts" element={<Navigate to="/observations" replace />} />
                <Route path="/anomalies" element={<Navigate to="/observations" replace />} />
                <Route path="/auth" element={<AuthPage />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
        <JournalFooter />
      </BrowserRouter>
    </AuthProvider>
    </I18nProvider>
  );
}

export default App;
