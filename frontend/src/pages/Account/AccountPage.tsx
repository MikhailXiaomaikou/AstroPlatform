import { useState } from "react";
import { Navigate } from "react-router-dom";
import SettingsPage from "../Settings/SettingsPage";
import ResearchHistoryPage from "../ResearchHistory/ResearchHistoryPage";
import UserToolsPage from "../UserTools/UserToolsPage";
import { useAuth } from "../../context/AuthContext";

export default function AccountPage() {
  const { user, loading } = useAuth();
  const [tab, setTab] = useState<"settings" | "research" | "tools">(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    return requested === "tools" || requested === "research" ? requested : "settings";
  });

  if (loading) return <div className="fits-loading">Loading account…</div>;
  if (!user) return <Navigate to="/auth" replace />;

  return (
    <div className="account-page">
      <h1>Account</h1>
      <div className="page-tabs">
        <button
          className={`page-tab${tab === "settings" ? " active" : ""}`}
          onClick={() => setTab("settings")}
        >
          Settings &amp; API Keys
        </button>
        <button
          className={`page-tab${tab === "research" ? " active" : ""}`}
          onClick={() => setTab("research")}
        >
          Research Profile
        </button>
        <button
          className={`page-tab${tab === "tools" ? " active" : ""}`}
          onClick={() => setTab("tools")}
        >
          User Tools
        </button>
      </div>
      {tab === "settings"
        ? <SettingsPage />
        : tab === "tools"
          ? <UserToolsPage />
          : <ResearchHistoryPage />}
    </div>
  );
}
