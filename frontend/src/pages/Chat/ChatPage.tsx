import { useState, useMemo, useRef, useEffect, useCallback, lazy, Suspense } from "react";
import {
  sendChatMessage,
  executeChatAction,
  getStoredApiKeys,
  writeStoredApiKeys,
  searchADS,
  getBibTeX,
  logOperation,
  uploadFITS,
  uploadGeneralFile,
  saveChatSession,
  renameChatSession,
  listChatSessions,
  loadChatSession,
  deleteChatSession,
  importChatSession,
  createSessionShare,
  listSessionShares,
  revokeSessionShare,
  createSessionSnapshot,
  listSessionSnapshots,
  restoreSessionSnapshot,
  diffSessionSnapshots,
  exportChatMarkdown,
  exportChatNotebook,
  exportChatLatex,
  exportChatBibTeX,
  generatePaperDraft,
  type ChatMessage,
  type ChatAction,
  type ADSReference,
  type ChatSessionSummary,
  type SessionShareItem,
  type SessionSnapshotItem,
  type SessionSnapshotDiff,
  type AnalysisValidationResult,
  type PaperDraftResponse,
  updatePaperDraft,
  validatePaperSession,
} from "../../api/client";
import MarkdownText from "../../components/chat/MarkdownText";
import ErrorBoundary from "../../components/ErrorBoundary";
import { useI18n } from "../../i18n";
import { useAuth } from "../../context/AuthContext";
import { useTracking } from "../../hooks/useTracking";
import { registerWorkspaceExport } from "../../utils/workspaceCache";
const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

/* Fullscreen image/plot modal */
function FullscreenModal({ src, onClose }: { src: string; onClose: () => void }) {
  const handleDownload = () => {
    const a = document.createElement("a");
    a.href = src;
    a.download = `astro_figure_${Date.now()}.png`;
    a.click();
  };
  return (
    <div className="figure-modal-backdrop" onClick={onClose}>
      <div className="figure-modal" onClick={(e) => e.stopPropagation()}>
        <img src={src} alt="Full-size figure" className="figure-modal-img" />
        <div className="figure-modal-toolbar">
          <button className="btn-secondary btn-small" onClick={handleDownload}>Download PNG</button>
          <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function ClickableFigure({ src, alt }: { src: string; alt: string }) {
  const [expanded, setExpanded] = useState(false);
  const handleDownload = () => {
    const a = document.createElement("a");
    a.href = src;
    a.download = `astro_figure_${Date.now()}.png`;
    a.click();
  };
  return (
    <>
      <div className="code-figure-wrapper">
        <img src={src} alt={alt} className="code-figure" onClick={() => setExpanded(true)} title="Click to expand" />
        <div className="code-figure-actions">
          <button className="btn-secondary btn-small" onClick={() => setExpanded(true)}>Expand</button>
          <button className="btn-secondary btn-small" onClick={handleDownload}>Download</button>
        </div>
      </div>
      {expanded && <FullscreenModal src={src} onClose={() => setExpanded(false)} />}
    </>
  );
}

interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: Map<number, Record<string, unknown>>;
}

function hasStoredAiKey(): boolean {
  const keys = getStoredApiKeys();
  return Object.values(keys).some((v) => typeof v === "string" && v.trim().length > 0);
}

function buildMinimalChatHistory(messages: DisplayMessage[]): ChatMessage[] {
  return messages.map((message) => {
    if (message.role !== "assistant" || !message.actions?.length) {
      return { role: message.role, content: message.content };
    }

    const pythonReplayActions = message.actions
      .filter((action) => action.action === "run_python")
      .map((action) => {
        const raw = action as unknown as Record<string, unknown>;
        const toolInput = (raw.tool_input && typeof raw.tool_input === "object")
          ? raw.tool_input as Record<string, unknown>
          : (raw.params && typeof raw.params === "object" ? raw.params as Record<string, unknown> : {});
        const code = typeof toolInput.code === "string" ? toolInput.code : "";
        if (!code.trim()) {
          return null;
        }
        const rawResult = raw.tool_result && typeof raw.tool_result === "object"
          ? raw.tool_result as Record<string, unknown>
          : undefined;
        return {
          action: "run_python",
          tool_input: { code },
          tool_result: rawResult ? { success: rawResult.success !== false } : { success: true },
        } as unknown as ChatAction;
      })
      .filter((action): action is ChatAction => action !== null);

    return {
      role: message.role,
      content: message.content,
      actions: pythonReplayActions.length > 0 ? pythonReplayActions : undefined,
    };
  });
}

function ActionCard({
  action,
  index,
  result,
  onExecute,
  executing,
}: {
  action: ChatAction;
  index: number;
  result?: Record<string, unknown>;
  onExecute: (index: number, action: ChatAction) => void;
  executing: boolean;
}) {
  const labels: Record<string, string> = {
    search: "Search databases",
    search_objects: "Search databases",
    adql: "Run ADQL query",
    run_adql: "Run ADQL query",
    arxiv: "Extract arXiv tables",
    run_pipeline: "Run pipeline",
    explain: "Explanation",
    plot: "Interactive Plot",
    generate_pipeline: "Generate Pipeline",
    modify_pipeline: "Modify Pipeline",
    comment_pipeline: "Comment on Pipeline",
    get_object_info: "Object Info",
    analyze_spectrum: "Spectrum Analysis",
    search_literature: "Literature Search",
    get_last_search_results: "Search Results",
    read_arxiv_paper: "Read Paper",
    run_python: "Python Code",
  };

  const icons: Record<string, string> = {
    search: "🔍",
    search_objects: "🔍",
    arxiv: "📄",
    adql: "📊",
    run_adql: "📊",
    run_pipeline: "⚙️",
    explain: "📖",
    plot: "📈",
    generate_pipeline: "🔧",
    modify_pipeline: "✏️",
    comment_pipeline: "💬",
    get_object_info: "🌌",
    analyze_spectrum: "🔬",
    search_literature: "📚",
    get_last_search_results: "📋",
    read_arxiv_paper: "📄",
    run_python: "🐍",
  };

  const isAutoExecuted = !!(action as Record<string, unknown>)._auto_executed;
  const autoResult = (action as Record<string, unknown>).tool_result as Record<string, unknown> | undefined;

  return (
    <div className={`chat-action-card${isAutoExecuted ? " auto-executed" : ""}`}>
      <div className="chat-action-header">
        <span className="chat-action-icon">
          {icons[action.action] || "▶"}
        </span>
        <span className="chat-action-label">
          {labels[action.action] || action.action}
          {isAutoExecuted && <span className="auto-badge">auto</span>}
        </span>
        {!isAutoExecuted && action.action !== "explain" && action.action !== "comment_pipeline" && !result && (
          <button
            className="btn-chat-action"
            onClick={() => onExecute(index, action)}
            disabled={executing}
          >
            {executing ? "Running..." : action.action === "generate_pipeline" ? "Create Pipeline" : "Execute"}
          </button>
        )}
      </div>
      <div className="chat-action-detail">
        {action.action === "search" && (
          <span>
            Query: <strong>{action.query as string}</strong> | Sources:{" "}
            {(action.sources as string[])?.join(", ") || "all"}
          </span>
        )}
        {action.action === "adql" && (
          <code className="chat-action-code">{action.query as string}</code>
        )}
        {action.action === "run_pipeline" && (
          <span>
            {((action.nodes as Array<{ type: string }>) || [])
              .map((n) => n.type)
              .join(" → ")}
          </span>
        )}
        {action.action === "explain" && (
          <span>Topic: {action.topic as string}</span>
        )}
        {action.action === "plot" && (
          <span>Chart: {(action.chart_type as string) || "auto"}</span>
        )}
        {action.action === "arxiv" && (
          <span>Paper: {action.arxiv_id as string}</span>
        )}
        {action.action === "generate_pipeline" && (
          <span>
            <strong>{String(action.name || "")}</strong>
            {action.description ? <> — {String(action.description)}</> : null}
            <br />
            {((action.dag as { nodes: Array<{ type: string }> })?.nodes || [])
              .map((n: { type: string }) => n.type)
              .join(" → ")}
          </span>
        )}
        {action.action === "modify_pipeline" && (
          <span>
            {((action.modifications as Array<{ action: string }>) || [])
              .map((m: { action: string }) => m.action)
              .join(", ")}
            {action.explanation ? <> — {String(action.explanation)}</> : null}
          </span>
        )}
        {action.action === "comment_pipeline" && (
          <span>{String(action.comment || "").slice(0, 100)}...</span>
        )}
      </div>
      {action.action === "plot" && (
        <div className="chat-plot-wrapper">
          <ErrorBoundary label="the chat plot">
            <Suspense fallback={<div className="fits-loading">Loading plot...</div>}>
              <PlotBuilder
                initialData={action.data as Record<string, unknown>}
                initialChartType={action.chart_type as string}
              />
            </Suspense>
          </ErrorBoundary>
        </div>
      )}
      {result && <ActionResult result={result} />}
      {isAutoExecuted && autoResult && !result && (
        <div className="chat-action-result auto-result">
          <AutoToolResult toolName={action.action} result={autoResult} />
        </div>
      )}
    </div>
  );
}

function StatsPanel({ rows, extraCols, onClose }: {
  rows: Array<Record<string, unknown>>;
  extraCols: string[];
  onClose: () => void;
}) {
  const numericCols = useMemo(() => {
    const cols: Array<{ key: string; values: number[] }> = [];
    const checkCols = ["ra", "dec", "magnitude", "redshift", ...extraCols];
    for (const col of checkCols) {
      const vals: number[] = [];
      for (const row of rows) {
        const v = extraCols.includes(col)
          ? ((row.extra || {}) as Record<string, unknown>)[col]
          : row[col];
        if (typeof v === "number" && isFinite(v)) vals.push(v);
      }
      if (vals.length > 0) cols.push({ key: col, values: vals });
    }
    return cols;
  }, [rows, extraCols]);

  function median(arr: number[]): number {
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function stdDev(arr: number[], mean: number): number {
    const sum = arr.reduce((s, v) => s + (v - mean) ** 2, 0);
    return Math.sqrt(sum / arr.length);
  }

  return (
    <div className="viz-overlay" onClick={onClose}>
      <div className="viz-overlay-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 700 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Statistics ({rows.length} objects)</h3>
          <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
        </div>
        <div className="chat-result-table-scroll">
          <table className="chat-result-table">
            <thead>
              <tr>
                <th>Column</th><th>Count</th><th>Mean</th><th>Median</th><th>Std Dev</th><th>Min</th><th>Max</th>
              </tr>
            </thead>
            <tbody>
              {numericCols.map(({ key, values }) => {
                const mean = values.reduce((a, b) => a + b, 0) / values.length;
                return (
                  <tr key={key}>
                    <td><strong>{key}</strong></td>
                    <td>{values.length}</td>
                    <td>{mean.toFixed(4)}</td>
                    <td>{median(values).toFixed(4)}</td>
                    <td>{stdDev(values, mean).toFixed(4)}</td>
                    <td>{Math.min(...values).toFixed(4)}</td>
                    <td>{Math.max(...values).toFixed(4)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function QualBadge({ value }: { value: string }) {
  const v = String(value).trim().toUpperCase();
  let color = "#888";
  if (v === "A" || v === "B") color = "#4ade80";
  else if (v === "C") color = "#facc15";
  else if (v === "D" || v === "E") color = "#f87171";
  return (
    <span style={{
      display: "inline-block",
      padding: "1px 5px",
      borderRadius: 4,
      fontSize: "0.72rem",
      fontWeight: 600,
      color: "#fff",
      backgroundColor: color,
    }}>
      {v}
    </span>
  );
}

function CitationModal({ objectName, onClose }: { objectName: string; onClose: () => void }) {
  const [refs, setRefs] = useState<ADSReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedBib, setCopiedBib] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    searchADS(objectName)
      .then((data) => { if (!cancelled) setRefs(data); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to query ADS"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [objectName]);

  async function handleCopyBib(bibcode: string) {
    try {
      const bib = await getBibTeX(bibcode);
      await navigator.clipboard.writeText(bib);
      setCopiedBib(bibcode);
      setTimeout(() => setCopiedBib(null), 2000);
    } catch (e) {
      // L5: previously silent.  Tell the user the copy failed so they can
      // try again (e.g., grant clipboard permission) or copy manually.
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Could not copy BibTeX: ${msg}`);
    }
  }

  return (
    <div className="viz-overlay" onClick={onClose}>
      <div className="viz-overlay-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 650 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>References for {objectName}</h3>
          <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
        </div>
        {loading && <p>Loading references from NASA ADS...</p>}
        {error && <p style={{ color: "#f87171" }}>{error}</p>}
        {!loading && refs.length === 0 && !error && <p>No references found.</p>}
        {refs.map((ref) => (
          <div key={ref.bibcode} style={{ marginBottom: 12, padding: 8, background: "rgba(255,255,255,0.05)", borderRadius: 6 }}>
            <div style={{ fontWeight: 600, fontSize: "0.85rem" }}>{ref.title}</div>
            <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.6)", marginTop: 2 }}>
              {ref.authors.slice(0, 3).join(", ")}{ref.authors.length > 3 ? " et al." : ""} ({ref.year})
            </div>
            <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)", marginTop: 2 }}>
              {ref.bibcode}
              {ref.doi && <span> | doi:{ref.doi}</span>}
            </div>
            <button
              className="btn-chat-action"
              style={{ marginTop: 4, fontSize: "0.72rem" }}
              onClick={() => handleCopyBib(ref.bibcode)}
            >
              {copiedBib === ref.bibcode ? "Copied!" : "Copy BibTeX"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function SearchResultTable({ data }: { data: Array<Record<string, unknown>> }) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [vizData, setVizData] = useState<Record<string, unknown> | null>(null);
  const [page, setPage] = useState(0);
  const [showCitation, setShowCitation] = useState<string | null>(null);
  const [showStats, setShowStats] = useState(false);
  const PAGE_SIZE = 15;

  // Deduplicate by name+source
  const unique = useMemo(() => {
    if (!data || data.length === 0) return [];
    const seen = new Set<string>();
    return data.filter((row) => {
      const key = `${row.source}-${row.name}-${(row.ra as number)?.toFixed(3)}-${(row.dec as number)?.toFixed(3)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [data]);

  const totalPages = Math.ceil(unique.length / PAGE_SIZE);
  const displayed = unique.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Dynamically discover extra columns — only show columns where >30% of rows have data
  // and skip quality/metadata columns
  const extraCols = useMemo(() => {
    const skipCols = new Set(["rvz_type", "coo_qual", "otype_txt", "galdim_angle"]);
    const counts: Record<string, number> = {};
    for (const row of unique) {
      const extra = row.extra as Record<string, unknown> | undefined;
      if (extra) {
        for (const k of Object.keys(extra)) {
          if (extra[k] != null && !skipCols.has(k)) {
            counts[k] = (counts[k] || 0) + 1;
          }
        }
      }
    }
    const threshold = Math.max(1, unique.length * 0.3);
    // Prioritize: quality flags last, important fields first
    const priority: Record<string, number> = {
      sp_type: 1, morph_type: 2, plx_value: 3, pmra: 4, pmdec: 5,
      rvz_radvel: 6, galdim_majaxis: 7, galdim_minaxis: 8,
    };
    return Object.entries(counts)
      .filter(([, count]) => count >= threshold)
      .filter(([k]) => !k.endsWith("_qual"))  // hide quality flags from main table
      .sort((a, b) => (priority[a[0]] || 99) - (priority[b[0]] || 99))
      .map(([k]) => k)
      .slice(0, 5);  // max 5 extra columns to keep table readable
  }, [unique]);

  // Friendly names for common SIMBAD columns
  const colLabels: Record<string, string> = {
    sp_type: "Spectral Type", morph_type: "Morphology", plx_value: "Parallax (mas)",
    pmra: "PM RA (mas/yr)", pmdec: "PM Dec (mas/yr)", rvz_radvel: "Radial Vel (km/s)",
    rvz_type: "Vel Type", galdim_majaxis: "Major Axis (')", galdim_minaxis: "Minor Axis (')",
    galdim_angle: "PA (°)", Fe_H_Fe_H: "[Fe/H]",
    flux_B: "B mag", flux_V: "V mag", flux_R: "R mag", flux_I: "I mag",
    flux_J: "J mag", flux_H: "H mag", flux_K: "K mag",
  };

  // Auto-hide base columns that are all null
  const hasMag = useMemo(() => unique.some((r) => r.magnitude != null), [unique]);
  const hasZ = useMemo(() => unique.some((r) => r.redshift != null), [unique]);
  const hasType = useMemo(() => unique.some((r) => r.object_type), [unique]);

  if (unique.length === 0) {
    return (
      <div className="chat-action-result">
        <p className="chat-result-empty">No results found.</p>
      </div>
    );
  }

  // Selection uses global indices into the unique array
  const pageStart = page * PAGE_SIZE;
  const pageIndices = displayed.map((_, i) => pageStart + i);
  const allSelected = displayed.length > 0 && pageIndices.every((gi) => selected.has(gi));
  const someSelected = pageIndices.some((gi) => selected.has(gi));

  function toggleAll() {
    if (allSelected) {
      const next = new Set(selected);
      for (const gi of pageIndices) next.delete(gi);
      setSelected(next);
    } else {
      const next = new Set(selected);
      for (const gi of pageIndices) next.add(gi);
      setSelected(next);
    }
  }

  function toggleOne(globalIdx: number) {
    const next = new Set(selected);
    if (next.has(globalIdx)) next.delete(globalIdx); else next.add(globalIdx);
    setSelected(next);
  }

  function getSelected() {
    return unique.filter((_, i) => selected.has(i));
  }

  function fmt(v: unknown): string {
    if (v == null) return "—";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
    return String(v);
  }

  function handleCite() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const name = rows[0].name as string;
    if (name) setShowCitation(name);
  }

  function handleShowStats() {
    const rows = getSelected();
    if (rows.length === 0) return;
    setShowStats(true);
  }

  function getVisibleCols(): string[] {
    const cols = ["name", "ra", "dec"];
    if (hasType) cols.push("object_type");
    if (hasMag) cols.push("magnitude");
    if (hasZ) cols.push("redshift");
    cols.push(...extraCols);
    return cols;
  }

  function handleDownload() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const cols = getVisibleCols();
    const header = cols.join(",");
    const csv = [
      header,
      ...rows.map((r) => {
        const extra = (r.extra || {}) as Record<string, unknown>;
        return cols.map((c) => {
          const val = extraCols.includes(c) ? extra[c] : r[c];
          if (val == null) return "";
          const s = String(val);
          return s.includes(",") ? `"${s.replace(/"/g, '""')}"` : s;
        }).join(",");
      }),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_results_${rows.length}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    logOperation("export", `Downloaded CSV with ${rows.length} objects`);
  }

  function handleDownloadVOTable() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const cols = getVisibleCols();
    const escapeXml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const numericCols = new Set(["ra", "dec", "magnitude", "redshift"]);
    const fields = cols.map((c) => {
      if (numericCols.has(c)) return `      <FIELD name="${escapeXml(c)}" datatype="double"/>`;
      return `      <FIELD name="${escapeXml(c)}" datatype="char" arraysize="*"/>`;
    }).join("\n");
    const trs = rows.map((r) => {
      const extra = (r.extra || {}) as Record<string, unknown>;
      const tds = cols.map((c) => {
        const val = extraCols.includes(c) ? extra[c] : r[c];
        return `<TD>${val != null ? escapeXml(String(val)) : ""}</TD>`;
      }).join("");
      return `        <TR>${tds}</TR>`;
    }).join("\n");
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE>
    <TABLE name="results">
${fields}
      <DATA>
        <TABLEDATA>
${trs}
        </TABLEDATA>
      </DATA>
    </TABLE>
  </RESOURCE>
</VOTABLE>`;
    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_results_${rows.length}.vot`;
    a.click();
    URL.revokeObjectURL(url);
    logOperation("export", `Downloaded VOTable with ${rows.length} objects`);
  }

  function handleVisualize() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const vd: Record<string, unknown> = {
      ra: rows.map((r) => r.ra),
      dec: rows.map((r) => r.dec),
      names: rows.map((r) => r.name),
      sources: rows.map((r) => r.source),
    };
    for (const col of extraCols) {
      const vals = rows.map((r) => ((r.extra || {}) as Record<string, unknown>)[col]).filter((v) => typeof v === "number");
      if (vals.length > 0) vd[col] = vals;
    }
    vd.magnitude = rows.map((r) => r.magnitude ?? null);
    vd.redshift = rows.map((r) => r.redshift ?? null);
    setVizData(vd);
    logOperation("visualize", `Visualized sky distribution of ${rows.length} objects`);
  }

  return (
    <div className="chat-action-result">
      {unique.length < data.length && (
        <p className="chat-result-dedup">{data.length - unique.length} duplicates removed</p>
      )}
      {selected.size > 0 && (
        <div className="chat-result-actions">
          <span className="chat-result-count">{selected.size} selected</span>
          <button className="btn-chat-action" onClick={handleDownload}>Download CSV</button>
          <button className="btn-chat-action" onClick={handleDownloadVOTable}>Download VOTable</button>
          <button className="btn-chat-action" onClick={handleVisualize}>Visualize</button>
          <button className="btn-chat-action" onClick={handleCite}>Cite</button>
          <button className="btn-chat-action" onClick={handleShowStats}>Statistics</button>
          <button className="btn-chat-action" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}
      <div className="chat-result-table-scroll">
        <table className="chat-result-table">
          <colgroup>
            <col className="col-check" />
            <col className="col-chat-name" />
            <col className="col-chat-ra" />
            <col className="col-chat-dec" />
            {hasType && <col className="col-chat-type" />}
            {hasMag && <col className="col-chat-mag" />}
            {hasZ && <col className="col-chat-z" />}
            {extraCols.map((c) => (
              <col key={c} className="col-chat-extra" />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th className="th-check">
                <input type="checkbox" checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                  onChange={toggleAll} className="row-checkbox" />
              </th>
              <th>Name</th>
              <th>RA</th>
              <th>Dec</th>
              {hasType && <th>Type</th>}
              {hasMag && <th>Mag</th>}
              {hasZ && <th>z</th>}
              {extraCols.map((c) => (
                <th key={c} title={c}>{colLabels[c] || c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayed.map((row, i) => {
              const globalIdx = pageStart + i;
              const extra = (row.extra || {}) as Record<string, unknown>;
              return (
                <tr key={globalIdx} className={selected.has(globalIdx) ? "row-selected" : ""}>
                  <td className="td-check">
                    <input type="checkbox" checked={selected.has(globalIdx)}
                      onChange={() => toggleOne(globalIdx)} className="row-checkbox" />
                  </td>
                  <td title={`${(row.source as string).toUpperCase()} | ${new Date().toISOString().slice(0, 10)}`}>{row.name as string}</td>
                  <td>{(row.ra as number)?.toFixed(5)}</td>
                  <td>{(row.dec as number)?.toFixed(5)}</td>
                  {hasType && <td>{(row.object_type as string) || "—"}</td>}
                  {hasMag && <td>{row.magnitude != null ? (row.magnitude as number).toFixed(2) : "—"}</td>}
                  {hasZ && <td>{row.redshift != null ? (row.redshift as number).toFixed(4) : "—"}</td>}
                  {extraCols.map((c) => (
                    <td key={c}>
                      {c.endsWith("_qual") && extra[c] != null
                        ? <QualBadge value={String(extra[c])} />
                        : fmt(extra[c])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="chat-result-pagination">
          <button className="btn-chat-action" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="chat-result-page-info">Page {page + 1} of {totalPages} ({unique.length} results)</span>
          <button className="btn-chat-action" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
      {vizData && (
        <div className="viz-overlay">
          <div className="viz-overlay-content">
            <ErrorBoundary label="the visualization">
              <Suspense fallback={<div className="fits-loading">Loading visualization...</div>}>
                <PlotBuilder
                  initialData={vizData}
                  initialChartType="sky_coverage"
                  onClose={() => setVizData(null)}
                />
              </Suspense>
            </ErrorBoundary>
          </div>
        </div>
      )}
      {showCitation && (
        <CitationModal objectName={showCitation} onClose={() => setShowCitation(null)} />
      )}
      {showStats && (
        <StatsPanel rows={getSelected()} extraCols={extraCols} onClose={() => setShowStats(false)} />
      )}
    </div>
  );
}

function AutoToolResult({ toolName, result }: { toolName: string; result: Record<string, unknown> }) {
  if (result.error) {
    return <div style={{ color: "var(--color-red)", fontSize: "0.8rem" }}>Error: {String(result.error)}</div>;
  }

  // Search results
  if (toolName === "search_objects") {
    const items = (result.results as Array<Record<string, unknown>>) || [];
    const total = (result.total as number) || items.length;
    return (
      <div>
        <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", marginBottom: 4 }}>
          Found {total} objects
        </div>
        {items.slice(0, 8).map((r, i) => (
          <div key={i} style={{ fontSize: "0.75rem", padding: "2px 0", display: "flex", gap: 8 }}>
            <span className={`badge badge-${r.source}`} style={{ fontSize: "0.6rem" }}>{String(r.source).toUpperCase()}</span>
            <span>{String(r.name)}</span>
            {r.redshift != null && <span style={{ color: "var(--color-text-tertiary)" }}>z={Number(r.redshift).toFixed(4)}</span>}
          </div>
        ))}
        {total > 8 && <div style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>...and {total - 8} more</div>}
      </div>
    );
  }

  // ADQL results
  if (toolName === "run_adql") {
    const cols = (result.columns as string[]) || [];
    const rowCount = (result.row_count as number) || 0;
    return (
      <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)" }}>
        Query returned {rowCount} rows, {cols.length} columns ({cols.slice(0, 5).join(", ")}{cols.length > 5 ? "..." : ""})
      </div>
    );
  }

  // Object info
  if (toolName === "get_object_info") {
    return (
      <div style={{ fontSize: "0.78rem" }}>
        <strong>{String(result.name)}</strong> — {String(result.object_type)}
        {result.redshift != null && <span> z={Number(result.redshift).toFixed(6)}</span>}
        {result.cross_ids ? <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>
          {(result.cross_ids as string[]).length} known identifiers
        </div> : null}
      </div>
    );
  }

  // Literature
  if (toolName === "search_literature") {
    const refs = (result.results as Array<Record<string, unknown>>) || [];
    return (
      <div>
        {refs.slice(0, 8).map((r, i) => (
          <div key={i} style={{ fontSize: "0.75rem", padding: "4px 0", borderBottom: "1px solid var(--color-border)" }}>
            <div>
              <a href={`https://ui.adsabs.harvard.edu/abs/${r.bibcode}`} target="_blank" rel="noopener noreferrer"
                style={{ color: "var(--color-accent)", textDecoration: "none" }}>
                {String(r.title)}
              </a>
            </div>
            <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.7rem" }}>
              {(r.authors as string[] || []).slice(0, 3).join(", ")}{(r.authors as string[] || []).length > 3 ? " et al." : ""} ({String(r.year)})
            </div>
            {r.abstract ? (
              <div style={{ color: "var(--color-text-secondary)", fontSize: "0.7rem", marginTop: 2, lineHeight: 1.3 }}>
                {String(r.abstract).slice(0, 200)}{String(r.abstract).length > 200 ? "..." : ""}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    );
  }

  // Pipeline
  if (toolName === "generate_pipeline") {
    const dag = result.dag as { nodes: Array<{ type: string }> } | undefined;
    return (
      <div style={{ fontSize: "0.78rem" }}>
        Pipeline <strong>{String(result.name)}</strong>: {dag?.nodes?.map(n => n.type).join(" → ")}
        <div style={{ marginTop: 4 }}>
          <button
            className="btn-secondary btn-small"
            onClick={() => {
              // Store the generated DAG so Pipeline Editor can load it
              if (dag) {
                localStorage.setItem("pipeline_autosave", JSON.stringify({
                  nodes: (dag as Record<string, unknown>).nodes,
                  edges: (dag as Record<string, unknown>).edges || [],
                  inputDataId: "example/fits/path.fits",
                }));
              }
              window.location.href = "/pipeline";
            }}
            style={{ fontSize: "0.72rem" }}
          >
            Open in Pipeline Editor
          </button>
        </div>
      </div>
    );
  }

  // Python code execution
  if (toolName === "run_python") {
    const success = result.success as boolean;
    const stdout = result.stdout as string || "";
    const error = result.error as string | undefined;
    const figures = (result.figures as string[]) || [];
    const variables = result.variables as Record<string, string> | undefined;
    const variableTypes = result.variable_types as Record<string, string> | undefined;
    const tb = result.traceback as string | undefined;

    return (
      <div className="code-result">
        {/* Status */}
        <div style={{ fontSize: "0.72rem", color: success ? "var(--color-green)" : "var(--color-red)", marginBottom: 4 }}>
          {success ? "Executed successfully" : `Error: ${error || "unknown"}`}
        </div>

        {/* Stdout */}
        {stdout && (
          <pre className="code-output">{stdout}</pre>
        )}

        {/* Traceback */}
        {tb && !success && (
          <pre className="code-output code-error">{tb.slice(-500)}</pre>
        )}

        {/* Figures */}
        {figures.map((b64, i) => (
          <ClickableFigure key={i} src={`data:image/png;base64,${b64}`} alt={`Figure ${i + 1}`} />
        ))}

        {/* Variables */}
        {variables && Object.keys(variables).length > 0 && (
          <details className="code-vars">
            <summary>Variables ({Object.keys(variables).length})</summary>
            {Object.entries(variables).map(([k, v]) => (
              <div key={k} className="code-var">
                <span className="code-var-name">{k}</span>
                {variableTypes?.[k] ? <span style={{ color: "var(--color-text-tertiary)" }}> ({variableTypes[k]})</span> : null}
                {" = "}
                <span className="code-var-val">{v}</span>
              </div>
            ))}
          </details>
        )}
      </div>
    );
  }

  // Default: compact JSON
  return (
    <pre style={{ fontSize: "0.7rem", maxHeight: 100, overflow: "auto", color: "var(--color-text-tertiary)" }}>
      {JSON.stringify(result, null, 1).slice(0, 500)}
    </pre>
  );
}

function ActionResult({ result }: { result: Record<string, unknown> }) {
  const type = result.type as string;

  if (type === "search_results") {
    return <SearchResultTable data={result.data as Array<Record<string, unknown>>} />;
  }

  if (type === "adql_results") {
    const d = result.data as Record<string, unknown>;
    const columns = d?.columns as string[];
    const tableData = d?.data as Record<string, (number | string | null)[]>;
    const rowCount = d?.row_count as number;
    if (!columns || columns.length === 0) {
      return (
        <div className="chat-action-result">
          <p className="chat-result-empty">Query returned no results.</p>
        </div>
      );
    }
    const numRows = Math.min(rowCount || 0, 20);
    return (
      <div className="chat-action-result">
        <div className="chat-result-table-scroll">
        <table className="chat-result-table chat-result-table-adql">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col} title={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: numRows }).map((_, rowIdx) => (
              <tr key={rowIdx}>
                {columns.map((col) => (
                  <td key={col} title={tableData[col]?.[rowIdx] != null ? String(tableData[col][rowIdx]) : undefined}>
                    {tableData[col]?.[rowIdx] != null
                      ? String(tableData[col][rowIdx])
                      : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {(rowCount || 0) > 20 && (
          <p className="chat-result-more">
            Showing 20 of {rowCount} rows
          </p>
        )}
      </div>
    );
  }

  if (type === "explanation") {
    return null;
  }

  if (type === "arxiv_tables") {
    const d = result.data as Record<string, unknown>;
    const tables = (d?.tables || []) as Array<{
      name: string; columns: string[]; rows: string[][]; row_count: number;
    }>;
    if (tables.length === 0) {
      return <div className="chat-action-result"><p className="chat-result-empty">No tables found.</p></div>;
    }
    return (
      <div className="chat-action-result">
        <p style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", margin: "0 0 0.5rem" }}>
          {d?.title as string} — {tables.length} table(s) found
        </p>
        {tables.map((t, ti) => (
          <div key={ti} style={{ marginBottom: "0.75rem" }}>
            <p style={{ fontSize: "0.75rem", fontWeight: 600, margin: "0 0 0.25rem" }}>{t.name}</p>
            <div className="chat-result-table-scroll">
              <table className="chat-result-table">
                <thead>
                  <tr>
                    {t.columns.map((c, ci) => <th key={ci}>{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {t.rows.slice(0, 30).map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => <td key={ci}>{cell}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {t.row_count > 30 && (
              <p className="chat-result-more">Showing 30 of {t.row_count} rows</p>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (type === "generated_pipeline") {
    const d = result.data as Record<string, unknown>;
    const dag = d?.dag as { nodes: Array<{ type: string; id: string }>; edges: Array<{ source: string; target: string }> };
    const warnings = (d?.warnings as string[]) || [];
    return (
      <div className="chat-action-result">
        <div style={{ padding: "0.5rem", background: "rgba(79,195,247,0.08)", borderRadius: 6, marginBottom: "0.5rem" }}>
          <strong style={{ color: "#4fc3f7" }}>{String(d?.name || "")}</strong>
          {d?.description ? <span style={{ color: "#aaa", marginLeft: 8 }}>{String(d.description)}</span> : null}
        </div>
        <div style={{ fontSize: "0.85rem", color: "#e0e0e0" }}>
          {dag?.nodes?.map((n, i) => (
            <span key={n.id}>
              {i > 0 && <span style={{ color: "#666", margin: "0 0.3rem" }}> → </span>}
              <span style={{ background: "#2a3a4a", padding: "2px 6px", borderRadius: 4 }}>{n.type}</span>
            </span>
          ))}
        </div>
        {warnings.length > 0 && (
          <div style={{ color: "#ffa726", fontSize: "0.75rem", marginTop: "0.3rem" }}>
            {warnings.join("; ")}
          </div>
        )}
        {typeof d?.template_id === "string" && (
          <div style={{ fontSize: "0.75rem", color: "#777", marginTop: "0.3rem" }}>
            Saved as template. <a href="/pipeline" style={{ color: "#4fc3f7" }}>Open in Pipeline Editor</a>
          </div>
        )}
      </div>
    );
  }

  if (type === "pipeline_modification") {
    const d = result.data as Record<string, unknown>;
    const mods = (d?.modifications as Array<Record<string, unknown>>) || [];
    return (
      <div className="chat-action-result">
        <div style={{ fontSize: "0.85rem", color: "#e0e0e0" }}>
          <strong>Pipeline modifications:</strong>
          <ul style={{ margin: "0.3rem 0", paddingLeft: "1.2rem" }}>
            {mods.map((m, i) => (
              <li key={i} style={{ marginBottom: "0.2rem" }}>
                <span style={{ color: "#4fc3f7" }}>{String(m.action)}</span>
                {m.node_id ? <> on <code>{String(m.node_id)}</code></> : null}
                {m.node ? <> — <code>{String((m.node as Record<string, unknown>).type)}</code></> : null}
              </li>
            ))}
          </ul>
          {d?.explanation ? <p style={{ color: "#aaa", fontSize: "0.8rem" }}>{String(d.explanation)}</p> : null}
        </div>
      </div>
    );
  }

  if (type === "pipeline_comment") {
    const d = result.data as Record<string, unknown>;
    return (
      <div className="chat-action-result">
        <div style={{ padding: "0.5rem", background: "rgba(255,167,38,0.08)", borderRadius: 6, fontSize: "0.85rem" }}>
          <strong style={{ color: "#ffa726" }}>AI Review Comment</strong>
          <p style={{ color: "#e0e0e0", margin: "0.3rem 0 0" }}>{String(d?.comment || "")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-action-result">
      <pre className="chat-result-raw">
        {JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}

function ApiKeyPrompt({ onSaved }: { onSaved: () => void }) {
  const [keyInput, setKeyInput] = useState("");
  const [provider, setProvider] = useState<"anthropic" | "openai" | "deepseek">("anthropic");

  const providerInfo: Record<string, { label: string; url: string; placeholder: string; rec?: boolean }> = {
    anthropic: { label: "Anthropic (Claude)", url: "https://console.anthropic.com/settings/keys", placeholder: "sk-ant-...", rec: true },
    openai:    { label: "OpenAI (GPT)",       url: "https://platform.openai.com/api-keys",        placeholder: "sk-..." },
    deepseek:  { label: "DeepSeek",            url: "https://platform.deepseek.com/api_keys",      placeholder: "sk-..." },
  };

  const info = providerInfo[provider];

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    // M9: route through the sessionStorage-first helper instead of writing
    // straight to localStorage.  Keys no longer leak across browser
    // restarts unless the user opts in via the persist flag.
    const keys = getStoredApiKeys();
    keys[provider] = key;
    writeStoredApiKeys(keys);
    try {
      sessionStorage.setItem("astro_ai_provider", provider);
    } catch {
      /* ignore */
    }
    onSaved();
  }

  return (
    <div className="chat-apikey-prompt">
      <h3>Configure API Key</h3>
      <p>To use the AI assistant, enter an API key from any supported provider.</p>
      <div className="chat-apikey-provider-select">
        {Object.entries(providerInfo).map(([key, p]) => (
          <button
            key={key}
            type="button"
            className={`btn-small ${provider === key ? "btn-primary" : "btn-secondary"}`}
            onClick={() => { setProvider(key as "anthropic" | "openai" | "deepseek"); setKeyInput(""); }}
          >
            {p.label}{p.rec ? " ★" : ""}
          </button>
        ))}
      </div>
      <p className="chat-apikey-hint">
        Get a key at{" "}
        <a href={info.url} target="_blank" rel="noopener noreferrer">
          {new URL(info.url).hostname}
        </a>
      </p>
      <form className="chat-apikey-form" onSubmit={handleSave}>
        <input
          type="text"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder={info.placeholder}
          className="chat-apikey-input"
          autoComplete="off"
        />
        <button type="submit" className="btn-primary" disabled={!keyInput.trim()}>
          Save & Start
        </button>
      </form>
    </div>
  );
}

interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: [number, Record<string, unknown>][];
}

function loadChatHistory(): DisplayMessage[] {
  try {
    const raw = localStorage.getItem("astro_chat_history");
    if (!raw) return [];
    const stored = JSON.parse(raw) as StoredMessage[];
    return stored.map((m) => ({
      ...m,
      actionResults: m.actionResults ? new Map(m.actionResults) : new Map(),
    }));
  } catch {
    return [];
  }
}

function saveChatHistory(messages: DisplayMessage[]) {
  try {
    const stored: StoredMessage[] = messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      actions: m.actions,
      actionResults: m.actionResults ? Array.from(m.actionResults.entries()) : undefined,
    }));
    localStorage.setItem("astro_chat_history", JSON.stringify(stored));
  } catch {
    // storage full or unavailable — silently ignore
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function generateLatexFallback(msgs: DisplayMessage[]): string {
  const esc = (s: string) => s.replace(/[&%$#_{}~^\\]/g, (c) => {
    const map: Record<string, string> = {
      "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_",
      "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "\\": "\\textbackslash{}",
    };
    return map[c] ?? c;
  });
  const lines: string[] = [
    "\\documentclass[12pt]{article}",
    "\\usepackage[utf8]{inputenc}",
    "\\usepackage{amsmath,graphicx,hyperref}",
    `\\title{Standard Astro Research Report}`,
    "\\author{Standard Astro User}",
    "\\date{\\today}",
    "\\begin{document}",
    "\\maketitle",
    "",
  ];
  for (const m of msgs) {
    if (m.role === "user") {
      lines.push("\\subsection*{User}", esc(m.content), "");
    } else {
      lines.push("\\subsection*{AI Assistant}", esc(m.content), "");
    }
  }
  lines.push("\\end{document}");
  return lines.join("\n");
}

const LOCAL_CHAT_SESSIONS_KEY = "astro_local_chat_sessions";

interface LocalChatSession {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
  messages: Array<{ role: string; content: string; actions?: unknown[] }>;
}

type ExportAction = "markdown" | "notebook" | "latex" | "bibtex";
type JournalFormat = "aastex" | "mnras" | "aa";
type ShareAccessLevel = "view" | "fork" | "comment";
type PaperTab =
  | "abstract"
  | "introduction"
  | "data_sources"
  | "analysis_methods"
  | "results"
  | "discussion"
  | "conclusions"
  | "acknowledgments";

interface ToastState {
  message: string;
  tone: "success" | "error" | "info";
}

function getPaperSectionText(paperJson: Record<string, unknown>, tab: PaperTab): string {
  switch (tab) {
    case "abstract":
      return String(paperJson.abstract || "");
    case "introduction":
      return String(((paperJson.introduction as Record<string, unknown> | undefined)?.text) || "");
    case "data_sources":
      return String(((paperJson.data_and_methods as Record<string, unknown> | undefined)?.data_sources) || "");
    case "analysis_methods":
      return String(((paperJson.data_and_methods as Record<string, unknown> | undefined)?.analysis_methods) || "");
    case "results":
      return String(((paperJson.results as Record<string, unknown> | undefined)?.text) || "");
    case "discussion":
      return String(((paperJson.discussion as Record<string, unknown> | undefined)?.text) || "");
    case "conclusions":
      return String(paperJson.conclusions || "");
    case "acknowledgments":
      return String(paperJson.acknowledgments || "");
    default:
      return "";
  }
}

function setPaperSectionText(
  paperJson: Record<string, unknown>,
  tab: PaperTab,
  value: string,
): Record<string, unknown> {
  const next = structuredClone(paperJson);
  switch (tab) {
    case "abstract":
      next.abstract = value;
      break;
    case "introduction":
      next.introduction = {
        ...((next.introduction as Record<string, unknown> | undefined) || {}),
        text: value,
      };
      break;
    case "data_sources":
      next.data_and_methods = {
        ...((next.data_and_methods as Record<string, unknown> | undefined) || {}),
        data_sources: value,
      };
      break;
    case "analysis_methods":
      next.data_and_methods = {
        ...((next.data_and_methods as Record<string, unknown> | undefined) || {}),
        analysis_methods: value,
      };
      break;
    case "results":
      next.results = {
        ...((next.results as Record<string, unknown> | undefined) || {}),
        text: value,
      };
      break;
    case "discussion":
      next.discussion = {
        ...((next.discussion as Record<string, unknown> | undefined) || {}),
        text: value,
      };
      break;
    case "conclusions":
      next.conclusions = value;
      break;
    case "acknowledgments":
      next.acknowledgments = value;
      break;
  }
  return next;
}

function readLocalChatSessions(): LocalChatSession[] {
  try {
    const raw = localStorage.getItem(LOCAL_CHAT_SESSIONS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeLocalChatSessions(sessions: LocalChatSession[]) {
  try {
    localStorage.setItem(LOCAL_CHAT_SESSIONS_KEY, JSON.stringify(sessions));
  } catch {
    // ignore storage failures
  }
}

function summarizeLocalSessions(): ChatSessionSummary[] {
  return readLocalChatSessions().map(({ id, title, message_count, updated_at }) => ({
    id,
    title,
    message_count,
    updated_at,
  }));
}

function saveLocalChatSession(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  sessionId?: string | null,
): { id: string } {
  const sessions = readLocalChatSessions();
  const id = sessionId || crypto.randomUUID();
  const title = messages.find((m) => m.role === "user")?.content.slice(0, 60) || "New Chat";
  const updated_at = new Date().toISOString();
  const session: LocalChatSession = {
    id,
    title,
    updated_at,
    message_count: messages.length,
    messages,
  };
  const next = [session, ...sessions.filter((s) => s.id !== id)].slice(0, 20);
  writeLocalChatSessions(next);
  return { id };
}

function loadLocalChatSession(id: string): LocalChatSession | null {
  return readLocalChatSessions().find((session) => session.id === id) || null;
}

function deleteLocalChatSession(id: string): void {
  writeLocalChatSessions(readLocalChatSessions().filter((session) => session.id !== id));
}

function NextStepsPanel({ onSend }: { onSend: (msg: string) => void }) {
  const steps = [
    { label: "Generate paper draft", prompt: "Generate a paper draft from this analysis" },
    { label: "Export as notebook", prompt: "Export this session as a Jupyter notebook" },
    { label: "Run sensitivity analysis", prompt: "Run a sensitivity analysis on these results" },
    { label: "Search related literature", prompt: "Search for related papers on ADS" },
  ];

  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "8px 0", borderTop: "1px solid var(--color-border)" }}>
      {steps.map((s, i) => (
        <button key={i} className="btn-ghost btn-small" onClick={() => onSend(s.prompt)} style={{ fontSize: "0.75rem" }}>
          {s.label}
        </button>
      ))}
    </div>
  );
}

export default function ChatPage() {
  const { user } = useAuth();
  const { t } = useI18n();
  const { track } = useTracking();
  const [hasKey, setHasKey] = useState(() => hasStoredAiKey());

  // Re-check API key on mount (picks up keys set in Settings page)
  useEffect(() => {
    if (!hasKey && hasStoredAiKey()) setHasKey(true);
  }); // intentionally no deps — runs every render but only sets state once

  const [messages, setMessages] = useState<DisplayMessage[]>(loadChatHistory);
  const [input, setInput] = useState("");
  const [pageError, _setPageError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState<Record<ExportAction, boolean>>({
    markdown: false,
    notebook: false,
    latex: false,
    bibtex: false,
  });
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [paperSessionId, setPaperSessionId] = useState<string | null>(null);
  const [paperFormat, setPaperFormat] = useState<JournalFormat>("aastex");
  const [paperValidation, setPaperValidation] = useState<AnalysisValidationResult | null>(null);
  const [paperDraft, setPaperDraft] = useState<PaperDraftResponse | null>(null);
  const [paperEditorJson, setPaperEditorJson] = useState<Record<string, unknown> | null>(null);
  const [paperTab, setPaperTab] = useState<PaperTab>("abstract");
  const [paperLoading, setPaperLoading] = useState(false);
  const [paperGenerating, setPaperGenerating] = useState(false);
  const [paperSaving, setPaperSaving] = useState(false);
  const [executingActions, setExecutingActions] = useState<Set<string>>(
    new Set()
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Session management
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [currentSessionTitle, setCurrentSessionTitle] = useState<string>("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    return localStorage.getItem("astro_chat_sidebar_collapsed") === "1";
  });
  const [sessionSearch, setSessionSearch] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved" | "idle">("idle");
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [shareAccessLevel, setShareAccessLevel] = useState<ShareAccessLevel>("view");
  const [shareExpiryHours, setShareExpiryHours] = useState<number>(72);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sessionShares, setSessionShares] = useState<SessionShareItem[]>([]);
  const [sessionSnapshots, setSessionSnapshots] = useState<SessionSnapshotItem[]>([]);
  const [snapshotName, setSnapshotName] = useState("");
  const [snapshotCompareSelection, setSnapshotCompareSelection] = useState<string[]>([]);
  const [snapshotDiff, setSnapshotDiff] = useState<SessionSnapshotDiff | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const pythonSessionIdRef = useRef<string>(crypto.randomUUID());
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showToast = useCallback((message: string, tone: ToastState["tone"] = "success") => {
    setToast({ message, tone });
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const rememberExportInWorkspace = useCallback(async (
    blob: Blob,
    filename: string,
    exportKind: ExportAction,
  ): Promise<boolean> => {
    if (!user) return false;
    try {
      const upload = await uploadGeneralFile(
        new File([blob], filename, { type: blob.type || "application/octet-stream" })
      );
      registerWorkspaceExport({
        id: upload.id,
        filename: upload.filename,
        storagePath: upload.path,
        exportKind,
        contentType: blob.type || "application/octet-stream",
        sizeBytes: blob.size,
        localOnly: false,
      });
      return true;
    } catch {
      return false;
    }
  }, [user]);

  const handleExport = useCallback(async (
    exportKind: ExportAction,
    label: string,
    filename: string,
    exporter: () => Promise<Blob>,
    options?: { emptyMessage?: string; fallback?: () => Blob; skipDownloadWhenEmpty?: boolean },
  ) => {
    setExporting((prev) => ({ ...prev, [exportKind]: true }));
    try {
      let blob = await exporter();
      if (blob.size === 0 && options?.skipDownloadWhenEmpty) {
        showToast(options.emptyMessage || `No ${label} content was available to export`, "info");
        return;
      }
      if (blob.size === 0) {
        if (options?.fallback) {
          blob = options.fallback();
        } else {
          throw new Error(options?.emptyMessage || `${label} export returned an empty file`);
        }
      }

      downloadBlob(blob, filename);
      const savedToWorkspace = await rememberExportInWorkspace(blob, filename, exportKind);
      const exportEventMap: Record<ExportAction, string> = {
        markdown: "export.paper_draft",
        notebook: "export.notebook",
        latex: "export.latex",
        bibtex: "export.paper_draft",
      };
      const combinedText = messages.map((msg) => msg.content).join(" ");
      const bibcodeMatches = combinedText.match(/\b\d{4}[A-Za-z][A-Za-z&.]+\.+\S+/g) || [];
      track(exportEventMap[exportKind], {
        journal_format: exportKind === "latex" ? "aastex" : undefined,
        sections: exportKind === "latex" ? ["chat_export"] : undefined,
        figures_count: messages.filter((msg) => (msg.actions || []).some((action) => action.action === "plot")).length,
        citations_count: exportKind === "bibtex" ? bibcodeMatches.length : undefined,
        cell_count: exportKind === "notebook" ? messages.length + 2 : undefined,
        word_count: combinedText.split(/\s+/).filter(Boolean).length,
      });

      if (savedToWorkspace) {
        showToast(`Exported ${label} successfully — saved to Workspace`, "success");
      } else if (user) {
        showToast(`Exported ${label} successfully — downloaded locally, but Workspace sync failed`, "success");
      } else {
        showToast(`Exported ${label} successfully — downloaded locally. Sign in to sync it to Workspace.`, "success");
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : `${label} export failed`;
      showToast(`Export failed: ${detail}`, "error");
    } finally {
      setExporting((prev) => ({ ...prev, [exportKind]: false }));
    }
  }, [messages, rememberExportInWorkspace, showToast, track, user]);

  const refreshSessions = useCallback(() => {
    if (user) {
      listChatSessions().then(setSessions).catch(() => setSessions([]));
      return;
    }
    setSessions(summarizeLocalSessions());
  }, [user]);

  const loadCollaborationState = useCallback(async (sessionId: string) => {
    if (!user) return;
    const [shares, snapshots] = await Promise.all([
      listSessionShares(sessionId),
      listSessionSnapshots(sessionId),
    ]);
    setSessionShares(shares);
    setSessionSnapshots(snapshots);
  }, [user]);

  const persistSession = useCallback(async (
    data: Array<{ role: string; content: string; actions?: unknown[] }>,
    sessionId?: string | null,
  ) => {
    if (user) {
      try {
        return await saveChatSession(data, sessionId || undefined);
      } catch {
        return saveLocalChatSession(data, sessionId);
      }
    }
    return saveLocalChatSession(data, sessionId);
  }, [user]);

  const ensurePersistedSession = useCallback(async () => {
    if (messages.length === 0) {
      throw new Error("Save at least one message before sharing or snapshotting this session");
    }
    if (!user) {
      throw new Error("Sign in to manage collaboration features");
    }
    const data = messages.map((message) => ({
      role: message.role,
      content: message.content,
      actions: message.actions,
    }));
    const saved = await saveChatSession(data, currentSessionId || undefined);
    setCurrentSessionId(saved.id);
    refreshSessions();
    return saved.id;
  }, [currentSessionId, messages, refreshSessions, user]);

  const handleOpenPaperDraft = useCallback(async () => {
    if (!user) {
      showToast("Sign in to generate a paper draft", "info");
      return;
    }
    if (messages.length === 0) {
      showToast("Add some analysis messages before generating a paper draft", "info");
      return;
    }

    setPaperModalOpen(true);
    setPaperLoading(true);
    setPaperDraft(null);
    setPaperEditorJson(null);
    setPaperValidation(null);
    try {
      const sessionData = messages.map((m) => ({
        role: m.role,
        content: m.content,
        actions: m.actions,
      }));
      const saved = await saveChatSession(sessionData, currentSessionId || undefined);
      setCurrentSessionId(saved.id);
      setPaperSessionId(saved.id);
      refreshSessions();

      const validation = await validatePaperSession(saved.id);
      setPaperValidation(validation);
      setPaperTab("abstract");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper validation failed";
      showToast(detail, "error");
      setPaperModalOpen(false);
    } finally {
      setPaperLoading(false);
    }
  }, [currentSessionId, messages, refreshSessions, showToast, user]);

  const handleGeneratePaper = useCallback(async (overrideValidation = false) => {
    if (!paperSessionId) {
      showToast("Save the session before generating a paper draft", "error");
      return;
    }

    setPaperGenerating(true);
    try {
      const draft = await generatePaperDraft(paperSessionId, paperFormat, overrideValidation);
      setPaperDraft(draft);
      setPaperEditorJson(draft.paper_json);
      setPaperValidation(draft.validation);
      track("export.paper_draft", {
        journal_format: paperFormat,
        word_count: String(draft.paper_json.abstract || "").split(/\s+/).filter(Boolean).length,
        figures_count: Array.isArray((draft.paper_json.results as Record<string, unknown> | undefined)?.figures)
          ? (((draft.paper_json.results as Record<string, unknown>).figures as unknown[]) || []).length
          : 0,
        citations_count: (draft.bibtex.match(/@\w+\{/g) || []).length,
      });
      showToast("Paper draft generated", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper generation failed";
      showToast(detail, "error");
    } finally {
      setPaperGenerating(false);
    }
  }, [paperFormat, paperSessionId, showToast, track]);

  const handleSavePaperDraft = useCallback(async () => {
    if (!paperDraft || !paperEditorJson) return;
    setPaperSaving(true);
    try {
      const updated = await updatePaperDraft(paperDraft.id, paperEditorJson);
      setPaperDraft(updated);
      setPaperEditorJson(updated.paper_json);
      showToast("Paper draft saved", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Saving paper draft failed";
      showToast(detail, "error");
    } finally {
      setPaperSaving(false);
    }
  }, [paperDraft, paperEditorJson, showToast]);

  const handleRegeneratePaperSection = useCallback(async () => {
    if (!paperSessionId || !paperEditorJson) return;
    setPaperGenerating(true);
    try {
      const regenerated = await generatePaperDraft(
        paperSessionId,
        paperFormat,
        paperValidation?.overall_status === "FAIL",
      );
      const nextPaperJson = setPaperSectionText(
        paperEditorJson,
        paperTab,
        getPaperSectionText(regenerated.paper_json, paperTab),
      );
      setPaperEditorJson(nextPaperJson);
      if (paperDraft) {
        const updated = await updatePaperDraft(paperDraft.id, nextPaperJson);
        setPaperDraft(updated);
        setPaperEditorJson(updated.paper_json);
      }
      showToast(`Regenerated ${paperTab.replace(/_/g, " ")}`, "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Section regeneration failed";
      showToast(detail, "error");
    } finally {
      setPaperGenerating(false);
    }
  }, [paperDraft, paperEditorJson, paperFormat, paperSessionId, paperTab, paperValidation?.overall_status, showToast]);

  useEffect(() => {
    // If caller requested a new session, clear history first
    const newSession = localStorage.getItem("astro_chat_new_session");
    if (newSession) {
      localStorage.removeItem("astro_chat_new_session");
      setMessages([]);
      setCurrentSessionId(null);
      pythonSessionIdRef.current = crypto.randomUUID();
      localStorage.removeItem("astro_chat_history");
      localStorage.removeItem("astro_chat_autosave_draft");
    }

    const draft = localStorage.getItem("astro_chat_draft");
    if (draft) {
      setInput(draft);
      localStorage.removeItem("astro_chat_draft");
    }

    // Recover autosaved draft if current session is empty
    if (!newSession && !draft) {
      try {
        const autosaved = localStorage.getItem("astro_chat_autosave_draft");
        if (autosaved) {
          const parsed = JSON.parse(autosaved) as DisplayMessage[];
          if (parsed.length > 0 && loadChatHistory().length === 0) {
            setMessages(parsed.map((m) => ({
              ...m,
              actionResults: new Map(),
            })));
          }
        }
      } catch { /* ignore */ }
    }
  }, []);

  useEffect(() => {
    localStorage.setItem("astro_chat_sidebar_collapsed", sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  // Autosave draft to localStorage (debounced)
  useEffect(() => {
    if (messages.length === 0) return;
    const timer = setTimeout(() => {
      try {
        localStorage.setItem("astro_chat_autosave_draft", JSON.stringify(messages.slice(-50)));
      } catch { /* quota exceeded -- ignore */ }
    }, 3000);
    return () => clearTimeout(timer);
  }, [messages]);

  const handleSaveSession = async () => {
    if (messages.length === 0) return;
    try {
      const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
      const res = await persistSession(data, currentSessionId);
      setCurrentSessionId(res.id);
      refreshSessions();
      showToast(user ? "Chat saved" : "Chat saved locally");
    } catch { /* ignore */ }
  };

  // Auto-save after each AI response completes (loading transitions false)
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;
    if (wasLoading && !loading && messages.length >= 2) {
      const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
      setSaveStatus("saving");
      persistSession(data, currentSessionId)
        .then((res: { id: string; title?: string }) => {
          setCurrentSessionId(res.id);
          if (res.title) setCurrentSessionTitle(res.title);
          setSaveStatus("saved");
          refreshSessions();
        })
        .catch(() => {
          setSaveStatus("unsaved");
        });
    }
  }, [currentSessionId, loading, messages, persistSession, refreshSessions]);

  // Mark as unsaved when messages change without saving
  useEffect(() => {
    if (messages.length > 0 && !loading) {
      setSaveStatus((prev) => (prev === "saved" ? "saved" : "unsaved"));
    }
  }, [messages.length, loading]);

  const handleLoadSession = async (id: string) => {
    try {
      const session = user ? await loadChatSession(id) : loadLocalChatSession(id);
      if (!session) return;
      const loaded: DisplayMessage[] = session.messages.map((m: Record<string, unknown>) => ({
        id: crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        content: m.content as string,
        actions: m.actions as ChatAction[] | undefined,
      }));
      setMessages(loaded);
      setCurrentSessionId(id);
      setCurrentSessionTitle((session as { title?: string }).title || "");
      setSaveStatus("saved");
      pythonSessionIdRef.current = crypto.randomUUID();
      saveChatHistory(loaded);
    } catch { /* ignore */ }
  };

  const handleNewChat = () => {
    // Confirm if there are unsaved messages
    if (messages.length >= 3 && saveStatus === "unsaved") {
      if (!window.confirm("Start a new chat? Your current conversation has unsaved changes.")) {
        return;
      }
    }
    setMessages([]);
    setCurrentSessionId(null);
    setCurrentSessionTitle("");
    setSaveStatus("idle");
    pythonSessionIdRef.current = crypto.randomUUID();
    localStorage.removeItem("astro_chat_history");
    localStorage.removeItem("astro_chat_autosave_draft");
  };

  const handleRenameSession = async (newTitle: string) => {
    const trimmed = newTitle.trim();
    if (!trimmed || !currentSessionId) {
      setEditingTitle(false);
      return;
    }
    const prevTitle = currentSessionTitle;
    setCurrentSessionTitle(trimmed);
    setEditingTitle(false);
    try {
      if (user) {
        await renameChatSession(currentSessionId, trimmed);
      }
      refreshSessions();
    } catch {
      setCurrentSessionTitle(prevTitle);
      showToast("Failed to rename session", "error");
    }
  };

  const handleDeleteSession = async (id: string) => {
    try {
      if (user) {
        await deleteChatSession(id);
      } else {
        deleteLocalChatSession(id);
      }
      refreshSessions();
      if (currentSessionId === id) setCurrentSessionId(null);
    } catch { /* ignore */ }
  };

  const handleImportSession = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        const result = await importChatSession(data);
        const session = await loadChatSession(result.id);
        const loaded: DisplayMessage[] = session.messages.map((m: Record<string, unknown>) => ({
          id: crypto.randomUUID(),
          role: m.role as "user" | "assistant",
          content: m.content as string,
          actions: m.actions as ChatAction[] | undefined,
        }));
        setMessages(loaded);
        setCurrentSessionId(result.id);
        pythonSessionIdRef.current = crypto.randomUUID();
        saveChatHistory(loaded);
        refreshSessions();
        showToast(`Imported "${result.title}" (${result.message_count} messages)`, "success");
      } catch (err) {
        const detail = err instanceof Error ? err.message : "Import failed";
        showToast(`Import failed: ${detail}`, "error");
      }
    };
    input.click();
  };

  useEffect(() => {
    const pendingSessionId = localStorage.getItem("astro_chat_open_session");
    if (!pendingSessionId || !user) return;
    localStorage.removeItem("astro_chat_open_session");
    loadChatSession(pendingSessionId)
      .then((session) => {
        const loaded: DisplayMessage[] = session.messages.map((message: Record<string, unknown>) => ({
          id: crypto.randomUUID(),
          role: message.role as "user" | "assistant",
          content: message.content as string,
          actions: message.actions as ChatAction[] | undefined,
        }));
        setMessages(loaded);
        setCurrentSessionId(pendingSessionId);
        pythonSessionIdRef.current = crypto.randomUUID();
        saveChatHistory(loaded);
      })
      .catch(() => {
        showToast("Could not load the requested session", "error");
      });
  }, [showToast, user]);

  const handleOpenCollaboration = useCallback(async () => {
    if (!user) {
      showToast("Sign in to share sessions and manage snapshots", "info");
      return;
    }
    try {
      const sessionId = await ensurePersistedSession();
      setShareLoading(true);
      await loadCollaborationState(sessionId);
      setShareModalOpen(true);
      setShareUrl(null);
      setSnapshotDiff(null);
      setSnapshotCompareSelection([]);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to prepare the session", "error");
    } finally {
      setShareLoading(false);
    }
  }, [ensurePersistedSession, loadCollaborationState, showToast, user]);

  const handleCreateShare = useCallback(async () => {
    try {
      const sessionId = await ensurePersistedSession();
      setShareLoading(true);
      const created = await createSessionShare(
        sessionId,
        shareAccessLevel,
        Number.isFinite(shareExpiryHours) && shareExpiryHours > 0 ? shareExpiryHours : undefined,
      );
      setShareUrl(created.share_url);
      await loadCollaborationState(sessionId);
      if (navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(created.share_url).catch(() => {});
      }
      showToast("Share link created and copied to clipboard", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create share link", "error");
    } finally {
      setShareLoading(false);
    }
  }, [ensurePersistedSession, loadCollaborationState, shareAccessLevel, shareExpiryHours, showToast]);

  const handleRevokeShare = useCallback(async (shareId: string) => {
    if (!currentSessionId) return;
    try {
      await revokeSessionShare(currentSessionId, shareId);
      await loadCollaborationState(currentSessionId);
      showToast("Share link revoked", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to revoke share", "error");
    }
  }, [currentSessionId, loadCollaborationState, showToast]);

  const handleCreateSnapshot = useCallback(async () => {
    try {
      const sessionId = await ensurePersistedSession();
      const label = snapshotName.trim() || `Snapshot ${new Date().toLocaleString()}`;
      await createSessionSnapshot(sessionId, label);
      setSnapshotName("");
      await loadCollaborationState(sessionId);
      showToast("Snapshot created", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create snapshot", "error");
    }
  }, [ensurePersistedSession, loadCollaborationState, showToast, snapshotName]);

  const handleRestoreSnapshot = useCallback(async (snapshotId: string) => {
    if (!currentSessionId) return;
    try {
      await restoreSessionSnapshot(currentSessionId, snapshotId);
      const session = await loadChatSession(currentSessionId);
      const loaded: DisplayMessage[] = session.messages.map((m: Record<string, unknown>) => ({
        id: crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        content: m.content as string,
        actions: m.actions as ChatAction[] | undefined,
      }));
      setMessages(loaded);
      saveChatHistory(loaded);
      await loadCollaborationState(currentSessionId);
      showToast("Snapshot restored", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to restore snapshot", "error");
    }
  }, [currentSessionId, loadCollaborationState, showToast]);

  const handleCompareSnapshots = useCallback(async () => {
    if (!currentSessionId || snapshotCompareSelection.length !== 2) return;
    try {
      const diff = await diffSessionSnapshots(
        currentSessionId,
        snapshotCompareSelection[0],
        snapshotCompareSelection[1],
      );
      setSnapshotDiff(diff);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to compare snapshots", "error");
    }
  }, [currentSessionId, showToast, snapshotCompareSelection]);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading, scrollToBottom]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    saveChatHistory(messages);
  }, [messages]);

  const [dragOver, setDragOver] = useState(false);
  const pendingSendRef = useRef(false);

  // Auto-send when input is set by FITS drop
  useEffect(() => {
    if (pendingSendRef.current && input.trim()) {
      pendingSendRef.current = false;
      handleSend();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  const handleFitsDrop = async (files: FileList) => {
    for (const file of Array.from(files)) {
      if (!file.name.toLowerCase().match(/\.(fits|fit|fts)$/)) continue;
      try {
        const result = await uploadFITS(file);
        const msg = `I uploaded a FITS file: ${file.name} (stored at ${result.fits_path}). Please analyze this spectrum.`;
        pendingSendRef.current = true;
        setInput(msg);
      } catch {
        setInput(`I want to analyze a FITS file but upload failed for ${file.name}.`);
      }
      break;
    }
  };

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: DisplayMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };

    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInput("");
    setLoading(true);

    try {
      track("ai.message_sent", {
        prompt_length_chars: text.length,
        prompt_length_words: text.split(/\s+/).filter(Boolean).length,
        topic_keywords: text.split(/[\s,.;:!?，。；：！？]+/).filter(Boolean).slice(0, 8),
      });
      const chatHistory = buildMinimalChatHistory(updatedMessages);

      logOperation("chat", `Search: ${text}`);

      // Build context from user's current workspace state
      const wsContext: Record<string, unknown> = {};
      try {
        const lastSearch = localStorage.getItem("astro_last_search");
        if (lastSearch) wsContext.last_search = JSON.parse(lastSearch);
      } catch { /* ignore */ }
      try {
        const workspace = localStorage.getItem("astro_workspace_files");
        if (workspace) wsContext.workspace_files = JSON.parse(workspace);
      } catch { /* ignore */ }
      try {
        const pipeline = localStorage.getItem("pipeline_autosave");
        if (pipeline) {
          const p = JSON.parse(pipeline);
          wsContext.current_pipeline = {
            node_count: p.nodes?.length || 0,
            node_types: (p.nodes || []).map((n: Record<string, unknown>) => (n.data as Record<string, unknown>)?.nodeType || n.type),
            input_data: p.inputDataId,
          };
        }
      } catch { /* ignore */ }
      try {
        const lastAdql = localStorage.getItem("astro_last_adql");
        if (lastAdql) wsContext.last_adql = JSON.parse(lastAdql);
      } catch { /* ignore */ }
      try {
        const lastAdqlRows = localStorage.getItem("astro_last_adql_rows");
        if (lastAdqlRows) wsContext.last_adql_rows = JSON.parse(lastAdqlRows);
      } catch { /* ignore */ }
      try {
        const lastAdqlResultSets = localStorage.getItem("astro_adql_result_sets");
        if (lastAdqlResultSets) wsContext.last_adql_result_sets = JSON.parse(lastAdqlResultSets);
      } catch { /* ignore */ }
      wsContext.python_session_id = pythonSessionIdRef.current;
      wsContext.current_session_id = currentSessionId;

      const response = await sendChatMessage(chatHistory, wsContext);

      const assistantMsg: DisplayMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: response.reply,
        actions:
          response.actions.length > 0 ? response.actions : undefined,
        actionResults: new Map(),
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: unknown) {
      let errorDetail = "Unknown error";
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response?: { data?: { detail?: string }; status?: number } }).response;
        errorDetail = resp?.data?.detail || `Request failed (${resp?.status})`;
        // If auth error, prompt to fix key
        if (resp?.status === 401) {
          setHasKey(false);
        }
      } else if (err instanceof Error) {
        errorDetail = err.message;
      }
      if (errorDetail.includes("Could not reach the backend server")) {
        errorDetail = "The request payload was likely rejected before the app server handled it. This usually happens when the previous tool results made the second-round chat request too large.";
      }
      const errorMsg: DisplayMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Sorry, I encountered an error: ${errorDetail}`,
      };
      setMessages((prev) => [...prev, errorMsg]);
      track("error.ai_failed", {
        agent_name: "chat_assistant",
        backend: "anthropic",
        error_type: "chat_failed",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteAction = async (
    msgId: string,
    actionIndex: number,
    action: ChatAction
  ) => {
    const key = `${msgId}-${actionIndex}`;
    const started = performance.now();
    setExecutingActions((prev) => new Set(prev).add(key));

    try {
      logOperation("action", `Execute: ${action.action}`);
      const result = await executeChatAction(
        action as Record<string, unknown>
      );
      track("ai.tool_called", {
        tool_name: action.action,
        params_summary: JSON.stringify(action).slice(0, 300),
        success: true,
        duration_ms: Math.round(performance.now() - started),
        error_msg: null,
      });
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === msgId) {
            const newResults = new Map(m.actionResults);
            newResults.set(actionIndex, result);
            return { ...m, actionResults: newResults };
          }
          return m;
        })
      );
    } catch (err: unknown) {
      const errorDetail =
        err instanceof Error ? err.message : "Unknown error";
      track("ai.tool_called", {
        tool_name: action.action,
        params_summary: JSON.stringify(action).slice(0, 300),
        success: false,
        duration_ms: Math.round(performance.now() - started),
        error_msg: errorDetail,
      });
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === msgId) {
            const newResults = new Map(m.actionResults);
            newResults.set(actionIndex, {
              type: "error",
              message: errorDetail,
            });
            return { ...m, actionResults: newResults };
          }
          return m;
        })
      );
    } finally {
      setExecutingActions((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Filter sessions by search query
  const filteredSessions = sessions.filter((s) =>
    !sessionSearch.trim() || s.title.toLowerCase().includes(sessionSearch.toLowerCase())
  );

  return (
    <div className={`chat-page chat-page-with-sidebar${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      {toast && (
        <div className="chat-toast" style={{
          background: toast.tone === "error" ? "#ef4444" : toast.tone === "info" ? "#0ea5e9" : "#22c55e",
        }}>{toast.message}</div>
      )}

      {/* Persistent session sidebar (like Claude desktop) */}
      <aside className="chat-sidebar" aria-label="Chat sessions">
        <div className="chat-sidebar-header">
          <button
            type="button"
            className="chat-sidebar-new"
            onClick={handleNewChat}
            title="New chat"
          >
            <span style={{ fontSize: "1.1rem" }}>+</span> New chat
          </button>
          <button
            type="button"
            className="chat-sidebar-toggle"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? "»" : "«"}
          </button>
        </div>
        {!sidebarCollapsed && (
          <>
            <div className="chat-sidebar-search">
              <input
                type="search"
                placeholder="Search chats..."
                value={sessionSearch}
                onChange={(e) => setSessionSearch(e.target.value)}
                className="chat-sidebar-search-input"
              />
            </div>
            <div className="chat-sidebar-list" role="list">
              {sessions.length === 0 && (
                <p className="chat-sidebar-empty">
                  {user ? "No saved chats yet." : "Sign in to sync chats across devices."}
                </p>
              )}
              {filteredSessions.map((s) => (
                <div
                  key={s.id}
                  className={`chat-sidebar-item${s.id === currentSessionId ? " active" : ""}`}
                  role="listitem"
                >
                  <button
                    className="chat-sidebar-item-load"
                    onClick={() => handleLoadSession(s.id)}
                    title={s.title}
                  >
                    <span className="chat-sidebar-item-title">{s.title || "New Chat"}</span>
                    <span className="chat-sidebar-item-meta">
                      {s.message_count} msg · {new Date(s.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                  <button
                    className="chat-sidebar-item-delete"
                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                    title="Delete"
                    aria-label={`Delete ${s.title}`}
                  >
                    ×
                  </button>
                </div>
              ))}
              {filteredSessions.length === 0 && sessions.length > 0 && (
                <p className="chat-sidebar-empty">No chats match your search.</p>
              )}
            </div>
          </>
        )}
      </aside>

      <div className="chat-main">
      <div className="chat-header">
        <div className="chat-header-row">
          <div className="chat-header-title-block">
            {currentSessionId && !editingTitle ? (
              <h2
                className="chat-header-title-editable"
                onClick={() => { setTitleDraft(currentSessionTitle || "New Chat"); setEditingTitle(true); }}
                title="Click to rename"
              >
                {currentSessionTitle || "New Chat"}
                <span className="chat-header-rename-hint">✎</span>
              </h2>
            ) : editingTitle ? (
              <input
                autoFocus
                className="chat-header-title-input"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => handleRenameSession(titleDraft)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRenameSession(titleDraft);
                  else if (e.key === "Escape") setEditingTitle(false);
                }}
                maxLength={200}
              />
            ) : (
              <h2>{t("nav.ai_assistant")}</h2>
            )}
            <p className="chat-header-subtitle">
              {currentSessionId && saveStatus !== "idle" && (
                <span className={`chat-save-indicator chat-save-${saveStatus}`}>
                  {saveStatus === "saving" && "● Saving..."}
                  {saveStatus === "saved" && "● Saved"}
                  {saveStatus === "unsaved" && "● Unsaved changes"}
                </span>
              )}
              {!currentSessionId && "Ask about astronomical objects, build pipelines, or run ADQL queries"}
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            {messages.length > 0 && (
              <button type="button" className="btn-secondary btn-small" onClick={handleSaveSession}>
                {t("chat.save")}
              </button>
            )}
            {messages.length > 0 && (
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={shareLoading}
                onClick={() => { void handleOpenCollaboration(); }}
              >
                {shareLoading ? "Opening..." : "Share / Snapshots"}
              </button>
            )}
            {user && messages.length > 0 && (
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={paperLoading || paperGenerating}
                onClick={() => { void handleOpenPaperDraft(); }}
              >
                {paperLoading ? "Checking..." : paperGenerating ? "Generating..." : "Generate Paper Draft"}
              </button>
            )}
            <button type="button" className="btn-secondary btn-small" onClick={handleNewChat}>
              {t("chat.new_chat")}
            </button>
            <button type="button" className="btn-secondary btn-small" onClick={handleImportSession} title={t("chat.import_title")}>
              {t("chat.import")}
            </button>
            {messages.length > 0 && (
              <>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.markdown}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "markdown",
                    "Markdown",
                    "ai_research_chat.md",
                    () => exportChatMarkdown(data),
                  );
                }}
              >
                {exporting.markdown ? "Exporting..." : t("common.export")}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.notebook}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "notebook",
                    "Notebook",
                    "ai_research_session.ipynb",
                    () => exportChatNotebook(data),
                  );
                }}
              >
                {exporting.notebook ? "Exporting..." : "Notebook"}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.latex}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "latex",
                    "LaTeX",
                    "astro_report.tex",
                    () => exportChatLatex(data),
                    {
                      fallback: () => new Blob([generateLatexFallback(messages)], { type: "application/x-tex" }),
                    },
                  );
                }}
              >
                {exporting.latex ? "Exporting..." : "LaTeX"}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.bibtex}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "bibtex",
                    "BibTeX",
                    "references.bib",
                    () => exportChatBibTeX(data),
                    { emptyMessage: "No references found to export", skipDownloadWhenEmpty: true },
                  );
                }}
              >
                {exporting.bibtex ? "Exporting..." : "BibTeX"}
              </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Old modal session panel removed — replaced by persistent sidebar */}

      {shareModalOpen && (
        <div className="viz-overlay" onClick={() => setShareModalOpen(false)}>
          <div
            className="viz-overlay-content"
            style={{ maxWidth: 920, width: "min(920px, 92vw)", maxHeight: "88vh", overflow: "auto" }}
            onClick={(event) => event.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0 }}>Share And Snapshots</h3>
                <div style={{ color: "var(--color-text-secondary)", marginTop: 4 }}>
                  Manage share links, forks, and point-in-time session restores.
                </div>
              </div>
              <button className="btn-secondary btn-small" onClick={() => setShareModalOpen(false)}>Close</button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}>
              <div style={{ padding: 14, borderRadius: 12, background: "rgba(15,23,42,0.04)" }}>
                <h4 style={{ marginTop: 0 }}>Create Share Link</h4>
                <div style={{ display: "grid", gap: 10 }}>
                  <label>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Access Level</div>
                    <select
                      className="search-input"
                      value={shareAccessLevel}
                      onChange={(event) => setShareAccessLevel(event.target.value as ShareAccessLevel)}
                    >
                      <option value="view">View</option>
                      <option value="fork">Fork</option>
                      <option value="comment">Comment</option>
                    </select>
                  </label>
                  <label>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Expiry (hours)</div>
                    <input
                      className="search-input"
                      type="number"
                      min={1}
                      value={shareExpiryHours}
                      onChange={(event) => setShareExpiryHours(Number(event.target.value) || 0)}
                    />
                  </label>
                  <button className="btn-primary" disabled={shareLoading} onClick={() => { void handleCreateShare(); }}>
                    {shareLoading ? "Creating..." : "Create Share Link"}
                  </button>
                  {shareUrl && (
                    <div className="fits-hint" style={{ wordBreak: "break-all" }}>
                      Latest link: {shareUrl}
                    </div>
                  )}
                </div>

                <div style={{ marginTop: 18 }}>
                  <h4>Active Shares</h4>
                  {sessionShares.length === 0 ? (
                    <div className="fits-hint">No active share links yet.</div>
                  ) : (
                    sessionShares.map((share) => (
                      <div key={share.id} className="note-card" style={{ marginBottom: 10 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                          <div>
                            <strong>{share.access_level}</strong>
                            <div style={{ color: "var(--color-text-secondary)", fontSize: "0.85rem", marginTop: 4 }}>
                              {share.expires_at ? `Expires ${new Date(share.expires_at).toLocaleString()}` : "No expiry"}
                            </div>
                            <div className="mono" style={{ marginTop: 6 }}>.../{share.share_token}</div>
                          </div>
                          <button className="btn-secondary btn-small" onClick={() => { void handleRevokeShare(share.id); }}>
                            Revoke
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div style={{ padding: 14, borderRadius: 12, background: "rgba(15,23,42,0.04)" }}>
                <h4 style={{ marginTop: 0 }}>Version Snapshots</h4>
                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  <input
                    className="search-input"
                    value={snapshotName}
                    onChange={(event) => setSnapshotName(event.target.value)}
                    placeholder='e.g. "before extinction correction"'
                  />
                  <button className="btn-secondary" onClick={() => { void handleCreateSnapshot(); }}>
                    Snapshot
                  </button>
                </div>
                {sessionSnapshots.length === 0 ? (
                  <div className="fits-hint">No snapshots yet.</div>
                ) : (
                  <div style={{ display: "grid", gap: 10 }}>
                    {sessionSnapshots.map((snapshot) => {
                      const selected = snapshotCompareSelection.includes(snapshot.id);
                      return (
                        <div key={snapshot.id} className="note-card">
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                            <div>
                              <strong>{snapshot.name}</strong>
                              <div className="note-date" style={{ marginTop: 4 }}>
                                {snapshot.created_at ? new Date(snapshot.created_at).toLocaleString() : "Unknown time"}
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 6 }}>
                              <button
                                className={`btn-secondary btn-small${selected ? " active" : ""}`}
                                onClick={() => {
                                  setSnapshotCompareSelection((prev) => {
                                    if (prev.includes(snapshot.id)) return prev.filter((id) => id !== snapshot.id);
                                    if (prev.length === 2) return [prev[1], snapshot.id];
                                    return [...prev, snapshot.id];
                                  });
                                }}
                              >
                                Compare
                              </button>
                              <button className="btn-secondary btn-small" onClick={() => { void handleRestoreSnapshot(snapshot.id); }}>
                                Restore
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <button
                    className="btn-secondary btn-small"
                    disabled={snapshotCompareSelection.length !== 2}
                    onClick={() => { void handleCompareSnapshots(); }}
                  >
                    Compare Selected
                  </button>
                </div>
                {snapshotDiff && (
                  <div className="fits-hint" style={{ marginTop: 12 }}>
                    Title changed: {snapshotDiff.updated_title ? "yes" : "no"} · Added messages: {snapshotDiff.added_messages} · Removed messages: {snapshotDiff.removed_messages}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="chat-messages">
        {pageError && <div className="error-banner">{pageError}</div>}
        {!hasKey && (
          <ApiKeyPrompt onSaved={() => setHasKey(true)} />
        )}
        {hasKey && messages.length === 0 && !loading && (
          <div className="chat-empty">
            <div className="chat-empty-icon">&#x2728;</div>
            <h3>How can I help with your research?</h3>
            <div className="chat-templates-grid">
              {[
                { icon: "\u{1F31F}", title: t("template.hr_diagram"), prompt: t("template.hr_desc"), difficulty: "beginner" as const },
                { icon: "\u{1F30C}", title: t("template.galaxy_redshift"), prompt: t("template.galaxy_desc"), difficulty: "beginner" as const },
                { icon: "\u2B50", title: t("template.variable_star"), prompt: t("template.variable_desc"), difficulty: "intermediate" as const },
                { icon: "\u{1F52D}", title: t("template.spectral"), prompt: t("template.spectral_desc"), difficulty: "intermediate" as const },
                { icon: "\u{1F4CA}", title: t("template.highz"), prompt: t("template.highz_desc"), difficulty: "advanced" as const },
                { icon: "\u{1F4AB}", title: t("template.supernova"), prompt: t("template.supernova_desc"), difficulty: "advanced" as const },
              ].map((tmpl, i) => (
                <button
                  key={i}
                  className="chat-template-card"
                  onClick={() => setInput(tmpl.prompt)}
                >
                  <div className="chat-template-header">
                    <span className="chat-template-icon">{tmpl.icon}</span>
                    <span className={`chat-template-badge badge-${tmpl.difficulty}`}>
                      {t(`template.difficulty.${tmpl.difficulty}`)}
                    </span>
                  </div>
                  <div className="chat-template-title">{tmpl.title}</div>
                  <div className="chat-template-desc">{tmpl.prompt}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`chat-message ${msg.role}`}>
            <div className="chat-message-avatar">
              {msg.role === "user" ? "U" : "AI"}
            </div>
            <div className="chat-message-body">
              <div className="chat-message-content">
                {msg.role === "assistant" ? (
                  <MarkdownText content={msg.content} />
                ) : (
                  msg.content.split("\n").map((line, i) => (
                    <p key={i}>{line || "\u00A0"}</p>
                  ))
                )}
              </div>
              {msg.actions && msg.actions.length > 0 && (
                <div className="chat-actions-list">
                  <span className="chat-actions-label">
                    Suggested actions:
                  </span>
                  {msg.actions.map((action, idx) => (
                    <ActionCard
                      key={idx}
                      action={action}
                      index={idx}
                      result={msg.actionResults?.get(idx)}
                      executing={executingActions.has(
                        `${msg.id}-${idx}`
                      )}
                      onExecute={(i, a) =>
                        handleExecuteAction(msg.id, i, a)
                      }
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="chat-message assistant">
            <div className="chat-message-avatar">AI</div>
            <div className="chat-message-body">
              <div className="chat-loading">
                <span className="chat-loading-dot" />
                <span className="chat-loading-dot" />
                <span className="chat-loading-dot" />
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {paperModalOpen && (
        <div className="viz-overlay" onClick={() => setPaperModalOpen(false)}>
          <div
            className="viz-overlay-content"
            style={{ maxWidth: 980, width: "min(980px, 92vw)", maxHeight: "88vh", overflow: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0 }}>Paper Draft</h3>
                <div style={{ color: "var(--color-text-secondary)", fontSize: "0.9rem", marginTop: 4 }}>
                  Validate the session, generate a draft, edit sections, and download LaTeX/BibTeX.
                </div>
              </div>
              <button className="btn-secondary btn-small" onClick={() => setPaperModalOpen(false)}>Close</button>
            </div>

            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontWeight: 600 }}>Journal</span>
                <select
                  value={paperFormat}
                  onChange={(e) => setPaperFormat(e.target.value as JournalFormat)}
                  className="search-input"
                  style={{ width: 160 }}
                >
                  <option value="aastex">AASTeX</option>
                  <option value="mnras">MNRAS</option>
                  <option value="aa">A&amp;A</option>
                </select>
              </label>
              <button
                className="btn-primary btn-small"
                disabled={paperLoading || paperGenerating || !paperSessionId}
                onClick={() => { void handleGeneratePaper(paperValidation?.overall_status === "FAIL"); }}
              >
                {paperGenerating ? "Generating..." : paperValidation?.overall_status === "FAIL" ? "Generate Anyway" : "Generate Draft"}
              </button>
              {paperDraft && (
                <>
                  <button
                    className="btn-secondary btn-small"
                    disabled={paperSaving || !paperEditorJson}
                    onClick={() => { void handleSavePaperDraft(); }}
                  >
                    {paperSaving ? "Saving..." : "Save Changes"}
                  </button>
                  <button
                    className="btn-secondary btn-small"
                    onClick={() => {
                      downloadBlob(
                        new Blob([paperDraft.latex_source], { type: "application/x-tex" }),
                        `${(paperDraft.paper_json.title as string || "standard_astro_draft").replace(/\s+/g, "_")}.tex`,
                      );
                    }}
                  >
                    Download LaTeX
                  </button>
                  <button
                    className="btn-secondary btn-small"
                    onClick={() => {
                      downloadBlob(
                        new Blob([paperDraft.bibtex], { type: "application/x-bibtex" }),
                        `${(paperDraft.paper_json.title as string || "standard_astro_references").replace(/\s+/g, "_")}.bib`,
                      );
                    }}
                  >
                    Download BibTeX
                  </button>
                </>
              )}
            </div>

            {paperLoading && (
              <div className="fits-loading" style={{ marginBottom: 16 }}>Inspecting session and running validation...</div>
            )}

            {paperValidation && (
              <div style={{ marginBottom: 18, padding: 14, borderRadius: 10, background: "rgba(15,23,42,0.05)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 10 }}>
                  <strong>
                    Validation: {paperValidation.overall_status} ({Math.round(paperValidation.score * 100)}%)
                  </strong>
                  <span style={{
                    padding: "4px 8px",
                    borderRadius: 999,
                    background:
                      paperValidation.overall_status === "FAIL" ? "#fee2e2" :
                      paperValidation.overall_status === "WARN" ? "#fef3c7" : "#dcfce7",
                    color:
                      paperValidation.overall_status === "FAIL" ? "#b91c1c" :
                      paperValidation.overall_status === "WARN" ? "#a16207" : "#166534",
                    fontWeight: 700,
                    fontSize: "0.75rem",
                  }}>
                    {paperValidation.overall_status}
                  </span>
                </div>
                <div style={{ display: "grid", gap: 10 }}>
                  {paperValidation.checks.map((check) => (
                    <div key={check.name} style={{ border: "1px solid rgba(15,23,42,0.08)", borderRadius: 8, padding: 10, background: "#fff" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                        <strong>{check.name.replace(/_/g, " ")}</strong>
                        <span style={{
                          fontSize: "0.72rem",
                          fontWeight: 700,
                          color: check.status === "FAIL" ? "#b91c1c" : check.status === "WARN" ? "#a16207" : "#166534",
                        }}>
                          {check.status}
                        </span>
                      </div>
                      <div style={{ fontSize: "0.88rem", marginTop: 6 }}>{check.details}</div>
                      <div style={{ fontSize: "0.82rem", color: "var(--color-text-secondary)", marginTop: 4 }}>
                        Recommendation: {check.recommendation}
                      </div>
                      <div style={{ marginTop: 8 }}>
                        <button
                          className="btn-secondary btn-small"
                          onClick={() => {
                            setInput(`Help me address this analysis validation issue in my current session: ${check.recommendation}`);
                            setPaperModalOpen(false);
                          }}
                        >
                          Send Fix Prompt to AI
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {paperDraft && paperEditorJson && (
              <>
                <div style={{ marginBottom: 12 }}>
                  <input
                    className="search-input"
                    style={{ width: "100%", fontSize: "1.05rem", fontWeight: 700 }}
                    value={String(paperEditorJson.title || "")}
                    onChange={(e) => setPaperEditorJson({ ...paperEditorJson, title: e.target.value })}
                    placeholder="Paper title"
                  />
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                  {[
                    ["abstract", "Abstract"],
                    ["introduction", "Introduction"],
                    ["data_sources", "Data"],
                    ["analysis_methods", "Methods"],
                    ["results", "Results"],
                    ["discussion", "Discussion"],
                    ["conclusions", "Conclusions"],
                    ["acknowledgments", "Acknowledgments"],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      className={`btn-small ${paperTab === key ? "btn-primary" : "btn-secondary"}`}
                      onClick={() => setPaperTab(key as PaperTab)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div style={{ marginBottom: 10 }}>
                  <textarea
                    className="chat-input"
                    style={{ minHeight: 260, width: "100%" }}
                    value={getPaperSectionText(paperEditorJson, paperTab)}
                    onChange={(e) => setPaperEditorJson(setPaperSectionText(paperEditorJson, paperTab, e.target.value))}
                  />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div style={{ color: "var(--color-text-secondary)", fontSize: "0.85rem" }}>
                    Figures: {Array.isArray(((paperEditorJson.results as Record<string, unknown> | undefined)?.figures))
                      ? ((((paperEditorJson.results as Record<string, unknown>).figures as unknown[]) || []).length)
                      : 0}
                    {" · "}
                    Tables: {Array.isArray(((paperEditorJson.results as Record<string, unknown> | undefined)?.tables))
                      ? ((((paperEditorJson.results as Record<string, unknown>).tables as unknown[]) || []).length)
                      : 0}
                  </div>
                  <button
                    className="btn-secondary btn-small"
                    disabled={paperGenerating}
                    onClick={() => { void handleRegeneratePaperSection(); }}
                  >
                    {paperGenerating ? "Regenerating..." : "Regenerate Section"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {messages.length > 0 && !loading && (
        <NextStepsPanel onSend={(msg) => {
          pendingSendRef.current = true;
          setInput(msg);
        }} />
      )}

      <div
        className={`chat-input-area${dragOver ? " drag-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFitsDrop(e.dataTransfer.files); }}
      >
        <div className="chat-input-wrapper">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={dragOver ? "Drop FITS file here..." : t("chat.placeholder")}
            rows={1}
            disabled={loading}
            aria-label="Message input"
          />
          <button
            className="btn-chat-send"
            onClick={handleSend}
            disabled={!input.trim() || loading}
            title="Send message (Enter)"
            aria-label="Send message"
          >
            &#x2191;
          </button>
        </div>
        <span className="chat-input-hint">
          {t("chat.hint")}
        </span>
      </div>
      </div>
    </div>
  );
}
