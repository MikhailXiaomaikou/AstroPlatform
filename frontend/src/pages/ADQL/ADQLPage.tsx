import { useState, useEffect } from "react";
import { adqlQuery, listADQLServices, type ADQLResult } from "../../api/client";

interface Service {
  id: string;
  name: string;
  url: string;
  description: string;
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
