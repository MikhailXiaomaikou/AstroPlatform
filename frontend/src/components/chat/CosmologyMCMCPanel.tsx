type ParameterSummary = {
  median?: number;
  mean?: number;
  std?: number;
  hdi_low_94?: number;
  hdi_high_94?: number;
  rhat?: number | null;
  ess_bulk?: number | null;
  status?: string;
};

function fmt(value: unknown, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1000 || (Math.abs(value) > 0 && Math.abs(value) < 0.001)) {
    return value.toExponential(3);
  }
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

export default function CosmologyMCMCPanel({ result }: { result: Record<string, unknown> }) {
  const params = (result.parameters || result.posterior_summary || {}) as Record<string, ParameterSummary>;
  const diagnostics = (result.chain_diagnostics || {}) as Record<string, unknown>;
  const publicationReady = result.publication_ready === true;
  const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  const parameterNames = Object.keys(params);
  const usedCount = Array.isArray(result.datasets_used) ? result.datasets_used.length : undefined;
  const notRunCount = Array.isArray(result.datasets_not_run) ? result.datasets_not_run.length : undefined;
  const compressed = result.compressed_likelihood_preliminary === true;

  return (
    <div style={{ fontSize: "0.8rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Cosmology MCMC</strong>
        <span>{String(result.model || "model?")} · {String(result.sampler || "sampler?")}</span>
        {compressed && <span style={{ fontSize: "0.72rem" }}>compressed likelihood</span>}
        <span
          style={{
            border: publicationReady ? "1px solid var(--color-green)" : "1px solid #d99a00",
            color: publicationReady ? "var(--color-green)" : "#a06500",
            background: publicationReady ? "rgba(34, 197, 94, 0.08)" : "rgba(255, 183, 0, 0.12)",
            borderRadius: 4,
            padding: "1px 6px",
            fontSize: "0.7rem",
            fontWeight: 600,
          }}
        >
          {publicationReady ? "publication-ready" : "not publication-ready"}
        </span>
        {status && status !== "COMPLETED" && <span style={{ fontSize: "0.72rem" }}>{status}</span>}
      </div>

      {!publicationReady && (
        <div
          style={{
            padding: "6px 8px",
            borderLeft: "3px solid #d99a00",
            background: "rgba(255, 183, 0, 0.1)",
            color: "var(--color-text-primary)",
            marginBottom: 8,
          }}
        >
          Posterior numbers are not citeable until ESS/R-hat diagnostics pass.
        </div>
      )}

      {compressed && (
        <div
          style={{
            padding: "6px 8px",
            borderLeft: "3px solid #2fbf71",
            background: "rgba(34, 197, 94, 0.08)",
            color: "var(--color-text-primary)",
            marginBottom: 8,
          }}
        >
          Preliminary compressed-Gaussian result. Used {usedCount ?? 0} compressed dataset(s)
          {notRunCount ? `; ${notRunCount} selected dataset(s) still need external likelihood chains.` : "."}
        </div>
      )}

      {parameterNames.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.74rem" }}>
            <thead>
              <tr style={{ color: "var(--color-text-tertiary)", textAlign: "left" }}>
                <th style={{ padding: "3px 6px" }}>Param</th>
                <th style={{ padding: "3px 6px" }}>Median</th>
                <th style={{ padding: "3px 6px" }}>94% HDI</th>
                <th style={{ padding: "3px 6px" }}>R-hat</th>
                <th style={{ padding: "3px 6px" }}>ESS</th>
                <th style={{ padding: "3px 6px" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {parameterNames.map((name) => {
                const item = params[name] || {};
                return (
                  <tr key={name} style={{ borderTop: "1px solid var(--color-border)" }}>
                    <td style={{ padding: "3px 6px", color: "var(--color-text-primary)", fontWeight: 600 }}>{name}</td>
                    <td style={{ padding: "3px 6px" }}>{fmt(item.median)}</td>
                    <td style={{ padding: "3px 6px" }}>{fmt(item.hdi_low_94)} to {fmt(item.hdi_high_94)}</td>
                    <td style={{ padding: "3px 6px" }}>{fmt(item.rhat, 3)}</td>
                    <td style={{ padding: "3px 6px" }}>{fmt(item.ess_bulk, 0)}</td>
                    <td style={{ padding: "3px 6px" }}>{item.status || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: 8, display: "flex", gap: 10, flexWrap: "wrap", color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>
        <span>seed={String(result.random_seed ?? "—")}</span>
        <span>data_hash={String(result.data_hash || "—").slice(0, 12)}</span>
        <span>samples={String(result.n_samples ?? "—")}</span>
        <span>acceptance={fmt(result.acceptance_fraction, 3)}</span>
        <span>diagnostics={String(diagnostics.overall_status || "—")}</span>
      </div>
    </div>
  );
}
