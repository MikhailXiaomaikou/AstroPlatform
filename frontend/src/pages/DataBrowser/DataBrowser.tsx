import { useState, useCallback, useRef, useEffect, lazy, Suspense } from "react";
import { useNavigate } from "react-router-dom";
import {
  searchData,
  advancedSearch,
  fetchObject,
  shareDataset,
  getFriends,
  getSearchHistory,
  crossMatch,
  logOperation,
  exportSearchNotebook,
  shareResults,
  getProfile,
} from "../../api/client";
import FITSBrowser from "../../components/fits/FITSBrowser";
import type {
  SearchResult,
  FetchResult,
  AdvancedSearchRequest,
  AdvancedSearchMeta,
  FriendItem,
  SearchHistoryItem,
  CrossMatchResult,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../i18n";
import { useTracking } from "../../hooks/useTracking";
import SearchBar from "./SearchBar";
import ResultsTable from "./ResultsTable";
import LiteraturePanel from "./LiteraturePanel";
import FITSPreview from "../../components/fits/FITSPreview";
import ObjectDetailPanel from "../../components/ObjectDetailPanel";
import AladinViewer from "../../components/viz/AladinViewer";
import { buildPipelineDraft, findWorkspaceFile, upsertWorkspaceFile } from "../../utils/workspaceCache";
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
  const { t } = useI18n();
  const navigate = useNavigate();
  const { track } = useTracking();
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingSources, setLoadingSources] = useState<string[]>([]);
  const [searched, setSearched] = useState(false);
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

  // Cross-match state
  const [showCrossMatch, setShowCrossMatch] = useState(false);
  const [crossMatchInput, setCrossMatchInput] = useState("");
  const [crossMatchRadius, setCrossMatchRadius] = useState("3.0");
  const [crossMatchResults, setCrossMatchResults] = useState<CrossMatchResult[] | null>(null);
  const [crossMatchLoading, setCrossMatchLoading] = useState(false);

  // Share to team state
  const [showSharePanel, setShowSharePanel] = useState(false);
  const [friends, setFriends] = useState<FriendItem[]>([]);
  const [shareTargetId, setShareTargetId] = useState("");
  const [sharing, setSharing] = useState(false);
  const [sharingToTeam, setSharingToTeam] = useState(false);

  // Object detail panel state
  const [detailObject, setDetailObject] = useState<{ name: string; ra: number; dec: number } | null>(null);
  const [showSkyView, setShowSkyView] = useState(false);

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

  function resolvePipelineInput(source: string, objectId: string) {
    return findWorkspaceFile(source, objectId)?.fits_path || `${source}/${objectId}`;
  }

  function openPipelineWithInput(inputDataId: string) {
    localStorage.setItem("pipeline_autosave", JSON.stringify(buildPipelineDraft(inputDataId)));
    window.location.href = "/pipeline";
  }

  function getSelectedResults(): SearchResult[] {
    return validResults.filter((r, i) => selectedKeys.has(`${r.source}-${r.object_id}-${i}`));
  }

  // ── Search handlers ──

  const handleSearch = async (query: string, sources: string[], radius: number) => {
    const started = performance.now();
    setLoading(true);
    setLoadingSources(sources);
    setError(null);
    setFetched(null);
    setSearchMeta(null);
    setSelectedKeys(new Set());
    lastSearchRef.current = { query, sources, radius };
    try {
      const data = await searchData(query, sources.join(","), undefined, undefined, radius);
      setResults(data);
      const sourceErrors = data.filter((r) => r.object_id === "error");
      const validCount = data.length - sourceErrors.length;
      track("search.query", {
        databases: sources,
        object_name: query,
        ra: null,
        dec: null,
        radius,
        result_count: validCount,
        query_time_ms: Math.round(performance.now() - started),
        filters: {},
      });
      if (validCount === 0 && sourceErrors.length > 0) {
        setError(
          sourceErrors
            .map((r) => `${r.source.toUpperCase()}: ${r.name.replace(/^Error querying [^:]+:\s*/, "")}`)
            .join(" | ")
        );
      }
      logOperation("search", `Searched ${sources.join(",")} for "${query}" (${data.length} results)`);
      // Save for AI context
      try { localStorage.setItem("astro_last_search", JSON.stringify({
        query, sources, radius, count: data.length,
        sample: data.slice(0, 5).map(r => ({ name: r.name, source: r.source, type: r.object_type, z: r.redshift })),
      })); } catch { /* */ }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Search failed";
      setError(msg);
      track("error.query_failed", {
        database: sources.join(","),
        error_type: "search_failed",
        error_msg: msg,
      });
    } finally {
      setLoading(false);
      setLoadingSources([]);
      setSearched(true);
    }
  };

  const handleAdvancedSearch = async (req: AdvancedSearchRequest) => {
    const started = performance.now();
    setLoading(true);
    setLoadingSources(req.sources ?? []);
    setError(null);
    setFetched(null);
    setSearchMeta(null);
    setSelectedKeys(new Set());
    lastSearchRef.current = null;
    try {
      const response = await advancedSearch(req);
      setResults(response.results);
      setSearchMeta(response.meta);
      track("search.query", {
        databases: req.sources || [],
        object_name: req.natural_query || "",
        ra: req.ra ?? null,
        dec: req.dec ?? null,
        radius: req.radius ?? 1.0,
        result_count: response.results.filter((r) => r.object_id !== "error").length,
        query_time_ms: Math.round(performance.now() - started),
        filters: response.meta.parsed_filters || {},
      });
      logOperation("search", `Advanced search (${response.results.length} results)`);
      try { localStorage.setItem("astro_last_search", JSON.stringify({
        query: req.natural_query || req.object_type || "advanced",
        count: response.results.length,
        sample: response.results.slice(0, 5).map(r => ({ name: r.name, source: r.source, type: r.object_type, z: r.redshift })),
      })); } catch { /* */ }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Advanced search failed";
      setError(msg);
      track("error.query_failed", {
        database: (req.sources || []).join(","),
        error_type: "advanced_search_failed",
        error_msg: msg,
      });
    } finally {
      setLoading(false);
      setLoadingSources([]);
      setSearched(true);
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
      // Retry once on network error
      let result;
      try {
        result = await fetchObject(source, objectId);
      } catch (firstErr) {
        if (firstErr instanceof Error && firstErr.message === "Network Error") {
          // Wait 2s and retry
          await new Promise(r => setTimeout(r, 2000));
          result = await fetchObject(source, objectId);
        } else {
          throw firstErr;
        }
      }
      if (result.fits_path) {
        upsertWorkspaceFile({
          id: result.file_id || undefined,
          source: result.source,
          object_id: result.object_id,
          fits_path: result.fits_path,
          metadata: { filename: result.filename },
          local_only: !result.file_id,
        });
      }
      setFetched(result);
    } catch (err: unknown) {
      let msg = `Failed to fetch FITS from ${source.toUpperCase()} for "${objectId}": `;
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response?: { status?: number; data?: { detail?: string } }}).response;
        if (resp?.data?.detail) {
          msg += resp.data.detail;
        } else if (resp?.status) {
          msg += `server returned HTTP ${resp.status}`;
        } else {
          msg += "no response from server";
        }
      } else if (err instanceof Error) {
        if (err.message === "Network Error") {
          const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
          msg += `cannot reach ${apiUrl}. This may be caused by: network/firewall blocking, ad blocker, or the backend is restarting. Try refreshing the page.`;
        } else if (err.message.includes("timeout")) {
          msg += "request timed out. The external data source may be slow. Try again.";
        } else {
          msg += err.message;
        }
      } else {
        msg += "unknown error";
      }
      // Add source-specific tips
      if (source === "sdss" && msg.includes("503")) {
        msg += " (SDSS SkyServer may be temporarily overloaded. Try again in a few minutes.)";
      }
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
    logOperation("visualize", `Visualized ${selected.length} selected objects`);
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
    logOperation("visualize", `Visualized all ${validResults.length} results`);
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
    logOperation("export", `Downloaded CSV with ${selected.length} objects`);
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
    logOperation("export", `Downloaded VOTable with ${selected.length} objects`);
  }

  async function handleCrossMatch() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    setCrossMatchLoading(true);
    setCrossMatchResults(null);
    try {
      // Parse the pasted CSV into list_b
      const lines = crossMatchInput.trim().split("\n").filter((l) => l.trim());
      const listB = lines.map((line) => {
        const parts = line.split(/[,\t]+/).map((s) => s.trim());
        return {
          ra: parseFloat(parts[0]) || 0,
          dec: parseFloat(parts[1]) || 0,
          name: parts[2] || `${parts[0]},${parts[1]}`,
        };
      });
      const listA = selected.map((r) => ({ ra: r.ra, dec: r.dec, name: r.name }));
      const radius = parseFloat(crossMatchRadius) || 3.0;
      const matches = await crossMatch(listA, listB, radius);
      setCrossMatchResults(matches);
      logOperation("crossmatch", `Cross-matched ${listA.length} x ${listB.length} objects, ${matches.length} matches`);
    } catch {
      setCrossMatchResults([]);
    } finally {
      setCrossMatchLoading(false);
    }
  }

  async function handleBatchFetch() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    setBulkAction("fetch");
    setBulkMsg(null);
    let done = 0;
    for (const r of selected) {
      try {
        const result = await fetchObject(r.source, r.object_id);
        if (result.fits_path) {
          upsertWorkspaceFile({
            id: result.file_id || undefined,
            source: result.source,
            object_id: result.object_id,
            fits_path: result.fits_path,
            metadata: { filename: result.filename },
            local_only: !result.file_id,
          });
        }
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

  async function handleShareWithTeam() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    setSharingToTeam(true);
    setBulkMsg(null);
    try {
      const profile = await getProfile();
      const objects = selected.map((r) => ({
        name: r.name,
        ra: r.ra,
        dec: r.dec,
        source: r.source,
        object_id: r.object_id,
        object_type: r.object_type || "",
        redshift: r.redshift ?? null,
        magnitude: r.magnitude ?? null,
      }));
      await shareResults(profile.id, `${selected.length} objects`, objects);
      setBulkMsg({ text: `Shared ${selected.length} objects with team.`, type: "ok" });
      setTimeout(() => setBulkMsg(null), 3000);
    } catch {
      setBulkMsg({ text: "Failed to share with team. Make sure you are signed in.", type: "err" });
    } finally {
      setSharingToTeam(false);
    }
  }

  function handleSendToAI() {
    const selected = getSelectedResults();
    if (selected.length === 0) return;
    const objectLines = selected.map((r) => {
      const parts = [r.name, `RA=${r.ra.toFixed(5)}`, `Dec=${r.dec.toFixed(5)}`, `source=${r.source}`];
      if (r.object_type) parts.push(`type=${r.object_type}`);
      if (r.redshift != null) parts.push(`z=${r.redshift}`);
      if (r.magnitude != null) parts.push(`mag=${r.magnitude}`);
      return parts.join(", ");
    });
    const draft =
      `I have selected these ${selected.length} astronomical objects from a search:\n\n` +
      objectLines.map((line) => `- ${line}`).join("\n") +
      `\n\nWhat can you tell me about them?`;
    localStorage.setItem("astro_chat_draft", draft);
    navigate("/chat");
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

  const [activeTab, setActiveTab] = useState<"search" | "files" | "literature">("search");

  return (
    <div className="data-browser">
      <h1>{t("nav.data_browser")}</h1>
      <div className="page-tabs">
        <button className={`page-tab${activeTab === "search" ? " active" : ""}`} onClick={() => setActiveTab("search")}>
          {t("search.search")}
        </button>
        <button className={`page-tab${activeTab === "files" ? " active" : ""}`} onClick={() => setActiveTab("files")}>
          {t("data.my_files")}
        </button>
        <button className={`page-tab${activeTab === "literature" ? " active" : ""}`} onClick={() => setActiveTab("literature")}>
          {t("data.literature")}
        </button>
      </div>

      {activeTab === "files" && <FITSBrowser />}

      {activeTab === "literature" && <LiteraturePanel />}

      {activeTab === "search" && <>
      <SearchBar onSearch={handleSearch} onAdvancedSearch={handleAdvancedSearch} loading={loading} />

      {loading && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
            padding: "1.25rem 1rem",
            marginBottom: "0.75rem",
            borderRadius: 10,
            background: "rgba(10, 132, 255, 0.08)",
            border: "1px solid rgba(10, 132, 255, 0.25)",
            color: "var(--color-accent, #0a84ff)",
            fontSize: "0.9rem",
            fontWeight: 500,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="spinner spinner-blue" style={{ width: 20, height: 20 }} />
            <span>
              Searching {loadingSources.length} source{loadingSources.length !== 1 ? "s" : ""}...
            </span>
          </div>
          {loadingSources.length > 0 && (
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "center" }}>
              {loadingSources.map((s) => (
                <span
                  key={s}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "2px 8px",
                    borderRadius: 6,
                    background: "rgba(10, 132, 255, 0.12)",
                    fontSize: "0.78rem",
                    fontWeight: 600,
                    letterSpacing: "0.02em",
                  }}
                >
                  <span
                    className="spinner spinner-blue"
                    style={{ width: 10, height: 10 }}
                  />
                  {s.toUpperCase()}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

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

      {/* ── Source Summary Bar ── */}
      {results.length > 0 && lastSearchRef.current && (() => {
        const queried = lastSearchRef.current!.sources;
        const sourceCounts: Record<string, number> = {};
        const sourceErrors: Record<string, string> = {};
        for (const s of queried) sourceCounts[s] = 0;
        for (const r of results) {
          if (r.object_id === "error") {
            sourceErrors[r.source] = ERROR_TYPE_LABELS[r.error_type ?? "unknown"] ?? "Error";
          } else {
            sourceCounts[r.source] = (sourceCounts[r.source] || 0) + 1;
          }
        }
        return (
          <div className="source-summary-bar">
            {queried.map((s) => {
              const count = sourceCounts[s] || 0;
              const errMsg = sourceErrors[s];
              return (
                <span
                  key={s}
                  className={`source-summary-chip${errMsg ? " source-chip-error" : count === 0 ? " source-chip-empty" : ""}`}
                  title={errMsg || `${count} results`}
                >
                  <span className={`badge badge-${s}`}>{s.toUpperCase()}</span>
                  {errMsg
                    ? <span className="source-chip-label">{errMsg}</span>
                    : <span className="source-chip-label">{count} results</span>
                  }
                </span>
              );
            })}
          </div>
        );
      })()}

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
                <button className="btn-secondary btn-small" onClick={async () => {
                  const sel = validResults.filter((_, i) => selectedKeys.has(`${validResults[i]?.source}-${validResults[i]?.object_id}-${i}`));
                  const data = sel.length > 0 ? sel : validResults;
                  try {
                    const blob = await exportSearchNotebook(lastSearchRef.current?.query || "search", data as unknown as Array<Record<string, unknown>>);
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a"); a.href = url;
                    a.download = `astro_${data.length}_objects.ipynb`; a.click();
                    URL.revokeObjectURL(url);
                  } catch { /* */ }
                }}>
                  Export Notebook
                </button>
                <button className="btn-secondary btn-small" onClick={handleSendToAI}>
                  Ask AI
                </button>
                <button className="btn-secondary btn-small" onClick={() => {
                  const firstSelected = getSelectedResults()[0];
                  if (!firstSelected) return;
                  openPipelineWithInput(resolvePipelineInput(firstSelected.source, firstSelected.object_id));
                }}>
                  Open in Pipeline
                </button>
                <button
                  className="btn-secondary btn-small"
                  onClick={handleBatchFetch}
                  disabled={bulkAction === "fetch"}
                >
                  {bulkAction === "fetch" ? "Saving..." : "Save to Workspace"}
                </button>
                <button className="btn-secondary btn-small" onClick={() => setShowCrossMatch(!showCrossMatch)}>
                  Cross-match...
                </button>
                {user && (
                  <>
                    <button className="btn-secondary btn-small" onClick={openSharePanel}>
                      Share to Friend
                    </button>
                    <button
                      className="btn-secondary btn-small"
                      onClick={handleShareWithTeam}
                      disabled={sharingToTeam}
                    >
                      {sharingToTeam ? "Sharing..." : "Share with Team"}
                    </button>
                  </>
                )}
                <button
                  className="btn-secondary btn-small"
                  onClick={() => setSelectedKeys(new Set())}
                >
                  Clear Selection
                </button>
              </>
            ) : (
              <>
                <button className="btn-secondary btn-small" onClick={handleVisualizeAll}>
                  Visualize All
                </button>
                <button className="btn-secondary btn-small" onClick={() => setShowSkyView(!showSkyView)}>
                  {showSkyView ? "Hide Sky View" : "Sky View"}
                </button>
              </>
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

      {/* ── Cross-match Panel ── */}
      {showCrossMatch && (
        <div className="share-panel">
          <div className="share-panel-header">
            <h3>Cross-match selected objects</h3>
            <button className="btn-close" onClick={() => { setShowCrossMatch(false); setCrossMatchResults(null); }}>Close</button>
          </div>
          <div style={{ padding: "8px 0" }}>
            <p style={{ fontSize: "0.8rem", marginBottom: 8 }}>
              Paste coordinates (RA,Dec[,name] per line) to cross-match against selected objects:
            </p>
            <textarea
              value={crossMatchInput}
              onChange={(e) => setCrossMatchInput(e.target.value)}
              placeholder={"180.0, 45.0, Source1\n180.1, 45.1, Source2"}
              rows={5}
              style={{ width: "100%", fontFamily: "monospace", fontSize: "0.8rem", padding: 8, borderRadius: 4, background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "inherit" }}
            />
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
              <label style={{ fontSize: "0.8rem" }}>
                Radius (arcsec):
                <input
                  type="text"
                  value={crossMatchRadius}
                  onChange={(e) => setCrossMatchRadius(e.target.value)}
                  style={{ width: 60, marginLeft: 4, padding: "2px 6px", fontFamily: "monospace", background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.1)", color: "inherit", borderRadius: 4 }}
                />
              </label>
              <button className="btn-primary" onClick={handleCrossMatch} disabled={crossMatchLoading || !crossMatchInput.trim()}>
                {crossMatchLoading ? "Matching..." : "Run Cross-match"}
              </button>
            </div>
            {crossMatchResults !== null && (
              <div style={{ marginTop: 12 }}>
                <p style={{ fontSize: "0.8rem", fontWeight: 600 }}>{crossMatchResults.length} matches found</p>
                {crossMatchResults.length > 0 && (
                  <div className="results-table-wrap" style={{ maxHeight: 200 }}>
                    <table className="results-table" style={{ fontSize: "0.75rem" }}>
                      <thead>
                        <tr>
                          <th>Source A</th><th>Source B</th><th>Sep (arcsec)</th>
                        </tr>
                      </thead>
                      <tbody>
                        {crossMatchResults.map((m, i) => (
                          <tr key={i}>
                            <td>{m.a_name}</td>
                            <td>{m.b_name}</td>
                            <td>{m.separation_arcsec.toFixed(2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {showSkyView && validResults.length > 0 && (() => {
        // Compute mean position and FOV from spread of results
        const ras = validResults.map(r => r.ra);
        const decs = validResults.map(r => r.dec);
        const meanRa = ras.reduce((a, b) => a + b, 0) / ras.length;
        const meanDec = decs.reduce((a, b) => a + b, 0) / decs.length;
        const raSpread = Math.max(...ras) - Math.min(...ras);
        const decSpread = Math.max(...decs) - Math.min(...decs);
        const spread = Math.max(raSpread, decSpread);
        // Add 20% padding, minimum 0.05 deg, maximum 30 deg
        const computedFov = Math.max(0.05, Math.min(30, spread * 1.2 || 0.5));

        return (
          <div style={{ marginBottom: "1rem" }}>
            <AladinViewer
              objects={validResults.map(r => ({ name: r.name, ra: r.ra, dec: r.dec, source: r.source, object_type: r.object_type }))}
              centerRa={meanRa}
              centerDec={meanDec}
              fov={computedFov}
            />
          </div>
        );
      })()}

      <ResultsTable
        results={validResults}
        onFetch={handleFetch}
        fetchingId={fetchingId}
        loading={loading}
        searched={searched}
        selectedKeys={selectedKeys}
        onSelectionChange={setSelectedKeys}
        onObjectClick={(name, ra, dec) => {
          setDetailObject({ name, ra, dec });
          const clicked = validResults.find((result) => result.name === name && result.ra === ra && result.dec === dec);
          if (clicked) {
            track("search.result_click", {
              object_name: clicked.name,
              object_type: clicked.object_type,
              source_database: clicked.source,
            });
            track("object.viewed", {
              object_name: clicked.name,
              object_type: clicked.object_type,
              ra: clicked.ra,
              dec: clicked.dec,
            });
          }
        }}
      />

      <ObjectDetailPanel
        objectName={detailObject?.name ?? ""}
        ra={detailObject?.ra}
        dec={detailObject?.dec}
        isOpen={detailObject !== null}
        onClose={() => setDetailObject(null)}
      />

      {fetched && (
        <div style={{ marginTop: "1rem" }}>
          {fetched.fits_path ? (
            <FITSPreview
              filename={fetched.filename}
              fitsPath={fetched.fits_path}
              source={fetched.source}
              objectId={fetched.object_id}
            />
          ) : (
            <div className="success-banner" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>FITS data retrieved from {fetched.source.toUpperCase()} for {fetched.object_id}.</span>
              <button className="btn-secondary btn-small" onClick={() => {
                // Re-fetch to try storing again
                handleFetch(fetched.source, fetched.object_id);
              }}>
                Retry & Preview
              </button>
            </div>
          )}
        </div>
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
      </>}
    </div>
  );
}
