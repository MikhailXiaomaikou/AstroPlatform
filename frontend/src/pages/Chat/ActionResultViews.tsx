// Manually-executed action result views: search result table (with
// stats/citation modals) and the typed ActionResult switch.
// Moved verbatim from ChatPage.tsx (behavior-preserving split).
import { useState, useMemo, useEffect, lazy, Suspense } from "react";
import { searchADS, getBibTeX, logOperation, type ADSReference } from "../../api/client";
import ErrorBoundary from "../../components/ErrorBoundary";

export const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

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

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Q3: async ADS fetch + reset loading/error on objectName change.
    let cancelled = false;
    setLoading(true);
    setError(null);
    searchADS(objectName)
      .then((data) => { if (!cancelled) setRefs(data); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to query ADS"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [objectName]);
  /* eslint-enable react-hooks/set-state-in-effect */

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

export function ActionResult({ result }: { result: Record<string, unknown> }) {
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
