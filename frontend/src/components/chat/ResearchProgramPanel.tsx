import PanelEmptyState from "./PanelEmptyState";

type ResearchPlan = {
  research_question?: string;
  hypotheses?: string[];
  required_probes?: string[];
  candidate_datasets?: {
    key?: string;
    display_name?: string;
    execution_level?: string;
    execution_mode?: string;
    probe?: string;
  }[];
  model_families?: string[];
  executable_level?: string;
  blocking_gaps?: string[];
  alpha_test_protocol?: {
    supported_scope?: string;
    not_supported?: string;
    required_artifacts?: string[];
  };
  proposed_experiment_matrix?: {
    label?: string;
    dataset_keys?: string[];
    model?: string;
  }[];
};

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function levelTone(level: string | undefined) {
  const key = String(level || "unknown").toLowerCase();
  if (key.includes("compressed") || key === "full_runner") {
    return { color: "#166534", bg: "rgba(34,197,94,.12)", border: "#22c55e" };
  }
  if (
    key.includes("config")
    || key === "mixed"
    || key.includes("partial")
    || key.includes("executed_not_ready")
  ) {
    return { color: "#8a5b00", bg: "rgba(245,158,11,.13)", border: "#d99a00" };
  }
  return { color: "#7f1d1d", bg: "rgba(239,68,68,.10)", border: "#ef4444" };
}

function Badge({ children, tone }: { children: string; tone?: string }) {
  const style = levelTone(tone || children);
  return (
    <span
      style={{
        color: style.color,
        background: style.bg,
        border: `1px solid ${style.border}`,
        borderRadius: 4,
        padding: "1px 6px",
        fontSize: "0.7rem",
        fontWeight: 600,
      }}
    >
      {children}
    </span>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function fmtNumber(value: unknown, digits = 3): string {
  // Number(null) === 0, so a JSON null (e.g. rhat: not computed on the
  // in-process runner) must be caught BEFORE coercion — otherwise the UI
  // renders a fabricated "0.000" for a diagnostic that was never computed.
  if (value === null || value === undefined) return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return Math.abs(n) >= 100 ? n.toFixed(0) : n.toFixed(digits);
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function cellChartStatus(cell: Record<string, unknown>): string {
  if (cell.publication_ready) return "ready";
  const level = String(cell.execution_level || "missing");
  if (level === "partial_dataset_run") return "partial";
  if (level === "executed_not_ready") return "not_ready";
  if (level === "config_only") return "config_only";
  return "missing";
}

function parameterSummaryRows(cell: Record<string, unknown>, label: string): Record<string, unknown>[] {
  const result = asRecord(cell.result);
  const params = asRecord(result.parameters || result.posterior_summary || result.derived_params);
  const preferred = ["H0", "S8", "omegam", "Omega_m", "sigma8", "w0", "wa", "beta_deg"];
  return preferred.flatMap((parameter) => {
    const summary = asRecord(params[parameter]);
    const median = finiteNumber(summary.median);
    if (median === null) return [];
    const low = finiteNumber(summary.hdi_low_94)
      ?? (Array.isArray(summary.hdi_94) ? finiteNumber(summary.hdi_94[0]) : null)
      ?? (finiteNumber(summary.std) !== null ? median - Number(summary.std) : null);
    const high = finiteNumber(summary.hdi_high_94)
      ?? (Array.isArray(summary.hdi_94) ? finiteNumber(summary.hdi_94[1]) : null)
      ?? (finiteNumber(summary.std) !== null ? median + Number(summary.std) : null);
    return [{
      label,
      parameter: parameter === "Omega_m" ? "omegam" : parameter,
      median,
      low,
      high,
      publication_ready: Boolean(cell.publication_ready),
      execution_level: cell.execution_level || "not_available",
    }];
  });
}

function buildFallbackResearchCharts(matrix: Record<string, unknown>[]): Record<string, unknown> {
  if (!matrix.length) return {};
  const matrixStatus = matrix.map((cell, index) => ({
    label: String(cell.label || `Cell ${index + 1}`),
    status: cellChartStatus(cell),
    execution_level: cell.execution_level || "not_available",
    publication_ready: Boolean(cell.publication_ready),
    dataset_keys: asArray<string>(cell.dataset_keys),
    model: cell.model,
  }));
  const posteriorForest = matrix.flatMap((cell, index) => (
    parameterSummaryRows(cell, String(cell.label || `Cell ${index + 1}`))
  ));
  const diagnostics = matrix.flatMap((cell, index) => {
    const result = asRecord(cell.result);
    const chain = asRecord(result.chain_diagnostics);
    const ess = finiteNumber(chain.proposal_ess ?? chain.ess_bulk ?? chain.posterior_ess);
    const rhat = finiteNumber(chain.rhat);
    if (ess === null && rhat === null) return [];
    const thresholds = asRecord(chain.thresholds);
    return [{
      label: String(cell.label || `Cell ${index + 1}`),
      ess,
      rhat,
      publication_ready: Boolean(cell.publication_ready),
      execution_level: cell.execution_level || "not_available",
      ess_threshold: finiteNumber(thresholds.ess_min) ?? 400,
      rhat_threshold: finiteNumber(thresholds.rhat_max) ?? 1.05,
    }];
  });
  return {
    chart_version: 1,
    matrix_status: matrixStatus,
    posterior_forest: posteriorForest.slice(0, 24),
    diagnostics: diagnostics.slice(0, 24),
    notes: [
      "Charts are deterministic renderings of current-turn Research Matrix cells.",
    ],
  };
}

function matrixCellStatus(cell: Record<string, unknown>): string {
  if (cell.publication_ready) return "compressed preliminary posterior";
  const level = String(cell.execution_level || "not_available");
  if (level === "partial_dataset_run") return "partial run; some datasets not included";
  if (level === "executed_not_ready") return "posterior attempted; diagnostics below threshold";
  if (level === "config_only") return "configuration only, no posterior run yet";
  return level.replace(/_/g, " ");
}

function statusColor(status: string): string {
  const key = status.toLowerCase();
  if (key === "ready") return "#16a34a";
  if (key === "partial") return "#0ea5e9";
  if (key === "not_ready") return "#d97706";
  if (key === "config_only") return "#a16207";
  return "#dc2626";
}

function ellipsize(label: string, max = 24): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

function MatrixStatusChart({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return null;
  const width = 520;
  const rowH = 24;
  const height = 20 + Math.min(rows.length, 8) * rowH;
  const visible = rows.slice(0, 8);
  return (
    <div>
      <div style={{ fontWeight: 600, color: "var(--color-text-primary)", marginBottom: 3 }}>Matrix status map</div>
      <svg role="img" aria-label="Research matrix status chart" viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", maxWidth: 640, height: "auto" }}>
        {visible.map((row, idx) => {
          const status = String(row.status || "missing");
          const y = 18 + idx * rowH;
          return (
            <g key={`${row.label || idx}`}>
              <text x="0" y={y + 11} fontSize="11" fill="currentColor">{ellipsize(String(row.label || `Cell ${idx + 1}`), 28)}</text>
              <rect x="190" y={y} width="210" height="14" rx="3" fill="rgba(120,120,120,.12)" />
              <rect x="190" y={y} width={status === "ready" ? 210 : status === "partial" ? 150 : status === "not_ready" ? 110 : status === "config_only" ? 75 : 42} height="14" rx="3" fill={statusColor(status)} />
              <text x="412" y={y + 11} fontSize="10" fill="currentColor">{String(row.execution_level || status).replace(/_/g, " ")}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function PosteriorForestChart({ rows }: { rows: Record<string, unknown>[] }) {
  const priority = ["H0", "S8", "omegam", "sigma8", "w0", "wa", "beta_deg"];
  const parameter = priority.find((name) => rows.some((row) => String(row.parameter) === name));
  if (!parameter) return null;
  const visible = rows.filter((row) => String(row.parameter) === parameter && finiteNumber(row.median) !== null).slice(0, 8);
  if (!visible.length) return null;
  const values = visible.flatMap((row) => {
    const median = finiteNumber(row.median);
    const low = finiteNumber(row.low) ?? median;
    const high = finiteNumber(row.high) ?? median;
    return [low, median, high].filter((v): v is number => v !== null);
  });
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = (max - min || 1) * 0.08;
  const xMin = min - pad;
  const xMax = max + pad;
  const x = (value: number) => 190 + ((value - xMin) / (xMax - xMin || 1)) * 250;
  const width = 520;
  const rowH = 25;
  const height = 32 + visible.length * rowH;
  return (
    <div>
      <div style={{ fontWeight: 600, color: "var(--color-text-primary)", marginBottom: 3 }}>Posterior forest: {parameter}</div>
      <svg role="img" aria-label={`Posterior forest chart for ${parameter}`} viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", maxWidth: 640, height: "auto" }}>
        <line x1="190" x2="440" y1="18" y2="18" stroke="rgba(120,120,120,.35)" />
        <text x="190" y="12" fontSize="10" fill="currentColor">{fmtNumber(xMin, 2)}</text>
        <text x="414" y="12" fontSize="10" fill="currentColor">{fmtNumber(xMax, 2)}</text>
        {visible.map((row, idx) => {
          const median = finiteNumber(row.median) ?? 0;
          const low = finiteNumber(row.low) ?? median;
          const high = finiteNumber(row.high) ?? median;
          const y = 34 + idx * rowH;
          const ready = Boolean(row.publication_ready);
          return (
            <g key={`${row.label || idx}-${parameter}`}>
              <text x="0" y={y + 4} fontSize="11" fill="currentColor">{ellipsize(String(row.label || `Cell ${idx + 1}`), 28)}</text>
              <line x1={x(low)} x2={x(high)} y1={y} y2={y} stroke={ready ? "#16a34a" : "#d97706"} strokeWidth="2" />
              <circle cx={x(median)} cy={y} r="4" fill={ready ? "#16a34a" : "#d97706"} />
              <text x="452" y={y + 4} fontSize="10" fill="currentColor">{fmtNumber(median, 2)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function DiagnosticsChart({ rows }: { rows: Record<string, unknown>[] }) {
  const visible = rows.filter((row) => finiteNumber(row.ess) !== null || finiteNumber(row.rhat) !== null).slice(0, 8);
  if (!visible.length) return null;
  const width = 520;
  const rowH = 25;
  const height = 24 + visible.length * rowH;
  return (
    <div>
      <div style={{ fontWeight: 600, color: "var(--color-text-primary)", marginBottom: 3 }}>Chain diagnostics</div>
      <svg role="img" aria-label="Chain diagnostics chart" viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", maxWidth: 640, height: "auto" }}>
        {visible.map((row, idx) => {
          const ess = finiteNumber(row.ess) ?? 0;
          const threshold = finiteNumber(row.ess_threshold) ?? 400;
          const rhat = finiteNumber(row.rhat);
          const y = 18 + idx * rowH;
          const barW = Math.min(220, Math.max(4, (ess / Math.max(threshold * 1.25, 1)) * 220));
          const pass = ess >= threshold && (rhat === null || rhat <= (finiteNumber(row.rhat_threshold) ?? 1.05));
          return (
            <g key={`${row.label || idx}-diag`}>
              <text x="0" y={y + 11} fontSize="11" fill="currentColor">{ellipsize(String(row.label || `Cell ${idx + 1}`), 28)}</text>
              <rect x="190" y={y} width="220" height="14" rx="3" fill="rgba(120,120,120,.12)" />
              <rect x="190" y={y} width={barW} height="14" rx="3" fill={pass ? "#16a34a" : "#d97706"} />
              <text x="422" y={y + 11} fontSize="10" fill="currentColor">ESS {fmtNumber(ess, 0)} · Rhat {fmtNumber(rhat, 3)}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function ResearchChartsView({ charts }: { charts: Record<string, unknown> }) {
  const matrixRows = asArray<Record<string, unknown>>(charts.matrix_status);
  const forestRows = asArray<Record<string, unknown>>(charts.posterior_forest);
  const diagnosticRows = asArray<Record<string, unknown>>(charts.diagnostics);
  if (!matrixRows.length && !forestRows.length && !diagnosticRows.length) return null;
  return (
    <div style={{ display: "grid", gap: 10, padding: "8px 0", borderTop: "1px solid var(--color-border)" }} data-testid="research-visual-diagnostics">
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Visual Diagnostics</strong>
        <Badge tone="compressed_preliminary">deterministic charts</Badge>
      </div>
      <MatrixStatusChart rows={matrixRows} />
      <PosteriorForestChart rows={forestRows} />
      <DiagnosticsChart rows={diagnosticRows} />
      {asArray<string>(charts.notes).slice(0, 1).map((note) => (
        <div key={note} style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>{note}</div>
      ))}
    </div>
  );
}

function PlanView({ plan }: { plan: ResearchPlan }) {
  const datasets = asArray<NonNullable<ResearchPlan["candidate_datasets"]>[number]>(plan.candidate_datasets);
  const matrix = asArray<NonNullable<ResearchPlan["proposed_experiment_matrix"]>[number]>(plan.proposed_experiment_matrix);
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Research Plan</strong>
        <Badge tone={plan.executable_level}>{String(plan.executable_level || "planned").replace(/_/g, " ")}</Badge>
        {asArray<string>(plan.required_probes).map((probe) => <Badge key={probe}>{probe}</Badge>)}
      </div>
      {plan.research_question ? (
        <div style={{ color: "var(--color-text-secondary)" }}>{plan.research_question}</div>
      ) : null}
      {asArray<string>(plan.hypotheses).length ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Hypotheses</strong>
          <ul style={{ margin: "4px 0 0 18px" }}>
            {asArray<string>(plan.hypotheses).slice(0, 4).map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      ) : null}
      {datasets.length ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Candidate data</strong>
          <div style={{ display: "grid", gap: 5, marginTop: 4 }}>
            {datasets.slice(0, 8).map((dataset) => (
              <div key={dataset.key} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 4 }}>
                {dataset.display_name || dataset.key}{" "}
                <Badge tone={dataset.execution_level}>{String(dataset.execution_level || dataset.execution_mode || "unknown").replace(/_/g, " ")}</Badge>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {matrix.length ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Experiment matrix</strong>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 3 }}>
            {matrix.slice(0, 5).map((cell) => `${cell.label}: ${asArray<string>(cell.dataset_keys).join(" + ")}`).join(" · ")}
          </div>
        </div>
      ) : null}
      {plan.alpha_test_protocol ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Alpha test protocol</strong>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 3 }}>
            {plan.alpha_test_protocol.supported_scope || "Registered-data exploratory research only."}
          </div>
        </div>
      ) : null}
      {asArray<string>(plan.blocking_gaps).length ? (
        <div style={{ color: "#8a5b00" }}>
          {asArray<string>(plan.blocking_gaps).slice(0, 3).map((gap) => <div key={gap}>⚠ {gap}</div>)}
        </div>
      ) : null}
    </div>
  );
}

function MatrixView({ result }: { result: Record<string, unknown> }) {
  const matrix = asArray<Record<string, unknown>>(result.matrix);
  const suppliedCharts = asRecord(result.research_charts);
  const charts = Object.keys(suppliedCharts).length ? suppliedCharts : buildFallbackResearchCharts(matrix);
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Research Matrix</strong>
        <Badge tone={result.publication_ready ? "compressed_preliminary" : "config_only"}>
          {result.publication_ready ? "runnable cells ready" : "partial"}
        </Badge>
        <span>{String(result.ready_cells || 0)} / {String(result.matrix_size || matrix.length)} ready</span>
      </div>
      <ResearchChartsView charts={charts} />
      {matrix.slice(0, 10).map((cell, index) => (
        <div key={`${cell.label || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
          {(() => {
            const resultObj = asRecord(cell.result);
            const diagnostics = asRecord(resultObj.chain_diagnostics);
            const parameters = asRecord(resultObj.parameters);
            const h0 = asRecord(parameters.H0);
            const notRun = asArray<Record<string, unknown>>(resultObj.datasets_not_run);
            const warnings = asArray<string>(cell.warnings);
            return (
              <>
                <strong style={{ color: "var(--color-text-primary)" }}>{String(cell.label || `Cell ${index + 1}`)}</strong>
                <div style={{ color: "var(--color-text-tertiary)" }}>
                  {asArray<string>(cell.dataset_keys).join(" + ")} · {String(cell.model || "lcdm")} ·{" "}
                  {matrixCellStatus(cell)}
                </div>
                {Object.keys(diagnostics).length || Object.keys(h0).length ? (
                  <div style={{ color: "var(--color-text-secondary)", fontSize: "0.74rem", marginTop: 2 }}>
                    H0 median {fmtNumber(h0.median)} · ESS {fmtNumber(diagnostics.proposal_ess ?? diagnostics.ess_bulk, 1)}
                    {" · "}Rhat {fmtNumber(diagnostics.rhat, 3)}
                    {diagnostics.thresholds ? " · threshold ESS≥400" : ""}
                  </div>
                ) : null}
                {notRun.length ? (
                  <div style={{ color: "#8a5b00", fontSize: "0.72rem", marginTop: 2 }}>
                    not numerically included: {notRun.map((entry) => String(entry.key || entry.display_name || "dataset")).join(", ")}
                  </div>
                ) : null}
                {warnings.slice(0, 2).map((warning) => (
                  <div key={warning} style={{ color: "#8a5b00", fontSize: "0.72rem", marginTop: 2 }}>
                    ⚠ {warning}
                  </div>
                ))}
              </>
            );
          })()}
        </div>
      ))}
    </div>
  );
}

function EvidenceView({ result }: { result: Record<string, unknown> }) {
  const graph = (result.evidence_graph || {}) as Record<string, unknown>;
  const supported = asArray<Record<string, unknown>>(graph.supported_claims);
  const unsupported = asArray<Record<string, unknown>>(graph.unsupported_claims);
  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Evidence Graph</strong>
        <Badge tone={unsupported.length ? "config_only" : "compressed_preliminary"}>
          {unsupported.length ? "unsupported claims flagged" : "claim graph ready"}
        </Badge>
      </div>
      <div>Claimable parameters: {asArray<string>(graph.claimable_parameters).join(", ") || "none"}</div>
      {supported.length ? <div>{supported.length} supported claim node(s)</div> : null}
      {unsupported.length ? <div style={{ color: "#8a5b00" }}>{unsupported.length} unsupported claim token(s)</div> : null}
    </div>
  );
}

function FactCheckView({ result }: { result: Record<string, unknown> }) {
  const report = (result.fact_check_report || result) as Record<string, unknown>;
  const claims = asArray<Record<string, unknown>>(report.claims);
  const checked = (report.checked_sources || {}) as Record<string, unknown>;
  const status = String(report.status || result.status || "unknown");
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Fact Check</strong>
        <Badge tone={status === "passed" ? "compressed_preliminary" : status === "warning" ? "config_only" : "not_available"}>
          {status}
        </Badge>
        <span>
          {String(report.verified_claim_count || 0)} verified · {String(report.unsupported_claim_count || 0)} unsupported
        </span>
      </div>
      {claims.slice(0, 8).map((claim, index) => (
        <div
          key={`${claim.text || index}`}
          title={[
            `Claim: ${String(claim.text || "Claim")}`,
            `Status: ${String(claim.status || "unknown")}`,
            `Support: ${String(claim.support_level || "not_applicable")}`,
            `Evidence: ${asArray<string>(claim.evidence_ids).join(", ") || "none"}`,
            claim.safe_rewrite ? `Safe rewrite: ${String(claim.safe_rewrite)}` : "No rewrite needed.",
          ].join("\n")}
          style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}
        >
          <div style={{ color: "var(--color-text-primary)" }}>{String(claim.text || "Claim")}</div>
          <div style={{ color: "var(--color-text-tertiary)" }}>
            {String(claim.kind || "fact")} · {String(claim.status || "unknown")} · {String(claim.support_level || "not_applicable")}
          </div>
          {claim.safe_rewrite ? (
            <div style={{ color: "#8a5b00", marginTop: 2 }}>Safe rewrite: {String(claim.safe_rewrite)}</div>
          ) : null}
        </div>
      ))}
      <div style={{ color: "var(--color-text-tertiary)" }}>
        Sources checked: {String(checked.dataset_count || 0)} dataset(s),{" "}
        {asArray<string>(checked.arxiv_ids).length} arXiv,{" "}
        {asArray<string>(checked.dois).length} DOI,{" "}
        {asArray<string>(checked.bibcodes).length} bibcode.
      </div>
    </div>
  );
}

function PaperToolMiningView({ result }: { result: Record<string, unknown> }) {
  const specs = asArray<Record<string, unknown>>(result.tool_specs);
  const metadata = (result.paper_metadata || {}) as Record<string, unknown>;
  const categoryCounts = (result.category_counts || {}) as Record<string, unknown>;
  const implCounts = (result.implementation_counts || {}) as Record<string, unknown>;
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Paper Tool Mining</strong>
        <Badge tone={result.analysis_status === "PAPER_TOOL_MINING_READY" ? "compressed_preliminary" : "config_only"}>
          {`${String(result.tool_count || specs.length)} ToolSpec(s)`}
        </Badge>
      </div>
      <div style={{ color: "var(--color-text-secondary)" }}>
        {String(metadata.title || metadata.paper_id || "Paper")}{" "}
        {metadata.arxiv_id ? <span>· arXiv:{String(metadata.arxiv_id)}</span> : null}
      </div>
      <div style={{ color: "var(--color-text-tertiary)" }}>
        Categories: {Object.entries(categoryCounts).map(([k, v]) => `${k} ${String(v)}`).join(", ") || "none"} ·{" "}
        Status: {Object.entries(implCounts).map(([k, v]) => `${k} ${String(v)}`).join(", ") || "none"}
      </div>
      {specs.slice(0, 10).map((spec, index) => {
        const spans = asArray<Record<string, unknown>>(spec.source_spans);
        const spanTitle = spans.map((span) => `${String(span.section || "")}: ${String(span.text || "")}`).join("\n\n");
        return (
          <div
            key={`${spec.tool_id || index}`}
            title={spanTitle || "No source span attached."}
            style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}
          >
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              <strong style={{ color: "var(--color-text-primary)" }}>{String(spec.method_name || "ToolSpec")}</strong>
              <Badge>{String(spec.tool_category || "tool")}</Badge>
              <Badge tone={String(spec.implementation_status || "missing")}>{String(spec.implementation_status || "missing")}</Badge>
              <span style={{ color: "var(--color-text-tertiary)" }}>
                confidence {String(spec.confidence ?? "n/a")}
              </span>
            </div>
            <div style={{ color: "var(--color-text-tertiary)", marginTop: 2 }}>
              {asArray<string>(spec.datasets).slice(0, 4).join(", ") || String(spec.canonical_capability || "capability")}
            </div>
          </div>
        );
      })}
      {result.blocked_reason ? (
        <div style={{ color: "#8a5b00" }}>Blocked: {String(result.blocked_reason)}</div>
      ) : null}
    </div>
  );
}

function PaperCandidatePoolView({ result }: { result: Record<string, unknown> }) {
  const papers = asArray<Record<string, unknown>>(result.candidate_papers);
  const queries = asArray<string>(result.attempted_queries);
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Paper Candidate Pool</strong>
        <Badge tone={papers.length ? "config_only" : "not_available"}>{`${String(result.candidate_count || papers.length)} candidate(s)`}</Badge>
        <span>{result.live_search_enabled ? "live arXiv enabled" : "seed/offline pool"}</span>
      </div>
      <div style={{ color: "var(--color-text-secondary)" }}>
        Candidate papers feed the 20-paper mining loop. They are not evidence for posterior, fit, or paper-conclusion claims.
      </div>
      {queries.length ? (
        <div style={{ color: "var(--color-text-tertiary)" }}>
          Queries: {queries.slice(0, 3).join(" · ")}{queries.length > 3 ? ` · +${queries.length - 3} more` : ""}
        </div>
      ) : null}
      {papers.slice(0, 10).map((paper, index) => (
        <div key={`${paper.arxiv_id || paper.paper_id || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <strong style={{ color: "var(--color-text-primary)" }}>{String(paper.title || paper.paper_id || "Paper")}</strong>
            {paper.arxiv_id ? <Badge>{`arXiv:${String(paper.arxiv_id)}`}</Badge> : null}
            <Badge tone={String(paper.mining_readiness || "metadata_or_abstract_only")}>
              {String(paper.mining_readiness || "metadata_or_abstract_only").replace(/_/g, " ")}
            </Badge>
          </div>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 2 }}>
            score {String(paper.relevance_score ?? "n/a")} ·{" "}
            {asArray<string>(paper.relevance_terms).slice(0, 6).join(", ") || "no relevance terms"}
          </div>
        </div>
      ))}
    </div>
  );
}

function PaperToolMiningLoopView({ result }: { result: Record<string, unknown> }) {
  const rounds = asArray<Record<string, unknown>>(result.rounds);
  const queue = asArray<Record<string, unknown>>(result.aggregate_implementation_queue);
  const state = (result.updated_state || {}) as Record<string, unknown>;
  const singleBatch = (result.batch_result || {}) as Record<string, unknown>;
  const selected = asArray<string>(result.selected_paper_ids);
  const history = asArray<Record<string, unknown>>(state.round_history);

  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Paper Mining Loop</strong>
        <Badge tone={result.analysis_status === "PAPER_TOOL_MINING_LOOP_EMPTY" ? "not_available" : "config_only"}>
          {result.analysis_status === "PAPER_TOOL_MINING_LOOP_EMPTY"
            ? "no unread papers"
            : `${String(result.rounds_run || rounds.length || 1)} round(s)`}
        </Badge>
        <span>batch size {String(result.batch_size || singleBatch.paper_count || 20)}</span>
      </div>
      <div style={{ color: "var(--color-text-secondary)" }}>
        Continuous local loop: mine 20 related papers, build ToolSpecs/gaps, then carry the updated state into the next round.
      </div>
      {selected.length ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>This round</strong>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 2 }}>
            Round {String(result.round_index || state.round_index || 1)} · {selected.length} paper(s) ·{" "}
            {String(result.remaining_unread || 0)} unread remaining
          </div>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 2 }}>
            {selected.slice(0, 8).join(", ")}
            {selected.length > 8 ? `, +${selected.length - 8} more` : ""}
          </div>
        </div>
      ) : null}
      {rounds.length ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Rounds</strong>
          <div style={{ display: "grid", gap: 5, marginTop: 4 }}>
            {rounds.slice(0, 6).map((round, index) => {
              const batch = (round.batch_result || {}) as Record<string, unknown>;
              return (
                <div key={`${round.round_index || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
                  Round {String(round.round_index || index + 1)} · {String(round.batch_size || batch.paper_count || 0)} paper(s) ·{" "}
                  {String(batch.tool_spec_count || 0)} ToolSpec(s)
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {singleBatch.tool_spec_count ? (
        <div style={{ color: "var(--color-text-tertiary)" }}>
          ToolSpecs: {String(singleBatch.tool_spec_count)} · mined papers: {String(singleBatch.mined_paper_count || 0)}
        </div>
      ) : null}
      {queue.length ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Next implementation queue</strong>
          <div style={{ display: "grid", gap: 5, marginTop: 4 }}>
            {queue.slice(0, 5).map((item, index) => (
              <div key={`${item.capability || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
                <span style={{ color: "var(--color-text-primary)" }}>{String(item.capability || "capability")}</span>{" "}
                <Badge tone={String(item.priority || "P3")}>{String(item.priority || "P3")}</Badge>
                <div style={{ color: "var(--color-text-tertiary)" }}>
                  {String(item.next_engineering_step || item.why || "")}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {history.length && !rounds.length ? (
        <div style={{ color: "var(--color-text-tertiary)" }}>
          Loop history: {history.length} recorded round(s).
        </div>
      ) : null}
      {result.bundle_path ? (
        <div style={{ color: "var(--color-text-tertiary)" }}>Local bundle: {String(result.bundle_path)}</div>
      ) : null}
      {result.message ? <div style={{ color: "#8a5b00" }}>{String(result.message)}</div> : null}
    </div>
  );
}

function ToolOntologyView({ result }: { result: Record<string, unknown> }) {
  const ontology = (result.ontology || {}) as Record<string, unknown>;
  const categories = (ontology.categories || {}) as Record<string, unknown>;
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Tool Ontology</strong>
        <Badge tone="compressed_preliminary">{`${String(result.cluster_count || ontology.cluster_count || 0)} cluster(s)`}</Badge>
      </div>
      {Object.entries(categories).slice(0, 8).map(([category, clusters]) => (
        <div key={category} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
          <strong style={{ color: "var(--color-text-primary)" }}>{category}</strong>
          <div style={{ color: "var(--color-text-tertiary)" }}>
            {asArray<Record<string, unknown>>(clusters)
              .slice(0, 4)
              .map((cluster) => `${String(cluster.canonical_capability)} (${String(cluster.paper_count || 0)} papers, ${String(cluster.status || "unknown")})`)
              .join(" · ")}
          </div>
        </div>
      ))}
    </div>
  );
}

function ToolGapMatrixView({ result }: { result: Record<string, unknown> }) {
  const rows = asArray<Record<string, unknown>>(result.gap_matrix);
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Tool Gap Matrix</strong>
        <Badge tone={rows.some((row) => row.current_status === "missing") ? "config_only" : "compressed_preliminary"}>
          {`${String(result.gap_count || rows.length)} capability row(s)`}
        </Badge>
      </div>
      {rows.slice(0, 10).map((row, index) => (
        <div
          key={`${row.capability || index}`}
          title={String(row.implementation_gap || "")}
          style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}
        >
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <strong style={{ color: "var(--color-text-primary)" }}>{String(row.capability || "capability")}</strong>
            <Badge>{String(row.tool_category || "tool")}</Badge>
            <Badge tone={String(row.current_status || "missing")}>{String(row.current_status || "missing")}</Badge>
            <Badge tone={String(row.priority || "P3")}>{String(row.priority || "P3")}</Badge>
          </div>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 2 }}>
            {String(row.research_value || "")}
          </div>
        </div>
      ))}
    </div>
  );
}

function ToolImplementationQueueView({ result }: { result: Record<string, unknown> }) {
  const queue = asArray<Record<string, unknown>>(result.implementation_queue);
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Implementation Queue</strong>
        <Badge tone="config_only">{`${String(result.queue_size || queue.length)} item(s)`}</Badge>
      </div>
      {queue.slice(0, 10).map((item, index) => (
        <div key={`${item.capability || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <strong style={{ color: "var(--color-text-primary)" }}>
              #{String(item.rank || index + 1)} {String(item.capability || "capability")}
            </strong>
            <Badge tone={String(item.priority || "P3")}>{String(item.priority || "P3")}</Badge>
            <Badge tone={String(item.current_status || "missing")}>{String(item.current_status || "missing")}</Badge>
          </div>
          <div style={{ color: "var(--color-text-tertiary)", marginTop: 2 }}>
            {String(item.next_engineering_step || item.why || "")}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function ResearchProgramPanel({ result }: { result: Record<string, unknown> }) {
  const normalized = (
    result.result && typeof result.result === "object" && !Array.isArray(result.result)
      ? (result.result as Record<string, unknown>)
      : result
  );
  const plan = (normalized.research_plan || {}) as ResearchPlan;
  const status = String(normalized.analysis_status || "");
  // Defensive: if result_provenance's status whitelist rewrites a domain-
  // specific status into a generic one (e.g. PARTIAL / COMPLETED / EMPTY),
  // fall back to payload presence so the panel still renders. Generic
  // statuses are not informative enough to pick a subview by themselves.
  const isGenericStatus = !status
    || status === "PARTIAL"
    || status === "COMPLETED"
    || status === "EMPTY";
  const hasPlan = status === "RESEARCH_PLAN_READY"
    || (isGenericStatus && Boolean(normalized.research_plan) && !Array.isArray(normalized.matrix));
  const hasMatrix = status.startsWith("RESEARCH_MATRIX")
    || (isGenericStatus && Array.isArray(normalized.matrix));
  const hasEvidence = status === "EVIDENCE_GRAPH_READY"
    || (isGenericStatus && Boolean(normalized.evidence_graph));
  const hasFactCheck = status === "FACT_CHECK_READY"
    || (isGenericStatus && Boolean(normalized.fact_check_report));
  const hasPaperMiningCandidate = status.startsWith("PAPER_MINING_CANDIDATE_POOL");
  const hasPaperMiningLoop = status.startsWith("PAPER_TOOL_MINING_LOOP");
  const hasPaperMining = status.startsWith("PAPER_TOOL_MINING") && !hasPaperMiningLoop;
  const hasToolOntology = status === "TOOL_ONTOLOGY_READY";
  const hasToolGap = status === "TOOL_GAP_MATRIX_READY";
  const hasToolQueue = status === "TOOL_IMPLEMENTATION_QUEUE_READY";
  const hasReport = status === "RESEARCH_REPORT_READY";
  // Final fallback: if NO subview matched, show PanelEmptyState with a
  // status-aware message instead of leaving the card body blank.
  const hasAnySubview =
    hasPlan || hasMatrix || hasEvidence || hasFactCheck
    || hasPaperMiningCandidate || hasPaperMiningLoop || hasPaperMining
    || hasToolOntology || hasToolGap || hasToolQueue || hasReport;

  return (
    <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      {hasPlan ? <PlanView plan={plan} /> : null}
      {hasMatrix ? <MatrixView result={normalized} /> : null}
      {hasEvidence ? <EvidenceView result={normalized} /> : null}
      {hasFactCheck ? <FactCheckView result={normalized} /> : null}
      {hasPaperMiningCandidate ? <PaperCandidatePoolView result={normalized} /> : null}
      {hasPaperMiningLoop ? <PaperToolMiningLoopView result={normalized} /> : null}
      {hasPaperMining ? <PaperToolMiningView result={normalized} /> : null}
      {hasToolOntology ? <ToolOntologyView result={normalized} /> : null}
      {hasToolGap ? <ToolGapMatrixView result={normalized} /> : null}
      {hasToolQueue ? <ToolImplementationQueueView result={normalized} /> : null}
      {hasReport ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Research Report Draft</strong>
          <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0", fontSize: "0.72rem" }}>
            {String(normalized.markdown || "")}
          </pre>
          {normalized.paper_draft_markdown ? (
            <>
              <strong style={{ color: "var(--color-text-primary)", display: "block", marginTop: 10 }}>
                Paper Draft
              </strong>
              <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0", fontSize: "0.72rem" }}>
                {String(normalized.paper_draft_markdown || "")}
              </pre>
            </>
          ) : null}
        </div>
      ) : null}
      {!hasAnySubview ? (
        <PanelEmptyState
          status={status || "UNKNOWN"}
          message={
            typeof normalized.__message_to_model__ === "string"
              ? normalized.__message_to_model__
              : typeof normalized.error === "string"
                ? normalized.error
                : undefined
          }
        />
      ) : null}
    </div>
  );
}
