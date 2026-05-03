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
  if (key.includes("config") || key === "mixed") {
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
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong style={{ color: "var(--color-text-primary)" }}>Research Matrix</strong>
        <Badge tone={result.publication_ready ? "compressed_preliminary" : "config_only"}>
          {result.publication_ready ? "runnable cells ready" : "partial"}
        </Badge>
        <span>{String(result.ready_cells || 0)} / {String(result.matrix_size || matrix.length)} ready</span>
      </div>
      {matrix.slice(0, 10).map((cell, index) => (
        <div key={`${cell.label || index}`} style={{ borderTop: "1px solid var(--color-border)", paddingTop: 5 }}>
          <strong style={{ color: "var(--color-text-primary)" }}>{String(cell.label || `Cell ${index + 1}`)}</strong>
          <div style={{ color: "var(--color-text-tertiary)" }}>
            {asArray<string>(cell.dataset_keys).join(" + ")} · {String(cell.model || "lcdm")} ·{" "}
            {cell.publication_ready ? "compressed preliminary posterior" : String(cell.execution_level || "not runnable")}
          </div>
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

export default function ResearchProgramPanel({ result }: { result: Record<string, unknown> }) {
  const plan = (result.research_plan || {}) as ResearchPlan;
  const status = String(result.analysis_status || "");
  return (
    <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      {status === "RESEARCH_PLAN_READY" ? <PlanView plan={plan} /> : null}
      {status.startsWith("RESEARCH_MATRIX") ? <MatrixView result={result} /> : null}
      {status === "EVIDENCE_GRAPH_READY" ? <EvidenceView result={result} /> : null}
      {status === "RESEARCH_REPORT_READY" ? (
        <div>
          <strong style={{ color: "var(--color-text-primary)" }}>Research Report Draft</strong>
          <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0", fontSize: "0.72rem" }}>
            {String(result.markdown || "")}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
