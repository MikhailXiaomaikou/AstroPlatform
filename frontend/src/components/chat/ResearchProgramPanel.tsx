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
          title={String(claim.safe_rewrite || "No rewrite needed.")}
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
  const plan = (result.research_plan || {}) as ResearchPlan;
  const status = String(result.analysis_status || "");
  return (
    <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
      {status === "RESEARCH_PLAN_READY" ? <PlanView plan={plan} /> : null}
      {status.startsWith("RESEARCH_MATRIX") ? <MatrixView result={result} /> : null}
      {status === "EVIDENCE_GRAPH_READY" ? <EvidenceView result={result} /> : null}
      {status === "FACT_CHECK_READY" ? <FactCheckView result={result} /> : null}
      {status.startsWith("PAPER_MINING_CANDIDATE_POOL") ? <PaperCandidatePoolView result={result} /> : null}
      {status.startsWith("PAPER_TOOL_MINING_LOOP") ? <PaperToolMiningLoopView result={result} /> : null}
      {status.startsWith("PAPER_TOOL_MINING") && !status.startsWith("PAPER_TOOL_MINING_LOOP") ? (
        <PaperToolMiningView result={result} />
      ) : null}
      {status === "TOOL_ONTOLOGY_READY" ? <ToolOntologyView result={result} /> : null}
      {status === "TOOL_GAP_MATRIX_READY" ? <ToolGapMatrixView result={result} /> : null}
      {status === "TOOL_IMPLEMENTATION_QUEUE_READY" ? <ToolImplementationQueueView result={result} /> : null}
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
