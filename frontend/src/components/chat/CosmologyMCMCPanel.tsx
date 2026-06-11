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

type Tension = {
  parameter?: string;
  dataset_a?: string;
  dataset_b?: string;
  delta?: number;
  sigma?: number;
  value_a?: number;
  value_b?: number;
  status?: string;
  reason?: string;
  comparison?: string;
  value_a_source?: string;
  value_b_source?: string;
  note?: string;
};

function fmt(value: unknown, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1000 || (Math.abs(value) > 0 && Math.abs(value) < 0.001)) {
    return value.toExponential(3);
  }
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

type ChainTier = "publication" | "exploratory" | "blocked";

function deriveChainTier(result: Record<string, unknown>): ChainTier {
  const explicit = String(result.chain_tier || "").toLowerCase();
  if (explicit === "publication" || explicit === "exploratory" || explicit === "blocked") {
    return explicit;
  }
  // Fallback for backends that don't emit chain_tier yet:
  if (result.publication_ready === true) return "publication";
  const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  if (status === "EXPLORATORY") return "exploratory";
  return "blocked";
}

const TIER_STYLE: Record<ChainTier, { label: string; color: string; bg: string; border: string }> = {
  publication: {
    label: "publication-ready",
    color: "var(--color-green)",
    bg: "rgba(34, 197, 94, 0.08)",
    border: "var(--color-green)",
  },
  exploratory: {
    label: "exploratory",
    color: "#a06500",
    bg: "rgba(255, 183, 0, 0.12)",
    border: "#d99a00",
  },
  blocked: {
    label: "blocked — do not cite",
    color: "#7f1d1d",
    bg: "rgba(239, 68, 68, 0.10)",
    border: "#ef4444",
  },
};

export default function CosmologyMCMCPanel({ result }: { result: Record<string, unknown> }) {
  const params = (result.parameters || result.posterior_summary || {}) as Record<string, ParameterSummary>;
  const diagnostics = (result.chain_diagnostics || {}) as Record<string, unknown>;
  const tier = deriveChainTier(result);
  const tierStyle = TIER_STYLE[tier];
  const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  const parameterNames = Object.keys(params);
  const usedCount = Array.isArray(result.datasets_used) ? result.datasets_used.length : undefined;
  const notRunCount = Array.isArray(result.datasets_not_run) ? result.datasets_not_run.length : undefined;
  const compressed = result.compressed_likelihood_preliminary === true || result.compressed_rotation_preliminary === true;
  const fullFidelity = result.claim_scope === "executable_full_fidelity_likelihoods";
  const warnings = Array.isArray(result.warnings) ? result.warnings.filter((item): item is string => typeof item === "string") : [];
  const exploratoryWarning = typeof result.__exploratory_warning__ === "string" ? result.__exploratory_warning__ : "";
  const blockedReason = warnings[0] || String(result.__message_to_model__ || "");
  const unavailableReason = blockedReason;
  const panelTitle = result.model === "isotropic_beta" ? "CMB Rotation" : "Cosmology MCMC";
  const tensionsRaw = Array.isArray(result.pairwise_tensions) ? (result.pairwise_tensions as Tension[]) : [];
  const tensions = tensionsRaw
    .filter((t) => typeof t.sigma === "number" && Number.isFinite(t.sigma))
    .sort((a, b) => (b.sigma ?? 0) - (a.sigma ?? 0))
    .slice(0, 8);
  const notComparableTensions = tensionsRaw
    .filter((t) => t.status === "not_comparable")
    .slice(0, 4);

  return (
    <div style={{ fontSize: "0.8rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
        <strong style={{ color: "var(--color-text-primary)" }}>{panelTitle}</strong>
        <span>{String(result.model || "model?")} · {String(result.sampler || "sampler?")}</span>
        {compressed && <span style={{ fontSize: "0.72rem" }}>compressed likelihood</span>}
        {fullFidelity && <span style={{ fontSize: "0.72rem" }}>full-fidelity likelihood</span>}
        <span
          style={{
            border: `1px solid ${tierStyle.border}`,
            color: tierStyle.color,
            background: tierStyle.bg,
            borderRadius: 4,
            padding: "1px 6px",
            fontSize: "0.7rem",
            fontWeight: 600,
          }}
        >
          {tierStyle.label}
        </span>
        {status && status !== "COMPLETED" && status !== "EXPLORATORY" && <span style={{ fontSize: "0.72rem" }}>{status}</span>}
      </div>

      {tier === "exploratory" && (
        <div
          style={{
            padding: "6px 8px",
            borderLeft: "3px solid #d99a00",
            background: "rgba(255, 183, 0, 0.10)",
            color: "var(--color-text-primary)",
            marginBottom: 8,
          }}
        >
          {exploratoryWarning
            || "Exploratory chain: posterior median and 1σ range may be discussed, "
              + "but MUST NOT be quoted as a published constraint or added to a bibcode pool."}
        </div>
      )}

      {tier === "blocked" && (
        <div
          style={{
            padding: "6px 8px",
            borderLeft: "3px solid #ef4444",
            background: "rgba(239, 68, 68, 0.08)",
            color: "var(--color-text-primary)",
            marginBottom: 8,
          }}
        >
          {parameterNames.length > 0
            ? (blockedReason
                || "Chain blocked: ESS/R-hat below the exploratory floor, or input is not claimable. "
                  + "Do not cite H0, Ωm, w0, wa, σ8, or HDI numbers from this result.")
            : unavailableReason || "No executable compressed likelihood was available for this selection."}
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

      {tensions.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ color: "var(--color-text-primary)", fontWeight: 600, marginBottom: 4, fontSize: "0.76rem" }}>
            Parameter tensions (pairwise)
          </div>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.72rem" }}>
              <thead>
                <tr style={{ color: "var(--color-text-tertiary)", textAlign: "left" }}>
                  <th style={{ padding: "3px 6px" }}>Param</th>
                  <th style={{ padding: "3px 6px" }}>Dataset A</th>
                  <th style={{ padding: "3px 6px" }}>Dataset B</th>
                  <th style={{ padding: "3px 6px" }}>Value A</th>
                  <th style={{ padding: "3px 6px" }}>Value B</th>
                  <th style={{ padding: "3px 6px" }}>Δ</th>
                  <th style={{ padding: "3px 6px" }}>σ</th>
                </tr>
              </thead>
              <tbody>
                {tensions.map((t, i) => {
                  const sigma = typeof t.sigma === "number" ? t.sigma : 0;
                  const strong = sigma >= 3;
                  const medium = sigma >= 2 && sigma < 3;
                  const rowBg = strong
                    ? "rgba(239, 68, 68, 0.10)"
                    : medium
                      ? "rgba(255, 183, 0, 0.08)"
                      : "transparent";
                  const sigmaColor = strong ? "#7f1d1d" : medium ? "#a06500" : "var(--color-text-secondary)";
                  return (
                    <tr key={`${t.parameter}-${t.dataset_a}-${t.dataset_b}-${i}`} style={{ borderTop: "1px solid var(--color-border)", background: rowBg }}>
                      <td style={{ padding: "3px 6px", color: "var(--color-text-primary)", fontWeight: 600 }}>
                        {String(t.parameter || "—")}
                        {t.comparison === "derived_pairwise" ? (
                          <span title={String(t.note || "Derived comparison")} style={{ marginLeft: 4, color: "var(--color-text-tertiary)", fontWeight: 500 }}>
                            derived
                          </span>
                        ) : null}
                      </td>
                      <td style={{ padding: "3px 6px" }}>{String(t.dataset_a || "—")}</td>
                      <td style={{ padding: "3px 6px" }}>{String(t.dataset_b || "—")}</td>
                      <td style={{ padding: "3px 6px" }}>{fmt(t.value_a, 3)}</td>
                      <td style={{ padding: "3px 6px" }}>{fmt(t.value_b, 3)}</td>
                      <td style={{ padding: "3px 6px" }}>{fmt(t.delta, 3)}</td>
                      <td style={{ padding: "3px 6px", color: sigmaColor, fontWeight: strong ? 700 : 500 }}>
                        {fmt(t.sigma, 2)}σ{strong ? " ⚠" : ""}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {notComparableTensions.length > 0 && (
        <div style={{ marginTop: 8, color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>
          Not comparable: {notComparableTensions.map((t) =>
            `${String(t.parameter || "parameter")} (${String(t.dataset_a || "A")} vs ${String(t.dataset_b || "B")})`
          ).join("; ")}. {String(notComparableTensions[0]?.reason || "Missing covariance information.")}
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
