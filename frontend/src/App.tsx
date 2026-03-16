import { Component, lazy, Suspense, type ErrorInfo, type ReactNode } from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import { AuthProvider, useAuth } from "./context/AuthContext";
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
        <div style={{ padding: "2rem", color: "#f5f5f7" }}>
          <h2>Something went wrong</h2>
          <pre style={{ color: "#FF453A", whiteSpace: "pre-wrap", marginTop: "1rem" }}>
            {this.state.error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              marginTop: "1rem",
              padding: "0.5rem 1rem",
              background: "#0A84FF",
              color: "#fff",
              border: "none",
              borderRadius: "8px",
              cursor: "pointer",
            }}
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
const BillingPage = lazy(() => import("./pages/Billing/BillingPage"));
const ChatPage = lazy(() => import("./pages/Chat/ChatPage"));
const SettingsPage = lazy(() => import("./pages/Settings/SettingsPage"));

function NavBar() {
  const { user, logout } = useAuth();

  return (
    <nav className="top-nav">
      <span className="logo">Astro Platform</span>
      <NavLink to="/">Data Browser</NavLink>
      <NavLink to="/pipeline">Pipeline</NavLink>
      <NavLink to="/workspace">Workspace</NavLink>
      <NavLink to="/adql">ADQL</NavLink>
      <NavLink to="/team">Team</NavLink>
      <NavLink to="/chat">AI Assistant</NavLink>
      {user && <NavLink to="/settings">Settings</NavLink>}
      <div className="nav-spacer" />
      {user ? (
        <div className="nav-user">
          <span className="nav-email">{user.email}</span>
          <span className="tier-badge tier-beta">beta</span>
          <button className="btn-nav-logout" onClick={logout}>
            Sign Out
          </button>
        </div>
      ) : (
        <NavLink to="/auth" className="btn-nav-auth">
          Sign In
        </NavLink>
      )}
    </nav>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
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
                <Route path="/billing" element={<BillingPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/auth" element={<AuthPage />} />
              </Routes>
            </Suspense>
          </ErrorBoundary>
        </main>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
