import { useState, useEffect } from "react";
import { adqlQuery, listADQLServices, logOperation } from "../../api/client";
import type { ADQLResult } from "../../api/client";

interface Service {
  id: string;
  name: string;
  url: string;
  description: string;
}

const TEMPLATE_QUERIES: Array<{ label: string; query: string; service: string; desc: string }> = [
  {
    label: "Gaia: photometry (G < 15)",
    service: "gaia",
    desc: "Astrometry + 3-band photometry. ~100% complete for G<20.",
    query: `SELECT TOP 200 source_id, ra, dec,
  phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag,
  bp_rp, parallax, pmra, pmdec
FROM gaiadr3.gaia_source
WHERE phot_g_mean_mag < 15
  AND parallax IS NOT NULL
ORDER BY phot_g_mean_mag`,
  },
  {
    label: "Gaia: full stellar params (bright)",
    service: "gaia",
    desc: "Includes Teff, logg, [M/H]. Only ~40% complete (needs BP/RP spectra).",
    query: `SELECT TOP 200 source_id, ra, dec,
  phot_g_mean_mag, bp_rp, parallax, pmra, pmdec,
  teff_gspphot, logg_gspphot, mh_gspphot,
  ag_gspphot, ebpminrp_gspphot
FROM gaiadr3.gaia_source
WHERE teff_gspphot IS NOT NULL
  AND phot_g_mean_mag < 16
ORDER BY phot_g_mean_mag`,
  },
  {
    label: "Gaia: radial velocities (G < 14)",
    service: "gaia",
    desc: "RV only available for ~5% of sources (bright stars G<14).",
    query: `SELECT TOP 200 source_id, ra, dec,
  phot_g_mean_mag, parallax, pmra, pmdec,
  radial_velocity, radial_velocity_error
FROM gaiadr3.gaia_source
WHERE radial_velocity IS NOT NULL
ORDER BY phot_g_mean_mag`,
  },
  {
    label: "Gaia: nearby stars (plx > 50 mas)",
    service: "gaia",
    desc: "Stars within ~20 pc. High completeness for parallax.",
    query: `SELECT TOP 200 source_id, ra, dec,
  phot_g_mean_mag, bp_rp, parallax, parallax_error,
  pmra, pmdec, ruwe
FROM gaiadr3.gaia_source
WHERE parallax > 50
  AND ruwe < 1.4
ORDER BY parallax DESC`,
  },
  {
    label: "SIMBAD: high-z galaxies",
    service: "simbad",
    desc: "Galaxies with measured redshift z > 4.",
    query: `SELECT TOP 200 main_id, ra, dec, otype,
  rvz_redshift, rvz_radvel, morph_type
FROM basic
WHERE otype = 'G'
  AND rvz_redshift > 4
  AND rvz_redshift IS NOT NULL
ORDER BY rvz_redshift DESC`,
  },
  {
    label: "SIMBAD: QSOs with redshift",
    service: "simbad",
    desc: "Quasars ordered by redshift.",
    query: `SELECT TOP 200 main_id, ra, dec,
  rvz_redshift, sp_type
FROM basic
WHERE otype = 'QSO'
  AND rvz_redshift IS NOT NULL
ORDER BY rvz_redshift DESC`,
  },
];

function loadQueryHistory(): string[] {
  try {
    const raw = localStorage.getItem("astro_adql_history");
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveQueryToHistory(q: string) {
  try {
    const history = loadQueryHistory();
    // Remove duplicate if exists, then prepend
    const filtered = history.filter((h) => h !== q);
    filtered.unshift(q);
    // Keep last 10
    localStorage.setItem("astro_adql_history", JSON.stringify(filtered.slice(0, 10)));
  } catch {
    // ignore
  }
}

const EXAMPLE_QUERIES: Record<string, string> = {
  gaia: `SELECT TOP 100 source_id, ra, dec, phot_g_mean_mag, parallax
FROM gaiadr3.gaia_source
WHERE 1=CONTAINS(
  POINT('ICRS', ra, dec),
  CIRCLE('ICRS', 83.633, 22.014, 0.1)
)
ORDER BY phot_g_mean_mag`,
  vizier: `SELECT TOP 50 *
FROM "II/246/out"
WHERE 1=CONTAINS(
  POINT('ICRS', RAJ2000, DEJ2000),
  CIRCLE('ICRS', 83.633, 22.014, 0.05)
)`,
  cadc: `SELECT TOP 50 *
FROM caom2.Observation
WHERE target_name = 'M31'`,
};

export default function ADQLPage() {
  const [services, setServices] = useState<Service[]>([]);
  const [service, setService] = useState("gaia");
  const [query, setQuery] = useState(EXAMPLE_QUERIES.gaia);
  const [result, setResult] = useState<ADQLResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queryHistory, setQueryHistory] = useState<string[]>(loadQueryHistory);

  useEffect(() => {
    listADQLServices().then(setServices).catch(() => {});
  }, []);

  const handleServiceChange = (svc: string) => {
    setService(svc);
    if (EXAMPLE_QUERIES[svc] && query === EXAMPLE_QUERIES[service]) {
      setQuery(EXAMPLE_QUERIES[svc]);
    }
  };

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await adqlQuery(query, service);
      setResult(res);
      saveQueryToHistory(query);
      setQueryHistory(loadQueryHistory());
      logOperation("adql", `ADQL query on ${service}: ${query.slice(0, 100)}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Query failed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="adql-page">
      <h1>ADQL Query</h1>
      <p className="adql-subtitle">
        Query astronomical databases using ADQL (Astronomical Data Query Language)
      </p>

      <div className="adql-controls">
        <div className="adql-service-select">
          <label>TAP Service</label>
          <div className="segmented-control">
            {services.map((s) => (
              <button
                key={s.id}
                className={`segment-btn${service === s.id ? " active" : ""}`}
                onClick={() => handleServiceChange(s.id)}
              >
                {s.name}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Query history chips */}
      {queryHistory.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
          <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.5)", lineHeight: "28px" }}>Recent:</span>
          {queryHistory.map((q, i) => (
            <button
              key={i}
              className="btn-secondary btn-small"
              style={{ fontSize: "0.7rem", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              onClick={() => setQuery(q)}
              title={q}
            >
              {q.slice(0, 50)}{q.length > 50 ? "..." : ""}
            </button>
          ))}
        </div>
      )}

      {/* Template buttons */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
        <span style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.5)", lineHeight: "28px" }}>Templates:</span>
        {TEMPLATE_QUERIES.map((t) => (
          <button
            key={t.label}
            className="btn-secondary btn-small"
            style={{ fontSize: "0.72rem" }}
            title={t.desc}
            onClick={() => { setQuery(t.query); setService(t.service); }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="adql-editor">
        <textarea
          className="adql-textarea"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          rows={8}
          spellCheck={false}
        />
        <div className="adql-actions">
          <button
            className="btn-primary"
            onClick={handleRun}
            disabled={loading || !query.trim()}
          >
            {loading ? <span className="spinner" /> : null}
            {loading ? "Running..." : "Run Query"}
          </button>
          <button
            className="btn-secondary"
            onClick={() => setQuery(EXAMPLE_QUERIES[service] || "")}
          >
            Load Example
          </button>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {result && (
        <div className="adql-results">
          <div className="adql-results-header">
            <span>{result.row_count} rows returned from {result.service}</span>
          </div>
          <div className="results-table-wrap">
            <table className="results-table">
              <thead>
                <tr>
                  {result.columns.map((col) => (
                    <th key={col}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Array.from({ length: Math.min(result.row_count, 100) }).map((_, i) => (
                  <tr key={i}>
                    {result.columns.map((col) => (
                      <td key={col} className="mono">
                        {result.data[col]?.[i] != null ? String(result.data[col][i]) : "—"}
                      </td>
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
