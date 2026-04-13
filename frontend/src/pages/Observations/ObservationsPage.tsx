import { useState } from "react";
import AlertDashboard from "../AlertDashboard/AlertDashboard";
import AnomalyExplorer from "../AnomalyExplorer/AnomalyExplorer";

export default function ObservationsPage() {
  const [tab, setTab] = useState<"alerts" | "anomalies">("alerts");

  return (
    <div className="observations-page">
      <div className="observations-tabs">
        <button
          className={`observations-tab${tab === "alerts" ? " active" : ""}`}
          onClick={() => setTab("alerts")}
        >
          Transient Alerts
        </button>
        <button
          className={`observations-tab${tab === "anomalies" ? " active" : ""}`}
          onClick={() => setTab("anomalies")}
        >
          Anomaly Explorer
        </button>
      </div>
      {tab === "alerts" ? <AlertDashboard /> : <AnomalyExplorer />}
    </div>
  );
}
