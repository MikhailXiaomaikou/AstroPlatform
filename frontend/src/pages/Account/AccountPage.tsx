import { useState } from "react";
import SettingsPage from "../Settings/SettingsPage";
import ResearchHistoryPage from "../ResearchHistory/ResearchHistoryPage";

export default function AccountPage() {
  const [tab, setTab] = useState<"settings" | "research">("settings");

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
      </div>
      {tab === "settings" ? <SettingsPage /> : <ResearchHistoryPage />}
    </div>
  );
}
