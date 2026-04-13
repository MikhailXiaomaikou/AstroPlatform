import { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { getAlerts, getAlertStats } from "../../api/client";
import type { TransientAlert, AlertStats } from "../../api/client";
import AladinViewer from "../../components/viz/AladinViewer";
import { useTracking } from "../../hooks/useTracking";

/* ── Type color mapping ── */

const TYPE_COLORS: Record<string, string> = {
  "SN Ia": "#e74c3c",
  "SN II": "#e67e22",
  CV: "#3498db",
  AGN: "#9b59b6",
  TDE: "#e91e63",
};

function typeColor(classification: string | null): string {
  if (!classification) return "#95a5a6";
  return TYPE_COLORS[classification] ?? "#95a5a6";
}

/* ── Sort helpers ── */

type SortKey = "source_id" | "classification" | "ra" | "dec" | "magnitude" | "discovery_date";
type SortDir = "asc" | "desc";

function compareAlerts(a: TransientAlert, b: TransientAlert, key: SortKey, dir: SortDir): number {
  let av: string | number | null;
  let bv: string | number | null;

  switch (key) {
    case "source_id":
      av = a.source_id;
      bv = b.source_id;
      break;
    case "classification":
      av = a.classification;
      bv = b.classification;
      break;
    case "ra":
      av = a.ra;
      bv = b.ra;
      break;
    case "dec":
      av = a.dec;
      bv = b.dec;
      break;
    case "magnitude":
      av = a.magnitude;
      bv = b.magnitude;
      break;
    case "discovery_date":
      av = a.discovery_date;
      bv = b.discovery_date;
      break;
  }

  // Nulls go last
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;

  let cmp: number;
  if (typeof av === "number" && typeof bv === "number") {
    cmp = av - bv;
  } else {
    cmp = String(av).localeCompare(String(bv));
  }
  return dir === "asc" ? cmp : -cmp;
}

/* ── Time window options ── */

const TIME_WINDOWS = [
  { label: "24h", days: 1 },
  { label: "3d", days: 3 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
];

const FILTER_TYPES = ["SN Ia", "SN II", "CV", "AGN", "TDE"];

/* ── Stats Bar ── */

function StatsBar({ stats, loading }: { stats: AlertStats | null; loading: boolean }) {
  const { t } = useI18n();

  if (loading) {
    return <div className="alert-stats-bar"><span className="alert-stats-loading">Loading stats...</span></div>;
  }
  if (!stats) {
    return <div className="alert-stats-bar"><span className="alert-stats-loading">{t("alerts.no_alerts")}</span></div>;
  }

  return (
    <div className="alert-stats-bar">
      <div className="alert-stat-item">
        <span className="alert-stat-label">{t("alerts.stats")}</span>
        <span className="alert-stat-value">{stats.total.toLocaleString()}</span>
      </div>
      {FILTER_TYPES.map((type) => (
        <div key={type} className="alert-stat-item">
          <span className="alert-type-badge" style={{ background: typeColor(type) }}>{type}</span>
          <span className="alert-stat-value">{stats.by_classification[type] ?? 0}</span>
        </div>
      ))}
      {stats.latest_ingestion && (
        <div className="alert-stat-item alert-stat-latest">
          <span className="alert-stat-label">Latest</span>
          <span className="alert-stat-value">{new Date(stats.latest_ingestion).toLocaleDateString()}</span>
        </div>
      )}
    </div>
  );
}

/* ── Detail Panel ── */

function DetailPanel({ alert, onClose }: { alert: TransientAlert; onClose: () => void }) {
  const navigate = useNavigate();

  const handleSendToAI = () => {
    const context = `Transient alert: ${alert.source_id} (${alert.source})\n` +
      `Classification: ${alert.classification ?? "Unknown"}\n` +
      `RA: ${alert.ra.toFixed(6)}, Dec: ${alert.dec.toFixed(6)}\n` +
      `Magnitude: ${alert.magnitude ?? "N/A"} (${alert.mag_band ?? ""})\n` +
      `Discovery: ${alert.discovery_date ?? "Unknown"}\n` +
      `Redshift: ${alert.redshift ?? "N/A"}\n` +
      `Host Galaxy: ${alert.host_galaxy ?? "N/A"}`;

    navigate("/chat", { state: { prefill: `Analyze this transient alert:\n\n${context}` } });
  };

  return (
    <>
      <div className="odp-backdrop" onClick={onClose} />
      <div className="odp-panel alert-detail-panel">
        <div className="odp-header">
          <button className="odp-close" onClick={onClose} title="Close">&times;</button>
          <h2 className="odp-title">{alert.source_id}</h2>
          <div className="odp-subtitle">
            <span className="alert-type-badge" style={{ background: typeColor(alert.classification) }}>
              {alert.classification ?? "Unclassified"}
            </span>
            <span style={{ marginLeft: 8 }}>{alert.source}</span>
          </div>
        </div>
        <div className="odp-body">
          <table className="odp-info-table">
            <tbody>
              <tr><td className="odp-label">RA</td><td>{alert.ra.toFixed(6)}</td></tr>
              <tr><td className="odp-label">Dec</td><td>{alert.dec.toFixed(6)}</td></tr>
              <tr><td className="odp-label">Magnitude</td><td>{alert.magnitude != null ? `${alert.magnitude.toFixed(2)} (${alert.mag_band ?? "?"})` : "N/A"}</td></tr>
              <tr><td className="odp-label">Discovery</td><td>{alert.discovery_date ?? "Unknown"}</td></tr>
              <tr><td className="odp-label">Confidence</td><td>{alert.classification_confidence != null ? `${(alert.classification_confidence * 100).toFixed(1)}%` : "N/A"}</td></tr>
              <tr><td className="odp-label">Redshift</td><td>{alert.redshift != null ? alert.redshift.toFixed(4) : "N/A"}</td></tr>
              <tr><td className="odp-label">Host Galaxy</td><td>{alert.host_galaxy ?? "N/A"}</td></tr>
              <tr><td className="odp-label">Source</td><td>{alert.source}</td></tr>
              <tr><td className="odp-label">Source ID</td><td>{alert.source_id}</td></tr>
            </tbody>
          </table>

          {/* Sky viewer centered on alert coordinates */}
          <div style={{ marginTop: "1.5rem" }}>
            <h3 style={{ fontSize: "0.85rem", marginBottom: 6, color: "var(--color-text-secondary)" }}>
              Sky View
            </h3>
            <AladinViewer
              ra={alert.ra}
              dec={alert.dec}
              fov={2 / 60}  /* 2 arcmin */
              height="280px"
              markers={[
                {
                  ra: alert.ra,
                  dec: alert.dec,
                  name: alert.source_id,
                  color: "#ef4444",
                  popup: `${alert.classification ?? "Unclassified"} | ${alert.source}`,
                },
              ]}
            />
          </div>

          <div style={{ marginTop: "1.5rem" }}>
            <button className="btn-primary" onClick={handleSendToAI}>
              Send to AI Assistant
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/* ── Main Component ── */

export default function AlertDashboard() {
  const { t } = useI18n();
  const { track } = useTracking();

  // Data state
  const [alerts, setAlerts] = useState<TransientAlert[]>([]);
  const [stats, setStats] = useState<AlertStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statsLoading, setStatsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [timeWindow, setTimeWindow] = useState(7);
  const [selectedTypes, setSelectedTypes] = useState<Set<string>>(new Set());

  // Sort state
  const [sortKey, setSortKey] = useState<SortKey>("discovery_date");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  // Detail panel
  const [selectedAlert, setSelectedAlert] = useState<TransientAlert | null>(null);

  // Fetch alerts
  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAlerts({ days: timeWindow, limit: 500 });
      setAlerts(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to fetch alerts";
      setError(msg);
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  }, [timeWindow]);

  // Fetch stats
  const fetchStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const data = await getAlertStats();
      setStats(data);
    } catch {
      // Stats are non-critical; silently degrade
      setStats(null);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  useEffect(() => { fetchAlerts(); }, [fetchAlerts]);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  // Toggle type filter
  const toggleType = (type: string) => {
    setSelectedTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  };

  // Toggle sort
  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const sortIndicator = (key: SortKey) => {
    if (sortKey !== key) return null;
    return <span className="sort-indicator">{sortDir === "asc" ? "\u25B2" : "\u25BC"}</span>;
  };

  // Filter + sort
  const filteredAlerts = useMemo(() => {
    let result = alerts;
    if (selectedTypes.size > 0) {
      result = result.filter((a) => a.classification != null && selectedTypes.has(a.classification));
    }
    result = [...result].sort((a, b) => compareAlerts(a, b, sortKey, sortDir));
    return result;
  }, [alerts, selectedTypes, sortKey, sortDir]);

  return (
    <div className="alert-dashboard">
      <h1 className="alert-dashboard-title">{t("alerts.title")}</h1>

      {/* Stats bar */}
      <StatsBar stats={stats} loading={statsLoading} />

      {/* Filter bar */}
      <div className="alert-filter-bar">
        <div className="alert-filter-types">
          {FILTER_TYPES.map((type) => (
            <label key={type} className="alert-filter-check">
              <input
                type="checkbox"
                checked={selectedTypes.has(type)}
                onChange={() => toggleType(type)}
              />
              <span className="alert-type-badge" style={{ background: typeColor(type) }}>{type}</span>
            </label>
          ))}
        </div>
        <select
          className="alert-time-select"
          value={timeWindow}
          onChange={(e) => setTimeWindow(Number(e.target.value))}
        >
          {TIME_WINDOWS.map((tw) => (
            <option key={tw.days} value={tw.days}>{tw.label}</option>
          ))}
        </select>
      </div>

      {/* Error banner */}
      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button className="btn-secondary" onClick={fetchAlerts} style={{ marginLeft: "1rem" }}>Retry</button>
        </div>
      )}

      {/* Alert table */}
      {loading ? (
        <div className="fits-loading">Loading alerts...</div>
      ) : filteredAlerts.length === 0 ? (
        <div className="empty-state">
          <p>{t("alerts.no_alerts")}</p>
        </div>
      ) : (
        <div className="results-table-wrap">
          <table className="results-table alert-table">
            <thead>
              <tr>
                <th className="th-sortable" onClick={() => handleSort("source_id")}>
                  Name {sortIndicator("source_id")}
                </th>
                <th className="th-sortable" onClick={() => handleSort("classification")}>
                  Type {sortIndicator("classification")}
                </th>
                <th className="th-sortable th-num" onClick={() => handleSort("ra")}>
                  RA {sortIndicator("ra")}
                </th>
                <th className="th-sortable th-num" onClick={() => handleSort("dec")}>
                  Dec {sortIndicator("dec")}
                </th>
                <th className="th-sortable th-num" onClick={() => handleSort("magnitude")}>
                  Mag {sortIndicator("magnitude")}
                </th>
                <th className="th-sortable" onClick={() => handleSort("discovery_date")}>
                  Discovery {sortIndicator("discovery_date")}
                </th>
              </tr>
            </thead>
            <tbody>
              {filteredAlerts.map((alert) => (
                <tr
                  key={alert.id}
                  className="alert-row"
                  onClick={() => {
                    setSelectedAlert(alert);
                    track("alert.viewed", {
                      alert_id: alert.source_id,
                      classification: alert.classification,
                      magnitude: alert.magnitude,
                    });
                  }}
                >
                  <td>
                    <button className="name-link">{alert.source_id}</button>
                  </td>
                  <td>
                    <span className="alert-type-badge" style={{ background: typeColor(alert.classification) }}>
                      {alert.classification ?? "?"}
                    </span>
                  </td>
                  <td className="td-num">{alert.ra.toFixed(4)}</td>
                  <td className="td-num">{alert.dec.toFixed(4)}</td>
                  <td className="td-num">{alert.magnitude != null ? alert.magnitude.toFixed(2) : "--"}</td>
                  <td>{alert.discovery_date ?? "--"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Watchlist placeholder */}
      <div className="alert-watchlist-placeholder">
        <h3>Watchlist</h3>
        <p>Coming Soon &mdash; save alerts and get notified on updates.</p>
      </div>

      {/* Detail panel */}
      {selectedAlert && (
        <DetailPanel alert={selectedAlert} onClose={() => setSelectedAlert(null)} />
      )}
    </div>
  );
}
