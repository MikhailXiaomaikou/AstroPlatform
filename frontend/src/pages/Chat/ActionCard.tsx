// Chat action cards: per-tool status classification, the live/final
// tool-turn summary banner, and the memoized ActionCard renderer.
// Moved verbatim from ChatPage.tsx (behavior-preserving split).
import { memo, Suspense } from "react";
import type { ChatAction } from "../../api/client";
import ResearchProgramPanel from "../../components/chat/ResearchProgramPanel";
import DataSourcesPanel from "../../components/chat/DataSourcesPanel";
import AckButton from "../../components/chat/AckButton";
import ErrorBoundary from "../../components/ErrorBoundary";
import { useConversationProvenance, type ConversationProvenance } from "../../hooks/useConversationProvenance";
import { ActionResult, PlotBuilder } from "./ActionResultViews";
import { AutoToolResult } from "./AutoToolResult";
import { ProvenanceLink } from "./ProvenanceLink";

function isNumericSanityWarning(message: string): boolean {
  const text = message.toLowerCase();
  return (
    text.includes("negative parallax")
    || text.includes("derived distance is meaningless")
    || text.includes("unphysical absolute magnitude")
    || text.includes("outside [0, 360)")
    || text.includes("outside [-90, 90]")
    || text.includes("outside [-30, 20]")
    || text.includes("blueshift this large")
    || text.includes("log g")
    || text.includes("distance modulus")
    || text.includes("negative; unphysical")
    || text.includes("exactly zero; check units")
    || text.includes("exactly zero; check abundance")
  );
}

function resultForAction(
  action: ChatAction,
  index: number,
  actionResults?: Map<number, Record<string, unknown>>,
): Record<string, unknown> | undefined {
  const streamedResult = actionResults?.get(index);
  if (streamedResult && typeof streamedResult === "object") return streamedResult;
  const embeddedResult = (action as Record<string, unknown>).tool_result;
  return embeddedResult && typeof embeddedResult === "object"
    ? embeddedResult as Record<string, unknown>
    : undefined;
}

export function VisibleResearchDiagnostics({
  actions,
  actionResults,
}: {
  actions: ChatAction[];
  actionResults?: Map<number, Record<string, unknown>>;
}) {
  const primary = actions
    .map((action, index) => ({
      tool: String(action.action || ""),
      result: resultForAction(action, index, actionResults),
    }))
    .find(({ tool, result }) => (
      tool === "run_research_matrix"
      && result
      && (
        (Array.isArray(result.matrix) && result.matrix.length > 0)
        || (typeof result.research_charts === "object" && result.research_charts !== null)
      )
    ));

  if (!primary?.result) return null;

  return (
    <div className="chat-visible-research-diagnostics">
      <ResearchProgramPanel result={primary.result} />
    </div>
  );
}

function getActionToolResult(action: ChatAction): Record<string, unknown> | undefined {
  const result = (action as Record<string, unknown>).tool_result;
  return result && typeof result === "object" ? result as Record<string, unknown> : undefined;
}

function isSyntheticToolAction(action: ChatAction): boolean {
  const result = getActionToolResult(action);
  if (!result) return false;
  const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  const origin = String(result.data_origin || "").toLowerCase();
  if (
    status === "FAILED"
    || status === "UNAVAILABLE"
    || result.success === false
    || (typeof result.error === "string" && result.error.trim() !== "")
  ) {
    return false;
  }
  return status === "SYNTHETIC" || origin === "synthetic";
}

function isUnavailableToolAction(action: ChatAction): boolean {
  const result = getActionToolResult(action);
  if (!result) return false;
  const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  return status === "UNAVAILABLE";
}

function summarizeTurnActions(actions: ChatAction[] | undefined) {
  const seen = new Set<string>();
  const acts = (actions || []).filter((action) => {
    const id = String((action as Record<string, unknown>)._tool_call_id || "");
    if (!id) return true;
    const key = `${String((action as Record<string, unknown>).action || "")}:${id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const synthetic = acts.filter(isSyntheticToolAction);
  const unavailable = acts.filter(isUnavailableToolAction);
  const failed = acts.filter((action) => {
    if (isSyntheticToolAction(action) || isUnavailableToolAction(action)) return false;
    const result = getActionToolResult(action);
    if (!result) return false;
    const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
    return status !== "PARTIAL" && status !== "EMPTY" && (
      status === "FAILED"
      || result.success === false
      || (typeof result.error === "string" && result.error.trim() !== "")
    );
  });
  const empty = acts.filter((action) => {
    if (isSyntheticToolAction(action)) return false;
    const result = getActionToolResult(action);
    if (!result) return false;
    const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
    return status === "EMPTY";
  });
  const total = acts.filter((action) => {
    const record = action as Record<string, unknown>;
    return !!record._auto_executed || !!record.tool_result;
  }).length;
  return { synthetic, unavailable, failed, empty, total };
}

export function ToolTurnSummary({
  actions,
  live = false,
}: {
  actions?: ChatAction[];
  live?: boolean;
}) {
  const { synthetic, unavailable, failed, empty, total } = summarizeTurnActions(actions);
  if ((failed.length + empty.length + synthetic.length + unavailable.length) === 0 || total === 0) return null;

  const hasSynthetic = synthetic.length > 0;
  const noDataCount = unavailable.length + failed.length + empty.length;
  return (
    <details
      className="chat-reply-failure-preamble"
      style={hasSynthetic ? {
        borderLeftColor: "var(--color-red)",
        background: "rgba(255, 69, 58, 0.1)",
      } : undefined}
      open={live || hasSynthetic}
    >
      <summary>
        {hasSynthetic ? "⚠⚠ " : "⚠ "}
        {live && <strong>Live tool summary: </strong>}
        {synthetic.length > 0 && (
          <strong>{synthetic.length} SYNTHETIC{synthetic.length === 1 ? "" : ""}</strong>
        )}
        {synthetic.length > 0 && noDataCount > 0 ? " + " : ""}
        {noDataCount > 0 && (
          <span>{noDataCount} no-data</span>
        )}
        {` of ${total} tool${total === 1 ? "" : "s"} this turn. `}
        {hasSynthetic && (
          <strong>
            Facts, numbers, and conclusions from synthetic tools are NOT observations — do NOT use them.
          </strong>
        )}
      </summary>
      <div style={{ marginTop: 6, fontSize: "0.78rem" }}>
        {synthetic.length > 0 && (
          <div style={{ color: "var(--color-red)" }}>
            <strong>Synthetic:</strong> {synthetic.map((action) => action.action).join(", ")}
          </div>
        )}
        {failed.length > 0 && <div>Failed: {failed.map((action) => action.action).join(", ")}</div>}
        {unavailable.length > 0 && <div>Maintenance: {unavailable.map((action) => action.action).join(", ")}</div>}
        {empty.length > 0 && <div>Empty: {empty.map((action) => action.action).join(", ")}</div>}
      </div>
    </details>
  );
}

function ActionCardInner({
  action,
  index,
  result,
  onExecute,
  executing,
  conversationProvenance,
  onCopyAcknowledgement,
  readOnly = false,
}: {
  action: ChatAction;
  index: number;
  result?: Record<string, unknown>;
  onExecute: (index: number, action: ChatAction) => void;
  executing: boolean;
  conversationProvenance?: ConversationProvenance;
  onCopyAcknowledgement?: () => void;
  // Read-only rendering (shared session pages): tool evidence is shown,
  // but nothing is executable and the per-process provenance link (which
  // needs the owner's live backend session) is hidden.
  readOnly?: boolean;
}) {
  const labels: Record<string, string> = {
    search: "Search databases",
    search_objects: "Search databases",
    adql: "Run ADQL query",
    run_adql: "Run ADQL query",
    arxiv: "Extract arXiv tables",
    run_pipeline: "Run pipeline",
    explain: "Explanation",
    plot: "Interactive Plot",
    generate_pipeline: "Generate Pipeline",
    modify_pipeline: "Modify Pipeline",
    comment_pipeline: "Comment on Pipeline",
    get_object_info: "Object Info",
    analyze_spectrum: "Spectrum Analysis",
    search_literature: "Literature Search",
    extract_literature_tables: "Extract Literature Tables",
    fit_line_lfr: "Fit Line Relation",
    get_last_search_results: "Search Results",
    read_arxiv_paper: "Read Paper",
    run_python: "Python Code",
    fit_cosmology_mcmc: "Cosmology MCMC",
    list_cosmology_datasets: "Cosmology Datasets",
    build_cosmology_likelihood: "Likelihood Builder",
    build_cosmology_robustness_matrix: "Robustness Matrix",
    run_cosmology_likelihood_chain: "Cosmology Chain",
    run_cmb_rotation_likelihood: "CMB Rotation",
    run_cosmology_robustness_matrix: "Robustness Run",
    assess_bao_bin_anomaly: "BAO Bin Diagnostic",
    run_nested_sampler: "Nested Sampler",
    evaluate_chain_diagnostics: "Chain Diagnostics",
    plan_research_program: "Research Plan",
    run_research_matrix: "Research Matrix",
    build_evidence_graph: "Evidence Graph",
    verify_research_facts: "Fact Check",
    export_research_report: "Research Report",
    build_paper_mining_candidate_pool: "Paper Candidate Pool",
    mine_paper_tools: "Paper Tool Mining",
    run_paper_tool_mining_batch: "Paper Mining Batch",
    build_tool_ontology: "Tool Ontology",
    build_tool_gap_matrix: "Tool Gap Matrix",
    rank_tool_implementation_queue: "Implementation Queue",
    run_paper_tool_mining_loop: "Mining Loop",
    run_cobaya_cosmology: "Cobaya Cosmology",
    get_cosmology_run_status: "Cosmology Job Status",
    load_cosmology_data_product: "Cosmology Data Product",
  };

  const icons: Record<string, string> = {
    search: "🔍",
    search_objects: "🔍",
    arxiv: "📄",
    adql: "📊",
    run_adql: "📊",
    run_pipeline: "⚙️",
    explain: "📖",
    plot: "📈",
    generate_pipeline: "🔧",
    modify_pipeline: "✏️",
    comment_pipeline: "💬",
    get_object_info: "🌌",
    analyze_spectrum: "🔬",
    search_literature: "📚",
    extract_literature_tables: "📄",
    fit_line_lfr: "📈",
    get_last_search_results: "📋",
    read_arxiv_paper: "📄",
    run_python: "🐍",
    fit_cosmology_mcmc: "📉",
    list_cosmology_datasets: "🧭",
    build_cosmology_likelihood: "🧩",
    build_cosmology_robustness_matrix: "▦",
    run_cosmology_likelihood_chain: "📉",
    run_cmb_rotation_likelihood: "🌀",
    run_cosmology_robustness_matrix: "▦",
    run_nested_sampler: "📉",
    evaluate_chain_diagnostics: "📋",
    plan_research_program: "🧭",
    run_research_matrix: "▦",
    build_evidence_graph: "🔗",
    verify_research_facts: "✓",
    export_research_report: "📝",
    build_paper_mining_candidate_pool: "📚",
    mine_paper_tools: "⛏",
    run_paper_tool_mining_batch: "⛏",
    build_tool_ontology: "🗂",
    build_tool_gap_matrix: "▦",
    rank_tool_implementation_queue: "📋",
    run_paper_tool_mining_loop: "🔁",
    run_cobaya_cosmology: "📉",
    get_cosmology_run_status: "⏱",
    load_cosmology_data_product: "📦",
  };

  const isAutoExecuted = !!(action as Record<string, unknown>)._auto_executed;
  const autoResult = (action as Record<string, unknown>).tool_result as Record<string, unknown> | undefined;
  const cardResult = autoResult && typeof autoResult === "object" ? autoResult : result;
  const toolProvenance = useConversationProvenance(cardResult);

  // R3: surface backend warnings on the card as a ⚠ chip.  Numeric
  // physical-range checks keep the "sanity warning" label; extraction/schema
  // warnings use a broader "data warning" label so raw-table issues are not
  // confused with physically impossible values.
  const rawWarnings = cardResult && typeof cardResult === "object" && "warnings" in cardResult
    ? (cardResult as { warnings?: unknown }).warnings
    : null;
  const warningMessages: string[] = Array.isArray(rawWarnings)
    ? rawWarnings.filter((w): w is string => typeof w === "string")
    : [];
  const hasNumericSanityWarning = warningMessages.some(isNumericSanityWarning)
    || (cardResult && typeof cardResult === "object" && typeof (cardResult as Record<string, unknown>).cmd_sanity_error === "string");
  const warningLabel = hasNumericSanityWarning ? "sanity warning" : "data warning";

  // F3.1: loud failure/empty surfacing.  The backend stamps
  // __tool_status__ = FAILED or EMPTY (F2.1), and analysis_status =
  // failed / empty on dead/no-data tool returns.  Turn the whole card
  // red / amber so the user sees it alongside the model's prose.
  // G4/R22: completed synthetic output is loud, but sandbox crash/OOM must
  // still render as Failed so the user sees the execution problem first.
  const toolStatus = cardResult && typeof cardResult === "object"
    ? String((cardResult as Record<string, unknown>).__tool_status__ || (cardResult as Record<string, unknown>).analysis_status || "").toUpperCase()
    : "";
  const dataOrigin = cardResult && typeof cardResult === "object"
    ? String((cardResult as Record<string, unknown>).data_origin || "").toLowerCase()
    : "";
  const hasErrorField = cardResult && typeof cardResult === "object"
    && typeof (cardResult as Record<string, unknown>).error === "string"
    && ((cardResult as Record<string, unknown>).error as string).trim() !== "";
  const explicitFail = cardResult && typeof cardResult === "object"
    && (cardResult as Record<string, unknown>).success === false;
  const errorClass = cardResult && typeof cardResult === "object"
    ? String((cardResult as Record<string, unknown>).error_class || "").toLowerCase()
    : "";
  const malformedRunPython = action.action === "run_python"
    && isAutoExecuted
    && cardResult
    && typeof cardResult === "object"
    && (
      Object.keys(cardResult).length === 0
      || errorClass === "subprocesscrash"
      || errorClass === "sandbox_crash"
    );
  const fatalRunPythonFailure = action.action === "run_python"
    && (
      malformedRunPython
      || errorClass === "oom"
      || errorClass === "sigsegv"
      || errorClass === "sandbox_crash"
      || errorClass === "subprocesscrash"
      || errorClass === "timeout"
    )
    && (explicitFail || hasErrorField || toolStatus === "FAILED" || malformedRunPython);
  const isToolSynthetic = !fatalRunPythonFailure && (toolStatus === "SYNTHETIC" || dataOrigin === "synthetic");
  const isToolPartial = !isToolSynthetic && toolStatus === "PARTIAL";
  const isToolUnavailable = !isToolSynthetic && !isToolPartial && toolStatus === "UNAVAILABLE";
  const isToolFailed = !isToolSynthetic && !isToolPartial && !isToolUnavailable && (toolStatus === "FAILED" || explicitFail || hasErrorField || malformedRunPython || fatalRunPythonFailure);
  const isToolEmpty = !isToolSynthetic && !isToolFailed && toolStatus === "EMPTY";

  return (
    <div
      className={`chat-action-card${isAutoExecuted ? " auto-executed" : ""}${isToolFailed ? " tool-failed" : ""}${isToolUnavailable ? " tool-unavailable" : ""}${isToolEmpty ? " tool-empty" : ""}${isToolSynthetic ? " tool-synthetic" : ""}`}
    >
      <div className="chat-action-header">
        <span className="chat-action-icon">
          {icons[action.action] || "▶"}
        </span>
        <span className="chat-action-label">
          {labels[action.action] || action.action}
          {isAutoExecuted && (
            <span
              className={`auto-badge${isToolFailed ? " failed" : ""}${isToolUnavailable ? " unavailable" : ""}${isToolEmpty ? " empty" : ""}${isToolSynthetic ? " failed" : ""}`}
              title={isToolSynthetic ? "Synthetic data — not from real observations" : isToolPartial ? "Tool produced partial output before failing" : isToolUnavailable ? "Tool source temporarily unavailable for provenance maintenance" : isToolFailed ? "Tool failed" : isToolEmpty ? "Tool returned no data" : "Auto-executed"}
            >
              {isToolSynthetic ? "⚠ SYNTHETIC" : isToolPartial ? "⚠ Partial" : isToolUnavailable ? "Maintenance" : isToolFailed ? "❌ Failed" : isToolEmpty ? "∅ Empty" : "auto"}
            </span>
          )}
          {warningMessages.length > 0 && (
            <span
              className="sanity-warning-chip"
              title={warningMessages.join("\n")}
              aria-label={`${warningLabel}: ${warningMessages.join("; ")}`}
              style={{
                marginLeft: 6,
                padding: "1px 6px",
                border: "1px solid #b8860b",
                borderRadius: 4,
                background: "#fff7e6",
                color: "#8a6a00",
                fontSize: "0.75em",
                cursor: "help",
              }}
            >
              ⚠ {warningMessages.length} {warningLabel}{warningMessages.length === 1 ? "" : "s"}
            </span>
          )}
        </span>
        {!readOnly && !isAutoExecuted && action.action !== "explain" && action.action !== "comment_pipeline" && !result && (
          <button
            className="btn-chat-action"
            onClick={() => onExecute(index, action)}
            disabled={executing}
          >
            {executing ? "Running..." : action.action === "generate_pipeline" ? "Create Pipeline" : "Execute"}
          </button>
        )}
      </div>
      {/* K1.C: When tool_result carries __message_to_model__ (typically SYNTHETIC /
          FAILED / EMPTY results do), show the first 400 characters to the user so a
          reviewer can immediately tell whether "the AI itself declared the wrong data_source"
          or "the system detected and flagged synthetic output".
          Only shown for SYNTHETIC to avoid adding an extra block to every FAILED/EMPTY card. */}
      {isToolSynthetic && autoResult && typeof (autoResult as Record<string, unknown>).__message_to_model__ === "string" && (
        <div
          className="chat-action-system-note"
          style={{
            margin: "0 0 0.5rem 0",
            padding: "6px 10px",
            fontSize: "0.72rem",
            lineHeight: 1.45,
            color: "var(--color-text-secondary, #666)",
            background: "rgba(255, 69, 58, 0.06)",
            borderLeft: "2px solid rgba(255, 69, 58, 0.4)",
            borderRadius: "2px",
          }}
        >
          {String((autoResult as Record<string, unknown>).__message_to_model__).slice(0, 400)}
          {String((autoResult as Record<string, unknown>).__message_to_model__).length > 400 ? "…" : ""}
        </div>
      )}
      <div className="chat-action-detail">
        {action.action === "search" && (
          <span>
            Query: <strong>{action.query as string}</strong> | Sources:{" "}
            {(action.sources as string[])?.join(", ") || "all"}
          </span>
        )}
        {action.action === "adql" && (
          <code className="chat-action-code">{action.query as string}</code>
        )}
        {action.action === "run_pipeline" && (
          <span>
            {((action.nodes as Array<{ type: string }>) || [])
              .map((n) => n.type)
              .join(" → ")}
          </span>
        )}
        {action.action === "explain" && (
          <span>Topic: {action.topic as string}</span>
        )}
        {action.action === "plot" && (
          <span>Chart: {(action.chart_type as string) || "auto"}</span>
        )}
        {action.action === "arxiv" && (
          <span>Paper: {action.arxiv_id as string}</span>
        )}
        {action.action === "generate_pipeline" && (
          <span>
            <strong>{String(action.name || "")}</strong>
            {action.description ? <> — {String(action.description)}</> : null}
            <br />
            {((action.dag as { nodes: Array<{ type: string }> })?.nodes || [])
              .map((n: { type: string }) => n.type)
              .join(" → ")}
          </span>
        )}
        {action.action === "modify_pipeline" && (
          <span>
            {((action.modifications as Array<{ action: string }>) || [])
              .map((m: { action: string }) => m.action)
              .join(", ")}
            {action.explanation ? <> — {String(action.explanation)}</> : null}
          </span>
        )}
        {action.action === "comment_pipeline" && (
          <span>{String(action.comment || "").slice(0, 100)}...</span>
        )}
      </div>
      {action.action === "plot" && (
        <div className="chat-plot-wrapper">
          <ErrorBoundary label="the chat plot">
            <Suspense fallback={<div className="fits-loading">Loading plot...</div>}>
              <PlotBuilder
                initialData={action.data as Record<string, unknown>}
                initialChartType={action.chart_type as string}
              />
            </Suspense>
          </ErrorBoundary>
        </div>
      )}
      {result && <ActionResult result={result} />}
      {isAutoExecuted && autoResult && !result && (
        <div className="chat-action-result auto-result">
          <DataSourcesPanel summary={toolProvenance} />
          <AckButton
            summary={conversationProvenance || toolProvenance}
            onCopied={onCopyAcknowledgement}
          />
          <AutoToolResult toolName={action.action} result={autoResult} />
          {/* 2026-07-03: provenance-ledger entry point for the run_id shown
              in this result's reproducibility envelope. Hidden in read-only
              (shared) views — the ledger is per-process on the owner's
              backend and would only ever answer for the live session. */}
          {!readOnly && <ProvenanceLink result={autoResult} />}
        </div>
      )}
    </div>
  );
}

// F5.2: harden action-card identity across streaming re-renders.  Without
// React.memo, every SSE tool_result event re-renders the ENTIRE message
// tree and remounts earlier cards, which invalidates refs held by ongoing
// focus/keyboard interactions (the "ref_60/ref_116 not found" error the
// reviewer saw).  Memo keyed on stable identity: tool name + reproducibility
// run_id when present, else the action index.
export const ActionCard = memo(ActionCardInner, (prev, next) => {
  if (prev.action !== next.action) return false;
  if (prev.index !== next.index) return false;
  if (prev.executing !== next.executing) return false;
  if (prev.conversationProvenance !== next.conversationProvenance) return false;
  // Result may mutate in-place when a deferred tool finishes — compare by
  // reproducibility.run_id when present (stable for the lifetime of the
  // tool call), otherwise by identity.
  const prevResult = prev.result as Record<string, unknown> | undefined;
  const nextResult = next.result as Record<string, unknown> | undefined;
  const prevRunId = (prevResult?.reproducibility as Record<string, unknown> | undefined)?.run_id as string | undefined;
  const nextRunId = (nextResult?.reproducibility as Record<string, unknown> | undefined)?.run_id as string | undefined;
  if (prevRunId || nextRunId) return prevRunId === nextRunId;
  return prevResult === nextResult;
});
