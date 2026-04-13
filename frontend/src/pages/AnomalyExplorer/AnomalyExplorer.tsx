import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  getAnomalyFeed,
  getAnomalyStats,
  dismissAnomaly,
} from "../../api/client";
import type {
  AnomalyItem,
  AnomalyFeedResponse,
  AnomalyStats,
} from "../../api/client";
import api from "../../api/client";

/* ── Score color helpers ── */

function scoreColor(score: number): string {
  if (score > 0.8) return "#e74c3c";
  if (score >= 0.5) return "#f1c40f";
  return "#2ecc71";
}

function scoreLabel(score: number): string {
  if (score > 0.8) return "High";
  if (score >= 0.5) return "Medium";
  return "Low";
}

/* ── Source badge colors ── */

const SOURCE_COLORS: Record<string, string> = {
  Query: "#3498db",
  Alert: "#e67e22",
  "Cross-wavelength": "#9b59b6",
};

/* ── Detection method badge colors ── */

const METHOD_COLORS: Record<string, string> = {
  IF: "#16a085",
  AE: "#2980b9",
  DBSCAN: "#8e44ad",
};

/* ── Styles (inline, matching AlertDashboard patterns) ── */

const styles = {
  page: {
    display: "flex",
    gap: "1.5rem",
    animation: "fadeIn 0.3s ease-out",
  } as const,
  mainCol: {
    flex: 1,
    minWidth: 0,
  } as const,
  sidebar: {
    width: "260px",
    flexShrink: 0,
  } as const,
  title: {
    fontSize: "1.5rem",
    fontWeight: 700,
    color: "var(--color-text)",
    margin: "0 0 1.25rem",
  } as const,
  statsBar: {
    display: "flex",
    alignItems: "center",
    gap: "1rem",
    padding: "0.85rem 1.25rem",
    background: "var(--color-muted)",
    borderRadius: "var(--radius-lg)",
    border: "1px solid var(--color-separator)",
    marginBottom: "1rem",
    flexWrap: "wrap" as const,
  },
  statLabel: {
    fontSize: "0.78rem",
    color: "var(--color-text-secondary)",
    fontWeight: 500,
  } as const,
  statValue: {
    fontSize: "0.85rem",
    fontWeight: 700,
    color: "var(--color-text)",
    fontVariantNumeric: "tabular-nums",
  } as const,
  card: {
    background: "var(--color-muted)",
    border: "1px solid var(--color-separator)",
    borderRadius: "var(--radius-lg)",
    padding: "1rem 1.25rem",
    marginBottom: "0.75rem",
  } as const,
  cardHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: "0.5rem",
  } as const,
  objectName: {
    fontSize: "1rem",
    fontWeight: 700,
    color: "var(--color-text)",
    margin: 0,
  } as const,
  coords: {
    fontSize: "0.75rem",
    color: "var(--color-text-tertiary)",
    fontVariantNumeric: "tabular-nums",
  } as const,
  scoreBarOuter: {
    height: "8px",
    background: "var(--color-subtle)",
    borderRadius: "4px",
    overflow: "hidden",
    width: "120px",
  } as const,
  badge: {
    display: "inline-block",
    padding: "0.15rem 0.5rem",
    borderRadius: "4px",
    fontSize: "0.7rem",
    fontWeight: 600,
    color: "#fff",
    letterSpacing: "0.02em",
    whiteSpace: "nowrap" as const,
  },
  badgeRow: {
    display: "flex",
    alignItems: "center",
    gap: "0.4rem",
    flexWrap: "wrap" as const,
    marginTop: "0.4rem",
  },
  features: {
    fontSize: "0.82rem",
    color: "var(--color-text-secondary)",
    margin: "0.5rem 0",
    lineHeight: 1.5,
  } as const,
  timestamp: {
    fontSize: "0.72rem",
    color: "var(--color-text-tertiary)",
  } as const,
  actions: {
    display: "flex",
    gap: "0.5rem",
    marginTop: "0.75rem",
    flexWrap: "wrap" as const,
  },
  filterSection: {
    background: "var(--color-muted)",
    border: "1px solid var(--color-separator)",
    borderRadius: "var(--radius-lg)",
    padding: "1rem 1.25rem",
    marginBottom: "1rem",
  } as const,
  filterTitle: {
    fontSize: "0.82rem",
    fontWeight: 600,
    color: "var(--color-text-secondary)",
    margin: "0 0 0.6rem",
  } as const,
  filterLabel: {
    display: "flex",
    alignItems: "center",
    gap: "0.3rem",
    cursor: "pointer",
    fontSize: "0.82rem",
    color: "var(--color-text)",
    marginBottom: "0.3rem",
  } as const,
  slider: {
    width: "100%",
    accentColor: "var(--color-accent)",
    cursor: "pointer",
  } as const,
  select: {
    width: "100%",
    padding: "0.35rem 0.7rem",
    borderRadius: "var(--radius-sm)",
    border: "1px solid var(--color-separator)",
    background: "var(--color-muted)",
    color: "var(--color-text)",
    fontSize: "0.82rem",
    fontFamily: "inherit",
    cursor: "pointer",
  } as const,
  pagination: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "0.75rem",
    marginTop: "1rem",
  } as const,
  modalBackdrop: {
    position: "fixed" as const,
    inset: 0,
    background: "rgba(0,0,0,0.5)",
    zIndex: 1000,
  },
  modalPanel: {
    position: "fixed" as const,
    top: "50%",
    left: "50%",
    transform: "translate(-50%, -50%)",
    width: "min(90vw, 700px)",
    maxHeight: "80vh",
    overflowY: "auto" as const,
    background: "var(--color-bg)",
    border: "1px solid var(--color-separator)",
    borderRadius: "var(--radius-lg)",
    padding: "1.5rem",
    zIndex: 1001,
  },
  modalTitle: {
    fontSize: "1.1rem",
    fontWeight: 700,
    color: "var(--color-text)",
    margin: "0 0 1rem",
  } as const,
  dossierSection: {
    marginBottom: "1rem",
  } as const,
  dossierLabel: {
    fontSize: "0.78rem",
    fontWeight: 600,
    color: "var(--color-text-secondary)",
    marginBottom: "0.3rem",
  } as const,
  dossierValue: {
    fontSize: "0.85rem",
    color: "var(--color-text)",
    lineHeight: 1.5,
  } as const,
} as const;

/* ── Dossier Modal ── */

function DossierModal({
  dossier,
  loading,
  error,
  onClose,
}: {
  dossier: Record<string, unknown> | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}) {
  if (!loading && !dossier && !error) return null;

  return (
    <>
      <div style={styles.modalBackdrop} onClick={onClose} />
      <div style={styles.modalPanel}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <h2 style={styles.modalTitle}>Object Dossier</h2>
          <button className="odp-close" onClick={onClose} title="Close">&times;</button>
        </div>
        {loading && <div className="fits-loading">Generating dossier...</div>}
        {error && <div className="error-banner">{error}</div>}
        {dossier && (
          <div>
            {Object.entries(dossier).map(([key, value]) => (
              <div key={key} style={styles.dossierSection}>
                <div style={styles.dossierLabel}>{key.replace(/_/g, " ").toUpperCase()}</div>
                <div style={styles.dossierValue}>
                  {typeof value === "object" && value !== null
                    ? <pre style={{ margin: 0, fontSize: "0.78rem", whiteSpace: "pre-wrap", color: "var(--color-text)" }}>{JSON.stringify(value, null, 2)}</pre>
                    : String(value ?? "N/A")
                  }
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/* ── Anomaly Card ── */

function AnomalyCard({
  item,
  onAnalyze,
  onDossier,
  onDismiss,
}: {
  item: AnomalyItem;
  onAnalyze: () => void;
  onDossier: () => void;
  onDismiss: () => void;
}) {
  return (
    <div style={styles.card}>
      {/* Header: name + score */}
      <div style={styles.cardHeader}>
        <div>
          <h3 style={styles.objectName}>{item.object_name}</h3>
          <div style={styles.coords}>
            RA {item.ra.toFixed(6)}, Dec {item.dec.toFixed(6)}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <span style={{ ...styles.statValue, color: scoreColor(item.anomaly_score) }}>
            {item.anomaly_score.toFixed(2)}
          </span>
          <span style={{ fontSize: "0.7rem", color: scoreColor(item.anomaly_score), fontWeight: 600 }}>
            {scoreLabel(item.anomaly_score)}
          </span>
        </div>
      </div>

      {/* Score bar */}
      <div style={styles.scoreBarOuter}>
        <div
          style={{
            height: "100%",
            width: `${Math.round(item.anomaly_score * 100)}%`,
            background: scoreColor(item.anomaly_score),
            borderRadius: "4px",
            transition: "width 0.3s ease",
          }}
        />
      </div>

      {/* Badges row: source + detection methods */}
      <div style={styles.badgeRow}>
        <span
          style={{
            ...styles.badge,
            background: SOURCE_COLORS[item.source] ?? "#95a5a6",
          }}
        >
          {item.source}
        </span>
        {item.detection_methods.map((method) => (
          <span
            key={method}
            style={{
              ...styles.badge,
              background: METHOD_COLORS[method] ?? "#7f8c8d",
            }}
          >
            {method}
          </span>
        ))}
      </div>

      {/* Unusual features */}
      <p style={styles.features}>
        <strong>Unusual features:</strong> {item.unusual_features || "None identified"}
      </p>

      {/* Timestamp */}
      <div style={styles.timestamp}>
        {new Date(item.timestamp).toLocaleString()}
      </div>

      {/* Action buttons */}
      <div style={styles.actions}>
        <button className="btn-primary" onClick={onAnalyze} style={{ fontSize: "0.78rem", padding: "0.35rem 0.75rem" }}>
          Analyze with AI
        </button>
        <button className="btn-secondary" onClick={onDossier} style={{ fontSize: "0.78rem", padding: "0.35rem 0.75rem" }}>
          Generate Dossier
        </button>
        <button
          className="btn-secondary"
          onClick={onDismiss}
          style={{ fontSize: "0.78rem", padding: "0.35rem 0.75rem", color: "var(--color-text-tertiary)" }}
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

/* ── Sidebar Filters ── */

const SOURCE_OPTIONS = ["Query", "Alert", "Cross-wavelength"];

function Sidebar({
  minScore,
  onMinScoreChange,
  selectedSources,
  onToggleSource,
  sort,
  onSortChange,
}: {
  minScore: number;
  onMinScoreChange: (v: number) => void;
  selectedSources: Set<string>;
  onToggleSource: (s: string) => void;
  sort: string;
  onSortChange: (v: string) => void;
}) {
  return (
    <aside style={styles.sidebar}>
      <h2 style={styles.title}>Filters</h2>

      {/* Score range */}
      <div style={styles.filterSection}>
        <div style={styles.filterTitle}>Min Anomaly Score: {minScore.toFixed(2)}</div>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={minScore}
          onChange={(e) => onMinScoreChange(Number(e.target.value))}
          style={styles.slider}
        />
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>
          <span>0.0</span>
          <span>1.0</span>
        </div>
      </div>

      {/* Source filter */}
      <div style={styles.filterSection}>
        <div style={styles.filterTitle}>Source</div>
        {SOURCE_OPTIONS.map((src) => (
          <label key={src} style={styles.filterLabel}>
            <input
              type="checkbox"
              checked={selectedSources.has(src)}
              onChange={() => onToggleSource(src)}
              style={{ accentColor: "var(--color-accent)", cursor: "pointer" }}
            />
            <span
              style={{
                ...styles.badge,
                background: SOURCE_COLORS[src] ?? "#95a5a6",
              }}
            >
              {src}
            </span>
          </label>
        ))}
      </div>

      {/* Sort */}
      <div style={styles.filterSection}>
        <div style={styles.filterTitle}>Sort by</div>
        <select
          value={sort}
          onChange={(e) => onSortChange(e.target.value)}
          style={styles.select}
        >
          <option value="score">Score (highest first)</option>
          <option value="date">Date (newest first)</option>
        </select>
      </div>
    </aside>
  );
}

/* ── Main Component ── */

export default function AnomalyExplorer() {
  const navigate = useNavigate();

  // Data state
  const [feed, setFeed] = useState<AnomalyFeedResponse>({ items: [], page: 1, per_page: 20, total: 0 });
  const [stats, setStats] = useState<AnomalyStats>({ total: 0, high_confidence: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filter state
  const [minScore, setMinScore] = useState(0);
  const [selectedSources, setSelectedSources] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState("score");
  const [page, setPage] = useState(1);

  // Detection threshold settings
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [ifContamination, setIfContamination] = useState(0.05);
  const [aeThreshold, setAeThreshold] = useState(95);
  const [dbscanMinSamples, setDbscanMinSamples] = useState(5);
  const [votingThreshold, setVotingThreshold] = useState(2);

  // Dismissed IDs (local state)
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set());

  // Dossier modal state
  const [dossierData, setDossierData] = useState<Record<string, unknown> | null>(null);
  const [dossierLoading, setDossierLoading] = useState(false);
  const [dossierError, setDossierError] = useState<string | null>(null);
  const [dossierOpen, setDossierOpen] = useState(false);

  // Build source filter param
  const sourceParam = useMemo(() => {
    if (selectedSources.size === 0) return undefined;
    return Array.from(selectedSources).join(",");
  }, [selectedSources]);

  // Fetch feed
  const fetchFeed = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getAnomalyFeed(page, {
        min_score: minScore > 0 ? minScore : undefined,
        source: sourceParam,
        sort,
        if_contamination: ifContamination,
        ae_threshold_percentile: aeThreshold,
        dbscan_min_samples: dbscanMinSamples,
        voting_threshold: votingThreshold,
      });
      setFeed(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to fetch anomaly feed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [page, minScore, sourceParam, sort, ifContamination, aeThreshold, dbscanMinSamples, votingThreshold]);

  // Fetch stats
  const fetchStats = useCallback(async () => {
    try {
      const data = await getAnomalyStats();
      setStats(data);
    } catch {
      // Stats are non-critical
    }
  }, []);

  useEffect(() => { fetchFeed(); }, [fetchFeed]);
  useEffect(() => { fetchStats(); }, [fetchStats]);

  // Toggle source filter
  const toggleSource = (src: string) => {
    setSelectedSources((prev) => {
      const next = new Set(prev);
      if (next.has(src)) {
        next.delete(src);
      } else {
        next.add(src);
      }
      return next;
    });
    setPage(1);
  };

  // Handle score slider change
  const handleMinScoreChange = (v: number) => {
    setMinScore(v);
    setPage(1);
  };

  // Handle sort change
  const handleSortChange = (v: string) => {
    setSort(v);
    setPage(1);
  };

  // Analyze with AI
  const handleAnalyze = (item: AnomalyItem) => {
    const prefill =
      `Analyze this anomalous astronomical object:\n\n` +
      `Object: ${item.object_name}\n` +
      `RA: ${item.ra.toFixed(6)}, Dec: ${item.dec.toFixed(6)}\n` +
      `Anomaly score: ${item.anomaly_score.toFixed(3)}\n` +
      `Source: ${item.source}\n` +
      `Detection methods: ${item.detection_methods.join(", ")}\n` +
      `Unusual features: ${item.unusual_features}\n\n` +
      `What could explain this anomaly? Provide possible astrophysical interpretations and suggest follow-up observations.`;

    localStorage.setItem("astro_chat_draft", prefill);
    localStorage.setItem("astro_chat_new_session", "1");
    navigate("/chat");
  };

  // Generate dossier
  const handleDossier = async (item: AnomalyItem) => {
    setDossierOpen(true);
    setDossierLoading(true);
    setDossierError(null);
    setDossierData(null);
    try {
      const { data } = await api.get("/api/dossier", {
        params: { ra: item.ra, dec: item.dec, name: item.object_name },
      });
      setDossierData(data as Record<string, unknown>);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to generate dossier";
      setDossierError(msg);
    } finally {
      setDossierLoading(false);
    }
  };

  // Dismiss
  const handleDismiss = async (item: AnomalyItem) => {
    setDismissedIds((prev) => new Set(prev).add(item.id));
    try {
      await dismissAnomaly(item.id);
    } catch {
      // Dismiss is best-effort; local state already updated
    }
  };

  // Filter out dismissed items
  const visibleItems = useMemo(
    () => feed.items.filter((item) => !dismissedIds.has(item.id)),
    [feed.items, dismissedIds],
  );

  const totalPages = Math.max(1, Math.ceil(feed.total / feed.per_page));

  return (
    <div style={{ animation: "fadeIn 0.3s ease-out" }}>
      <h1 style={styles.title}>Anomaly Explorer</h1>

      {/* Summary stats bar */}
      <div style={styles.statsBar}>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span style={styles.statLabel}>Anomalies found</span>
          <span style={styles.statValue}>{stats.total.toLocaleString()}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <span style={styles.statLabel}>High-confidence (score &gt; 0.8)</span>
          <span style={{ ...styles.statValue, color: "#e74c3c" }}>
            {stats.high_confidence.toLocaleString()}
          </span>
        </div>
      </div>

      {/* Detection settings toggle + collapsible panel */}
      <div style={{ marginBottom: "1rem" }}>
        <button
          className="btn-secondary"
          onClick={() => setSettingsOpen((v) => !v)}
          style={{ fontSize: "0.82rem", padding: "0.4rem 0.85rem" }}
        >
          {settingsOpen ? "Hide Settings" : "Settings"}
        </button>
        {settingsOpen && (
          <div style={{ ...styles.filterSection, marginTop: "0.75rem" }}>
            <div style={styles.filterTitle}>Detection Thresholds</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem 1.25rem" }}>
              {/* IF Contamination */}
              <label style={{ display: "block" }}>
                <span style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", fontWeight: 500 }}>
                  IF Contamination
                </span>
                <input
                  type="number"
                  min={0.01}
                  max={0.5}
                  step={0.01}
                  value={ifContamination}
                  onChange={(e) => { setIfContamination(Number(e.target.value)); setPage(1); }}
                  style={{ ...styles.select, display: "block", marginTop: "0.25rem" }}
                />
              </label>
              {/* AE Threshold */}
              <label style={{ display: "block" }}>
                <span style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", fontWeight: 500 }}>
                  AE Threshold Percentile
                </span>
                <input
                  type="number"
                  min={80}
                  max={99}
                  step={1}
                  value={aeThreshold}
                  onChange={(e) => { setAeThreshold(Number(e.target.value)); setPage(1); }}
                  style={{ ...styles.select, display: "block", marginTop: "0.25rem" }}
                />
              </label>
              {/* DBSCAN Min Samples */}
              <label style={{ display: "block" }}>
                <span style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", fontWeight: 500 }}>
                  DBSCAN Min Samples
                </span>
                <input
                  type="number"
                  min={2}
                  max={20}
                  step={1}
                  value={dbscanMinSamples}
                  onChange={(e) => { setDbscanMinSamples(Number(e.target.value)); setPage(1); }}
                  style={{ ...styles.select, display: "block", marginTop: "0.25rem" }}
                />
              </label>
              {/* Voting Threshold */}
              <label style={{ display: "block" }}>
                <span style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", fontWeight: 500 }}>
                  Voting Threshold
                </span>
                <select
                  value={votingThreshold}
                  onChange={(e) => { setVotingThreshold(Number(e.target.value)); setPage(1); }}
                  style={{ ...styles.select, display: "block", marginTop: "0.25rem" }}
                >
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                  <option value={3}>3</option>
                </select>
              </label>
            </div>
          </div>
        )}
      </div>

      {/* Error banner */}
      {error && (
        <div className="error-banner">
          <span>{error}</span>
          <button className="btn-secondary" onClick={fetchFeed} style={{ marginLeft: "1rem" }}>Retry</button>
        </div>
      )}

      {/* Main layout: feed + sidebar */}
      <div style={styles.page}>
        {/* Feed column */}
        <div style={styles.mainCol}>
          {loading ? (
            <div className="fits-loading">Loading anomalies...</div>
          ) : visibleItems.length === 0 ? (
            <div className="empty-state">
              <p>No anomalies found matching your filters.</p>
            </div>
          ) : (
            <>
              {visibleItems.map((item) => (
                <AnomalyCard
                  key={item.id}
                  item={item}
                  onAnalyze={() => handleAnalyze(item)}
                  onDossier={() => handleDossier(item)}
                  onDismiss={() => handleDismiss(item)}
                />
              ))}

              {/* Pagination */}
              {totalPages > 1 && (
                <div style={styles.pagination}>
                  <button
                    className="btn-secondary"
                    disabled={page <= 1}
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    style={{ fontSize: "0.82rem" }}
                  >
                    Previous
                  </button>
                  <span style={{ fontSize: "0.82rem", color: "var(--color-text-secondary)" }}>
                    Page {page} of {totalPages}
                  </span>
                  <button
                    className="btn-secondary"
                    disabled={page >= totalPages}
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    style={{ fontSize: "0.82rem" }}
                  >
                    Next
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        {/* Sidebar filters */}
        <Sidebar
          minScore={minScore}
          onMinScoreChange={handleMinScoreChange}
          selectedSources={selectedSources}
          onToggleSource={toggleSource}
          sort={sort}
          onSortChange={handleSortChange}
        />
      </div>

      {/* Dossier modal */}
      {dossierOpen && (
        <DossierModal
          dossier={dossierData}
          loading={dossierLoading}
          error={dossierError}
          onClose={() => {
            setDossierOpen(false);
            setDossierData(null);
            setDossierError(null);
          }}
        />
      )}
    </div>
  );
}
