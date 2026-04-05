import { useState, useMemo, useRef, useEffect, useCallback, lazy, Suspense } from "react";
import {
  sendChatMessage,
  executeChatAction,
  getStoredApiKey,
  searchADS,
  getBibTeX,
  logOperation,
  type ChatMessage,
  type ChatAction,
  type ADSReference,
} from "../../api/client";
import MarkdownText from "../../components/chat/MarkdownText";
const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: Map<number, Record<string, unknown>>;
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
        <Suspense fallback={<div className="fits-loading">Loading plot...</div>}>
          <PlotBuilder
            initialData={action.data as Record<string, unknown>}
            initialChartType={action.chart_type as string}
          />
        </Suspense>
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
    searchADS(objectName)
      .then(setRefs)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to query ADS"))
      .finally(() => setLoading(false));
  }, [objectName]);

  async function handleCopyBib(bibcode: string) {
    try {
      const bib = await getBibTeX(bibcode);
      await navigator.clipboard.writeText(bib);
      setCopiedBib(bibcode);
      setTimeout(() => setCopiedBib(null), 2000);
    } catch {
      // fallback: ignore
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
            <Suspense fallback={<div className="fits-loading">Loading visualization...</div>}>
              <PlotBuilder
                initialData={vizData}
                initialChartType="sky_coverage"
                onClose={() => setVizData(null)}
              />
            </Suspense>
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
        {refs.slice(0, 5).map((r, i) => (
          <div key={i} style={{ fontSize: "0.75rem", padding: "2px 0" }}>
            <span style={{ color: "var(--color-text-tertiary)" }}>{String(r.year)}</span>{" "}
            {String(r.title).slice(0, 80)}{String(r.title).length > 80 ? "..." : ""}
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

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    try {
      const keys = JSON.parse(localStorage.getItem("astro_api_keys") || "{}");
      keys.anthropic = key;
      localStorage.setItem("astro_api_keys", JSON.stringify(keys));
    } catch {
      localStorage.setItem("astro_api_keys", JSON.stringify({ anthropic: key }));
    }
    onSaved();
  }

  return (
    <div className="chat-apikey-prompt">
      <h3>Configure API Key</h3>
      <p>To use the AI assistant, enter your Anthropic API key.</p>
      <p className="chat-apikey-hint">
        Get one at{" "}
        <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">
          console.anthropic.com
        </a>
        {" "}(new accounts get $5 free credit)
      </p>
      <form className="chat-apikey-form" onSubmit={handleSave}>
        <input
          type="text"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder="sk-ant-..."
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

export default function ChatPage() {
  const [hasKey, setHasKey] = useState(() => !!getStoredApiKey("anthropic"));
  const [messages, setMessages] = useState<DisplayMessage[]>(loadChatHistory);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [executingActions, setExecutingActions] = useState<Set<string>>(
    new Set()
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
      const chatHistory: ChatMessage[] = updatedMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      logOperation("chat", `Search: ${text}`);
      const response = await sendChatMessage(chatHistory);

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
      const errorMsg: DisplayMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Sorry, I encountered an error: ${errorDetail}`,
      };
      setMessages((prev) => [...prev, errorMsg]);
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
    setExecutingActions((prev) => new Set(prev).add(key));

    try {
      logOperation("action", `Execute: ${action.action}`);
      const result = await executeChatAction(
        action as Record<string, unknown>
      );
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

  return (
    <div className="chat-page">
      <div className="chat-header">
        <div className="chat-header-row">
          <div>
            <h2>AI Research Assistant</h2>
            <p>
              Ask about astronomical objects, build pipelines, or run ADQL queries
            </p>
          </div>
          {messages.length > 0 && (
            <button
              className="btn-secondary btn-small"
              onClick={() => {
                setMessages([]);
                localStorage.removeItem("astro_chat_history");
              }}
            >
              Clear Chat
            </button>
          )}
        </div>
      </div>

      <div className="chat-messages">
        {!hasKey && (
          <ApiKeyPrompt onSaved={() => setHasKey(true)} />
        )}
        {hasKey && messages.length === 0 && !loading && (
          <div className="chat-empty">
            <div className="chat-empty-icon">&#x2728;</div>
            <h3>How can I help with your research?</h3>
            <div className="chat-suggestions">
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput("Search for quasars near RA=180, Dec=45")
                }
              >
                Search for quasars near RA=180, Dec=45
              </button>
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput(
                    "Write an ADQL query to find bright stars in Gaia DR3"
                  )
                }
              >
                Write an ADQL query for bright Gaia stars
              </button>
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput("How do I denoise a spectrum and fit emission lines?")
                }
              >
                How to denoise a spectrum and fit lines?
              </button>
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput("Explain the difference between ICRS and Galactic coordinates")
                }
              >
                Explain ICRS vs Galactic coordinates
              </button>
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

      <div className="chat-input-area">
        <div className="chat-input-wrapper">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about astronomical data, pipelines, or ADQL queries..."
            rows={1}
            disabled={loading}
          />
          <button
            className="btn-chat-send"
            onClick={handleSend}
            disabled={!input.trim() || loading}
            title="Send message (Enter)"
          >
            &#x2191;
          </button>
        </div>
        <span className="chat-input-hint">
          Press Enter to send, Shift+Enter for new line
        </span>
      </div>
    </div>
  );
}
