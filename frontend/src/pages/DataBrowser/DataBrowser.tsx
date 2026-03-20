import { useState, useCallback, useRef, useEffect, lazy, Suspense } from "react";
import {
  searchData,
  advancedSearch,
  fetchObject,
  shareDataset,
  getFriends,
  getSearchHistory,
  type SearchResult,
  type FetchResult,
  type AdvancedSearchRequest,
  type AdvancedSearchMeta,
  type FriendItem,
  type SearchHistoryItem,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import SearchBar from "./SearchBar";
import ResultsTable from "./ResultsTable";
import FITSPreview from "../../components/fits/FITSPreview";
const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

const ERROR_TYPE_LABELS: Record<string, string> = {
  timeout: "Timed out",
  connection: "Connection failed",
  auth: "Authentication error",
  rate_limit: "Rate limited",
  server_error: "Server error",
  unknown: "Unexpected error",
};

export default function DataBrowser() {
  const { user } = useAuth();
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchingId, setFetchingId] = useState<string | null>(null);
  const [fetched, setFetched] = useState<FetchResult | null>(null);
  const [retryingSource, setRetryingSource] = useState<string | null>(null);
  const [showViz, setShowViz] = useState(false);
  const [vizData, setVizData] = useState<Record<string, unknown> | null>(null);
  const [searchMeta, setSearchMeta] = useState<AdvancedSearchMeta | null>(null);

  // Multi-select state
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [bulkAction, setBulkAction] = useState<string | null>(null);
  const [bulkMsg, setBulkMsg] = useState<{ text: string; type: "ok" | "err" } | null>(null);

  // Share to team state
  const [showSharePanel, setShowSharePanel] = useState(false);
  const [friends, setFriends] = useState<FriendItem[]>([]);
  const [shareTargetId, setShareTargetId] = useState("");
  const [sharing, setSharing] = useState(false);

  const lastSearchRef = useRef<{ query: string; sources: string[]; radius: number } | null>(null);

  // Search history state
  const [recentSearches, setRecentSearches] = useState<SearchHistoryItem[]>([]);

  useEffect(() => {
    getSearchHistory()
      .then((items) => setRecentSearches(items.slice(0, 5)))
      .catch(() => setRecentSearches([]));
  }, []);

  // Helpers: get selected SearchResult objects from keys
  const validResults = results.filter((r) => r.object_id !== "error");
  const errorEntries = results.filter((r) => r.object_id === "error");

  function getSelectedResults(): SearchResult[] {
    return validResults.filter((r, i) => selectedKeys.has(`${r.source}-${r.object_id}-${i}`));
  }

  // ── Search handlers ──

  const handleSearch = async (query: string, sources: string[], radius: number) => {
    setLoading(true);
    setError(null);
    setFetched(null);
    setSearchMeta(null);
    setSelectedKeys(new Set());
    lastSearchRef.current = { query, sources, radius };
    try {
      const data = await searchData(query, sources.join(","), undefined, undefined, radius);
      setResults(data);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Search failed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleAdvancedSearch = async (req: AdvancedSearchRequest) => {
    setLoading(true);
    setError(null);
    setFetched(null);
    setSearchMeta(null);
    setSelectedKeys(new Set());
    lastSearchRef.current = null;
    try {
      const response = await advancedSearch(req);
      setResults(response.results);
      setSearchMeta(response.meta);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Advanced search failed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleRetrySource = useCallback(async (source: string) => {
    if (!lastSearchRef.current) return;
    const { query, radius } = lastSearchRef.current;
    setRetryingSource(source);
    try {
      const data = await searchData(query, source, undefined, undefined, radius);
      setResults((prev) => {
        const filtered = prev.filter((r) => !(r.source === source && r.object_id === "error"));
        return [...filtered, ...data];
      });
    } catch {
      // leave existing error
    } finally {
      setRetryingSource(null);
    }
  }, []);

  const handleFetch = async (source: string, objectId: string) => {
    const key = `${source}-${objectId}`;
    setFetchingId(key);
    setError(null);
    try {
      const result = await fetchObject(source, objectId);
      setFetched(result);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Fetch failed";
      setError(msg);
    } finally {
      setFetchingId(null);
    }
  };

  // ── Bulk actions ──

  function handleVisualizeSelected() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    const data: Record<string, unknown> = {
      ra: selected.map((r) => r.ra),
      dec: selected.map((r) => r.dec),
      magnitude: selected.map((r) => r.magnitude ?? null),
      redshift: selected.map((r) => r.redshift ?? null),
      names: selected.map((r) => r.name),
      sources: selected.map((r) => r.source),
    };
    setVizData(data);
    setShowViz(true);
  }

  function handleVisualizeAll() {
    if (validResults.length === 0) return;
    const data: Record<string, unknown> = {
      ra: validResults.map((r) => r.ra),
      dec: validResults.map((r) => r.dec),
      magnitude: validResults.map((r) => r.magnitude ?? null),
      redshift: validResults.map((r) => r.redshift ?? null),
      names: validResults.map((r) => r.name),
      sources: validResults.map((r) => r.source),
    };
    setVizData(data);
    setShowViz(true);
  }

  function handleDownloadSelected() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    // Build CSV content
    const header = "source,name,object_id,ra,dec,type,magnitude,redshift";
    const rows = selected.map((r) =>
      [
        r.source,
        `"${r.name.replace(/"/g, '""')}"`,
        `"${r.object_id.replace(/"/g, '""')}"`,
        r.ra,
        r.dec,
        r.object_type || "",
        r.magnitude ?? "",
        r.redshift ?? "",
      ].join(",")
    );
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_search_results_${selected.length}_objects.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleDownloadVOTable() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;

    const escapeXml = (s: string) =>
      s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

    const rows = selected
      .map(
        (r) =>
          `        <TR><TD>${escapeXml(r.source)}</TD><TD>${escapeXml(r.name)}</TD><TD>${r.ra}</TD><TD>${r.dec}</TD><TD>${escapeXml(r.object_type || "")}</TD><TD>${r.magnitude ?? ""}</TD><TD>${r.redshift ?? ""}</TD></TR>`
      )
      .join("\n");

    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE>
    <TABLE name="results">
      <FIELD name="source" datatype="char" arraysize="*"/>
      <FIELD name="name" datatype="char" arraysize="*"/>
      <FIELD name="ra" datatype="double"/>
      <FIELD name="dec" datatype="double"/>
      <FIELD name="object_type" datatype="char" arraysize="*"/>
      <FIELD name="magnitude" datatype="double"/>
      <FIELD name="redshift" datatype="double"/>
      <DATA><TABLEDATA>
${rows}
      </TABLEDATA></DATA>
    </TABLE>
  </RESOURCE>
</VOTABLE>`;

    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_search_results_${selected.length}_objects.vot`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleBatchFetch() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    setBulkAction("fetch");
    setBulkMsg(null);
    let done = 0;
    for (const r of selected) {
      try {
        await fetchObject(r.source, r.object_id);
        done++;
      } catch {
        // continue with remaining
      }
    }
    setBulkAction(null);
    if (done === 0) {
      setBulkMsg({ text: "Failed to save any objects to workspace.", type: "err" });
    } else if (done < selected.length) {
      setBulkMsg({ text: `Saved ${done}/${selected.length} objects (some failed).`, type: "err" });
    } else {
      setBulkMsg({ text: `Saved ${done} objects to workspace.`, type: "ok" });
      // Clear after 3 seconds
      setTimeout(() => setBulkMsg(null), 3000);
    }
  }

  async function openSharePanel() {
    setShowSharePanel(true);
    if (user) {
      try {
        const data = await getFriends();
        setFriends(data.filter((f) => f.status === "accepted"));
      } catch {
        setFriends([]);
      }
    }
  }

  async function handleShareToFriend() {
    if (!shareTargetId) return;
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    setSharing(true);
    setBulkMsg(null);

    // Fetch objects to workspace first, then share the data files
    let shared = 0;
    for (const r of selected) {
      try {
        const result = await fetchObject(r.source, r.object_id);
        if (result.file_id) {
          await shareDataset(result.file_id, shareTargetId);
          shared++;
        }
      } catch {
        // continue with remaining
      }
    }
    setSharing(false);
    setShowSharePanel(false);
    if (shared > 0) {
      setBulkMsg(null);
    } else {
      setBulkMsg({ text: "Failed to share. Make sure you are signed in.", type: "err" });
    }
  }

  const selectedCount = selectedKeys.size;

  // Build criteria chips from search meta
  const criteriaChips: Array<{ label: string; value: string }> = [];
  if (searchMeta) {
    const pf = searchMeta.parsed_filters;
    if (pf.redshift_min != null || pf.redshift_max != null) {
      const zMin = pf.redshift_min as number | undefined;
      const zMax = pf.redshift_max as number | undefined;
      if (zMin != null && zMax != null) {
        criteriaChips.push({ label: "Redshift", value: `z = ${zMin} - ${zMax}` });
      } else if (zMin != null) {
        criteriaChips.push({ label: "Redshift", value: `z > ${zMin}` });
      } else if (zMax != null) {
        criteriaChips.push({ label: "Redshift", value: `z < ${zMax}` });
      }
    }
    if (pf.spectral_line) {
      const lineInfo = pf.spectral_line_info as Record<string, unknown> | undefined;
      const lineName = lineInfo?.name ?? pf.spectral_line;
      const wl = lineInfo?.rest_wavelength_um;
      criteriaChips.push({
        label: "Line",
        value: `${lineName}${wl ? ` ${(wl as number).toFixed(2)}\u03BCm` : ""}`,
      });
    }
    if (pf.object_type) {
      criteriaChips.push({ label: "Object", value: pf.object_type as string });
    }
    if (pf.observation_type) {
      criteriaChips.push({ label: "Obs. Type", value: pf.observation_type as string });
    }
    if (searchMeta.observed_freq_min_ghz != null || searchMeta.observed_freq_max_ghz != null) {
      const fMin = searchMeta.observed_freq_min_ghz;
      const fMax = searchMeta.observed_freq_max_ghz;
      if (fMin != null && fMax != null) {
        criteriaChips.push({ label: "Obs. Freq", value: `${fMin.toFixed(1)} - ${fMax.toFixed(1)} GHz` });
      } else if (fMin != null) {
        criteriaChips.push({ label: "Obs. Freq", value: `> ${fMin.toFixed(1)} GHz` });
      }
    }
    if (searchMeta.observed_wavelength_min_um != null || searchMeta.observed_wavelength_max_um != null) {
      const wMin = searchMeta.observed_wavelength_min_um;
      const wMax = searchMeta.observed_wavelength_max_um;
      if (wMin != null && wMax != null) {
        criteriaChips.push({ label: "Obs. \u03bb", value: `${wMin.toFixed(2)} - ${wMax.toFixed(2)} \u03BCm` });
      }
    }
    if (searchMeta.suggested_sources.length > 0) {
      criteriaChips.push({
        label: "Suggested Sources",
        value: searchMeta.suggested_sources.map((s) => s.toUpperCase()).join(", "),
      });
    }
  }

  return (
    <div className="data-browser">
      <h1>Data Browser</h1>
      <SearchBar onSearch={handleSearch} onAdvancedSearch={handleAdvancedSearch} loading={loading} />

      {results.length === 0 && !loading && recentSearches.length > 0 && (
        <div className="recent-searches">
          <span className="recent-searches-label">Recent Searches:</span>
          {recentSearches.map((item) => (
            <button
              key={item.id}
              className="recent-search-chip"
              onClick={() => handleSearch(item.query, item.sources ? item.sources.split(",") : [], 0.05)}
            >
              {item.query}
            </button>
          ))}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
      {bulkMsg && (
        <div className={bulkMsg.type === "ok" ? "success-banner" : "error-banner"}>
          {bulkMsg.text}
        </div>
      )}

      {criteriaChips.length > 0 && (
        <div className="search-criteria-chips">
          {criteriaChips.map((chip, i) => (
            <span key={i} className="criteria-chip">
              <span className="chip-label">{chip.label}:</span>{" "}
              <span className="chip-value">{chip.value}</span>
            </span>
          ))}
        </div>
      )}

      {errorEntries.length > 0 && (
        <div className="source-errors">
          {errorEntries.map((entry) => {
            const label = ERROR_TYPE_LABELS[entry.error_type ?? "unknown"] ?? "Error";
            const retries =
              typeof entry.extra?.retries_attempted === "number" ? entry.extra.retries_attempted : null;
            return (
              <div key={entry.source} className="source-error-banner">
                <div className="source-error-info">
                  <span className="source-error-source">{entry.source.toUpperCase()}</span>
                  <span className="source-error-type">{label}</span>
                  <span className="source-error-message">
                    {entry.name}
                    {retries !== null && (
                      <span className="source-error-retries"> ({retries} retries attempted)</span>
                    )}
                  </span>
                </div>
                <button
                  className="btn-retry"
                  disabled={retryingSource === entry.source}
                  onClick={() => handleRetrySource(entry.source)}
                >
                  {retryingSource === entry.source ? <span className="spinner" /> : null}
                  {retryingSource === entry.source ? "Retrying..." : "Retry"}
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Bulk Action Bar ── */}
      {validResults.length > 0 && (
        <div className="bulk-action-bar">
          <span className="bulk-count">
            {selectedCount > 0
              ? `${selectedCount} of ${validResults.length} selected`
              : `${validResults.length} results`}
          </span>
          <div className="bulk-actions">
            {selectedCount > 0 ? (
              <>
                <button className="btn-secondary btn-small" onClick={handleVisualizeSelected}>
                  Visualize Selected
                </button>
                <button className="btn-secondary btn-small" onClick={handleDownloadSelected}>
                  Download CSV
                </button>
                <button className="btn-secondary btn-small" onClick={handleDownloadVOTable}>
                  Download VOTable
                </button>
                <button
                  className="btn-secondary btn-small"
                  onClick={handleBatchFetch}
                  disabled={bulkAction === "fetch"}
                >
                  {bulkAction === "fetch" ? "Saving..." : "Save to Workspace"}
                </button>
                {user && (
                  <button className="btn-secondary btn-small" onClick={openSharePanel}>
                    Share to Team
                  </button>
                )}
                <button
                  className="btn-secondary btn-small"
                  onClick={() => setSelectedKeys(new Set())}
                >
                  Clear Selection
                </button>
              </>
            ) : (
              <button className="btn-secondary btn-small" onClick={handleVisualizeAll}>
                Visualize All
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Share Panel ── */}
      {showSharePanel && (
        <div className="share-panel">
          <div className="share-panel-header">
            <h3>Share {selectedCount} objects to a friend</h3>
            <button className="btn-close" onClick={() => setShowSharePanel(false)}>Close</button>
          </div>
          {friends.length === 0 ? (
            <p className="empty-msg">No friends yet. Add friends in the Team page first.</p>
          ) : (
            <div className="share-panel-body">
              <select
                value={shareTargetId}
                onChange={(e) => setShareTargetId(e.target.value)}
                className="team-select"
              >
                <option value="">Select a friend...</option>
                {friends.map((f) => (
                  <option key={f.user_id} value={f.user_id}>
                    {f.email}
                  </option>
                ))}
              </select>
              <button
                className="btn-primary"
                onClick={handleShareToFriend}
                disabled={!shareTargetId || sharing}
              >
                {sharing ? "Sharing..." : "Share"}
              </button>
            </div>
          )}
        </div>
      )}

      <ResultsTable
        results={validResults}
        onFetch={handleFetch}
        fetchingId={fetchingId}
        loading={loading}
        selectedKeys={selectedKeys}
        onSelectionChange={setSelectedKeys}
      />

      {fetched && (
        <FITSPreview
          filename={fetched.filename}
          fitsPath={fetched.fits_path}
          source={fetched.source}
          objectId={fetched.object_id}
        />
      )}
      {showViz && vizData && (
        <div className="viz-overlay">
          <div className="viz-overlay-content">
            <Suspense fallback={<div className="fits-loading">Loading visualization...</div>}>
              <PlotBuilder
                initialData={vizData}
                initialChartType="sky_coverage"
                onClose={() => setShowViz(false)}
              />
            </Suspense>
          </div>
        </div>
      )}
    </div>
  );
}
