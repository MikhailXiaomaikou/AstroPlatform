import { useState, useEffect, useMemo, lazy, Suspense } from "react";
import { useNavigate } from "react-router-dom";
import { adqlQuery, listADQLServices, logOperation, exportSearchNotebook } from "../../api/client";
import type { ADQLResult } from "../../api/client";
import { useTracking } from "../../hooks/useTracking";

const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

/* ── Templates (per service) ── */
const TEMPLATES: Record<string, Array<{ label: string; tip: string; q: string }>> = {
  gaia: [
    { label: "Photometry", tip: "G/BP/RP + parallax + PM. ~100% for G<20.",
      q: "SELECT TOP 200 source_id, ra, dec, phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, bp_rp, parallax, pmra, pmdec\nFROM gaiadr3.gaia_source\nWHERE phot_g_mean_mag < 15 AND parallax IS NOT NULL\nORDER BY phot_g_mean_mag" },
    { label: "Stellar params", tip: "Teff, logg, [M/H]. ~40% complete.",
      q: "SELECT TOP 200 source_id, ra, dec, phot_g_mean_mag, bp_rp, parallax, teff_gspphot, logg_gspphot, mh_gspphot\nFROM gaiadr3.gaia_source\nWHERE teff_gspphot IS NOT NULL AND phot_g_mean_mag < 16\nORDER BY phot_g_mean_mag" },
    { label: "Radial velocity", tip: "RV only ~5% of sources (G<14).",
      q: "SELECT TOP 200 source_id, ra, dec, phot_g_mean_mag, parallax, radial_velocity, radial_velocity_error\nFROM gaiadr3.gaia_source\nWHERE radial_velocity IS NOT NULL\nORDER BY phot_g_mean_mag" },
    { label: "Nearby (plx>50)", tip: "Stars within ~20 pc.",
      q: "SELECT TOP 200 source_id, ra, dec, phot_g_mean_mag, bp_rp, parallax, parallax_error, pmra, pmdec, ruwe\nFROM gaiadr3.gaia_source\nWHERE parallax > 50 AND ruwe < 1.4\nORDER BY parallax DESC" },
  ],
  simbad: [
    { label: "z>4 galaxies", tip: "Galaxies with measured redshift.",
      q: "SELECT TOP 200 main_id, ra, dec, otype, rvz_redshift, morph_type\nFROM basic\nWHERE otype = 'G' AND rvz_redshift > 4 AND rvz_redshift IS NOT NULL\nORDER BY rvz_redshift DESC" },
    { label: "QSOs", tip: "Quasars by redshift.",
      q: "SELECT TOP 200 main_id, ra, dec, rvz_redshift, sp_type\nFROM basic\nWHERE otype = 'QSO' AND rvz_redshift IS NOT NULL\nORDER BY rvz_redshift DESC" },
    { label: "Seyfert galaxies", tip: "Active galaxies.",
      q: "SELECT TOP 200 main_id, ra, dec, otype, rvz_redshift, sp_type\nFROM basic\nWHERE otype = 'Sy1' OR otype = 'Sy2'\nORDER BY rvz_redshift DESC" },
  ],
  vizier: [
    { label: "2MASS catalog", tip: "J/H/K photometry near Orion.",
      q: "SELECT TOP 50 RAJ2000, DEJ2000, Jmag, Hmag, Kmag\nFROM \"II/246/out\"\nWHERE 1=CONTAINS(POINT('ICRS', RAJ2000, DEJ2000), CIRCLE('ICRS', 83.633, -5.375, 0.1))" },
    { label: "SDSS DR16 photo", tip: "Optical photometry.",
      q: "SELECT TOP 50 ra, dec, psfMag_u, psfMag_g, psfMag_r, psfMag_i, psfMag_z\nFROM \"V/154/sdss16\"\nWHERE 1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', 180.0, 0.0, 0.1))" },
  ],
  cadc: [
    { label: "JWST observations", tip: "James Webb archival data.",
      q: "SELECT TOP 50 observationID, target_name, instrument_name, dataProductType, calibrationLevel\nFROM caom2.Observation\nWHERE collection = 'JWST'\nORDER BY observationID DESC" },
    { label: "By target", tip: "Search CADC by target name.",
      q: "SELECT TOP 50 observationID, collection, target_name, instrument_name, dataProductType\nFROM caom2.Observation\nWHERE target_name = 'M31'" },
  ],
};

const DEFAULTS: Record<string, string> = {
  gaia: "SELECT TOP 100 source_id, ra, dec, phot_g_mean_mag, parallax\nFROM gaiadr3.gaia_source\nWHERE phot_g_mean_mag < 10\nORDER BY phot_g_mean_mag",
  vizier: 'SELECT TOP 50 *\nFROM "II/246/out"\nWHERE 1=CONTAINS(POINT(\'ICRS\', RAJ2000, DEJ2000), CIRCLE(\'ICRS\', 83.633, 22.014, 0.05))',
  cadc: "SELECT TOP 50 *\nFROM caom2.Observation\nWHERE target_name = 'M31'",
  simbad: "SELECT TOP 100 main_id, ra, dec, otype, rvz_redshift\nFROM basic\nWHERE otype = 'G' AND rvz_redshift IS NOT NULL\nORDER BY rvz_redshift DESC",
};

/* ── History ── */
function getHistory(): string[] {
  try { return JSON.parse(localStorage.getItem("astro_adql_history") || "[]"); } catch { return []; }
}
function addHistory(q: string) {
  const h = getHistory().filter((x) => x !== q);
  h.unshift(q);
  localStorage.setItem("astro_adql_history", JSON.stringify(h.slice(0, 10)));
}

/* ── Format cell value ── */
function fmtCell(v: number | string | null): string {
  if (v == null) return "—";
  if (typeof v === "number") {
    if (Number.isInteger(v) || Math.abs(v) > 1e12) return String(v);
    return v.toFixed(4);
  }
  return String(v);
}

function extractRows(result: ADQLResult, limit = 1000): Array<Record<string, unknown>> {
  return Array.from({ length: Math.min(result.row_count, limit) }).map((_, i) => {
    const row: Record<string, unknown> = {};
    for (const col of result.columns) row[col] = result.data[col]?.[i];
    return row;
  });
}

function preferredChartType(_result: ADQLResult | null): string {
  return "scatter_custom";
}

const PAGE_SIZE = 25;

/* ── Component ── */
export default function ADQLPage() {
  const navigate = useNavigate();
  const { track } = useTracking();
  const [services, setServices] = useState<Array<{ id: string; name: string }>>([]);
  const [svc, setSvc] = useState("gaia");
  const [query, setQuery] = useState(DEFAULTS.gaia);
  const [result, setResult] = useState<ADQLResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState(getHistory);
  const [page, setPage] = useState(0);
  const [showViz, setShowViz] = useState(false);
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortAsc, setSortAsc] = useState(true);
  const [exportMsg, setExportMsg] = useState<string | null>(null);

  useEffect(() => { listADQLServices().then(setServices).catch(() => {}); }, []);

  function switchSvc(id: string) {
    setSvc(id);
    if (DEFAULTS[id] && query === DEFAULTS[svc]) setQuery(DEFAULTS[id]);
  }

  async function run() {
    const started = performance.now();
    setLoading(true); setError(null); setResult(null); setPage(0); setSortCol(null); setSortAsc(true);
    try {
      const res = await adqlQuery(query, svc);
      setResult(res);
      track("search.adql", {
        query_text: query,
        database: svc,
        result_count: res.row_count,
        success: true,
        error_msg: null,
        query_time_ms: Math.round(performance.now() - started),
      });
      const rows = extractRows(res);
      try {
        localStorage.setItem("astro_last_adql", JSON.stringify({
          service: svc,
          query,
          row_count: res.row_count,
          columns: res.columns,
          sample: rows.slice(0, 5),
        }));
        localStorage.setItem("astro_last_adql_rows", JSON.stringify(rows));
      } catch { /* ignore */ }
      addHistory(query); setHistory(getHistory());
      logOperation("adql", `${svc}: ${query.slice(0, 80)}`);
    } catch (err: unknown) {
      const detail =
        err && typeof err === "object" && "response" in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || "Query failed")
          : (err instanceof Error ? err.message : "Query failed");
      track("search.adql", {
        query_text: query,
        database: svc,
        result_count: 0,
        success: false,
        error_msg: detail,
        query_time_ms: Math.round(performance.now() - started),
      });
      track("error.query_failed", {
        database: svc,
        error_type: "adql_failed",
        error_msg: detail,
      });
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response?: { data?: { detail?: string } } }).response;
        setError(resp?.data?.detail || "Query failed");
      } else {
        setError(err instanceof Error ? err.message : "Query failed");
      }
    } finally { setLoading(false); }
  }

  function downloadCSV() {
    if (!result) return;
    const header = result.columns.join(",");
    const rows = Array.from({ length: result.row_count }).map((_, i) =>
      result.columns.map((c) => {
        const v = result.data[c]?.[i];
        if (v == null) return "";
        const s = String(v);
        return s.includes(",") ? `"${s.replace(/"/g, '""')}"` : s;
      }).join(",")
    );
    const blob = new Blob([header + "\n" + rows.join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `adql_${svc}_${result.row_count}rows.csv`; a.click();
    logOperation("export", `ADQL CSV export: ${result.row_count} rows from ${svc}`);
    track("export.csv", { row_count: result.row_count, column_count: result.columns.length });
  }

  function handleSort(col: string) {
    if (sortCol === col) {
      setSortAsc(!sortAsc);
    } else {
      setSortCol(col);
      setSortAsc(true);
    }
    setPage(0);
  }

  function sortIndicator(col: string) {
    if (sortCol !== col) return null;
    return <span className="sort-indicator">{sortAsc ? " \u25B2" : " \u25BC"}</span>;
  }

  const sortedIndices = useMemo(() => {
    if (!result) return [];
    const indices = Array.from({ length: result.row_count }, (_, i) => i);
    if (!sortCol) return indices;
    const colData = result.data[sortCol];
    if (!colData) return indices;
    indices.sort((a, b) => {
      const va = colData[a];
      const vb = colData[b];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return sortAsc ? va - vb : vb - va;
      const sa = String(va), sb = String(vb);
      const cmp = sa.localeCompare(sb);
      return sortAsc ? cmp : -cmp;
    });
    return indices;
  }, [result, sortCol, sortAsc]);

  const totalPages = result ? Math.ceil(Math.min(result.row_count, 500) / PAGE_SIZE) : 0;
  const visibleRows = useMemo(() => {
    if (!sortedIndices.length) return [];
    const start = page * PAGE_SIZE;
    return sortedIndices.slice(start, start + PAGE_SIZE);
  }, [sortedIndices, page]);

  return (
    <div className="adql-page">
      <h1>ADQL Query</h1>

      {/* Service selector */}
      <div className="adql-controls">
        <div className="segmented-control">
          {services.map((s) => (
            <button key={s.id} className={`segment-btn${svc === s.id ? " active" : ""}`}
              onClick={() => switchSvc(s.id)}>{s.name}</button>
          ))}
        </div>
      </div>

      {/* Templates — filtered by current service */}
      {(TEMPLATES[svc] || []).length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "8px 0" }}>
          {(TEMPLATES[svc] || []).map((t) => (
            <button key={t.label} className="btn-secondary btn-small" title={t.tip}
              style={{ fontSize: "0.72rem" }}
              onClick={() => setQuery(t.q)}>{t.label}</button>
          ))}
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", margin: "4px 0 8px" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)", lineHeight: "24px" }}>Recent:</span>
          {history.map((q, i) => (
            <button key={i} className="btn-secondary btn-small"
              style={{ fontSize: "0.65rem", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              onClick={() => setQuery(q)} title={q}>
              {q.slice(0, 40)}{q.length > 40 ? "…" : ""}
            </button>
          ))}
        </div>
      )}

      {/* Editor */}
      <textarea className="adql-textarea" value={query} onChange={(e) => setQuery(e.target.value)}
        rows={6} spellCheck={false} />

      <div style={{ display: "flex", gap: 8, margin: "8px 0" }}>
        <button className="btn-primary" onClick={run} disabled={loading || !query.trim()}>
          {loading ? "Running…" : "Run Query"}
        </button>
        {result && (
          <>
            <button className="btn-secondary" onClick={downloadCSV}>
              Download CSV ({result.row_count} rows)
            </button>
            <button className="btn-secondary" onClick={async () => {
              try {
                setExportMsg("Generating notebook…");
                const rows = extractRows(result, 200);
                const blob = await exportSearchNotebook(query, rows);
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a"); a.href = url;
                a.download = `adql_${result.row_count}_rows.ipynb`; a.click();
                URL.revokeObjectURL(url);
                setExportMsg("Notebook exported successfully");
                track("export.notebook", { cell_count: 3 + rows.length });
                setTimeout(() => setExportMsg(null), 2500);
              } catch (e) {
                setError(e instanceof Error ? e.message : "Notebook export failed");
                setExportMsg(null);
              }
            }}>
              Jupyter Notebook
            </button>
            <button className="btn-secondary" onClick={() => {
              localStorage.setItem(
                "astro_chat_draft",
                `Use get_adql_results() to analyze the ADQL result set that is already loaded from this ${svc.toUpperCase()} query. Explain the schema, summarize the sample, and suggest the next Python analysis steps:\n\n${query}`
              );
              localStorage.setItem("astro_chat_new_session", "1");
              navigate("/chat");
            }}>
              Send to AI
            </button>
            <button className="btn-secondary" onClick={() => setShowViz(!showViz)}>
              {showViz ? "Hide Chart" : "Visualize"}
            </button>
          </>
        )}
      </div>

      {showViz && result && (
        <div style={{ marginBottom: "1rem" }}>
          <Suspense fallback={<div className="fits-loading">Loading visualization...</div>}>
            <PlotBuilder
              initialData={result.data as Record<string, unknown>}
              initialChartType={preferredChartType(result)}
              onClose={() => setShowViz(false)}
            />
          </Suspense>
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
      {exportMsg && <div className="success-banner">{exportMsg}</div>}

      {/* Results */}
      {result && (
        <div className="adql-results">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
            <span style={{ fontSize: "0.82rem", color: "var(--color-text-secondary)" }}>
              {result.row_count} rows from {result.service} · {result.columns.length} columns
            </span>
            {totalPages > 1 && (
              <div style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.78rem" }}>
                <button className="btn-secondary btn-small" disabled={page === 0}
                  onClick={() => setPage(page - 1)}>Prev</button>
                <span>{page + 1}/{totalPages}</span>
                <button className="btn-secondary btn-small" disabled={page >= totalPages - 1}
                  onClick={() => setPage(page + 1)}>Next</button>
              </div>
            )}
          </div>
          <div className="results-table-wrap" style={{ overflowX: "auto" }}>
            <table className="results-table adql-results-table" style={{ minWidth: Math.max(result.columns.length * 120, 600) }}>
              <colgroup>
                {result.columns.map((c) => (
                  <col key={c} style={{ width: `${100 / result.columns.length}%` }} />
                ))}
              </colgroup>
              <thead>
                <tr>{result.columns.map((c) => (
                  <th key={c} title={c} className="th-sortable" style={{ cursor: "pointer" }}
                    onClick={() => handleSort(c)}>{c}{sortIndicator(c)}</th>
                ))}</tr>
              </thead>
              <tbody>
                {visibleRows.map((i) => (
                  <tr key={i}>
                    {result.columns.map((c) => (
                      <td key={c} className="mono" title={String(result.data[c]?.[i] ?? "")}>{fmtCell(result.data[c]?.[i])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
