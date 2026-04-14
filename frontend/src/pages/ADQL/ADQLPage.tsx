import { useState, useEffect, useMemo, lazy, Suspense } from "react";
import { useNavigate } from "react-router-dom";
import { adqlQuery, listADQLServices, logOperation, exportSearchNotebook } from "../../api/client";
import type { ADQLResult } from "../../api/client";
import { useTracking } from "../../hooks/useTracking";
import ADQLEditor from "../../components/ADQLEditor";
import { useI18n } from "../../i18n";

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
    // Normalize column names to lowercase for consistent access
    for (const col of result.columns) row[col.toLowerCase()] = result.data[col]?.[i];
    return row;
  });
}

function toFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function enrichAdqlRows(rows: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  return rows.map((row) => {
    const next = { ...row };
    if (next.bp_rp == null) {
      const bp = toFiniteNumber(next.phot_bp_mean_mag);
      const rp = toFiniteNumber(next.phot_rp_mean_mag);
      if (bp != null && rp != null) next.bp_rp = bp - rp;
    }
    if (next.abs_g_mag == null) {
      const g = toFiniteNumber(next.phot_g_mean_mag);
      const parallax = toFiniteNumber(next.parallax);
      if (g != null && parallax != null && parallax > 0) {
        const distancePc = 1000 / parallax;
        next.abs_g_mag = g - 5 * (Math.log10(distancePc) - 1);
      }
    }
    return next;
  });
}

type StoredAdqlResultSet = {
  service: string;
  query: string;
  row_count: number;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  stored_at: string;
};

const ADQL_RESULT_SET_STORAGE_KEY = "astro_adql_result_sets";
const MAX_STORED_ADQL_RESULT_SETS = 6;
const MAX_STORED_ADQL_ROWS = 500;

function readStoredAdqlResultSets(): StoredAdqlResultSet[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(ADQL_RESULT_SET_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed as StoredAdqlResultSet[] : [];
  } catch {
    return [];
  }
}

function persistAdqlResultSet(service: string, query: string, result: ADQLResult) {
  const rows = enrichAdqlRows(extractRows(result, MAX_STORED_ADQL_ROWS));
  const derivedColumns = new Set(result.columns);
  if (rows.some((row) => row.bp_rp != null)) derivedColumns.add("bp_rp");
  if (rows.some((row) => row.abs_g_mag != null)) derivedColumns.add("abs_g_mag");
  const nextSet: StoredAdqlResultSet = {
    service,
    query,
    row_count: result.row_count,
    columns: Array.from(derivedColumns),
    rows,
    stored_at: new Date().toISOString(),
  };
  const existing = readStoredAdqlResultSets().filter((item) => item.query !== query || item.service !== service);
  existing.push(nextSet);
  localStorage.setItem(
    ADQL_RESULT_SET_STORAGE_KEY,
    JSON.stringify(existing.slice(-MAX_STORED_ADQL_RESULT_SETS))
  );
}

function preferredChartType(_result: ADQLResult | null): string {
  return "scatter_custom";
}

const TEMPLATE_LABEL_I18N: Record<string, string> = {
  "Photometry": "adql.template_photometry",
  "Stellar params": "adql.template_stellar_params",
  "Radial velocity": "adql.template_radial_velocity",
  "Nearby (plx>50)": "adql.template_nearby",
  "z>4 galaxies": "adql.template_z4_galaxies",
  "QSOs": "adql.template_qsos",
  "Seyfert galaxies": "adql.template_seyfert",
  "2MASS catalog": "adql.template_2mass",
  "SDSS DR16 photo": "adql.template_sdss_photo",
  "JWST observations": "adql.template_jwst",
  "By target": "adql.template_by_target",
};

const PAGE_SIZE = 25;

/* ── Component ── */
export default function ADQLPage() {
  const navigate = useNavigate();
  const { t } = useI18n();
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
      persistAdqlResultSet(svc, query, res);
      try {
        localStorage.setItem("astro_last_adql", JSON.stringify({
          service: svc,
          query,
          row_count: res.row_count,
          columns: res.columns,
          sample: rows.slice(0, 5),
        }));
        localStorage.setItem("astro_last_adql_rows", JSON.stringify(enrichAdqlRows(rows)));
      } catch { /* ignore */ }
      addHistory(query); setHistory(getHistory());
      logOperation("adql", `${svc}: ${query.slice(0, 80)}`);
    } catch (err: unknown) {
      const detail =
        err && typeof err === "object" && "response" in err
          ? ((err as { response?: { data?: { detail?: string } } }).response?.data?.detail || t("adql.query_failed"))
          : (err instanceof Error ? err.message : t("adql.query_failed"));
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
        setError(resp?.data?.detail || t("adql.query_failed"));
      } else {
        setError(err instanceof Error ? err.message : t("adql.query_failed"));
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
      <h1>{t("adql.title")}</h1>

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
          {(TEMPLATES[svc] || []).map((tmpl) => (
            <button key={tmpl.label} className="btn-secondary btn-small" title={tmpl.tip}
              style={{ fontSize: "0.72rem" }}
              onClick={() => setQuery(tmpl.q)}>{TEMPLATE_LABEL_I18N[tmpl.label] ? t(TEMPLATE_LABEL_I18N[tmpl.label]) : tmpl.label}</button>
          ))}
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", margin: "4px 0 8px" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)", lineHeight: "24px" }}>{t("adql.recent")}</span>
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
      <ADQLEditor value={query} onChange={setQuery} placeholder={t("adql.placeholder")} />

      <div style={{ display: "flex", gap: 8, margin: "8px 0" }}>
        <button className="btn-primary" onClick={run} disabled={loading || !query.trim()}>
          {loading ? t("adql.running") : t("adql.run_query")}
        </button>
        {result && (
          <>
            <button className="btn-secondary" onClick={downloadCSV}>
              {t("adql.download_csv")} ({result.row_count} {t("adql.rows")})
            </button>
            <button className="btn-secondary" onClick={async () => {
              try {
                setExportMsg(t("adql.generating_notebook"));
                const rows = extractRows(result, 200);
                const blob = await exportSearchNotebook(query, rows);
                const url = URL.createObjectURL(blob);
                const a = document.createElement("a"); a.href = url;
                a.download = `adql_${result.row_count}_rows.ipynb`; a.click();
                URL.revokeObjectURL(url);
                setExportMsg(t("adql.notebook_exported"));
                track("export.notebook", { cell_count: 3 + rows.length });
                setTimeout(() => setExportMsg(null), 2500);
              } catch (e) {
                setError(e instanceof Error ? e.message : t("adql.notebook_export_failed"));
                setExportMsg(null);
              }
            }}>
              {t("adql.jupyter_notebook")}
            </button>
            <button className="btn-secondary" onClick={() => {
              localStorage.setItem(
                "astro_chat_draft",
                `Use get_adql_result_sets() to inspect the ADQL result history already loaded from the ADQL page. The latest result is from ${svc.toUpperCase()}. If you only need the newest rows, use get_adql_results(). Explain the schema, summarize the sample, and then continue with Python analysis defensively (check keys, empty arrays, and row counts before plotting).\n\nLatest query:\n${query}`
              );
              navigate("/chat");
            }}>
              {t("adql.send_to_ai")}
            </button>
            <button className="btn-secondary" onClick={() => setShowViz(!showViz)}>
              {showViz ? t("adql.hide_chart") : t("adql.visualize")}
            </button>
          </>
        )}
      </div>

      {showViz && result && (
        <div style={{ marginBottom: "1rem" }}>
          <Suspense fallback={<div className="fits-loading">{t("fits.loading_viz")}</div>}>
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
              {result.row_count} {t("adql.rows_from")} {result.service} · {result.columns.length} {t("adql.columns")}
            </span>
            {totalPages > 1 && (
              <div style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.78rem" }}>
                <button className="btn-secondary btn-small" disabled={page === 0}
                  onClick={() => setPage(page - 1)}>{t("adql.prev")}</button>
                <span>{page + 1}/{totalPages}</span>
                <button className="btn-secondary btn-small" disabled={page >= totalPages - 1}
                  onClick={() => setPage(page + 1)}>{t("common.next")}</button>
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
