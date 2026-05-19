import { useState, useMemo, useRef, useEffect, useCallback, lazy, memo, Suspense } from "react";
import type { MouseEvent } from "react";
import {
  sendChatMessage,
  executeChatAction,
  getStoredApiKeys,
  writeStoredApiKeys,
  writeStoredAiProvider,
  getPreferredAiProvider,
  getPreferredAiModelProfile,
  AI_MODEL_OPTIONS,
  searchADS,
  getBibTeX,
  logOperation,
  uploadFITS,
  uploadGeneralFile,
  saveChatSession,
  renameChatSession,
  listChatSessions,
  loadChatSession,
  deleteChatSession,
  importChatSession,
  createSessionShare,
  listSessionShares,
  revokeSessionShare,
  createSessionSnapshot,
  listSessionSnapshots,
  restoreSessionSnapshot,
  diffSessionSnapshots,
  exportChatMarkdown,
  exportChatNotebook,
  exportChatHtml,
  exportChatLatex,
  exportChatBibTeX,
  generatePaperDraft,
  publishPaperDraft,
  unpublishPaperDraft,
  type ChatMessage,
  type ChatAction,
  type ThinkingEvent,
  type ADSReference,
  type ChatSessionSummary,
  type SessionShareItem,
  type SessionSnapshotItem,
  type SessionSnapshotDiff,
  type AnalysisValidationResult,
  type PaperDraftResponse,
  type AIModelProfile,
  updatePaperDraft,
  validatePaperSession,
} from "../../api/client";
import MarkdownText from "../../components/chat/MarkdownText";
import AckButton from "../../components/chat/AckButton";
import CosmologyMCMCPanel from "../../components/chat/CosmologyMCMCPanel";
import CosmologyLikelihoodPanel from "../../components/chat/CosmologyLikelihoodPanel";
import ResearchProgramPanel from "../../components/chat/ResearchProgramPanel";
import DataSourcesPanel from "../../components/chat/DataSourcesPanel";
import ErrorBoundary from "../../components/ErrorBoundary";
import { useI18n } from "../../i18n";
import { useAuth } from "../../context/AuthContext";
import { useTracking } from "../../hooks/useTracking";
import { useConversationProvenance, type ConversationProvenance } from "../../hooks/useConversationProvenance";
import { readWorkspaceCache, registerWorkspaceExport } from "../../utils/workspaceCache";
const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

/* Fullscreen image/plot modal */
function FullscreenModal({ src, onClose }: { src: string; onClose: () => void }) {
  const handleDownload = () => {
    const a = document.createElement("a");
    a.href = src;
    a.download = `astro_figure_${Date.now()}.png`;
    a.click();
  };
  return (
    <div className="figure-modal-backdrop" onClick={onClose}>
      <div className="figure-modal" onClick={(e) => e.stopPropagation()}>
        <img src={src} alt="Full-size figure" className="figure-modal-img" />
        <div className="figure-modal-toolbar">
          <button className="btn-secondary btn-small" onClick={handleDownload}>Download PNG</button>
          <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function ClickableFigure({ src, alt }: { src: string; alt: string }) {
  const [expanded, setExpanded] = useState(false);
  const handleDownload = () => {
    const a = document.createElement("a");
    a.href = src;
    a.download = `astro_figure_${Date.now()}.png`;
    a.click();
  };
  return (
    <>
      <div className="code-figure-wrapper">
        <img src={src} alt={alt} className="code-figure" onClick={() => setExpanded(true)} title="Click to expand" />
        <div className="code-figure-actions">
          <button className="btn-secondary btn-small" onClick={() => setExpanded(true)}>Expand</button>
          <button className="btn-secondary btn-small" onClick={handleDownload}>Download</button>
        </div>
      </div>
      {expanded && <FullscreenModal src={src} onClose={() => setExpanded(false)} />}
    </>
  );
}

type LineRelationPlotData = {
  x?: number[];
  y?: number[];
  labels?: string[];
  fit_line?: { x?: number[]; y?: number[] };
  x_label?: string;
  y_label?: string;
  n_points?: number;
};

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

function LineRelationPlot({ plotData }: { plotData: LineRelationPlotData }) {
  const x = Array.isArray(plotData.x) ? plotData.x.filter((v) => Number.isFinite(v)) : [];
  const y = Array.isArray(plotData.y) ? plotData.y.filter((v) => Number.isFinite(v)) : [];
  if (x.length < 2 || y.length < 2 || x.length !== y.length) return null;

  const width = 640;
  const height = 360;
  const pad = { left: 58, right: 22, top: 26, bottom: 52 };
  const fitX = (plotData.fit_line?.x || []).filter((v) => Number.isFinite(v));
  const fitY = (plotData.fit_line?.y || []).filter((v) => Number.isFinite(v));
  const minMax = (values: number[]) => {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return [min - span * 0.08, max + span * 0.08] as const;
  };
  const [xMin, xMax] = minMax([...x, ...fitX]);
  const [yMin, yMax] = minMax([...y, ...fitY]);
  const xScale = (v: number) => pad.left + ((v - xMin) / (xMax - xMin || 1)) * (width - pad.left - pad.right);
  const yScale = (v: number) => height - pad.bottom - ((v - yMin) / (yMax - yMin || 1)) * (height - pad.top - pad.bottom);
  const labels = Array.isArray(plotData.labels) ? plotData.labels : [];

  return (
    <figure className="line-relation-plot" aria-label="Line relation fit preview">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        <title>Line relation fit preview</title>
        <rect x={0} y={0} width={width} height={height} rx={4} fill="var(--color-surface, #fff)" />
        <line x1={pad.left} y1={height - pad.bottom} x2={width - pad.right} y2={height - pad.bottom} stroke="var(--color-separator, #ddd)" />
        <line x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} stroke="var(--color-separator, #ddd)" />
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const xv = xMin + (xMax - xMin) * t;
          const yv = yMin + (yMax - yMin) * t;
          return (
            <g key={t}>
              <line x1={xScale(xv)} y1={pad.top} x2={xScale(xv)} y2={height - pad.bottom} stroke="rgba(0,0,0,0.06)" />
              <line x1={pad.left} y1={yScale(yv)} x2={width - pad.right} y2={yScale(yv)} stroke="rgba(0,0,0,0.06)" />
              <text x={xScale(xv)} y={height - pad.bottom + 18} textAnchor="middle">{xv.toFixed(2)}</text>
              <text x={pad.left - 8} y={yScale(yv) + 4} textAnchor="end">{yv.toFixed(2)}</text>
            </g>
          );
        })}
        {fitX.length >= 2 && fitY.length >= 2 && (
          <line
            x1={xScale(fitX[0])}
            y1={yScale(fitY[0])}
            x2={xScale(fitX[1])}
            y2={yScale(fitY[1])}
            stroke="var(--color-accent, #9f3a38)"
            strokeWidth={2.5}
          />
        )}
        {x.map((xv, i) => (
          <circle key={`${xv}-${i}`} cx={xScale(xv)} cy={yScale(y[i])} r={3.3} fill="var(--color-ink, #1f1f1f)">
            <title>{labels[i] ? `${labels[i]}: ` : ""}{xv.toFixed(3)}, {y[i].toFixed(3)}</title>
          </circle>
        ))}
        <text x={(pad.left + width - pad.right) / 2} y={height - 12} textAnchor="middle">{plotData.x_label || "x"}</text>
        <text x={16} y={(pad.top + height - pad.bottom) / 2} textAnchor="middle" transform={`rotate(-90 16 ${(pad.top + height - pad.bottom) / 2})`}>
          {plotData.y_label || "y"}
        </text>
      </svg>
      <figcaption>
        Line fit preview from {plotData.n_points || x.length} cited measurement rows.
      </figcaption>
    </figure>
  );
}

interface ThinkingStep {
  kind: "agent_text" | "tool_call" | "tool_progress" | "tool_result" | "status" | "tools_disabled" | "workflow_budget" | "workflow_checkpoint";
  agent?: string;
  tool?: string;
  text?: string;
  input?: unknown;
  stage?: string;
  result?: unknown;
  mode?: string;
  cacheRefs?: string[];
  // G3.5 — backend stripped these tools from the toolkit this iteration.
  disabled?: string[];
  iteration?: number;
  maxIterations?: number;
}

interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: Map<number, Record<string, unknown>>;
  // Pending-marker set while the SSE stream is in flight.  A bubble with
  // _pending set is rendered as a spinner; on successful reply we replace
  // it with the real content, on error we rewrite it to an error bubble,
  // and on page reload we reconcile against the server copy or offer retry.
  _pending?: { started_at: number };
  // Live thinking timeline accumulated while _pending is true.  The user
  // sees what the agent is doing in real time (tool calls, intermediate
  // text between tool rounds).  Cleared when the message is replaced with
  // the final reply.
  _thinking?: ThinkingStep[];
  // F3.2: populated when the backend emits an honest_abstention SSE event.
  // Rendered as a distinctive pale-blue ✓ card instead of raw markdown.
  _abstention?: {
    failed_tools?: string;
    empty_tools?: string;
    rationale?: string;
    suggested_next_step?: string;
    reason?: string;
  };
  // R11-NEW-1: 当错误气泡需要操作按钮 (如 payload_too_large 引导"开始新聊天")
  // 时填此字段. UI 根据值 render 对应 CTA. 保持 undefined 不影响普通气泡.
  _action_hint?: "new_chat";
}

function getActionToolResult(action: ChatAction): Record<string, unknown> | undefined {
  const result = (action as Record<string, unknown>).tool_result;
  return result && typeof result === "object" ? result as Record<string, unknown> : undefined;
}

// Stage 3 Bug 4 修复: server 返回的 actions 字段类型是 unknown[], 不能直接强 cast
// 成 ChatAction[]. 这里过滤掉缺 `action` 字段或形状不对的脏数据, 防止 ActionCard
// 拿到不完整对象后渲染成空白卡片. 用在 refresh-resume / figure-rehydrate 两处.
function validateActions(raw: unknown): ChatAction[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (a): a is ChatAction =>
      a !== null
      && typeof a === "object"
      && typeof (a as { action?: unknown }).action === "string",
  );
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
    return status !== "PARTIAL" && (
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

function ToolTurnSummary({
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

function hasStoredAiKey(): boolean {
  const keys = getStoredApiKeys();
  return Object.values(keys).some((v) => typeof v === "string" && v.trim().length > 0);
}

function modelDisplayLabel(profile: AIModelProfile | null): string {
  const provider = getPreferredAiProvider() || "anthropic";
  const profileId = getPreferredAiModelProfile(provider);
  const localOption = (AI_MODEL_OPTIONS[provider] || []).find((option) => option.id === profileId);
  const label = profile?.display_name || localOption?.label || profileId || "Claude default";
  const resolved = profile?.resolved_model_id;
  if (profileId === "openai:gpt-5.5" && resolved && resolved !== "gpt-5.5") {
    return `${label} -> ${resolved} fallback`;
  }
  return label;
}

function buildMinimalChatHistory(messages: DisplayMessage[]): ChatMessage[] {
  return messages.map((message) => {
    if (message.role !== "assistant" || !message.actions?.length) {
      return { role: message.role, content: message.content };
    }

    const pythonReplayActions = message.actions
      .filter((action) => action.action === "run_python")
      .map((action) => {
        const raw = action as unknown as Record<string, unknown>;
        const toolInput = (raw.tool_input && typeof raw.tool_input === "object")
          ? raw.tool_input as Record<string, unknown>
          : (raw.params && typeof raw.params === "object" ? raw.params as Record<string, unknown> : {});
        const code = typeof toolInput.code === "string" ? toolInput.code : "";
        if (!code.trim()) {
          return null;
        }
        const rawResult = raw.tool_result && typeof raw.tool_result === "object"
          ? raw.tool_result as Record<string, unknown>
          : undefined;
        return {
          action: "run_python",
          tool_input: { code },
          tool_result: rawResult ? { success: rawResult.success !== false } : { success: true },
        } as unknown as ChatAction;
      })
      .filter((action): action is ChatAction => action !== null);

    return {
      role: message.role,
      content: message.content,
      actions: pythonReplayActions.length > 0 ? pythonReplayActions : undefined,
    };
  });
}

// F3.2: Honest-abstention card.  Rendered instead of raw markdown when
// the backend emitted <tools_returned_nothing/> as the model's reply.
// This is the UI celebration of model honesty — pale-blue ✓ bubble,
// lists failed/empty tools, shows rationale and suggested next step,
// and offers a single-click retry that pre-fills the suggestion.
function HonestAbstentionCard({
  abstention,
  onRetry,
}: {
  abstention: NonNullable<DisplayMessage["_abstention"]>;
  onRetry?: (suggestedInput: string) => void;
}) {
  const failed = (abstention.failed_tools || "").trim();
  const empty = (abstention.empty_tools || "").trim();
  const rationale = (abstention.rationale || "").trim();
  const nextStep = (abstention.suggested_next_step || "").trim();
  const reason = abstention.reason || "no_tools";

  const header: Record<string, string> = {
    empty: "Honest reply — tools returned no data",
    failed: "Honest reply — tools failed to run",
    mixed: "Honest reply — tools returned no data and some failed",
    no_tools: "Honest reply — no claims to make",
  };

  return (
    <div className="chat-abstention-card" role="note" aria-label="honest abstention">
      <div className="abstention-header">
        <span aria-hidden="true">✓</span>
        <span>{header[reason] || header.no_tools}</span>
        <span
          aria-label="What is an honest abstention?"
          title={
            "The AI saw every tool this turn return no usable data (empty or " +
            "errored).  Instead of inventing numbers to sound helpful, it " +
            "output a structured abstention tag, which the UI renders as " +
            "this card.  This is the model's expected behaviour when it " +
            "has no data — not a bug."
          }
          style={{
            marginLeft: "auto",
            fontSize: "0.8rem",
            opacity: 0.6,
            cursor: "help",
            fontWeight: 400,
          }}
        >
          ⓘ
        </span>
      </div>
      {(failed || empty) && (
        <div className="abstention-tools">
          {failed && <span>Failed: <code>{failed}</code> </span>}
          {empty && <span>Empty: <code>{empty}</code></span>}
        </div>
      )}
      {rationale && <div className="abstention-rationale">{rationale}</div>}
      {nextStep && (
        <div className="abstention-next-step">
          <strong>Suggested next step:</strong> {nextStep}
        </div>
      )}
      {!rationale && !nextStep && (
        <div className="abstention-rationale">
          No numerical claims are made because no tool produced data this
          turn.  Please rephrase your question, provide target values
          explicitly, or try the suggested next step above.
        </div>
      )}
      {onRetry && nextStep && (
        <button
          className="abstention-retry"
          onClick={() => onRetry(nextStep)}
          title="Pre-fill the input with the suggested next step"
        >
          Try it
        </button>
      )}
    </div>
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
}: {
  action: ChatAction;
  index: number;
  result?: Record<string, unknown>;
  onExecute: (index: number, action: ChatAction) => void;
  executing: boolean;
  conversationProvenance?: ConversationProvenance;
  onCopyAcknowledgement?: () => void;
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
    run_cosmology_robustness_matrix: "Robustness Run",
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
        {!isAutoExecuted && action.action !== "explain" && action.action !== "comment_pipeline" && !result && (
          <button
            className="btn-chat-action"
            onClick={() => onExecute(index, action)}
            disabled={executing}
          >
            {executing ? "Running..." : action.action === "generate_pipeline" ? "Create Pipeline" : "Execute"}
          </button>
        )}
      </div>
      {/* K1.C: 当 tool_result 带 __message_to_model__ 时(通常 SYNTHETIC /
          FAILED / EMPTY 会带), 把前 400 字给用户看一眼 — reviewer 能立刻
          区分"AI 自己声明错了 data_source"还是"系统检测判定合成".
          现在只在 SYNTHETIC 情况下展示, 避免每个 FAILED/EMPTY 卡片都多一块. */}
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
const ActionCard = memo(ActionCardInner, (prev, next) => {
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

function StatsPanel({ rows, extraCols, onClose }: {
  rows: Array<Record<string, unknown>>;
  extraCols: string[];
  onClose: () => void;
}) {
  const numericCols = useMemo(() => {
    const cols: Array<{ key: string; values: number[] }> = [];
    const checkCols = ["ra", "dec", "magnitude", "redshift", ...extraCols];
    for (const col of checkCols) {
      const vals: number[] = [];
      for (const row of rows) {
        const v = extraCols.includes(col)
          ? ((row.extra || {}) as Record<string, unknown>)[col]
          : row[col];
        if (typeof v === "number" && isFinite(v)) vals.push(v);
      }
      if (vals.length > 0) cols.push({ key: col, values: vals });
    }
    return cols;
  }, [rows, extraCols]);

  function median(arr: number[]): number {
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  function stdDev(arr: number[], mean: number): number {
    const sum = arr.reduce((s, v) => s + (v - mean) ** 2, 0);
    return Math.sqrt(sum / arr.length);
  }

  return (
    <div className="viz-overlay" onClick={onClose}>
      <div className="viz-overlay-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 700 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Statistics ({rows.length} objects)</h3>
          <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
        </div>
        <div className="chat-result-table-scroll">
          <table className="chat-result-table">
            <thead>
              <tr>
                <th>Column</th><th>Count</th><th>Mean</th><th>Median</th><th>Std Dev</th><th>Min</th><th>Max</th>
              </tr>
            </thead>
            <tbody>
              {numericCols.map(({ key, values }) => {
                const mean = values.reduce((a, b) => a + b, 0) / values.length;
                return (
                  <tr key={key}>
                    <td><strong>{key}</strong></td>
                    <td>{values.length}</td>
                    <td>{mean.toFixed(4)}</td>
                    <td>{median(values).toFixed(4)}</td>
                    <td>{stdDev(values, mean).toFixed(4)}</td>
                    <td>{Math.min(...values).toFixed(4)}</td>
                    <td>{Math.max(...values).toFixed(4)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function QualBadge({ value }: { value: string }) {
  const v = String(value).trim().toUpperCase();
  let color = "#888";
  if (v === "A" || v === "B") color = "#4ade80";
  else if (v === "C") color = "#facc15";
  else if (v === "D" || v === "E") color = "#f87171";
  return (
    <span style={{
      display: "inline-block",
      padding: "1px 5px",
      borderRadius: 4,
      fontSize: "0.72rem",
      fontWeight: 600,
      color: "#fff",
      backgroundColor: color,
    }}>
      {v}
    </span>
  );
}

function CitationModal({ objectName, onClose }: { objectName: string; onClose: () => void }) {
  const [refs, setRefs] = useState<ADSReference[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copiedBib, setCopiedBib] = useState<string | null>(null);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Q3: async ADS fetch + reset loading/error on objectName change.
    let cancelled = false;
    setLoading(true);
    setError(null);
    searchADS(objectName)
      .then((data) => { if (!cancelled) setRefs(data); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to query ADS"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [objectName]);
  /* eslint-enable react-hooks/set-state-in-effect */

  async function handleCopyBib(bibcode: string) {
    try {
      const bib = await getBibTeX(bibcode);
      await navigator.clipboard.writeText(bib);
      setCopiedBib(bibcode);
      setTimeout(() => setCopiedBib(null), 2000);
    } catch (e) {
      // L5: previously silent.  Tell the user the copy failed so they can
      // try again (e.g., grant clipboard permission) or copy manually.
      const msg = e instanceof Error ? e.message : String(e);
      alert(`Could not copy BibTeX: ${msg}`);
    }
  }

  return (
    <div className="viz-overlay" onClick={onClose}>
      <div className="viz-overlay-content" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 650 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>References for {objectName}</h3>
          <button className="btn-secondary btn-small" onClick={onClose}>Close</button>
        </div>
        {loading && <p>Loading references from NASA ADS...</p>}
        {error && <p style={{ color: "#f87171" }}>{error}</p>}
        {!loading && refs.length === 0 && !error && <p>No references found.</p>}
        {refs.map((ref) => (
          <div key={ref.bibcode} style={{ marginBottom: 12, padding: 8, background: "rgba(255,255,255,0.05)", borderRadius: 6 }}>
            <div style={{ fontWeight: 600, fontSize: "0.85rem" }}>{ref.title}</div>
            <div style={{ fontSize: "0.75rem", color: "rgba(255,255,255,0.6)", marginTop: 2 }}>
              {ref.authors.slice(0, 3).join(", ")}{ref.authors.length > 3 ? " et al." : ""} ({ref.year})
            </div>
            <div style={{ fontSize: "0.72rem", color: "rgba(255,255,255,0.4)", marginTop: 2 }}>
              {ref.bibcode}
              {ref.doi && <span> | doi:{ref.doi}</span>}
            </div>
            <button
              className="btn-chat-action"
              style={{ marginTop: 4, fontSize: "0.72rem" }}
              onClick={() => handleCopyBib(ref.bibcode)}
            >
              {copiedBib === ref.bibcode ? "Copied!" : "Copy BibTeX"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function SearchResultTable({ data }: { data: Array<Record<string, unknown>> }) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [vizData, setVizData] = useState<Record<string, unknown> | null>(null);
  const [page, setPage] = useState(0);
  const [showCitation, setShowCitation] = useState<string | null>(null);
  const [showStats, setShowStats] = useState(false);
  const PAGE_SIZE = 15;

  // Deduplicate by name+source
  const unique = useMemo(() => {
    if (!data || data.length === 0) return [];
    const seen = new Set<string>();
    return data.filter((row) => {
      const key = `${row.source}-${row.name}-${(row.ra as number)?.toFixed(3)}-${(row.dec as number)?.toFixed(3)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [data]);

  const totalPages = Math.ceil(unique.length / PAGE_SIZE);
  const displayed = unique.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Dynamically discover extra columns — only show columns where >30% of rows have data
  // and skip quality/metadata columns
  const extraCols = useMemo(() => {
    const skipCols = new Set(["rvz_type", "coo_qual", "otype_txt", "galdim_angle"]);
    const counts: Record<string, number> = {};
    for (const row of unique) {
      const extra = row.extra as Record<string, unknown> | undefined;
      if (extra) {
        for (const k of Object.keys(extra)) {
          if (extra[k] != null && !skipCols.has(k)) {
            counts[k] = (counts[k] || 0) + 1;
          }
        }
      }
    }
    const threshold = Math.max(1, unique.length * 0.3);
    // Prioritize: quality flags last, important fields first
    const priority: Record<string, number> = {
      sp_type: 1, morph_type: 2, plx_value: 3, pmra: 4, pmdec: 5,
      rvz_radvel: 6, galdim_majaxis: 7, galdim_minaxis: 8,
    };
    return Object.entries(counts)
      .filter(([, count]) => count >= threshold)
      .filter(([k]) => !k.endsWith("_qual"))  // hide quality flags from main table
      .sort((a, b) => (priority[a[0]] || 99) - (priority[b[0]] || 99))
      .map(([k]) => k)
      .slice(0, 5);  // max 5 extra columns to keep table readable
  }, [unique]);

  // Friendly names for common SIMBAD columns
  const colLabels: Record<string, string> = {
    sp_type: "Spectral Type", morph_type: "Morphology", plx_value: "Parallax (mas)",
    pmra: "PM RA (mas/yr)", pmdec: "PM Dec (mas/yr)", rvz_radvel: "Radial Vel (km/s)",
    rvz_type: "Vel Type", galdim_majaxis: "Major Axis (')", galdim_minaxis: "Minor Axis (')",
    galdim_angle: "PA (°)", Fe_H_Fe_H: "[Fe/H]",
    flux_B: "B mag", flux_V: "V mag", flux_R: "R mag", flux_I: "I mag",
    flux_J: "J mag", flux_H: "H mag", flux_K: "K mag",
  };

  // Auto-hide base columns that are all null
  const hasMag = useMemo(() => unique.some((r) => r.magnitude != null), [unique]);
  const hasZ = useMemo(() => unique.some((r) => r.redshift != null), [unique]);
  const hasType = useMemo(() => unique.some((r) => r.object_type), [unique]);

  if (unique.length === 0) {
    return (
      <div className="chat-action-result">
        <p className="chat-result-empty">No results found.</p>
      </div>
    );
  }

  // Selection uses global indices into the unique array
  const pageStart = page * PAGE_SIZE;
  const pageIndices = displayed.map((_, i) => pageStart + i);
  const allSelected = displayed.length > 0 && pageIndices.every((gi) => selected.has(gi));
  const someSelected = pageIndices.some((gi) => selected.has(gi));

  function toggleAll() {
    if (allSelected) {
      const next = new Set(selected);
      for (const gi of pageIndices) next.delete(gi);
      setSelected(next);
    } else {
      const next = new Set(selected);
      for (const gi of pageIndices) next.add(gi);
      setSelected(next);
    }
  }

  function toggleOne(globalIdx: number) {
    const next = new Set(selected);
    if (next.has(globalIdx)) next.delete(globalIdx); else next.add(globalIdx);
    setSelected(next);
  }

  function getSelected() {
    return unique.filter((_, i) => selected.has(i));
  }

  function fmt(v: unknown): string {
    if (v == null) return "—";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4);
    return String(v);
  }

  function handleCite() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const name = rows[0].name as string;
    if (name) setShowCitation(name);
  }

  function handleShowStats() {
    const rows = getSelected();
    if (rows.length === 0) return;
    setShowStats(true);
  }

  function getVisibleCols(): string[] {
    const cols = ["name", "ra", "dec"];
    if (hasType) cols.push("object_type");
    if (hasMag) cols.push("magnitude");
    if (hasZ) cols.push("redshift");
    cols.push(...extraCols);
    return cols;
  }

  function handleDownload() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const cols = getVisibleCols();
    const header = cols.join(",");
    const csv = [
      header,
      ...rows.map((r) => {
        const extra = (r.extra || {}) as Record<string, unknown>;
        return cols.map((c) => {
          const val = extraCols.includes(c) ? extra[c] : r[c];
          if (val == null) return "";
          const s = String(val);
          return s.includes(",") ? `"${s.replace(/"/g, '""')}"` : s;
        }).join(",");
      }),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_results_${rows.length}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    logOperation("export", `Downloaded CSV with ${rows.length} objects`);
  }

  function handleDownloadVOTable() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const cols = getVisibleCols();
    const escapeXml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const numericCols = new Set(["ra", "dec", "magnitude", "redshift"]);
    const fields = cols.map((c) => {
      if (numericCols.has(c)) return `      <FIELD name="${escapeXml(c)}" datatype="double"/>`;
      return `      <FIELD name="${escapeXml(c)}" datatype="char" arraysize="*"/>`;
    }).join("\n");
    const trs = rows.map((r) => {
      const extra = (r.extra || {}) as Record<string, unknown>;
      const tds = cols.map((c) => {
        const val = extraCols.includes(c) ? extra[c] : r[c];
        return `<TD>${val != null ? escapeXml(String(val)) : ""}</TD>`;
      }).join("");
      return `        <TR>${tds}</TR>`;
    }).join("\n");
    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">
  <RESOURCE>
    <TABLE name="results">
${fields}
      <DATA>
        <TABLEDATA>
${trs}
        </TABLEDATA>
      </DATA>
    </TABLE>
  </RESOURCE>
</VOTABLE>`;
    const blob = new Blob([xml], { type: "application/xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_results_${rows.length}.vot`;
    a.click();
    URL.revokeObjectURL(url);
    logOperation("export", `Downloaded VOTable with ${rows.length} objects`);
  }

  function handleVisualize() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const vd: Record<string, unknown> = {
      ra: rows.map((r) => r.ra),
      dec: rows.map((r) => r.dec),
      names: rows.map((r) => r.name),
      sources: rows.map((r) => r.source),
    };
    for (const col of extraCols) {
      const vals = rows.map((r) => ((r.extra || {}) as Record<string, unknown>)[col]).filter((v) => typeof v === "number");
      if (vals.length > 0) vd[col] = vals;
    }
    vd.magnitude = rows.map((r) => r.magnitude ?? null);
    vd.redshift = rows.map((r) => r.redshift ?? null);
    setVizData(vd);
    logOperation("visualize", `Visualized sky distribution of ${rows.length} objects`);
  }

  return (
    <div className="chat-action-result">
      {unique.length < data.length && (
        <p className="chat-result-dedup">{data.length - unique.length} duplicates removed</p>
      )}
      {selected.size > 0 && (
        <div className="chat-result-actions">
          <span className="chat-result-count">{selected.size} selected</span>
          <button className="btn-chat-action" onClick={handleDownload}>Download CSV</button>
          <button className="btn-chat-action" onClick={handleDownloadVOTable}>Download VOTable</button>
          <button className="btn-chat-action" onClick={handleVisualize}>Visualize</button>
          <button className="btn-chat-action" onClick={handleCite}>Cite</button>
          <button className="btn-chat-action" onClick={handleShowStats}>Statistics</button>
          <button className="btn-chat-action" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}
      <div className="chat-result-table-scroll">
        <table className="chat-result-table">
          <colgroup>
            <col className="col-check" />
            <col className="col-chat-name" />
            <col className="col-chat-ra" />
            <col className="col-chat-dec" />
            {hasType && <col className="col-chat-type" />}
            {hasMag && <col className="col-chat-mag" />}
            {hasZ && <col className="col-chat-z" />}
            {extraCols.map((c) => (
              <col key={c} className="col-chat-extra" />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th className="th-check">
                <input type="checkbox" checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                  onChange={toggleAll} className="row-checkbox" />
              </th>
              <th>Name</th>
              <th>RA</th>
              <th>Dec</th>
              {hasType && <th>Type</th>}
              {hasMag && <th>Mag</th>}
              {hasZ && <th>z</th>}
              {extraCols.map((c) => (
                <th key={c} title={c}>{colLabels[c] || c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayed.map((row, i) => {
              const globalIdx = pageStart + i;
              const extra = (row.extra || {}) as Record<string, unknown>;
              return (
                <tr key={globalIdx} className={selected.has(globalIdx) ? "row-selected" : ""}>
                  <td className="td-check">
                    <input type="checkbox" checked={selected.has(globalIdx)}
                      onChange={() => toggleOne(globalIdx)} className="row-checkbox" />
                  </td>
                  <td title={`${(row.source as string).toUpperCase()} | ${new Date().toISOString().slice(0, 10)}`}>{row.name as string}</td>
                  <td>{(row.ra as number)?.toFixed(5)}</td>
                  <td>{(row.dec as number)?.toFixed(5)}</td>
                  {hasType && <td>{(row.object_type as string) || "—"}</td>}
                  {hasMag && <td>{row.magnitude != null ? (row.magnitude as number).toFixed(2) : "—"}</td>}
                  {hasZ && <td>{row.redshift != null ? (row.redshift as number).toFixed(4) : "—"}</td>}
                  {extraCols.map((c) => (
                    <td key={c}>
                      {c.endsWith("_qual") && extra[c] != null
                        ? <QualBadge value={String(extra[c])} />
                        : fmt(extra[c])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="chat-result-pagination">
          <button className="btn-chat-action" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="chat-result-page-info">Page {page + 1} of {totalPages} ({unique.length} results)</span>
          <button className="btn-chat-action" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
      {vizData && (
        <div className="viz-overlay">
          <div className="viz-overlay-content">
            <ErrorBoundary label="the visualization">
              <Suspense fallback={<div className="fits-loading">Loading visualization...</div>}>
                <PlotBuilder
                  initialData={vizData}
                  initialChartType="sky_coverage"
                  onClose={() => setVizData(null)}
                />
              </Suspense>
            </ErrorBoundary>
          </div>
        </div>
      )}
      {showCitation && (
        <CitationModal objectName={showCitation} onClose={() => setShowCitation(null)} />
      )}
      {showStats && (
        <StatsPanel rows={getSelected()} extraCols={extraCols} onClose={() => setShowStats(false)} />
      )}
    </div>
  );
}

function displayValue(value: unknown, fallback = "—"): string {
  if (value == null) return fallback;
  const text = String(value).trim();
  if (!text || text.toLowerCase() === "undefined" || text.toLowerCase() === "null") return fallback;
  return text;
}

function compactStderrWarnings(text: string): { text: string; folded: boolean; originalLines: number; displayedLines: number } {
  const lines = text.split(/\r?\n/).filter((line) => line.trim() !== "");
  if (lines.length === 0) {
    return { text, folded: false, originalLines: 0, displayedLines: 0 };
  }
  const groups = new Map<string, { first: string; count: number }>();
  for (const line of lines) {
    const trimmed = line.trimEnd();
    const key = /Glyph\s+\d+.*missing from font/i.test(trimmed)
      ? "matplotlib:glyph-missing"
      : trimmed;
    const current = groups.get(key);
    if (current) {
      current.count += 1;
    } else {
      groups.set(key, { first: trimmed, count: 1 });
    }
  }
  const folded = groups.size < lines.length;
  if (!folded) {
    return { text, folded: false, originalLines: lines.length, displayedLines: lines.length };
  }
  const compacted = Array.from(groups.values()).map((entry) =>
    entry.count > 1 ? `${entry.first}\n  [repeated ${entry.count} times]` : entry.first
  );
  return {
    text: compacted.join("\n"),
    folded: true,
    originalLines: lines.length,
    displayedLines: compacted.length,
  };
}

function AutoToolResult({ toolName, result }: { toolName: string; result: Record<string, unknown> }) {
  const resultStatus = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
  if (resultStatus === "UNAVAILABLE") {
    const message = typeof result.user_facing_message === "string" && result.user_facing_message.trim()
      ? result.user_facing_message
      : typeof result.error === "string" && result.error.trim()
        ? result.error
        : "This source is temporarily unavailable while its provenance metadata is being upgraded.";
    const alternatives = Array.isArray(result.available_alternatives)
      ? (result.available_alternatives as unknown[]).filter((item): item is string => typeof item === "string")
      : [];
    return (
      <div className="tool-unavailable-message">
        <strong>Source under maintenance</strong>
        <div>{message}</div>
        {alternatives.length > 0 && (
          <div>Available alternatives: {alternatives.join(", ")}</div>
        )}
      </div>
    );
  }

  if (result.error) {
    return <div style={{ color: "var(--color-red)", fontSize: "0.8rem" }}>Error: {String(result.error)}</div>;
  }

  // Search results
  if (toolName === "search_objects") {
    const items = (result.results as Array<Record<string, unknown>>) || [];
    const total = (result.total as number) || items.length;
    return (
      <div>
        <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", marginBottom: 4 }}>
          Found {total} objects
        </div>
        {items.slice(0, 8).map((r, i) => (
          (() => {
            const source = displayValue(r.source, "unknown");
            const sourceClass = source.toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
            const name = displayValue(r.name ?? r.object_id ?? r.main_id ?? r.id, "Unnamed object");
            return (
              <div key={i} style={{ fontSize: "0.75rem", padding: "2px 0", display: "flex", gap: 8 }}>
                <span className={`badge badge-${sourceClass}`} style={{ fontSize: "0.6rem" }}>{source.toUpperCase()}</span>
                <span>{name}</span>
                {r.redshift != null && <span style={{ color: "var(--color-text-tertiary)" }}>z={Number(r.redshift).toFixed(4)}</span>}
              </div>
            );
          })()
        ))}
        {total > 8 && <div style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>...and {total - 8} more</div>}
      </div>
    );
  }

  // ADQL results
  if (toolName === "run_adql") {
    const cols = (result.columns as string[]) || [];
    const rowCount = (result.row_count as number) || 0;
    const attemptLog = Array.isArray(result.attempt_log)
      ? (result.attempt_log as Array<Record<string, unknown>>)
      : [];
    const retryLog = Array.isArray(result.retry_log)
      ? (result.retry_log as string[])
      : [];
    const successStages = attemptLog.filter((entry) =>
      String(entry.stage || "").includes("success")
    );
    const failureStages = attemptLog.filter((entry) =>
      String(entry.stage || "").includes("error") || String(entry.stage || "").includes("timeout")
    );
    const finalSuccess = successStages[successStages.length - 1];
    const successMessage = finalSuccess && typeof finalSuccess.message === "string"
      ? finalSuccess.message
      : "ADQL query succeeded";
    // W5 (PART W): expose the actually-executed ADQL in a folded block so
    // user can read / copy the SQL. Previously only row-count summary was
    // shown on auto-executed cards; the manual-Execute ActionCard already
    // showed the query, but run_adql auto-results did not.
    const queryText = typeof result.query === "string" ? (result.query as string) : "";
    const serviceName = typeof result.service === "string" ? (result.service as string) : "";
    // X4 (PART X): 半径 auto-shrink 醒目 banner (不折叠). 修复 B6 Pleiades
    // 半径 0.75° → 0.375° 无警告问题.
    const radiusAutoReduced = result.radius_auto_reduced === true;
    const originalRadiusDeg = typeof result.original_radius_deg === "number"
      ? result.original_radius_deg
      : null;
    const finalRadiusDeg = typeof result.final_radius_deg === "number"
      ? result.final_radius_deg
      : null;
    return (
      <div style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", lineHeight: 1.45 }}>
        <div style={{ color: "#2e7d32", fontWeight: 600 }}>
          ✓ {successMessage}: {rowCount} rows, {cols.length} columns
          {serviceName ? ` · ${serviceName}` : ""}
        </div>
        {radiusAutoReduced && originalRadiusDeg !== null && finalRadiusDeg !== null && (
          <div
            style={{
              padding: "6px 10px",
              margin: "4px 0",
              background: "rgba(255, 200, 0, 0.15)",
              borderLeft: "3px solid #e8a800",
              fontSize: "0.75rem",
              lineHeight: 1.4,
              color: "var(--color-text-primary, #1a1a1a)",
            }}
          >
            ⚠ Search radius auto-reduced from{" "}
            <strong>{originalRadiusDeg}°</strong> to{" "}
            <strong>{finalRadiusDeg}°</strong>{" "}
            (TAP timeout on original query). Membership count may be
            smaller than expected — consider tighter filters if you need
            the full original radius.
          </div>
        )}
        <div>
          Columns: {cols.slice(0, 5).join(", ")}{cols.length > 5 ? "..." : ""}
        </div>
        {queryText && (
          <details style={{ marginTop: 4 }}>
            <summary style={{ cursor: "pointer" }}>
              Show ADQL query ({queryText.length} chars)
            </summary>
            <pre
              style={{
                margin: "4px 0 0 0",
                padding: "6px 8px",
                background: "var(--color-bg-code, rgba(0,0,0,0.04))",
                fontSize: "0.72rem",
                borderRadius: 3,
                overflowX: "auto",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              <code>{queryText}</code>
            </pre>
          </details>
        )}
        {(attemptLog.length > 0 || retryLog.length > 0) && (
          <details style={{ marginTop: 4 }}>
            <summary>
              {failureStages.length + retryLog.length > 0
                ? `Recovered after ${failureStages.length + retryLog.length} retry/fallback step${failureStages.length + retryLog.length === 1 ? "" : "s"}`
                : "Execution details"}
            </summary>
            <ul style={{ margin: "4px 0 0 1rem", padding: 0 }}>
              {attemptLog.slice(-8).map((entry, i) => (
                <li key={`attempt-${i}`}>
                  {String(entry.message || entry.stage || "ADQL progress")}
                </li>
              ))}
              {retryLog.map((entry, i) => (
                <li key={`retry-${i}`}>{entry}</li>
              ))}
            </ul>
          </details>
        )}
      </div>
    );
  }

  // Object info
  if (toolName === "get_object_info") {
    // B-S2: fallback for null / undefined / empty object_type.
    // Backend SDSS normalization now returns "Unknown" (see
    // sdss.py), but older cached data may still be empty — render
    // "—" rather than the literal string "undefined".
    const objType = displayValue(result.object_type, "—");
    return (
      <div style={{ fontSize: "0.78rem" }}>
        <strong>{String(result.name)}</strong> — {objType}
        {result.redshift != null && <span> z={Number(result.redshift).toFixed(6)}</span>}
        {result.cross_ids ? <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.72rem" }}>
          {(result.cross_ids as string[]).length} known identifiers
        </div> : null}
      </div>
    );
  }

  // Literature
  if (toolName === "search_literature") {
    const refs = (result.results as Array<Record<string, unknown>>) || [];
    return (
      <div>
        {refs.slice(0, 8).map((r, i) => (
          <div key={i} style={{ fontSize: "0.75rem", padding: "4px 0", borderBottom: "1px solid var(--color-border)" }}>
            <div>
              <a href={`https://ui.adsabs.harvard.edu/abs/${r.bibcode}`} target="_blank" rel="noopener noreferrer"
                style={{ color: "var(--color-accent)", textDecoration: "none" }}>
                {String(r.title)}
              </a>
            </div>
            <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.7rem" }}>
              {(r.authors as string[] || []).slice(0, 3).join(", ")}{(r.authors as string[] || []).length > 3 ? " et al." : ""} ({String(r.year)})
            </div>
            {r.abstract ? (
              <div style={{ color: "var(--color-text-secondary)", fontSize: "0.7rem", marginTop: 2, lineHeight: 1.3 }}>
                {String(r.abstract).slice(0, 200)}{String(r.abstract).length > 200 ? "..." : ""}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    );
  }

  if (toolName === "extract_literature_tables") {
    const tables = (Array.isArray(result.tables) ? result.tables : []) as Array<{
      name?: string;
      caption?: string;
      columns?: string[];
      rows?: string[][];
      row_count?: number;
      extraction_method?: string;
    }>;
    const measurements = (Array.isArray(result.line_measurements) ? result.line_measurements : []) as Array<Record<string, unknown>>;
    return (
      <div style={{ fontSize: "0.75rem" }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
          <span className="tool-status-chip tool-status-chip-completed">
            {tables.length} raw table{tables.length === 1 ? "" : "s"}
          </span>
          <span className={`tool-status-chip ${measurements.length > 0 ? "tool-status-chip-completed" : "tool-status-chip-partial"}`}>
            {measurements.length > 0 ? `${measurements.length} usable line measurement${measurements.length === 1 ? "" : "s"}` : "needs column mapping"}
          </span>
          {measurements.length > 0 ? (
            <span className="tool-status-chip tool-status-chip-completed">Ready for fitting</span>
          ) : null}
          {result.cache_key ? (
            <code style={{ fontSize: "0.7rem" }}>cache: {String(result.cache_key)}</code>
          ) : null}
        </div>
        {measurements.length > 0 && (
          <div style={{ marginBottom: 8, color: "var(--color-text-secondary)" }}>
            Typed rows can support fitting; cite the paper and table label for quoted values.
          </div>
        )}
        {tables.slice(0, 3).map((table, tableIndex) => {
          const columns = Array.isArray(table.columns) ? table.columns : [];
          const rows = Array.isArray(table.rows) ? table.rows : [];
          return (
            <div key={`${table.name || "table"}-${tableIndex}`} style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                {table.name || table.caption || `Table ${tableIndex + 1}`}
                {table.extraction_method ? (
                  <span style={{ color: "var(--color-text-tertiary)", fontWeight: 400 }}> · {table.extraction_method}</span>
                ) : null}
              </div>
              <div className="chat-result-table-scroll">
                <table className="chat-result-table literature-table-preview">
                  <thead>
                    <tr>{columns.map((column, columnIndex) => <th key={columnIndex} title={column}>{column}</th>)}</tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 8).map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {row.slice(0, columns.length || row.length).map((cell, cellIndex) => <td key={cellIndex} title={cell}>{cell}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(table.row_count || rows.length) > 8 && (
                <p className="chat-result-more">Showing 8 of {table.row_count || rows.length} rows</p>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (toolName === "fit_line_lfr") {
    const publicationReady = result.publication_ready === true;
    const nUsed = Number(result.n_used || 0);
    const beta = typeof result.beta === "number" ? result.beta : undefined;
    const alpha = typeof result.alpha === "number" ? result.alpha : undefined;
    const scatter = typeof result.scatter_dex === "number" ? result.scatter_dex : undefined;
    const citations = ((result.citation_summary as Record<string, unknown> | undefined)?.citations || []) as string[];
    return (
      <div style={{ fontSize: "0.75rem" }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
          <span className={`tool-status-chip ${publicationReady ? "tool-status-chip-completed" : "tool-status-chip-partial"}`}>
            {publicationReady ? "Publication-ready" : "Exploratory only"}
          </span>
          <span className="tool-status-chip tool-status-chip-completed">{nUsed} fitted rows</span>
          {result.cache_key ? <code style={{ fontSize: "0.7rem" }}>cache: {String(result.cache_key)}</code> : null}
        </div>
        {alpha !== undefined && beta !== undefined ? (
          <div style={{ marginBottom: 6 }}>
            log L = {alpha.toFixed(3)} + {beta.toFixed(3)} log(FWHM/100 km/s)
          </div>
        ) : null}
        {result.plot_data && typeof result.plot_data === "object" ? (
          <LineRelationPlot plotData={result.plot_data as LineRelationPlotData} />
        ) : null}
        <div style={{ color: "var(--color-text-secondary)" }}>
          {typeof result.pearson_r === "number" ? <span>r={Number(result.pearson_r).toFixed(3)} · </span> : null}
          {typeof result.pearson_p === "number" ? <span>p={Number(result.pearson_p).toExponential(2)} · </span> : null}
          {scatter !== undefined ? <span>scatter={scatter.toFixed(3)} dex</span> : null}
        </div>
        {citations.length > 0 ? (
          <div style={{ marginTop: 6, color: "var(--color-text-tertiary)" }}>
            Citations: {citations.slice(0, 4).join(", ")}{citations.length > 4 ? `, +${citations.length - 4} more` : ""}
          </div>
        ) : null}
      </div>
    );
  }

  if (
    toolName === "fit_cosmology_mcmc"
    || toolName === "run_cobaya_cosmology"
    || toolName === "get_cosmology_run_status"
    || toolName === "run_cosmology_likelihood_chain"
    || toolName === "run_nested_sampler"
    || toolName === "evaluate_chain_diagnostics"
  ) {
    const nestedResult = result.result && typeof result.result === "object"
      ? result.result as Record<string, unknown>
      : result;
    return <CosmologyMCMCPanel result={nestedResult} />;
  }

  if (
    toolName === "list_cosmology_datasets"
    || toolName === "build_cosmology_likelihood"
    || toolName === "build_cosmology_robustness_matrix"
    || toolName === "run_cosmology_robustness_matrix"
  ) {
    return <CosmologyLikelihoodPanel result={result} />;
  }

  if (
    toolName === "plan_research_program"
    || toolName === "run_research_matrix"
    || toolName === "build_evidence_graph"
    || toolName === "verify_research_facts"
    || toolName === "export_research_report"
    || toolName === "build_paper_mining_candidate_pool"
    || toolName === "mine_paper_tools"
    || toolName === "run_paper_tool_mining_batch"
    || toolName === "build_tool_ontology"
    || toolName === "build_tool_gap_matrix"
    || toolName === "rank_tool_implementation_queue"
    || toolName === "run_paper_tool_mining_loop"
  ) {
    return <ResearchProgramPanel result={result} />;
  }

  // Stage 4 (2026-05-19): literature spot-check banner.
  // 3 状态: passed (绿) / failed (红) / unavailable (灰)
  // 数据 shape 来自 literature_spot_check.SpotCheckResult.to_dict().
  if (toolName === "spot_check_literature_value") {
    if (result.spot_check_disabled) {
      return (
        <div style={{ fontSize: "0.78rem", color: "#6b6b6b", fontStyle: "italic" }}>
          ℹ Literature spot-check is disabled in this deployment
          (LITERATURE_SPOT_CHECK_ENABLED=false).
        </div>
      );
    }
    const status = String(result.status || "unavailable");
    const passed = status === "passed";
    const failed = status === "failed";
    const bg = passed
      ? "rgba(46,106,78,.10)"
      : failed
        ? "rgba(239,68,68,.10)"
        : "rgba(107,107,107,.10)";
    const border = passed ? "#2e6a4e" : failed ? "#ef4444" : "#9b9b9b";
    const fgColor = passed ? "#166534" : failed ? "#7f1d1d" : "#4b4b4b";
    const icon = passed ? "✓" : failed ? "⚠" : "ℹ";
    const label = passed
      ? "VERIFIED"
      : failed
        ? "FAILED VERIFICATION"
        : "VERIFICATION UNAVAILABLE";
    const fmtNum = (v: unknown): string =>
      v == null || typeof v !== "number" ? "—" : v.toFixed(4);
    const fmtSigma = (v: unknown): string =>
      v == null || typeof v !== "number" ? "—" : `${v.toFixed(2)}σ`;
    return (
      <div
        style={{
          fontSize: "0.78rem",
          color: fgColor,
          background: bg,
          border: `1px solid ${border}`,
          borderRadius: 4,
          padding: "8px 12px",
          marginTop: 4,
        }}
      >
        <div style={{ fontWeight: 600, marginBottom: 4 }}>
          {icon} Literature spot-check: {label}
        </div>
        <div>
          Target: <code>{String(result.target || "")}</code> (
          {String(result.quantity_type || "")})
        </div>
        <div>
          Paper value: <strong>{fmtNum(result.paper_value)}</strong>
          &nbsp;|&nbsp; Our value: <strong>{fmtNum(result.our_value)}</strong>
          &nbsp;|&nbsp; Distance: <strong>{fmtSigma(result.sigma_distance)}</strong>
          {result.margin_sigma != null && typeof result.margin_sigma === "number"
            ? ` (threshold ${result.margin_sigma.toFixed(1)}σ)`
            : ""}
        </div>
        <div style={{ marginTop: 4, opacity: 0.85 }}>
          {String(result.reason || "")}
        </div>
        <div
          style={{
            marginTop: 4,
            fontSize: "0.72rem",
            opacity: 0.7,
            fontStyle: "italic",
          }}
        >
          {String(result.disclaimer || "")}
        </div>
      </div>
    );
  }

  // Pipeline
  // Stage 3 Bug 2: "Open in Pipeline Editor" button removed — M3 (2026-05-18)
  // deleted the /pipeline page, so this button always navigated to a 404
  // and wrote a dead `pipeline_autosave` localStorage entry that no consumer
  // could read. We still show the AI-generated DAG inline as reference.
  if (toolName === "generate_pipeline") {
    const dag = result.dag as { nodes: Array<{ type: string }> } | undefined;
    return (
      <div style={{ fontSize: "0.78rem" }}>
        Pipeline <strong>{String(result.name)}</strong>: {dag?.nodes?.map(n => n.type).join(" → ")}
      </div>
    );
  }

  // Python code execution
  if (toolName === "run_python") {
    const success = result.success as boolean;
    const status = String(result.__tool_status__ || result.analysis_status || "").toUpperCase();
    const isPartial = status === "PARTIAL";
    const isEmpty = status === "EMPTY";
    const errorClass = result.error_class as string | undefined;
    const fatalFailure = !success && (
      status === "FAILED"
      || String(errorClass || "").toLowerCase() === "oom"
      || String(errorClass || "").toLowerCase() === "sigsegv"
      || String(errorClass || "").toLowerCase() === "sandbox_crash"
      || String(errorClass || "").toLowerCase() === "subprocesscrash"
      || String(errorClass || "").toLowerCase() === "timeout"
    );
    const isSynthetic = !fatalFailure && (status === "SYNTHETIC" || String(result.data_origin || "").toLowerCase() === "synthetic");
    const stdout = result.stdout as string || "";
    const error = result.error as string | undefined;
    const figures = (result.figures as string[]) || [];
    const variables = result.variables as Record<string, string> | undefined;
    const variableTypes = result.variable_types as Record<string, string> | undefined;
    const tb = result.traceback as string | undefined;
    // R6 post: stderr 作为一级字段 (即便空串也存在); stderr_note 在
    // "stderr 为空但非零退出" 时提示用户 subprocess 崩在 Python 启动
    // 阶段, 去 /api/admin/sandbox/health 看真实 Python error.
    const stderr = result.stderr as string | undefined;
    const stderrText = stderr ?? "";
    const stderrDisplay = compactStderrWarnings(stderrText);
    const stderrNote = result.stderr_note as string | undefined;
    const showStderrPanel = stderrText.trim() !== "" || !success;
    // When localStorage was over its soft cap, figures may have been
    // replaced with {__figures_offloaded__: N}.  Show a placeholder so the
    // user knows the figures existed + why they're gone right now.  The
    // boot-time rehydrate (see "Figure-rehydrate" useEffect) fetches the
    // full session from the server asynchronously, so this placeholder
    // will normally flash briefly then disappear.
    const figuresOffloaded = typeof result.__figures_offloaded__ === "number"
      ? (result.__figures_offloaded__ as number)
      : undefined;

    // F0.6: surface the backend's typed error.  The old "Python sandbox
    // returned no message (check backend logs)" fallback is gone — F0.2
    // on the backend guarantees every failure carries a concrete error
    // message.  If we still see no error for a failed call, the tool
    // response itself is malformed.
    const errorDisplay = isSynthetic
      ? "Synthetic output (not citeable)"
      : isEmpty
      ? "Tool returned no data"
      : success
      ? "Executed successfully"
      : isPartial && error && error.trim()
        ? `Partial output before error: ${error}`
      : error && error.trim()
        ? `Error: ${error}`
        : "Error: run_python returned an empty response (tool response malformed; check backend logs)";

    // Map F0.2 error_class to a short chip label.
    const errorClassLabel: Record<string, string> = {
      sandbox_crash: "Sandbox crash",
      SubprocessCrash: "Sandbox crash",
      oom: "Out of memory",
      timeout: "Timed out",
      name_error: "NameError",
      import_error: "ImportError",
      system_exit: "SystemExit",
      syntax_error: "SyntaxError",
      runtime_error: "Runtime error",
      empty_input: "Empty code",
      unknown: "Unknown",
    };

    return (
      <div className="code-result">
        {/* Status */}
        <div style={{ fontSize: "0.72rem", color: isEmpty ? "var(--color-yellow, #ffd60a)" : success ? "var(--color-green)" : isPartial ? "var(--color-yellow, #ffd60a)" : "var(--color-red)", marginBottom: 4, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          <span>{errorDisplay}</span>
          {!success && errorClass && errorClassLabel[errorClass] && (
            <span
              title={`error_class: ${errorClass}`}
              style={{
                fontSize: "0.65rem",
                padding: "1px 6px",
                borderRadius: 3,
                border: "1px solid var(--color-red)",
                color: "var(--color-red)",
                background: "rgba(255, 69, 58, 0.08)",
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              {errorClassLabel[errorClass]}
            </span>
          )}
        </div>

        {/* Stdout */}
        {isSynthetic && stdout && (
          <div className="code-synthetic-output-note">
            Synthetic stdout is shown for audit only. Do not cite these numbers as observational results.
          </div>
        )}
        {stdout && (
          <pre className="code-output">{stdout}</pre>
        )}

        {/* Traceback */}
        {tb && !success && (
          <pre className="code-output code-error">{tb.slice(-500)}</pre>
        )}

        {/* stderr / warnings 成功时也要显示；warnings.warn 和 print(..., file=sys.stderr)
            不代表工具失败, 但对用户调试很关键。 */}
        {showStderrPanel && (
          <div className={`code-stderr-panel${!success ? " failed" : ""}`}>
            <div className="code-stderr-label">
              {success ? "STDERR / WARNINGS" : "STDERR"} {!stderrText ? "(empty — subprocess crashed early)" : ""}
            </div>
            <pre className="code-output code-stderr-output">
              {stderrText !== ""
                ? stderrDisplay.text
                : "(empty — subprocess crashed before Python stderr capture ran; check error_class + traceback fields above, or the /api/admin/sandbox/health endpoint)"}
            </pre>
            {stderrDisplay.folded && (
              <div className="code-stderr-fold-note">
                Folded repeated warnings: {stderrDisplay.originalLines} lines → {stderrDisplay.displayedLines}.
              </div>
            )}
            {stderrNote && (
              <div style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)", fontStyle: "italic", marginTop: 4, lineHeight: 1.45 }}>
                {stderrNote}
              </div>
            )}
          </div>
        )}

        {/* Figures */}
        {figures.map((b64, i) => (
          <ClickableFigure key={i} src={`data:image/png;base64,${b64}`} alt={`Figure ${i + 1}`} />
        ))}

        {/* Offloaded-figure placeholder.  The localStorage pruner had to
            drop N figures to stay under the 4 MB cap; the boot-time
            rehydrate should replace this with the real figures as soon
            as the server responds. */}
        {figuresOffloaded !== undefined && figuresOffloaded > 0 && figures.length === 0 && (
          <div
            style={{
              padding: "0.7rem 0.9rem",
              border: "1px dashed var(--color-scrollbar)",
              borderRadius: 6,
              background: "rgba(160, 101, 0, 0.06)",
              fontSize: "0.82rem",
              color: "var(--color-text-secondary)",
              margin: "0.4rem 0",
            }}
          >
            📊 {figuresOffloaded} figure{figuresOffloaded === 1 ? "" : "s"} were
            generated here but were offloaded from browser cache to save space.
            {" "}
            Reloading from the server now — if they don't appear in a few
            seconds, the session may not be saved server-side.
          </div>
        )}

        {/* Variables */}
        {variables && Object.keys(variables).length > 0 && (
          <details className="code-vars">
            <summary>Variables ({Object.keys(variables).length})</summary>
            {Object.entries(variables).map(([k, v]) => (
              <div key={k} className="code-var">
                <span className="code-var-name">{k}</span>
                {variableTypes?.[k] ? <span style={{ color: "var(--color-text-tertiary)" }}> ({variableTypes[k]})</span> : null}
                {" = "}
                <span className="code-var-val">{v}</span>
              </div>
            ))}
          </details>
        )}
      </div>
    );
  }

  // ──────────────────────────────────────────────────────────────────
  // Dedicated renderers for high-frequency tools. Each replaces the
  // default JSON-truncate fallback with a formatted data card that the
  // user can actually read at a glance.
  // ──────────────────────────────────────────────────────────────────

  // fit_isochrone: best_fit {log_age, age_myr, distance_pc, A_V}, turnoff,
  // method, n_data, note, warnings[]
  if (toolName === "fit_isochrone") {
    const bf = result.best_fit as Record<string, unknown> | undefined;
    const to = result.turnoff as Record<string, unknown> | undefined;
    const err = result.error as string | undefined;
    const warnings = Array.isArray(result.warnings) ? (result.warnings as string[]) : [];
    const method = String(result.method || "");
    if (err) {
      return (
        <div style={{ fontSize: "0.82rem" }}>
          <div style={{ color: "var(--color-red)" }}>Isochrone fit failed: {err}</div>
          {result.message ? <div style={{ color: "var(--color-text-tertiary)", fontSize: "0.75rem", marginTop: 3 }}>{String(result.message)}</div> : null}
        </div>
      );
    }
    return (
      <div style={{ fontSize: "0.82rem" }}>
        {bf && (
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 10px", marginBottom: 6 }}>
            <strong>Age:</strong>
            <span>{bf.age_myr != null ? `${bf.age_myr} Myr` : "—"} {bf.log_age != null ? <span style={{ color: "var(--color-text-tertiary)" }}>(log₁₀ = {String(bf.log_age)})</span> : null}</span>
            <strong>Distance:</strong>
            <span>{bf.distance_pc != null ? `${bf.distance_pc} pc` : "—"}</span>
            <strong>A_V:</strong>
            <span>{bf.A_V != null ? `${bf.A_V} mag` : "—"}</span>
          </div>
        )}
        {to && (
          <div style={{ fontSize: "0.75rem", color: "var(--color-text-tertiary)", marginBottom: 4 }}>
            Turnoff: BP-RP={String(to.bp_rp ?? "—")}, M_G={String(to.abs_mag_G ?? "—")}, mass≈{String(to.approx_mass_msun ?? "—")} M☉
          </div>
        )}
        <div style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>
          {method} · N_data = {String(result.n_data ?? "?")}
        </div>
        {warnings.length > 0 && (
          <details style={{ marginTop: 4, fontSize: "0.75rem" }}>
            <summary style={{ color: "#a06500", cursor: "pointer" }}>⚠ {warnings.length} warning{warnings.length === 1 ? "" : "s"}</summary>
            <ul style={{ margin: "4px 0 0 0", paddingLeft: 18 }}>
              {warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
          </details>
        )}
      </div>
    );
  }

  // query_gaia_cluster: row_count, columns, median_parallax_mas,
  // stdev_parallax_mas, mean_pmra, mean_pmdec, center_ra, center_dec, radius_deg
  if (toolName === "query_gaia_cluster") {
    const err = result.error as string | undefined;
    if (err) {
      return <div style={{ color: "var(--color-red)", fontSize: "0.82rem" }}>Cluster query failed: {err}</div>;
    }
    const rowCount = (result.row_count as number) || 0;
    const medPlx = result.median_parallax_mas as number | undefined;
    const stdPlx = result.stdev_parallax_mas as number | undefined;
    const meanPmra = result.mean_pmra as number | undefined;
    const meanPmdec = result.mean_pmdec as number | undefined;
    const cRa = result.center_ra as number | undefined;
    const cDec = result.center_dec as number | undefined;
    const radius = result.radius_deg as number | undefined;
    return (
      <div style={{ fontSize: "0.82rem" }}>
        <div style={{ marginBottom: 4 }}>
          <strong>{rowCount}</strong> member{rowCount === 1 ? "" : "s"} returned
          {radius != null && (
            <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>
              within {radius}° of ({cRa?.toFixed(3)}, {cDec?.toFixed(3)})
            </span>
          )}
        </div>
        {rowCount > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "3px 10px", fontSize: "0.77rem" }}>
            {medPlx != null && (<>
              <span style={{ color: "var(--color-text-tertiary)" }}>Median parallax:</span>
              <span>{medPlx.toFixed(3)} mas {stdPlx != null && <span style={{ color: "var(--color-text-tertiary)" }}>(σ = {stdPlx.toFixed(3)})</span>}</span>
            </>)}
            {meanPmra != null && (<>
              <span style={{ color: "var(--color-text-tertiary)" }}>Mean μ_α:</span>
              <span>{meanPmra.toFixed(2)} mas/yr</span>
            </>)}
            {meanPmdec != null && (<>
              <span style={{ color: "var(--color-text-tertiary)" }}>Mean μ_δ:</span>
              <span>{meanPmdec.toFixed(2)} mas/yr</span>
            </>)}
          </div>
        )}
      </div>
    );
  }

  // get_extinction: e_b_v, a_v, r_v, method, galactic_l/b, a_g/a_v/...
  if (toolName === "get_extinction") {
    const err = result.error as string | undefined;
    if (err) {
      return <div style={{ color: "var(--color-red)", fontSize: "0.82rem" }}>Extinction lookup failed: {err}</div>;
    }
    const ebv = result.e_b_v as number | undefined;
    const av = result.a_v as number | undefined;
    const rv = result.r_v as number | undefined;
    const method = String(result.method || "");
    const l = result.galactic_l_deg as number | undefined;
    const b = result.galactic_b_deg as number | undefined;
    const note = result.note as string | undefined;
    // Pick out band-specific a_x entries (a_g, a_b, a_r, a_j, etc.)
    const bandEntries = Object.entries(result)
      .filter(([k, v]) => /^a_[a-z]$/i.test(k) && typeof v === "number")
      .map(([k, v]) => [k.slice(2).toUpperCase(), v as number] as const);
    return (
      <div style={{ fontSize: "0.82rem" }}>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "3px 10px", marginBottom: 4 }}>
          {ebv != null && (<>
            <strong>E(B−V):</strong>
            <span>{ebv.toFixed(4)} mag</span>
          </>)}
          {av != null && (<>
            <strong>A_V:</strong>
            <span>{av.toFixed(3)} mag {rv != null && <span style={{ color: "var(--color-text-tertiary)" }}>(R_V = {rv})</span>}</span>
          </>)}
          {bandEntries.length > 0 && (<>
            <strong>Bands:</strong>
            <span>{bandEntries.map(([k, v]) => `A_${k}=${v.toFixed(3)}`).join(", ")}</span>
          </>)}
          {(l != null || b != null) && (<>
            <span style={{ color: "var(--color-text-tertiary)" }}>Galactic (ℓ, b):</span>
            <span>({l?.toFixed(2)}°, {b?.toFixed(2)}°)</span>
          </>)}
        </div>
        <div style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>method: {method}</div>
        {note && <div style={{ fontSize: "0.7rem", color: "#a06500", marginTop: 3 }}>⚠ {note}</div>}
      </div>
    );
  }

  // crossmatch_catalogs: match_count, showing, columns, rows, join_type, radius_arcsec
  if (toolName === "crossmatch_catalogs") {
    const err = result.error as string | undefined;
    if (err) {
      return <div style={{ color: "var(--color-red)", fontSize: "0.82rem" }}>Cross-match failed: {err}</div>;
    }
    const matchCount = (result.match_count as number) || 0;
    const showing = (result.showing as number) || 0;
    const radius = result.radius_arcsec as number | undefined;
    const joinType = String(result.join_type || "");
    const columns = (result.columns as string[]) || [];
    const rows = (result.rows as Array<Record<string, unknown>>) || [];
    return (
      <div style={{ fontSize: "0.82rem" }}>
        <div style={{ marginBottom: 4 }}>
          <strong>{matchCount}</strong> match{matchCount === 1 ? "" : "es"}
          {radius != null && <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>within {radius}″ ({joinType})</span>}
          {showing < matchCount && <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>(showing first {showing})</span>}
        </div>
        {rows.length > 0 && columns.length > 0 && (
          <div style={{ overflowX: "auto", maxHeight: 200, border: "1px solid var(--color-separator)", borderRadius: 4 }}>
            <table style={{ fontSize: "0.72rem", borderCollapse: "collapse", width: "100%" }}>
              <thead>
                <tr>
                  {columns.slice(0, 8).map((c) => (
                    <th key={c} style={{ position: "sticky", top: 0, background: "var(--color-muted)", padding: "3px 6px", textAlign: "left", fontWeight: 600 }}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 20).map((row, i) => (
                  <tr key={i}>
                    {columns.slice(0, 8).map((c) => {
                      const v = row[c];
                      const display = v == null ? "—" : (typeof v === "number" ? Number(v).toPrecision(6) : String(v).slice(0, 32));
                      return <td key={c} style={{ padding: "2px 6px", borderTop: "1px solid var(--color-separator)" }}>{display}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  // get_object_dossier: object {name, ra, dec}, photometry, astrometry, redshift,
  // object_type, spectral_type, cross_ids, sources_queried, sources_responded
  if (toolName === "get_object_dossier") {
    const err = result.error as string | undefined;
    if (err) {
      return <div style={{ color: "var(--color-red)", fontSize: "0.82rem" }}>Dossier lookup failed: {err}</div>;
    }
    const obj = result.object as Record<string, unknown> | undefined;
    const photometry = result.photometry as Record<string, unknown> | undefined;
    const astrometry = result.astrometry as Record<string, unknown> | undefined;
    const redshift = result.redshift as Record<string, unknown> | undefined;
    const objType = displayValue(result.object_type, "");
    const specType = displayValue(result.spectral_type, "");
    const crossIds = Array.isArray(result.cross_ids) ? (result.cross_ids as string[]) : [];
    const sourcesResponded = (result.sources_responded as string[] | number | undefined);
    const name = String(obj?.name || "Unknown");
    const ra = obj?.ra as number | undefined;
    const dec = obj?.dec as number | undefined;
    return (
      <div style={{ fontSize: "0.82rem" }}>
        <div style={{ marginBottom: 6 }}>
          <strong>{name}</strong>
          {objType && <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>({objType}{specType ? ` · ${specType}` : ""})</span>}
          {ra != null && dec != null && (
            <div style={{ fontSize: "0.7rem", color: "var(--color-text-tertiary)" }}>RA {ra.toFixed(5)}°, Dec {dec.toFixed(5)}°</div>
          )}
        </div>
        {redshift && Object.keys(redshift).length > 0 && (
          <div style={{ fontSize: "0.76rem", marginBottom: 3 }}>
            <strong>z:</strong> {(redshift.value as number | null) != null ? (redshift.value as number).toPrecision(5) : "—"}
            {redshift.source != null && <span style={{ color: "var(--color-text-tertiary)", marginLeft: 6 }}>({String(redshift.source)})</span>}
          </div>
        )}
        {photometry && Object.keys(photometry).length > 0 && (
          <details style={{ fontSize: "0.76rem", marginTop: 3 }}>
            <summary style={{ cursor: "pointer" }}>Photometry ({Object.keys(photometry).length} bands)</summary>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 8px", marginTop: 4, paddingLeft: 10, fontSize: "0.73rem" }}>
              {Object.entries(photometry).slice(0, 12).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <span style={{ color: "var(--color-text-tertiary)" }}>{k}:</span>
                  <span>{typeof v === "number" ? v.toFixed(3) : String(v).slice(0, 40)}</span>
                </div>
              ))}
            </div>
          </details>
        )}
        {astrometry && Object.keys(astrometry).length > 0 && (
          <details style={{ fontSize: "0.76rem", marginTop: 3 }}>
            <summary style={{ cursor: "pointer" }}>Astrometry</summary>
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "2px 8px", marginTop: 4, paddingLeft: 10, fontSize: "0.73rem" }}>
              {Object.entries(astrometry).slice(0, 10).map(([k, v]) => (
                <div key={k} style={{ display: "contents" }}>
                  <span style={{ color: "var(--color-text-tertiary)" }}>{k}:</span>
                  <span>{typeof v === "number" ? v.toFixed(4) : String(v).slice(0, 40)}</span>
                </div>
              ))}
            </div>
          </details>
        )}
        {crossIds.length > 0 && (
          <details style={{ fontSize: "0.76rem", marginTop: 3 }}>
            <summary style={{ cursor: "pointer" }}>Cross-IDs ({crossIds.length})</summary>
            <div style={{ fontSize: "0.72rem", paddingLeft: 10, marginTop: 4, color: "var(--color-text-tertiary)" }}>
              {crossIds.slice(0, 20).join(" · ")}
            </div>
          </details>
        )}
        {sourcesResponded != null && (
          <div style={{ fontSize: "0.68rem", color: "var(--color-text-tertiary)", marginTop: 4 }}>
            Sources: {Array.isArray(sourcesResponded) ? sourcesResponded.join(", ") : String(sourcesResponded)}
          </div>
        )}
      </div>
    );
  }

  // Default: compact JSON
  return (
    <pre style={{ fontSize: "0.7rem", maxHeight: 100, overflow: "auto", color: "var(--color-text-tertiary)" }}>
      {JSON.stringify(result, null, 1).slice(0, 500)}
    </pre>
  );
}

function ActionResult({ result }: { result: Record<string, unknown> }) {
  const type = result.type as string;

  if (type === "search_results") {
    return <SearchResultTable data={result.data as Array<Record<string, unknown>>} />;
  }

  if (type === "adql_results") {
    const d = result.data as Record<string, unknown>;
    const columns = d?.columns as string[];
    const tableData = d?.data as Record<string, (number | string | null)[]>;
    const rowCount = d?.row_count as number;
    if (!columns || columns.length === 0) {
      return (
        <div className="chat-action-result">
          <p className="chat-result-empty">Query returned no results.</p>
        </div>
      );
    }
    const numRows = Math.min(rowCount || 0, 20);
    return (
      <div className="chat-action-result">
        <div className="chat-result-table-scroll">
        <table className="chat-result-table chat-result-table-adql">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col} title={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: numRows }).map((_, rowIdx) => (
              <tr key={rowIdx}>
                {columns.map((col) => (
                  <td key={col} title={tableData[col]?.[rowIdx] != null ? String(tableData[col][rowIdx]) : undefined}>
                    {tableData[col]?.[rowIdx] != null
                      ? String(tableData[col][rowIdx])
                      : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        {(rowCount || 0) > 20 && (
          <p className="chat-result-more">
            Showing 20 of {rowCount} rows
          </p>
        )}
      </div>
    );
  }

  if (type === "explanation") {
    return null;
  }

  if (type === "arxiv_tables") {
    const d = result.data as Record<string, unknown>;
    const tables = (d?.tables || []) as Array<{
      name: string; columns: string[]; rows: string[][]; row_count: number;
    }>;
    if (tables.length === 0) {
      return <div className="chat-action-result"><p className="chat-result-empty">No tables found.</p></div>;
    }
    return (
      <div className="chat-action-result">
        <p style={{ fontSize: "0.78rem", color: "var(--color-text-secondary)", margin: "0 0 0.5rem" }}>
          {d?.title as string} — {tables.length} table(s) found
        </p>
        {tables.map((t, ti) => (
          <div key={ti} style={{ marginBottom: "0.75rem" }}>
            <p style={{ fontSize: "0.75rem", fontWeight: 600, margin: "0 0 0.25rem" }}>{t.name}</p>
            <div className="chat-result-table-scroll">
              <table className="chat-result-table">
                <thead>
                  <tr>
                    {t.columns.map((c, ci) => <th key={ci}>{c}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {t.rows.slice(0, 30).map((row, ri) => (
                    <tr key={ri}>
                      {row.map((cell, ci) => <td key={ci}>{cell}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {t.row_count > 30 && (
              <p className="chat-result-more">Showing 30 of {t.row_count} rows</p>
            )}
          </div>
        ))}
      </div>
    );
  }

  if (type === "generated_pipeline") {
    const d = result.data as Record<string, unknown>;
    const dag = d?.dag as { nodes: Array<{ type: string; id: string }>; edges: Array<{ source: string; target: string }> };
    const warnings = (d?.warnings as string[]) || [];
    return (
      <div className="chat-action-result">
        <div style={{ padding: "0.5rem", background: "rgba(79,195,247,0.08)", borderRadius: 6, marginBottom: "0.5rem" }}>
          <strong style={{ color: "#4fc3f7" }}>{String(d?.name || "")}</strong>
          {d?.description ? <span style={{ color: "#aaa", marginLeft: 8 }}>{String(d.description)}</span> : null}
        </div>
        <div style={{ fontSize: "0.85rem", color: "#e0e0e0" }}>
          {dag?.nodes?.map((n, i) => (
            <span key={n.id}>
              {i > 0 && <span style={{ color: "#666", margin: "0 0.3rem" }}> → </span>}
              <span style={{ background: "#2a3a4a", padding: "2px 6px", borderRadius: 4 }}>{n.type}</span>
            </span>
          ))}
        </div>
        {warnings.length > 0 && (
          <div style={{ color: "#ffa726", fontSize: "0.75rem", marginTop: "0.3rem" }}>
            {warnings.join("; ")}
          </div>
        )}
        {typeof d?.template_id === "string" && (
          <div style={{ fontSize: "0.75rem", color: "#777", marginTop: "0.3rem" }}>
            Saved as template. <a href="/pipeline" style={{ color: "#4fc3f7" }}>Open in Pipeline Editor</a>
          </div>
        )}
      </div>
    );
  }

  if (type === "pipeline_modification") {
    const d = result.data as Record<string, unknown>;
    const mods = (d?.modifications as Array<Record<string, unknown>>) || [];
    return (
      <div className="chat-action-result">
        <div style={{ fontSize: "0.85rem", color: "#e0e0e0" }}>
          <strong>Pipeline modifications:</strong>
          <ul style={{ margin: "0.3rem 0", paddingLeft: "1.2rem" }}>
            {mods.map((m, i) => (
              <li key={i} style={{ marginBottom: "0.2rem" }}>
                <span style={{ color: "#4fc3f7" }}>{String(m.action)}</span>
                {m.node_id ? <> on <code>{String(m.node_id)}</code></> : null}
                {m.node ? <> — <code>{String((m.node as Record<string, unknown>).type)}</code></> : null}
              </li>
            ))}
          </ul>
          {d?.explanation ? <p style={{ color: "#aaa", fontSize: "0.8rem" }}>{String(d.explanation)}</p> : null}
        </div>
      </div>
    );
  }

  if (type === "pipeline_comment") {
    const d = result.data as Record<string, unknown>;
    return (
      <div className="chat-action-result">
        <div style={{ padding: "0.5rem", background: "rgba(255,167,38,0.08)", borderRadius: 6, fontSize: "0.85rem" }}>
          <strong style={{ color: "#ffa726" }}>AI Review Comment</strong>
          <p style={{ color: "#e0e0e0", margin: "0.3rem 0 0" }}>{String(d?.comment || "")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-action-result">
      <pre className="chat-result-raw">
        {JSON.stringify(result, null, 2)}
      </pre>
    </div>
  );
}

function ApiKeyPrompt({ onSaved }: { onSaved: () => void }) {
  const [keyInput, setKeyInput] = useState("");
  const [provider, setProvider] = useState<"anthropic" | "openai" | "deepseek">("anthropic");

  const providerInfo: Record<string, { label: string; url: string; placeholder: string; rec?: boolean }> = {
    anthropic: { label: "Anthropic (Claude)", url: "https://console.anthropic.com/settings/keys", placeholder: "sk-ant-...", rec: true },
    openai:    { label: "OpenAI (GPT)",       url: "https://platform.openai.com/api-keys",        placeholder: "sk-..." },
    deepseek:  { label: "DeepSeek",            url: "https://platform.deepseek.com/api_keys",      placeholder: "sk-..." },
  };

  const info = providerInfo[provider];

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    // M9: route through the sessionStorage-first helper instead of writing
    // straight to localStorage.  Keys no longer leak across browser
    // restarts unless the user opts in via the persist flag.
    const keys = getStoredApiKeys();
    keys[provider] = key;
    writeStoredApiKeys(keys);
    writeStoredAiProvider(provider);
    onSaved();
  }

  return (
    <div className="chat-apikey-prompt">
      <h3>Configure API Key</h3>
      <p>To use the AI assistant, enter an API key from any supported provider.</p>
      <div className="chat-apikey-provider-select">
        {Object.entries(providerInfo).map(([key, p]) => (
          <button
            key={key}
            type="button"
            className={`btn-small ${provider === key ? "btn-primary" : "btn-secondary"}`}
            onClick={() => { setProvider(key as "anthropic" | "openai" | "deepseek"); setKeyInput(""); }}
          >
            {p.label}{p.rec ? " ★" : ""}
          </button>
        ))}
      </div>
      <p className="chat-apikey-hint">
        Get a key at{" "}
        <a href={info.url} target="_blank" rel="noopener noreferrer">
          {new URL(info.url).hostname}
        </a>
      </p>
      <form className="chat-apikey-form" onSubmit={handleSave}>
        <input
          type="text"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder={info.placeholder}
          className="chat-apikey-input"
          autoComplete="off"
        />
        <button type="submit" className="btn-primary" disabled={!keyInput.trim()}>
          Save & Start
        </button>
      </form>
    </div>
  );
}

interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: [number, Record<string, unknown>][];
  // Marker written before the stream starts; replaced in place with the real
  // reply on success, or with an error bubble on failure.  If the page is
  // refreshed mid-stream, loadChatHistory reconciles against the server copy
  // or surfaces a "retry" CTA instead of a blank bubble.
  _pending?: { started_at: number };
}

const ANON_CHAT_SCOPE = "anon";
const CHAT_HISTORY_STORAGE_KEY = "astro_chat_history";
const CHAT_DRAFT_STORAGE_KEY = "astro_chat_draft";
const CHAT_AUTOSAVE_DRAFT_STORAGE_KEY = "astro_chat_autosave_draft";
const CURRENT_CHAT_SESSION_STORAGE_KEY = "astro_current_chat_session_id";
const LOCAL_CHAT_SESSIONS_STORAGE_KEY = "astro_local_chat_sessions";

type ChatStorageUser = { id?: string | null } | null | undefined;

function chatStorageScope(user: ChatStorageUser): string {
  const id = String(user?.id || "").trim();
  return id ? `user:${id}` : ANON_CHAT_SCOPE;
}

function scopedChatStorageKey(baseKey: string, scope: string): string {
  return `${baseKey}:${scope}`;
}

function clearFreshChatStorage(scope: string): void {
  const exactKeys = new Set([
    scopedChatStorageKey(CHAT_HISTORY_STORAGE_KEY, scope),
    scopedChatStorageKey(CHAT_AUTOSAVE_DRAFT_STORAGE_KEY, scope),
    scopedChatStorageKey(CURRENT_CHAT_SESSION_STORAGE_KEY, scope),
    CHAT_HISTORY_STORAGE_KEY,
    CHAT_AUTOSAVE_DRAFT_STORAGE_KEY,
    CURRENT_CHAT_SESSION_STORAGE_KEY,
  ]);
  for (let i = localStorage.length - 1; i >= 0; i -= 1) {
    const key = localStorage.key(i);
    if (!key) continue;
    if (
      exactKeys.has(key)
      || key.startsWith(`${CHAT_HISTORY_STORAGE_KEY}:`)
      || key.startsWith(`${CHAT_AUTOSAVE_DRAFT_STORAGE_KEY}:`)
      || key.startsWith(`${CURRENT_CHAT_SESSION_STORAGE_KEY}:`)
    ) {
      localStorage.removeItem(key);
    }
  }
}

function loadChatHistory(scope: string): DisplayMessage[] {
  try {
    const raw = localStorage.getItem(scopedChatStorageKey(CHAT_HISTORY_STORAGE_KEY, scope));
    if (!raw) return [];
    const stored = JSON.parse(raw) as StoredMessage[];
    return stored.map((m) => ({
      ...m,
      actionResults: m.actionResults ? new Map(m.actionResults) : new Map(),
    }));
  } catch {
    return [];
  }
}

// Soft cap for serialized chat history (localStorage limit is typically 5 MB).
// When we cross this threshold we prune in two passes: first strip the heavy
// `tool_result` blobs off the oldest messages, then drop entire oldest
// messages if we still overflow.  Keeps user-visible text + action names.
const CHAT_HISTORY_SOFT_CAP_BYTES = 4 * 1024 * 1024;

function serializeStored(messages: DisplayMessage[]): StoredMessage[] {
  return messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    actions: m.actions,
    actionResults: m.actionResults ? Array.from(m.actionResults.entries()) : undefined,
    _pending: m._pending,
  }));
}

// Heavy non-figure fields we strip first under memory pressure.  Figures
// are the USER-VISIBLE product of run_python and must not be stripped
// until absolutely necessary (the reviewer found plots vanishing from
// history on reload — they were collateral damage of this pruner).
const _STRIP_ORDER: string[][] = [
  // Pass A: strip the heaviest non-user-visible fields.  These are
  // assistant-facing caches (e.g. full ADQL rows, verbose variable
  // reprs) that the AI can re-fetch via get_cached_results.
  ["rows", "data", "raw_data", "results", "traceback"],
  // Pass B: trim variables (can carry large ndarray reprs even after
  // our sandbox-side _safe_var_repr cap).
  ["variables", "variable_types"],
  // Pass C: stdout (usually small, but can be 50 KB).
  ["stdout"],
  // Pass D: figures — LAST resort.  We replace the array with a marker
  // dict {__figures_offloaded__: N} so the UI can render a
  // "N figures were offloaded to save space" placeholder + trigger a
  // server rehydrate via getChatSession.
  ["figures"],
];

function _pruneToolResults(stored: StoredMessage[]): boolean {
  // Walk from oldest to newest; strip the heaviest field from the first
  // un-pruned tool_result we find. Returns true if anything was changed.
  // New order: strip non-figure fields FIRST; figures only as last resort.
  // This fixes the reviewer-reported regression where every tool_result
  // (including its figures) was collapsed to {__offloaded__: true} on the
  // first pruning pass, losing user-visible plots on page reload.
  for (const keySet of _STRIP_ORDER) {
    for (let i = 0; i < stored.length; i++) {
      const actions = stored[i].actions;
      if (!actions) continue;
      for (const action of actions) {
        const tr = (action as { tool_result?: unknown }).tool_result;
        if (!tr || typeof tr !== "object") continue;
        const trObj = tr as Record<string, unknown>;
        if (trObj.__offloaded__) continue;  // already fully offloaded
        let changed = false;
        for (const key of keySet) {
          if (key in trObj) {
            if (key === "figures" && Array.isArray(trObj[key])) {
              const n = (trObj[key] as unknown[]).length;
              if (n > 0) {
                trObj.__figures_offloaded__ = n;
                trObj.figures = [];
                changed = true;
              }
            } else if (trObj[key] !== undefined) {
              delete trObj[key];
              changed = true;
            }
          }
        }
        if (changed) return true;
      }
    }
  }
  // Final fallback: nothing left to strip selectively — fall back to the
  // old behaviour of collapsing the whole result.
  for (let i = 0; i < stored.length; i++) {
    const actions = stored[i].actions;
    if (!actions) continue;
    for (const action of actions) {
      const tr = (action as { tool_result?: unknown }).tool_result;
      if (tr && typeof tr === "object" && !(tr as { __offloaded__?: true }).__offloaded__) {
        (action as { tool_result?: unknown }).tool_result = { __offloaded__: true };
        return true;
      }
    }
  }
  return false;
}

function safeSetChatHistory(messages: DisplayMessage[], scope: string): { written: boolean; droppedMessages: number } {
  let stored = serializeStored(messages);
  let payload = JSON.stringify(stored);
  let droppedMessages = 0;
  const storageKey = scopedChatStorageKey(CHAT_HISTORY_STORAGE_KEY, scope);

  // Pass 1: if over cap, strip heavy tool_result payloads oldest-first.
  while (payload.length > CHAT_HISTORY_SOFT_CAP_BYTES && _pruneToolResults(stored)) {
    payload = JSON.stringify(stored);
  }
  // Pass 2: if still over, drop oldest messages entirely.
  while (payload.length > CHAT_HISTORY_SOFT_CAP_BYTES && stored.length > 2) {
    stored.shift();
    droppedMessages++;
    payload = JSON.stringify(stored);
  }

  try {
    localStorage.setItem(storageKey, payload);
    localStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
    return { written: true, droppedMessages };
  } catch {
    // QuotaExceededError — fall back to keeping only the last 20 messages
    // with tool_results stripped.
    try {
      stored = serializeStored(messages.slice(-20));
      while (_pruneToolResults(stored)) {
        /* strip all tool results */
      }
      localStorage.setItem(storageKey, JSON.stringify(stored));
      localStorage.removeItem(CHAT_HISTORY_STORAGE_KEY);
      return { written: true, droppedMessages: Math.max(droppedMessages, messages.length - 20) };
    } catch {
      return { written: false, droppedMessages };
    }
  }
}

function saveChatHistory(messages: DisplayMessage[], scope: string): void {
  safeSetChatHistory(messages, scope);
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function generateLatexFallback(msgs: DisplayMessage[]): string {
  const esc = (s: string) => s.replace(/[&%$#_{}~^\\]/g, (c) => {
    const map: Record<string, string> = {
      "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_",
      "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "\\": "\\textbackslash{}",
    };
    return map[c] ?? c;
  });
  const lines: string[] = [
    "\\documentclass[12pt]{article}",
    "\\usepackage[utf8]{inputenc}",
    "\\usepackage{amsmath,graphicx,hyperref}",
    `\\title{Standard Astro Research Report}`,
    "\\author{Standard Astro User}",
    "\\date{\\today}",
    "\\begin{document}",
    "\\maketitle",
    "",
  ];
  for (const m of msgs) {
    if (m.role === "user") {
      lines.push("\\subsection*{User}", esc(m.content), "");
    } else {
      lines.push("\\subsection*{AI Assistant}", esc(m.content), "");
    }
  }
  lines.push("\\end{document}");
  return lines.join("\n");
}

interface LocalChatSession {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
  messages: Array<{ role: string; content: string; actions?: unknown[] }>;
}

type ExportAction = "markdown" | "notebook" | "html" | "latex" | "bibtex";
type JournalFormat = "aastex" | "mnras" | "aa";
type ShareAccessLevel = "view" | "fork" | "comment";
type PaperTab =
  | "abstract"
  | "introduction"
  | "data_sources"
  | "analysis_methods"
  | "results"
  | "discussion"
  | "conclusions"
  | "acknowledgments";

interface ToastState {
  message: string;
  tone: "success" | "error" | "info";
}

function getPaperSectionText(paperJson: Record<string, unknown>, tab: PaperTab): string {
  switch (tab) {
    case "abstract":
      return String(paperJson.abstract || "");
    case "introduction":
      return String(((paperJson.introduction as Record<string, unknown> | undefined)?.text) || "");
    case "data_sources":
      return String(((paperJson.data_and_methods as Record<string, unknown> | undefined)?.data_sources) || "");
    case "analysis_methods":
      return String(((paperJson.data_and_methods as Record<string, unknown> | undefined)?.analysis_methods) || "");
    case "results":
      return String(((paperJson.results as Record<string, unknown> | undefined)?.text) || "");
    case "discussion":
      return String(((paperJson.discussion as Record<string, unknown> | undefined)?.text) || "");
    case "conclusions":
      return String(paperJson.conclusions || "");
    case "acknowledgments":
      return String(paperJson.acknowledgments || "");
    default:
      return "";
  }
}

function setPaperSectionText(
  paperJson: Record<string, unknown>,
  tab: PaperTab,
  value: string,
): Record<string, unknown> {
  const next = structuredClone(paperJson);
  switch (tab) {
    case "abstract":
      next.abstract = value;
      break;
    case "introduction":
      next.introduction = {
        ...((next.introduction as Record<string, unknown> | undefined) || {}),
        text: value,
      };
      break;
    case "data_sources":
      next.data_and_methods = {
        ...((next.data_and_methods as Record<string, unknown> | undefined) || {}),
        data_sources: value,
      };
      break;
    case "analysis_methods":
      next.data_and_methods = {
        ...((next.data_and_methods as Record<string, unknown> | undefined) || {}),
        analysis_methods: value,
      };
      break;
    case "results":
      next.results = {
        ...((next.results as Record<string, unknown> | undefined) || {}),
        text: value,
      };
      break;
    case "discussion":
      next.discussion = {
        ...((next.discussion as Record<string, unknown> | undefined) || {}),
        text: value,
      };
      break;
    case "conclusions":
      next.conclusions = value;
      break;
    case "acknowledgments":
      next.acknowledgments = value;
      break;
  }
  return next;
}

function localChatSessionsKey(scope: string): string {
  return scopedChatStorageKey(LOCAL_CHAT_SESSIONS_STORAGE_KEY, scope);
}

function readLocalChatSessions(scope: string): LocalChatSession[] {
  try {
    const raw = localStorage.getItem(localChatSessionsKey(scope));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeLocalChatSessions(sessions: LocalChatSession[], scope: string) {
  try {
    localStorage.setItem(localChatSessionsKey(scope), JSON.stringify(sessions));
    localStorage.removeItem(LOCAL_CHAT_SESSIONS_STORAGE_KEY);
  } catch {
    // ignore storage failures
  }
}

function summarizeLocalSessions(scope: string): ChatSessionSummary[] {
  return readLocalChatSessions(scope).map(({ id, title, message_count, updated_at }) => ({
    id,
    title,
    message_count,
    updated_at,
  }));
}

function saveLocalChatSession(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  scope: string,
  sessionId?: string | null,
): { id: string } {
  const sessions = readLocalChatSessions(scope);
  const id = sessionId || crypto.randomUUID();
  const title = messages.find((m) => m.role === "user")?.content.slice(0, 60) || "New Chat";
  const updated_at = new Date().toISOString();
  const session: LocalChatSession = {
    id,
    title,
    updated_at,
    message_count: messages.length,
    messages,
  };
  const next = [session, ...sessions.filter((s) => s.id !== id)].slice(0, 20);
  writeLocalChatSessions(next, scope);
  return { id };
}

function loadLocalChatSession(id: string, scope: string): LocalChatSession | null {
  return readLocalChatSessions(scope).find((session) => session.id === id) || null;
}

function deleteLocalChatSession(id: string, scope: string): void {
  writeLocalChatSessions(readLocalChatSessions(scope).filter((session) => session.id !== id), scope);
}

// W6 (PART W): "Validate assumptions first" 是 NextStepsPanel 新增的
// 首项. B3/B4 回归里发现缺少一个"报告前主动验证每个数值 claim 的工具
// 归属"的入口 — 点击后让 AI 逐条列出每个数值 claim 的 tool_result 来源,
// 或明确说 "not measured this turn". 这是零幻觉门的配套 UX: 哪些数字
// 没 tool 支撑, AI 自己先检查, 不等 validator 兜底 block.
// Exported 以便 ChatPage.test.tsx 做回归.
// PART Y Q3: HMR fast-refresh 对此文件失效是已知接受的代价 — 拆分常量到
// 独立文件需要更新测试 import 路径, 收益小.
// eslint-disable-next-line react-refresh/only-export-components
export const NEXT_STEPS_PANEL_ACTIONS: Array<{ label: string; prompt: string }> = [
  {
    label: "Validate assumptions first",
    prompt:
      "Before we write up the results, please re-verify each of our " +
      "key assumptions using the tools we actually called this turn. " +
      "List every numeric claim that would go into a report (age, " +
      "distance, mass, period, class, membership count, ...) and for " +
      "each one state which tool_result supplied the value, or say " +
      "'not measured this turn'. If any assumption has no tool backing, " +
      "propose the minimum extra tool calls needed (search_literature " +
      "for a citation, fit_isochrone for a new fit, run_adql for a Gaia " +
      "column).",
  },
  { label: "Export as notebook", prompt: "Export this session as a Jupyter notebook" },
  { label: "Run sensitivity analysis", prompt: "Run a sensitivity analysis on these results" },
  { label: "Search related literature", prompt: "Search for related papers on ADS" },
];

function NextStepsPanel({ onSend }: { onSend: (msg: string) => void }) {
  return (
    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "8px 0", borderTop: "1px solid var(--color-border)" }}>
      {NEXT_STEPS_PANEL_ACTIONS.map((s, i) => (
        <button key={i} className="btn-ghost btn-small" onClick={() => onSend(s.prompt)} style={{ fontSize: "0.75rem" }}>
          {s.label}
        </button>
      ))}
    </div>
  );
}

export default function ChatPage() {
  const { user } = useAuth();
  // PART Y Q3: depend on user?.id, not the user object — chatStorageScope
  // is a function of identity, not of the wrapping object's reference.
  // Re-computing on every user-object reference change would invalidate
  // localStorage scope on every Auth refresh.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const storageScope = useMemo(() => chatStorageScope(user), [user?.id]);
  const freshChatRequestedAtBoot = new URLSearchParams(window.location.search).has("fresh_chat");
  const { t } = useI18n();
  const { track } = useTracking();
  const [hasKey, setHasKey] = useState(() => hasStoredAiKey());

  // F4.1: in addition to the browser-side stored key, ask the backend
  // which server-side backends are configured (env vars + user's stored
  // server keys).  Either-or is enough to enable the Send button.
  const [serverBackendReady, setServerBackendReady] = useState<boolean | null>(null);
  const [serverBackendList, setServerBackendList] = useState<string[]>([]);
  const [selectedModelStatus, setSelectedModelStatus] = useState<AIModelProfile | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { getAIBackendStatus } = await import("../../api/client");
        const provider = getPreferredAiProvider();
        const modelProfile = getPreferredAiModelProfile(provider);
        const status = await getAIBackendStatus(provider, modelProfile);
        if (cancelled) return;
        setServerBackendReady(!status.needs_setup);
        setServerBackendList(status.configured_backends);
        setSelectedModelStatus(status.selected_model_status || null);
      } catch {
        if (cancelled) return;
        setServerBackendReady(null); // unknown — don't block
      }
    })();
    return () => { cancelled = true; };
  }, []);
  const aiBackendReady = hasKey || serverBackendReady === true;

  // Re-check API key on mount (picks up keys set in Settings page).
  // PART Y Q3: intentionally re-runs every render to pick up cross-tab
  // localStorage changes; the early return guards against feedback loops.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!hasKey && hasStoredAiKey()) setHasKey(true);
  });

  const [messages, setMessages] = useState<DisplayMessage[]>(() => (
    freshChatRequestedAtBoot ? [] : loadChatHistory(storageScope)
  ));
  const conversationProvenance = useConversationProvenance(messages);
  const [input, setInput] = useState("");
  const [pageError, _setPageError] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [loading, setLoading] = useState(false);
  // R0d: abort controller for the in-flight chat stream.  A Stop button in
  // the composer calls abort() to cancel the fetch + let the backend task
  // observe the disconnect and unwind.
  const chatAbortRef = useRef<AbortController | null>(null);
  const [exporting, setExporting] = useState<Record<ExportAction, boolean>>({
    markdown: false,
    notebook: false,
    html: false,
    latex: false,
    bibtex: false,
  });
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [paperSessionId, setPaperSessionId] = useState<string | null>(null);
  const [paperFormat, setPaperFormat] = useState<JournalFormat>("aastex");
  const [paperValidation, setPaperValidation] = useState<AnalysisValidationResult | null>(null);
  const [paperDraft, setPaperDraft] = useState<PaperDraftResponse | null>(null);
  const [paperEditorJson, setPaperEditorJson] = useState<Record<string, unknown> | null>(null);
  const [paperTab, setPaperTab] = useState<PaperTab>("abstract");
  const [paperLoading, setPaperLoading] = useState(false);
  const [paperGenerating, setPaperGenerating] = useState(false);
  const [paperSaving, setPaperSaving] = useState(false);
  const [executingActions, setExecutingActions] = useState<Set<string>>(
    new Set()
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const messagesRef = useRef<DisplayMessage[]>(messages);

  // Session management
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const currentSessionIdRef = useRef<string | null>(null);
  const [currentSessionTitle, setCurrentSessionTitle] = useState<string>("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    return localStorage.getItem("astro_chat_sidebar_collapsed") === "1";
  });
  const [sessionSearch, setSessionSearch] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved" | "idle">("idle");
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [shareAccessLevel, setShareAccessLevel] = useState<ShareAccessLevel>("view");
  const [shareExpiryHours, setShareExpiryHours] = useState<number>(72);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sessionShares, setSessionShares] = useState<SessionShareItem[]>([]);
  const [sessionSnapshots, setSessionSnapshots] = useState<SessionSnapshotItem[]>([]);
  const [snapshotName, setSnapshotName] = useState("");
  const [snapshotCompareSelection, setSnapshotCompareSelection] = useState<string[]>([]);
  const [snapshotDiff, setSnapshotDiff] = useState<SessionSnapshotDiff | null>(null);
  const [shareLoading, setShareLoading] = useState(false);
  const pythonSessionIdRef = useRef<string>(crypto.randomUUID());
  const freshChatRequestRef = useRef(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentSessionScopeRef = useRef(storageScope);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  const showToast = useCallback((message: string, tone: ToastState["tone"] = "success") => {
    setToast({ message, tone });
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
    toastTimerRef.current = setTimeout(() => setToast(null), 4000);
  }, []);

  const rememberExportInWorkspace = useCallback(async (
    blob: Blob,
    filename: string,
    exportKind: ExportAction,
  ): Promise<boolean> => {
    if (!user) return false;
    try {
      const upload = await uploadGeneralFile(
        new File([blob], filename, { type: blob.type || "application/octet-stream" })
      );
      registerWorkspaceExport({
        id: upload.id,
        filename: upload.filename,
        storagePath: upload.path,
        exportKind,
        contentType: blob.type || "application/octet-stream",
        sizeBytes: blob.size,
        localOnly: false,
      }, storageScope);
      return true;
    } catch {
      return false;
    }
  }, [user, storageScope]);

  const handleExport = useCallback(async (
    exportKind: ExportAction,
    label: string,
    filename: string,
    exporter: () => Promise<Blob>,
    options?: { emptyMessage?: string; fallback?: () => Blob; skipDownloadWhenEmpty?: boolean },
  ) => {
    setExporting((prev) => ({ ...prev, [exportKind]: true }));
    try {
      let blob = await exporter();
      if (blob.size === 0 && options?.skipDownloadWhenEmpty) {
        showToast(options.emptyMessage || `No ${label} content was available to export`, "info");
        return;
      }
      if (blob.size === 0) {
        if (options?.fallback) {
          blob = options.fallback();
        } else {
          throw new Error(options?.emptyMessage || `${label} export returned an empty file`);
        }
      }

      downloadBlob(blob, filename);
      const savedToWorkspace = await rememberExportInWorkspace(blob, filename, exportKind);
      const exportEventMap: Record<ExportAction, string> = {
        markdown: "export.paper_draft",
        notebook: "export.notebook",
        html: "export.html",
        latex: "export.latex",
        bibtex: "export.paper_draft",
      };
      const combinedText = messages.map((msg) => msg.content).join(" ");
      const bibcodeMatches = combinedText.match(/\b\d{4}[A-Za-z][A-Za-z&.]+\.+\S+/g) || [];
      track(exportEventMap[exportKind], {
        journal_format: exportKind === "latex" ? "aastex" : undefined,
        sections: exportKind === "latex" ? ["chat_export"] : undefined,
        figures_count: messages.filter((msg) => (msg.actions || []).some((action) => action.action === "plot")).length,
        citations_count: exportKind === "bibtex" ? bibcodeMatches.length : undefined,
        cell_count: exportKind === "notebook" ? messages.length + 2 : undefined,
        word_count: combinedText.split(/\s+/).filter(Boolean).length,
      });

      if (savedToWorkspace) {
        showToast(`Exported ${label} successfully — saved to Workspace`, "success");
      } else if (user) {
        showToast(`Exported ${label} successfully — downloaded locally, but Workspace sync failed`, "success");
      } else {
        showToast(`Exported ${label} successfully — downloaded locally. Sign in to sync it to Workspace.`, "success");
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : `${label} export failed`;
      showToast(`Export failed: ${detail}`, "error");
    } finally {
      setExporting((prev) => ({ ...prev, [exportKind]: false }));
    }
  }, [messages, rememberExportInWorkspace, showToast, track, user]);

  const refreshSessions = useCallback(() => {
    if (user) {
      listChatSessions().then(setSessions).catch(() => setSessions([]));
      return;
    }
    setSessions(summarizeLocalSessions(storageScope));
  }, [storageScope, user]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const loadCollaborationState = useCallback(async (sessionId: string) => {
    if (!user) return;
    const [shares, snapshots] = await Promise.all([
      listSessionShares(sessionId),
      listSessionSnapshots(sessionId),
    ]);
    setSessionShares(shares);
    setSessionSnapshots(snapshots);
  }, [user]);

  const persistSession = useCallback(async (
    data: Array<{ role: string; content: string; actions?: unknown[] }>,
    sessionId?: string | null,
  ) => {
    if (user) {
      try {
        return await saveChatSession(data, sessionId || undefined);
      } catch {
        return saveLocalChatSession(data, storageScope, sessionId);
      }
    }
    return saveLocalChatSession(data, storageScope, sessionId);
  }, [storageScope, user]);

  const ensurePersistedSession = useCallback(async () => {
    if (messages.length === 0) {
      throw new Error("Save at least one message before sharing or snapshotting this session");
    }
    if (!user) {
      throw new Error("Sign in to manage collaboration features");
    }
    const data = messages.map((message) => ({
      role: message.role,
      content: message.content,
      actions: message.actions,
    }));
    const saved = await saveChatSession(data, currentSessionId || undefined);
    setCurrentSessionId(saved.id);
    refreshSessions();
    return saved.id;
  }, [currentSessionId, messages, refreshSessions, user]);

  const handleOpenPaperDraft = useCallback(async () => {
    if (!user) {
      showToast("Sign in to generate a paper draft", "info");
      return;
    }
    if (messages.length === 0) {
      showToast("Add some analysis messages before generating a paper draft", "info");
      return;
    }

    setPaperModalOpen(true);
    setPaperLoading(true);
    setPaperDraft(null);
    setPaperEditorJson(null);
    setPaperValidation(null);
    try {
      const sessionData = messages.map((m) => ({
        role: m.role,
        content: m.content,
        actions: m.actions,
      }));
      const saved = await saveChatSession(sessionData, currentSessionId || undefined);
      setCurrentSessionId(saved.id);
      setPaperSessionId(saved.id);
      refreshSessions();

      const validation = await validatePaperSession(saved.id);
      setPaperValidation(validation);
      setPaperTab("abstract");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper validation failed";
      showToast(detail, "error");
      setPaperModalOpen(false);
    } finally {
      setPaperLoading(false);
    }
  }, [currentSessionId, messages, refreshSessions, showToast, user]);

  const handleGeneratePaper = useCallback(async (overrideValidation = false) => {
    if (!paperSessionId) {
      showToast("Save the session before generating a paper draft", "error");
      return;
    }

    setPaperGenerating(true);
    try {
      const draft = await generatePaperDraft(paperSessionId, paperFormat, overrideValidation);
      setPaperDraft(draft);
      setPaperEditorJson(draft.paper_json);
      setPaperValidation(draft.validation);
      track("export.paper_draft", {
        journal_format: paperFormat,
        word_count: String(draft.paper_json.abstract || "").split(/\s+/).filter(Boolean).length,
        figures_count: Array.isArray((draft.paper_json.results as Record<string, unknown> | undefined)?.figures)
          ? (((draft.paper_json.results as Record<string, unknown>).figures as unknown[]) || []).length
          : 0,
        citations_count: (draft.bibtex.match(/@\w+\{/g) || []).length,
      });
      showToast("Paper draft generated", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper generation failed";
      showToast(detail, "error");
    } finally {
      setPaperGenerating(false);
    }
  }, [paperFormat, paperSessionId, showToast, track]);

  const handleSavePaperDraft = useCallback(async () => {
    if (!paperDraft || !paperEditorJson) return;
    setPaperSaving(true);
    try {
      const updated = await updatePaperDraft(paperDraft.id, paperEditorJson);
      setPaperDraft(updated);
      setPaperEditorJson(updated.paper_json);
      showToast("Paper draft saved", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Saving paper draft failed";
      showToast(detail, "error");
    } finally {
      setPaperSaving(false);
    }
  }, [paperDraft, paperEditorJson, showToast]);

  const handleTogglePaperPublish = useCallback(async () => {
    if (!paperDraft) return;
    setPaperSaving(true);
    try {
      const updated = paperDraft.is_public
        ? await unpublishPaperDraft(paperDraft.id)
        : await publishPaperDraft(paperDraft.id);
      setPaperDraft(updated);
      setPaperEditorJson(updated.paper_json);
      if (updated.is_public && updated.public_url) {
        const absolute = new URL(updated.public_url, window.location.origin).toString();
        if (navigator.clipboard?.writeText) {
          void navigator.clipboard.writeText(absolute).catch(() => {});
        }
        showToast("Paper draft published and link copied", "success");
      } else {
        showToast("Paper draft unpublished", "success");
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper publication update failed";
      showToast(detail, "error");
    } finally {
      setPaperSaving(false);
    }
  }, [paperDraft, showToast]);

  const handleRegeneratePaperSection = useCallback(async () => {
    if (!paperSessionId || !paperEditorJson) return;
    setPaperGenerating(true);
    try {
      const regenerated = await generatePaperDraft(
        paperSessionId,
        paperFormat,
        paperValidation?.overall_status === "FAIL",
      );
      const nextPaperJson = setPaperSectionText(
        paperEditorJson,
        paperTab,
        getPaperSectionText(regenerated.paper_json, paperTab),
      );
      setPaperEditorJson(nextPaperJson);
      if (paperDraft) {
        const updated = await updatePaperDraft(paperDraft.id, nextPaperJson);
        setPaperDraft(updated);
        setPaperEditorJson(updated.paper_json);
      }
      showToast(`Regenerated ${paperTab.replace(/_/g, " ")}`, "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Section regeneration failed";
      showToast(detail, "error");
    } finally {
      setPaperGenerating(false);
    }
  }, [paperDraft, paperEditorJson, paperFormat, paperSessionId, paperTab, paperValidation?.overall_status, showToast]);

  useEffect(() => {
    // Stage 3 Bug 5 修复: 原 mount effect 里跑 4-5 个 setMessages, 其中两个是
    // async (refresh-resume + figure-rehydrate) fire-and-forget, 顺序由网络
    // 决定, 偶尔消息闪烁/乱序. 重构为:
    //   Phase 1 (sync): 决定 initialMessages, 一次 setMessages 设进去
    //   Phase 2 (async): 一个 loadChatSession 同时承担 refresh-resume +
    //                    figure-rehydrate 两件事, 最后一次 setMessages 合并
    // 总 setMessages 调用 ≤ 2 次, 没有并发 fetch.
    const draftKey = scopedChatStorageKey(CHAT_DRAFT_STORAGE_KEY, storageScope);
    const autosaveDraftKey = scopedChatStorageKey(CHAT_AUTOSAVE_DRAFT_STORAGE_KEY, storageScope);
    const currentSessionKey = scopedChatStorageKey(CURRENT_CHAT_SESSION_STORAGE_KEY, storageScope);
    const scopedSessionId = localStorage.getItem(currentSessionKey);
    setCurrentSessionId(scopedSessionId);
    if (!scopedSessionId) setCurrentSessionTitle("");

    // ── Phase 1 (sync): determine initialMessages ──
    const urlParams = new URLSearchParams(window.location.search);
    const urlRequestedFreshChat = urlParams.has("fresh_chat");
    const newSession = localStorage.getItem("astro_chat_new_session") || urlRequestedFreshChat;

    let initialMessages: DisplayMessage[];
    if (newSession) {
      localStorage.removeItem("astro_chat_new_session");
      initialMessages = [];
      messagesRef.current = [];
      setCurrentSessionId(null);
      currentSessionIdRef.current = null;
      freshChatRequestRef.current = true;
      pythonSessionIdRef.current = crypto.randomUUID();
      clearFreshChatStorage(storageScope);
      if (urlRequestedFreshChat) {
        urlParams.delete("fresh_chat");
        const nextQuery = urlParams.toString();
        window.history.replaceState(
          null,
          "",
          `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ""}${window.location.hash}`,
        );
      }
    } else {
      initialMessages = loadChatHistory(storageScope);
    }

    const draft = localStorage.getItem(draftKey);
    if (draft) {
      setInput(draft);
      localStorage.removeItem(draftKey);
    }
    localStorage.removeItem(CHAT_DRAFT_STORAGE_KEY);

    // One-time migration: legacy `astro_chat_autosave_draft` is retired; fold
    // its content into astro_chat_history if the latter is empty, then clean up.
    if (!newSession && !draft && initialMessages.length === 0) {
      try {
        const autosaved = localStorage.getItem(autosaveDraftKey);
        if (autosaved) {
          const parsed = JSON.parse(autosaved) as DisplayMessage[];
          if (parsed.length > 0) {
            initialMessages = parsed.map((m) => ({ ...m, actionResults: new Map() }));
            safeSetChatHistory(initialMessages, storageScope);
          }
        }
      } catch { /* ignore */ }
      localStorage.removeItem(autosaveDraftKey);
      localStorage.removeItem(CHAT_AUTOSAVE_DRAFT_STORAGE_KEY);
    }

    setMessages(initialMessages);

    // ── Phase 2 (async): single server fetch for refresh-resume + figure-rehydrate ──
    if (newSession || !user) return;
    const sid = localStorage.getItem(currentSessionKey);
    if (!sid) return;

    const last = initialMessages[initialMessages.length - 1];
    const needRefreshResume = !!(last && last._pending && last.role === "assistant");
    const needFigureRehydrate = initialMessages.some((m) =>
      (m.actions || []).some((a) => {
        const tr = (a as Record<string, unknown>).tool_result as Record<string, unknown> | undefined;
        if (!tr || typeof tr !== "object") return false;
        return tr.__offloaded__ === true || tr.__figures_offloaded__ !== undefined;
      }),
    );
    if (!needRefreshResume && !needFigureRehydrate) return;

    void loadChatSession(sid)
      .then((session) => {
        const srvMsgs = session.messages || [];
        setMessages((prev) => {
          let nextMessages = prev;

          // 2a. Refresh-resume: replace pending bubble with server reply.
          // Falls through to the in-UI "interrupted, retry" bubble if the
          // server has no newer reply either.
          if (needRefreshResume && last) {
            const prevUser = initialMessages[initialMessages.length - 2];
            if (prevUser) {
              const idx = srvMsgs.findIndex(
                (sm) => sm.role === "user" && sm.content === prevUser.content,
              );
              if (idx >= 0 && idx + 1 < srvMsgs.length && srvMsgs[idx + 1].role === "assistant") {
                // Stage 3 Bug 4: validateActions 过滤脏数据
                const validated = validateActions(srvMsgs[idx + 1].actions);
                const adopted: DisplayMessage = {
                  id: last.id,
                  role: "assistant",
                  content: srvMsgs[idx + 1].content,
                  actions: validated.length > 0 ? validated : undefined,
                  actionResults: new Map(),
                };
                nextMessages = nextMessages.map((m) => (m.id === last.id ? adopted : m));
              }
            }
          }

          // 2b. Figure-rehydrate: replace offloaded markers with server figures.
          // Stage 3 Bug 3: 用尾对齐 array index 匹配, content 前 50 字符 sanity.
          if (needFigureRehydrate) {
            const offset = srvMsgs.length - initialMessages.length;
            nextMessages = nextMessages.map((m, localIdx) => {
              if (m.role !== "assistant" || !m.actions?.length) return m;
              const locallyOffloaded = m.actions.some((a) => {
                const tr = (a as Record<string, unknown>).tool_result as Record<string, unknown> | undefined;
                return tr && typeof tr === "object" && (
                  tr.__offloaded__ === true || tr.__figures_offloaded__ !== undefined
                );
              });
              if (!locallyOffloaded) return m;
              const srvIdx = localIdx + offset;
              if (srvIdx < 0 || srvIdx >= srvMsgs.length) return m;
              const srvMsg = srvMsgs[srvIdx];
              if (srvMsg.role !== "assistant" || typeof srvMsg.content !== "string") return m;
              if (srvMsg.content.slice(0, 50) !== m.content.slice(0, 50)) return m;
              const validated = validateActions(srvMsg.actions);
              if (validated.length > 0) {
                return { ...m, actions: validated };
              }
              return m;
            });
          }

          return nextMessages;
        });
      })
      .catch(() => { /* keep locally-pruned state if server unreachable */ });
  }, [storageScope, user, showToast]);

  useEffect(() => {
    localStorage.setItem("astro_chat_sidebar_collapsed", sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  // Mirror the current session id to localStorage so boot-time reconciliation
  // (refresh-resume) can ask the server about the right session without
  // waiting for the sidebar list to hydrate.
  useEffect(() => {
    if (currentSessionScopeRef.current !== storageScope) {
      currentSessionScopeRef.current = storageScope;
      return;
    }
    const currentSessionKey = scopedChatStorageKey(CURRENT_CHAT_SESSION_STORAGE_KEY, storageScope);
    if (currentSessionId) {
      localStorage.setItem(currentSessionKey, currentSessionId);
      localStorage.removeItem(CURRENT_CHAT_SESSION_STORAGE_KEY);
    } else {
      localStorage.removeItem(currentSessionKey);
    }
  }, [currentSessionId, storageScope]);

  // Chat persistence: a single debounced scheduler replaces the old three-path
  // write (astro_chat_history on every setMessages + astro_chat_autosave_draft
  // at 3s + immediate server POST on loading-false).  localStorage is
  // quota-guarded; server save is debounced 5s and flushed on tab hide/unload
  // so we never lose the latest state but also don't hammer the API.
  const serverSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingSavePayloadRef = useRef<{ data: Array<{ role: string; content: string; actions?: ChatAction[] }>; sessionId: string | null } | null>(null);

  const flushServerSaveRef = useRef<() => Promise<void>>(async () => {});
  flushServerSaveRef.current = async () => {
    const pending = pendingSavePayloadRef.current;
    if (!pending) return;
    pendingSavePayloadRef.current = null;
    if (serverSaveTimerRef.current) {
      clearTimeout(serverSaveTimerRef.current);
      serverSaveTimerRef.current = null;
    }
    setSaveStatus("saving");
    try {
      const res = await persistSession(pending.data, pending.sessionId) as { id: string; title?: string };
      setCurrentSessionId(res.id);
      if (res.title) setCurrentSessionTitle(res.title);
      setSaveStatus("saved");
      refreshSessions();
    } catch {
      setSaveStatus("unsaved");
    }
  };

  const handleSaveSession = async () => {
    if (messages.length === 0) return;
    await flushServerSaveRef.current();
    showToast(user ? "Chat saved" : "Chat saved locally");
  };

  // Auto-save: queue a debounced server save after each AI response completes
  // (loading transitions true -> false).  Previously this fired an immediate
  // POST on every turn; now a 5s window lets rapid successive turns coalesce
  // into a single request.
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;
    if (wasLoading && !loading && messages.length >= 2) {
      const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
      pendingSavePayloadRef.current = { data, sessionId: currentSessionId };
      setSaveStatus("saving");
      if (serverSaveTimerRef.current) clearTimeout(serverSaveTimerRef.current);
      serverSaveTimerRef.current = setTimeout(() => {
        void flushServerSaveRef.current();
      }, 5000);
    }
  }, [currentSessionId, loading, messages]);

  // Flush the debounced save on tab hide / unload so we never lose state.
  useEffect(() => {
    const flush = () => { void flushServerSaveRef.current(); };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") flush();
    };
    window.addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("beforeunload", flush);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  // Mark as unsaved when messages change without saving
  useEffect(() => {
    if (messages.length > 0 && !loading) {
      setSaveStatus((prev) => (prev === "saved" ? "saved" : "unsaved"));
    }
  }, [messages.length, loading]);

  const handleLoadSession = async (id: string) => {
    try {
      const session = user ? await loadChatSession(id) : loadLocalChatSession(id, storageScope);
      if (!session) return;
      const loaded: DisplayMessage[] = session.messages.map((m: Record<string, unknown>) => ({
        id: crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        content: m.content as string,
        actions: m.actions as ChatAction[] | undefined,
      }));
      setMessages(loaded);
      setCurrentSessionId(id);
      setCurrentSessionTitle((session as { title?: string }).title || "");
      setSaveStatus("saved");
      pythonSessionIdRef.current = crypto.randomUUID();
      saveChatHistory(loaded, storageScope);
    } catch { /* ignore */ }
  };

  const startFreshChat = useCallback(() => {
    // Start a genuinely fresh chat synchronously. The old native confirm
    // dialog could be dismissed by automation/browser timing, leaving users
    // visually on the old session while they thought they had started a new
    // one. Existing chat persistence keeps prior sessions available from the
    // sidebar, so the primary action here must be deterministic.
    setMessages([]);
    messagesRef.current = [];
    setCurrentSessionId(null);
    currentSessionIdRef.current = null;
    setCurrentSessionTitle("");
    setSaveStatus("idle");
    setInput("");
    freshChatRequestRef.current = true;
    pythonSessionIdRef.current = crypto.randomUUID();
    pendingSavePayloadRef.current = null;
    if (serverSaveTimerRef.current) {
      clearTimeout(serverSaveTimerRef.current);
      serverSaveTimerRef.current = null;
    }
    clearFreshChatStorage(storageScope);
    // Stage 3 Bug 2: the 4 ADQL/search keys this used to clear (astro_last_adql,
    // astro_last_adql_rows, astro_adql_result_sets, astro_last_search) are no
    // longer written anywhere — M3 (2026-05-18) deleted the /adql and /search
    // pages that produced them. Removed the dead cleanup lines. The two chat
    // signal keys below are still live and must keep being cleared.
    localStorage.removeItem("astro_chat_new_session");
    localStorage.removeItem("astro_chat_open_session");
    window.history.replaceState(null, "", "/chat");
  }, [storageScope]);

  const handleNewChat = (event?: MouseEvent<HTMLAnchorElement | HTMLButtonElement>) => {
    event?.preventDefault();
    startFreshChat();
  };

  useEffect(() => {
    const handleFreshChatClick = (event: globalThis.MouseEvent) => {
      const target = event.target instanceof Element
        ? event.target.closest("[data-fresh-chat='true']")
        : null;
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      startFreshChat();
    };
    document.addEventListener("click", handleFreshChatClick, true);
    return () => {
      document.removeEventListener("click", handleFreshChatClick, true);
    };
  }, [startFreshChat]);

  const handleRenameSession = async (newTitle: string) => {
    const trimmed = newTitle.trim();
    if (!trimmed || !currentSessionId) {
      setEditingTitle(false);
      return;
    }
    const prevTitle = currentSessionTitle;
    setCurrentSessionTitle(trimmed);
    setEditingTitle(false);
    try {
      if (user) {
        await renameChatSession(currentSessionId, trimmed);
      }
      refreshSessions();
    } catch {
      setCurrentSessionTitle(prevTitle);
      showToast("Failed to rename session", "error");
    }
  };

  const handleDeleteSession = async (id: string) => {
    try {
      if (user) {
        await deleteChatSession(id);
      } else {
        deleteLocalChatSession(id, storageScope);
      }
      refreshSessions();
      if (currentSessionId === id) setCurrentSessionId(null);
    } catch { /* ignore */ }
  };

  const handleImportSession = () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json";
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        const result = await importChatSession(data);
        const session = await loadChatSession(result.id);
        const loaded: DisplayMessage[] = session.messages.map((m: Record<string, unknown>) => ({
          id: crypto.randomUUID(),
          role: m.role as "user" | "assistant",
          content: m.content as string,
          actions: m.actions as ChatAction[] | undefined,
        }));
        setMessages(loaded);
        setCurrentSessionId(result.id);
        pythonSessionIdRef.current = crypto.randomUUID();
        saveChatHistory(loaded, storageScope);
        refreshSessions();
        showToast(`Imported "${result.title}" (${result.message_count} messages)`, "success");
      } catch (err) {
        const detail = err instanceof Error ? err.message : "Import failed";
        showToast(`Import failed: ${detail}`, "error");
      }
    };
    input.click();
  };

  useEffect(() => {
    const pendingSessionId = localStorage.getItem("astro_chat_open_session");
    if (!pendingSessionId || !user) return;
    localStorage.removeItem("astro_chat_open_session");
    loadChatSession(pendingSessionId)
      .then((session) => {
        const loaded: DisplayMessage[] = session.messages.map((message: Record<string, unknown>) => ({
          id: crypto.randomUUID(),
          role: message.role as "user" | "assistant",
          content: message.content as string,
          actions: message.actions as ChatAction[] | undefined,
        }));
        setMessages(loaded);
        setCurrentSessionId(pendingSessionId);
        pythonSessionIdRef.current = crypto.randomUUID();
        saveChatHistory(loaded, storageScope);
      })
      .catch(() => {
        showToast("Could not load the requested session", "error");
      });
  }, [showToast, user, storageScope]);

  const handleOpenCollaboration = useCallback(async () => {
    if (!user) {
      showToast("Sign in to share sessions and manage snapshots", "info");
      return;
    }
    try {
      const sessionId = await ensurePersistedSession();
      setShareLoading(true);
      await loadCollaborationState(sessionId);
      setShareModalOpen(true);
      setShareUrl(null);
      setSnapshotDiff(null);
      setSnapshotCompareSelection([]);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to prepare the session", "error");
    } finally {
      setShareLoading(false);
    }
  }, [ensurePersistedSession, loadCollaborationState, showToast, user]);

  const handleCreateShare = useCallback(async () => {
    try {
      const sessionId = await ensurePersistedSession();
      setShareLoading(true);
      const created = await createSessionShare(
        sessionId,
        shareAccessLevel,
        Number.isFinite(shareExpiryHours) && shareExpiryHours > 0 ? shareExpiryHours : undefined,
      );
      setShareUrl(created.share_url);
      await loadCollaborationState(sessionId);
      if (navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(created.share_url).catch(() => {});
      }
      showToast("Share link created and copied to clipboard", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create share link", "error");
    } finally {
      setShareLoading(false);
    }
  }, [ensurePersistedSession, loadCollaborationState, shareAccessLevel, shareExpiryHours, showToast]);

  const handleRevokeShare = useCallback(async (shareId: string) => {
    if (!currentSessionId) return;
    try {
      await revokeSessionShare(currentSessionId, shareId);
      await loadCollaborationState(currentSessionId);
      showToast("Share link revoked", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to revoke share", "error");
    }
  }, [currentSessionId, loadCollaborationState, showToast]);

  const handleCreateSnapshot = useCallback(async () => {
    try {
      const sessionId = await ensurePersistedSession();
      const label = snapshotName.trim() || `Snapshot ${new Date().toLocaleString()}`;
      await createSessionSnapshot(sessionId, label);
      setSnapshotName("");
      await loadCollaborationState(sessionId);
      showToast("Snapshot created", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create snapshot", "error");
    }
  }, [ensurePersistedSession, loadCollaborationState, showToast, snapshotName]);

  const handleRestoreSnapshot = useCallback(async (snapshotId: string) => {
    if (!currentSessionId) return;
    try {
      await restoreSessionSnapshot(currentSessionId, snapshotId);
      const session = await loadChatSession(currentSessionId);
      const loaded: DisplayMessage[] = session.messages.map((m: Record<string, unknown>) => ({
        id: crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        content: m.content as string,
        actions: m.actions as ChatAction[] | undefined,
      }));
      setMessages(loaded);
      saveChatHistory(loaded, storageScope);
      await loadCollaborationState(currentSessionId);
      showToast("Snapshot restored", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to restore snapshot", "error");
    }
  }, [currentSessionId, loadCollaborationState, showToast, storageScope]);

  const handleCompareSnapshots = useCallback(async () => {
    if (!currentSessionId || snapshotCompareSelection.length !== 2) return;
    try {
      const diff = await diffSessionSnapshots(
        currentSessionId,
        snapshotCompareSelection[0],
        snapshotCompareSelection[1],
      );
      setSnapshotDiff(diff);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to compare snapshots", "error");
    }
  }, [currentSessionId, showToast, snapshotCompareSelection]);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    // 点击"新对话"会 setMessages([]), 空列表不滚动 — 避免新会话启动时
    // 页面莫名跳到底部锚点.
    if (messages.length === 0) return;
    scrollToBottom();
  }, [messages, loading, scrollToBottom]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    saveChatHistory(messages, storageScope);
  }, [messages, storageScope]);

  const [dragOver, setDragOver] = useState(false);
  const pendingSendRef = useRef(false);

  // Auto-send when input is set by FITS drop
  useEffect(() => {
    if (pendingSendRef.current && input.trim()) {
      pendingSendRef.current = false;
      handleSend();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  const handleFitsDrop = async (files: FileList) => {
    for (const file of Array.from(files)) {
      if (!file.name.toLowerCase().match(/\.(fits|fit|fts)$/)) continue;
      try {
        const result = await uploadFITS(file);
        const msg = `I uploaded a FITS file: ${file.name} (stored at ${result.fits_path}). Please analyze this spectrum.`;
        pendingSendRef.current = true;
        setInput(msg);
      } catch {
        setInput(`I want to analyze a FITS file but upload failed for ${file.name}.`);
      }
      break;
    }
  };

  const handleSend = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || loading) return;
    const startFresh = freshChatRequestRef.current;
    const baseMessages = startFresh ? [] : messagesRef.current;
    const sessionIdForRequest = startFresh ? null : currentSessionIdRef.current;
    if (startFresh) {
      currentSessionIdRef.current = null;
      setCurrentSessionId(null);
      freshChatRequestRef.current = false;
    }

    const userMsg: DisplayMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };
    // Pending-marker for the assistant bubble — persisted immediately so that
    // a mid-stream reload knows "a reply was expected here".  Replaced with
    // the real reply on success (or an error bubble on failure) below.
    const pendingMarker: DisplayMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      _pending: { started_at: Date.now() },
      _thinking: [],
    };
    const pendingId = pendingMarker.id;

    // Live thinking timeline — backend emits agent_text / tool_call /
    // tool_result events during the agent loop; we append each to the
    // pending bubble so the user sees what the AI is doing in real time.
    const onThinking = (evt: ThinkingEvent) => {
      if (evt.type === "status") return; // heartbeat, ignore in UI
      if (evt.type === "honest_abstention") {
        // F3.2: stash the abstention payload on the pending message so
        // the final render path can show HonestAbstentionCard.
        setMessages((prev) =>
          prev.map((m) =>
            m.id === pendingId
              ? { ...m, _abstention: evt.payload }
              : m,
          ),
        );
        return;
      }
      const step: ThinkingStep = {
        kind: evt.type,
        agent: "agent" in evt ? evt.agent : undefined,
        tool: evt.type === "tool_call" || evt.type === "tool_progress" || evt.type === "tool_result" ? evt.tool : undefined,
        text:
          evt.type === "agent_text" || evt.type === "tool_progress"
            ? (evt.type === "agent_text" ? evt.content : evt.message)
            : evt.type === "workflow_budget"
              ? `${evt.mode} budget: ${evt.agent_loop_seconds}s loop, ${evt.max_iterations} iterations`
              : evt.type === "workflow_checkpoint"
                ? (evt.checkpoint_summary || (evt.tool_name ? `${evt.tool_name} ${evt.status || ""}` : "checkpoint available"))
                : undefined,
        input: evt.type === "tool_call" ? evt.input : undefined,
        stage: evt.type === "tool_progress" ? evt.stage : undefined,
        result: evt.type === "tool_result" ? evt.result : undefined,
        mode: evt.type === "workflow_budget" ? evt.mode : undefined,
        cacheRefs: evt.type === "workflow_checkpoint" ? evt.cache_refs : undefined,
        disabled: evt.type === "tools_disabled" ? evt.disabled : undefined,
        iteration: evt.type === "tools_disabled" || evt.type === "tool_call" ? evt.iteration : undefined,
        maxIterations: evt.type === "tool_call" ? evt.max_iterations : undefined,
      };
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? { ...m, _thinking: [...(m._thinking || []), step] }
            : m,
        ),
      );
    };

    let streamedActions: ChatAction[] = [];
    const onActions = (actions: ChatAction[]) => {
      streamedActions = actions;
      setMessages((prev) => {
        const next = prev.map((m) =>
          m.id === pendingId
            ? { ...m, actions: actions.length > 0 ? actions : undefined }
            : m,
        );
        saveChatHistory(next, storageScope);
        return next;
      });
    };

    const updatedMessages = [...baseMessages, userMsg, pendingMarker];
    messagesRef.current = updatedMessages;
    setMessages(updatedMessages);
    // H0.5: clear input whenever the text we're sending matches the
    // current input state (i.e. came from the user, not an external
    // inject).  The old `!overrideText` check broke when the Send
    // button started passing overrideText=input explicitly.
    if (!overrideText || overrideText === input) setInput("");
    setLoading(true);

    try {
      track("ai.message_sent", {
        prompt_length_chars: text.length,
        prompt_length_words: text.split(/\s+/).filter(Boolean).length,
        topic_keywords: text.split(/[\s,.;:!?，。；：！？]+/).filter(Boolean).slice(0, 8),
      });
      const chatHistory = buildMinimalChatHistory(updatedMessages);

      logOperation("chat", `Search: ${text}`);

      // Build context from user's current workspace state.
      // Stage 3 Bug 2 修复: 5 个 localStorage key 在 M3 (2026-05-18) 删除
      // /search /adql /pipeline /workspace 4 个 page 后已经没人写, 留着读
      // 出来的只可能是上个用户 (同一台电脑) 残留的 stale 数据, 是隐私泄漏源.
      // 全部删除. 仍然活的 astro_workspace_files 走 readWorkspaceCache(scope)
      // 显式带用户 scope, 不再裸 getItem.
      const wsContext: Record<string, unknown> = {};
      try {
        const workspaceFiles = readWorkspaceCache(storageScope);
        if (workspaceFiles.length > 0) wsContext.workspace_files = workspaceFiles;
      } catch { /* ignore */ }
      wsContext.python_session_id = pythonSessionIdRef.current;
      wsContext.current_session_id = sessionIdForRequest;

      // R0d: create a fresh AbortController per request so the Stop button
      // can cancel this stream specifically.
      const abort = new AbortController();
      chatAbortRef.current = abort;
      const response = await sendChatMessage(chatHistory, wsContext, onThinking, abort.signal, onActions);

      // F3.2: carry forward any _abstention that arrived on a thinking event
      // before the final reply.  Without this the card would flash and
      // disappear when the pending marker is replaced.
      setMessages((prev) => prev.map((m) => {
        if (m.id !== pendingId) return m;
        return {
          id: pendingId,
          role: "assistant" as const,
          content: response.reply,
          actions: response.actions.length > 0 ? response.actions : undefined,
          actionResults: new Map(),
          _abstention: m._abstention,
        };
      }));
    } catch (err: unknown) {
      // R0d: user-initiated abort is not an error — rewrite the pending
      // bubble to a cancelled state and exit the catch early.
      if (err instanceof DOMException && err.name === "AbortError") {
        const cancelledMsg: DisplayMessage = {
          id: pendingId,
          role: "assistant",
          content: "⏹ Reply cancelled by user.",
        };
        setMessages((prev) => prev.map((m) => (m.id === pendingId ? cancelledMsg : m)));
        return;
      }
      let errorDetail = "Unknown error";
      let errorClass: string | undefined;
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response?: { data?: { detail?: string }; status?: number } }).response;
        errorDetail = resp?.data?.detail || `Request failed (${resp?.status})`;
        // If auth error, prompt to fix key
        if (resp?.status === 401) {
          setHasKey(false);
        }
      } else if (err instanceof Error) {
        errorDetail = err.message;
        // R11-NEW-1: error_class 从 stream 里的 'error' 事件传上来 (client.ts 挂到 Error instance).
        errorClass = (err as Error & { error_class?: string }).error_class;
      }
      if (errorDetail.includes("Could not reach the backend server")) {
        errorDetail = "The request payload was likely rejected before the app server handled it. This usually happens when the previous tool results made the second-round chat request too large.";
        errorClass = errorClass || "payload_too_large";
      }
      // R11-NEW-1: payload 太大时, 在错误气泡里加一条"开始新聊天"按钮
      // (通过 markdown link 用一个保留的 sentinel, 再在 MarkdownText 里识别
      // 或在 content 里直接写提示文字).  轻量做法: 错误消息尾部带一段
      // 引导 + 按钮通过 DisplayMessage._action_hint 实现, 让 UI 渲染按钮.
      const hint = errorClass === "payload_too_large"
        ? "\n\n👉 点下方按钮开始新聊天 (会清空当前会话的历史):"
        : "";
      const errorMsg: DisplayMessage = {
        id: pendingId,
        role: "assistant",
        content: `Sorry, I encountered an error: ${errorDetail}${hint}`,
        actions: streamedActions.length > 0 ? streamedActions : undefined,
        _action_hint: errorClass === "payload_too_large" ? "new_chat" : undefined,
      };
      setMessages((prev) => prev.map((m) => (m.id === pendingId ? errorMsg : m)));
      track("error.ai_failed", {
        agent_name: "chat_assistant",
        backend: "anthropic",
        error_type: "chat_failed",
      });
    } finally {
      chatAbortRef.current = null;
      setLoading(false);
    }
  };

  const handleStop = () => {
    // R0d: user-triggered cancel of the in-flight chat stream.
    chatAbortRef.current?.abort();
  };

  const handleExecuteAction = async (
    msgId: string,
    actionIndex: number,
    action: ChatAction
  ) => {
    const key = `${msgId}-${actionIndex}`;
    const started = performance.now();
    setExecutingActions((prev) => new Set(prev).add(key));

    try {
      logOperation("action", `Execute: ${action.action}`);
      const result = await executeChatAction(
        action as Record<string, unknown>
      );
      track("ai.tool_called", {
        tool_name: action.action,
        params_summary: JSON.stringify(action).slice(0, 300),
        success: true,
        duration_ms: Math.round(performance.now() - started),
        error_msg: null,
      });
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === msgId) {
            const newResults = new Map(m.actionResults);
            newResults.set(actionIndex, result);
            return { ...m, actionResults: newResults };
          }
          return m;
        })
      );
    } catch (err: unknown) {
      const errorDetail =
        err instanceof Error ? err.message : "Unknown error";
      track("ai.tool_called", {
        tool_name: action.action,
        params_summary: JSON.stringify(action).slice(0, 300),
        success: false,
        duration_ms: Math.round(performance.now() - started),
        error_msg: errorDetail,
      });
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === msgId) {
            const newResults = new Map(m.actionResults);
            newResults.set(actionIndex, {
              type: "error",
              message: errorDetail,
            });
            return { ...m, actionResults: newResults };
          }
          return m;
        })
      );
    } finally {
      setExecutingActions((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Filter sessions by search query
  const filteredSessions = sessions.filter((s) =>
    !sessionSearch.trim() || s.title.toLowerCase().includes(sessionSearch.toLowerCase())
  );

  return (
    <div className={`chat-page chat-page-with-sidebar${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      {messages.length === 0 && currentSessionId === null && !loading && (
        <span data-testid="fresh-chat-ready" hidden />
      )}
      {toast && (
        <div className="chat-toast" style={{
          background: toast.tone === "error" ? "#ef4444" : toast.tone === "info" ? "#0ea5e9" : "#22c55e",
        }}>{toast.message}</div>
      )}

      {/* Persistent session sidebar (like Claude desktop) */}
      <aside className="chat-sidebar" aria-label="Chat sessions">
        <div className="chat-sidebar-header">
          <a
            href="/chat?fresh_chat=1"
            role="button"
            className="chat-sidebar-new"
            data-fresh-chat="true"
            title="New chat"
            onClick={handleNewChat}
          >
            <span style={{ fontSize: "1.1rem" }}>+</span> New chat
          </a>
          <button
            type="button"
            className="chat-sidebar-toggle"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? "»" : "«"}
          </button>
        </div>
        {!sidebarCollapsed && (
          <>
            <div className="chat-sidebar-search">
              <input
                type="search"
                placeholder="Search chats..."
                value={sessionSearch}
                onChange={(e) => setSessionSearch(e.target.value)}
                className="chat-sidebar-search-input"
              />
            </div>
            <div className="chat-sidebar-list" role="list">
              {sessions.length === 0 && (
                <p className="chat-sidebar-empty">
                  {user ? "No saved chats yet." : "Sign in to sync chats across devices."}
                </p>
              )}
              {filteredSessions.map((s) => (
                <div
                  key={s.id}
                  className={`chat-sidebar-item${s.id === currentSessionId ? " active" : ""}`}
                  role="listitem"
                >
                  <button
                    className="chat-sidebar-item-load"
                    onClick={() => handleLoadSession(s.id)}
                    title={s.title}
                  >
                    <span className="chat-sidebar-item-title">{s.title || "New Chat"}</span>
                    <span className="chat-sidebar-item-meta">
                      {s.message_count} msg · {new Date(s.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                  <button
                    className="chat-sidebar-item-delete"
                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                    title="Delete"
                    aria-label={`Delete ${s.title}`}
                  >
                    ×
                  </button>
                </div>
              ))}
              {filteredSessions.length === 0 && sessions.length > 0 && (
                <p className="chat-sidebar-empty">No chats match your search.</p>
              )}
            </div>
          </>
        )}
      </aside>

      <div className="chat-main">
      <div className="chat-header">
        <div className="chat-header-row">
          <div className="chat-header-title-block">
            {currentSessionId && !editingTitle ? (
              <h2
                className="chat-header-title-editable"
                onClick={() => { setTitleDraft(currentSessionTitle || "New Chat"); setEditingTitle(true); }}
                title="Click to rename"
              >
                {currentSessionTitle || "New Chat"}
                <span className="chat-header-rename-hint">✎</span>
              </h2>
            ) : editingTitle ? (
              <input
                autoFocus
                className="chat-header-title-input"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={() => handleRenameSession(titleDraft)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRenameSession(titleDraft);
                  else if (e.key === "Escape") setEditingTitle(false);
                }}
                maxLength={200}
              />
            ) : (
              <h2>{t("nav.ai_assistant")}</h2>
            )}
            <p className="chat-header-subtitle">
              {currentSessionId && saveStatus !== "idle" && (
                <span className={`chat-save-indicator chat-save-${saveStatus}`}>
                  {saveStatus === "saving" && "● Saving..."}
                  {saveStatus === "saved" && "● Saved"}
                  {saveStatus === "unsaved" && "● Unsaved changes"}
                </span>
              )}
              {!currentSessionId && "Ask about astronomical objects, build pipelines, or run ADQL queries"}
            </p>
          </div>
          <div style={{ display: "flex", gap: "0.4rem" }}>
            {messages.length > 0 && (
              <button type="button" className="btn-secondary btn-small" onClick={handleSaveSession}>
                {t("chat.save")}
              </button>
            )}
            {messages.length > 0 && (
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={shareLoading}
                onClick={() => { void handleOpenCollaboration(); }}
              >
                {shareLoading ? "Opening..." : "Share / Snapshots"}
              </button>
            )}
            {user && messages.length > 0 && (
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={paperLoading || paperGenerating}
                onClick={() => { void handleOpenPaperDraft(); }}
              >
                {paperLoading ? "Checking..." : paperGenerating ? "Generating..." : "Generate Paper Draft"}
              </button>
            )}
            <a
              href="/chat?fresh_chat=1"
              role="button"
              className="btn-secondary btn-small"
              data-fresh-chat="true"
              onClick={handleNewChat}
            >
              {t("chat.new_chat")}
            </a>
            <button type="button" className="btn-secondary btn-small" onClick={handleImportSession} title={t("chat.import_title")}>
              {t("chat.import")}
            </button>
            {messages.length > 0 && (
              <>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.markdown}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "markdown",
                    "Markdown",
                    "ai_research_chat.md",
                    () => exportChatMarkdown(data, undefined, currentSessionId),
                  );
                }}
              >
                {exporting.markdown ? "Exporting..." : t("common.export")}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.html}
                title="Self-contained HTML — opens in any browser, figures embedded, Ctrl+P to print as PDF"
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "html",
                    "HTML",
                    "ai_research_chat.html",
                    () => exportChatHtml(data, undefined, currentSessionId),
                  );
                }}
              >
                {exporting.html ? "Exporting..." : "HTML"}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.notebook}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "notebook",
                    "Notebook",
                    "ai_research_session.ipynb",
                    () => exportChatNotebook(data, undefined, currentSessionId),
                  );
                }}
              >
                {exporting.notebook ? "Exporting..." : "Notebook"}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.latex}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "latex",
                    "LaTeX",
                    "astro_report.tex",
                    () => exportChatLatex(data),
                    {
                      fallback: () => new Blob([generateLatexFallback(messages)], { type: "application/x-tex" }),
                    },
                  );
                }}
              >
                {exporting.latex ? "Exporting..." : "LaTeX"}
              </button>
              <button
                type="button"
                className="btn-secondary btn-small"
                disabled={exporting.bibtex}
                onClick={() => {
                  const data = messages.map(m => ({ role: m.role, content: m.content, actions: m.actions }));
                  void handleExport(
                    "bibtex",
                    "BibTeX",
                    "references.bib",
                    () => exportChatBibTeX(data),
                    { emptyMessage: "No references found to export", skipDownloadWhenEmpty: true },
                  );
                }}
              >
                {exporting.bibtex ? "Exporting..." : "BibTeX"}
              </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Old modal session panel removed — replaced by persistent sidebar */}

      {shareModalOpen && (
        <div className="viz-overlay" onClick={() => setShareModalOpen(false)}>
          <div
            className="viz-overlay-content"
            style={{ maxWidth: 920, width: "min(920px, 92vw)", maxHeight: "88vh", overflow: "auto" }}
            onClick={(event) => event.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0 }}>Share And Snapshots</h3>
                <div style={{ color: "var(--color-text-secondary)", marginTop: 4 }}>
                  Manage share links, forks, and point-in-time session restores.
                </div>
              </div>
              <button className="btn-secondary btn-small" onClick={() => setShareModalOpen(false)}>Close</button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}>
              <div style={{ padding: 14, borderRadius: 12, background: "rgba(15,23,42,0.04)" }}>
                <h4 style={{ marginTop: 0 }}>Create Share Link</h4>
                <div style={{ display: "grid", gap: 10 }}>
                  <label>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Access Level</div>
                    <select
                      className="search-input"
                      value={shareAccessLevel}
                      onChange={(event) => setShareAccessLevel(event.target.value as ShareAccessLevel)}
                    >
                      <option value="view">View</option>
                      <option value="fork">Fork</option>
                      <option value="comment">Comment</option>
                    </select>
                  </label>
                  <label>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Expiry (hours)</div>
                    <input
                      className="search-input"
                      type="number"
                      min={1}
                      value={shareExpiryHours}
                      onChange={(event) => setShareExpiryHours(Number(event.target.value) || 0)}
                    />
                  </label>
                  <button className="btn-primary" disabled={shareLoading} onClick={() => { void handleCreateShare(); }}>
                    {shareLoading ? "Creating..." : "Create Share Link"}
                  </button>
                  {shareUrl && (
                    <div className="fits-hint" style={{ wordBreak: "break-all" }}>
                      Latest link: {shareUrl}
                    </div>
                  )}
                </div>

                <div style={{ marginTop: 18 }}>
                  <h4>Active Shares</h4>
                  {sessionShares.length === 0 ? (
                    <div className="fits-hint">No active share links yet.</div>
                  ) : (
                    sessionShares.map((share) => (
                      <div key={share.id} className="note-card" style={{ marginBottom: 10 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                          <div>
                            <strong>{share.access_level}</strong>
                            <div style={{ color: "var(--color-text-secondary)", fontSize: "0.85rem", marginTop: 4 }}>
                              {share.expires_at ? `Expires ${new Date(share.expires_at).toLocaleString()}` : "No expiry"}
                            </div>
                            <div className="mono" style={{ marginTop: 6 }}>.../{share.share_token}</div>
                          </div>
                          <button className="btn-secondary btn-small" onClick={() => { void handleRevokeShare(share.id); }}>
                            Revoke
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div style={{ padding: 14, borderRadius: 12, background: "rgba(15,23,42,0.04)" }}>
                <h4 style={{ marginTop: 0 }}>Version Snapshots</h4>
                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  <input
                    className="search-input"
                    value={snapshotName}
                    onChange={(event) => setSnapshotName(event.target.value)}
                    placeholder='e.g. "before extinction correction"'
                  />
                  <button className="btn-secondary" onClick={() => { void handleCreateSnapshot(); }}>
                    Snapshot
                  </button>
                </div>
                {sessionSnapshots.length === 0 ? (
                  <div className="fits-hint">No snapshots yet.</div>
                ) : (
                  <div style={{ display: "grid", gap: 10 }}>
                    {sessionSnapshots.map((snapshot) => {
                      const selected = snapshotCompareSelection.includes(snapshot.id);
                      return (
                        <div key={snapshot.id} className="note-card">
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                            <div>
                              <strong>{snapshot.name}</strong>
                              <div className="note-date" style={{ marginTop: 4 }}>
                                {snapshot.created_at ? new Date(snapshot.created_at).toLocaleString() : "Unknown time"}
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 6 }}>
                              <button
                                className={`btn-secondary btn-small${selected ? " active" : ""}`}
                                onClick={() => {
                                  setSnapshotCompareSelection((prev) => {
                                    if (prev.includes(snapshot.id)) return prev.filter((id) => id !== snapshot.id);
                                    if (prev.length === 2) return [prev[1], snapshot.id];
                                    return [...prev, snapshot.id];
                                  });
                                }}
                              >
                                Compare
                              </button>
                              <button className="btn-secondary btn-small" onClick={() => { void handleRestoreSnapshot(snapshot.id); }}>
                                Restore
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <button
                    className="btn-secondary btn-small"
                    disabled={snapshotCompareSelection.length !== 2}
                    onClick={() => { void handleCompareSnapshots(); }}
                  >
                    Compare Selected
                  </button>
                </div>
                {snapshotDiff && (
                  <div className="fits-hint" style={{ marginTop: 12 }}>
                    Title changed: {snapshotDiff.updated_title ? "yes" : "no"} · Added messages: {snapshotDiff.added_messages} · Removed messages: {snapshotDiff.removed_messages}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="chat-messages">
        {pageError && <div className="error-banner">{pageError}</div>}
        {aiBackendReady && (
          <div
            style={{
              background: "rgba(78, 201, 176, 0.07)",
              border: "1px solid rgba(78, 201, 176, 0.24)",
              borderLeft: "3px solid #4ec9b0",
              padding: "0.45rem 0.75rem",
              borderRadius: 6,
              margin: "0.4rem 0",
              fontSize: "0.8rem",
            }}
          >
            Using {modelDisplayLabel(selectedModelStatus)}. Model selection is manual; fallback only runs after backend failure.
          </div>
        )}
        {/* F4.1: top-of-chat banner when NEITHER a browser-stored key
            NOR a server-side backend is configured.  Using the same
            ApiKeyPrompt body below handles the key entry — this banner
            just surfaces the state loudly + links to the Settings page. */}
        {!aiBackendReady && serverBackendReady === false && (
          <div
            style={{
              background: "rgba(255, 69, 58, 0.08)",
              border: "1px solid rgba(255, 69, 58, 0.3)",
              borderLeft: "3px solid var(--color-red)",
              padding: "0.7rem 0.9rem",
              borderRadius: 6,
              margin: "0.5rem 0",
              fontSize: "0.9rem",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 12,
              flexWrap: "wrap",
            }}
          >
            <div>
              <strong>AI backend not configured.</strong> Add an API key
              below (stays in this browser) or configure one server-side
              before sending messages.
            </div>
            <a
              href="/account"
              style={{
                padding: "0.35rem 0.8rem",
                background: "var(--color-red)",
                color: "white",
                borderRadius: 4,
                textDecoration: "none",
                fontSize: "0.82rem",
                whiteSpace: "nowrap",
              }}
            >
              Open Settings →
            </a>
          </div>
        )}
        {/* When the server has a backend but the browser does not, Send
            still works (falls back to server env).  Show a subtle note. */}
        {!hasKey && serverBackendReady === true && serverBackendList.length > 0 && (
          <div
            style={{
              background: "rgba(42, 93, 123, 0.07)",
              border: "1px solid rgba(42, 93, 123, 0.25)",
              borderLeft: "3px solid #2a5d7b",
              padding: "0.5rem 0.8rem",
              borderRadius: 6,
              margin: "0.4rem 0",
              fontSize: "0.82rem",
            }}
          >
            Using server-side AI backend ({serverBackendList.join(", ")}).
            You can add your own key below for better rate limits.
          </div>
        )}
        {!aiBackendReady && (
          <ApiKeyPrompt onSaved={() => setHasKey(true)} />
        )}
        {aiBackendReady && messages.length === 0 && !loading && (
          <div className="chat-empty">
            <div className="chat-empty-icon">&#x2728;</div>
            <h3>How can I help with your research?</h3>
            <div className="chat-templates-grid">
              {[
                { icon: "\u{1F31F}", title: t("template.hr_diagram"), prompt: t("template.hr_desc"), difficulty: "beginner" as const },
                { icon: "\u{1F30C}", title: t("template.galaxy_redshift"), prompt: t("template.galaxy_desc"), difficulty: "beginner" as const },
                { icon: "\u2B50", title: t("template.variable_star"), prompt: t("template.variable_desc"), difficulty: "intermediate" as const },
                { icon: "\u{1F52D}", title: t("template.spectral"), prompt: t("template.spectral_desc"), difficulty: "intermediate" as const },
                { icon: "\u{1F4CA}", title: t("template.highz"), prompt: t("template.highz_desc"), difficulty: "advanced" as const },
                { icon: "\u{1F4AB}", title: t("template.supernova"), prompt: t("template.supernova_desc"), difficulty: "advanced" as const },
              ].map((tmpl, i) => (
                <button
                  key={i}
                  className="chat-template-card"
                  onClick={() => setInput(tmpl.prompt)}
                >
                  <div className="chat-template-header">
                    <span className="chat-template-icon">{tmpl.icon}</span>
                    <span className={`chat-template-badge badge-${tmpl.difficulty}`}>
                      {t(`template.difficulty.${tmpl.difficulty}`)}
                    </span>
                  </div>
                  <div className="chat-template-title">{tmpl.title}</div>
                  <div className="chat-template-desc">{tmpl.prompt}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`chat-message ${msg.role}`}>
            <div className="chat-message-avatar">
              {msg.role === "user" ? "U" : "AI"}
            </div>
            <div className="chat-message-body">
              <div className="chat-message-content">
                {msg._pending ? (
                  <div>
                    <em style={{ opacity: 0.7 }}>
                      {/* E2.4: three-state banner.  (a) still receiving
                          events ⇒ "Thinking"; (b) old marker with no
                          events ever seen ⇒ "interrupted" (true error);
                          (c) fresh marker with no events yet ⇒
                          "Reconnecting" (likely just reloaded during
                          stream).  Previously a false "interrupted"
                          flashed whenever the page remounted during a
                          healthy stream. */}
                      {msg._thinking && msg._thinking.length > 0
                        ? "Thinking…"
                        : Date.now() - msg._pending.started_at > 60_000
                          ? "The previous reply was interrupted before the backend responded."
                          : "Reconnecting to your in-flight reply…"}
                    </em>
                    <ToolTurnSummary actions={msg.actions} live />
                    {msg._thinking && msg._thinking.length > 0 && (
                      <ul className="chat-thinking-timeline" style={{
                        listStyle: "none",
                        padding: 0,
                        margin: "8px 0 0 0",
                        fontSize: "0.85em",
                        opacity: 0.85,
                      }}>
                        {msg._thinking.map((step, i) => (
                          <li key={i} style={{ padding: "3px 0", borderLeft: "2px solid #ccc", paddingLeft: 8, marginBottom: 2 }}>
                            {step.kind === "agent_text" && (
                              <span>💭 {step.text}</span>
                            )}
                            {step.kind === "tool_call" && (
                              <span>
                                🔧 <strong>{step.tool}</strong>
                                {step.iteration && step.maxIterations ? (
                                  <code style={{ marginLeft: 6, fontSize: "0.85em", color: "#666" }}>
                                    {step.iteration}/{step.maxIterations}
                                  </code>
                                ) : null}
                                {step.input && typeof step.input === "object" ? (
                                  <code style={{ marginLeft: 6, fontSize: "0.9em", color: "#666" }}>
                                    {JSON.stringify(step.input).slice(0, 140)}
                                  </code>
                                ) : null}
                              </span>
                            )}
                            {step.kind === "tool_progress" && (
                              <span>
                                ↻ <strong>{step.tool}</strong>
                                <span style={{ marginLeft: 6, color: "#4b5563" }}>
                                  {step.text}
                                </span>
                                {step.stage ? (
                                  <code style={{ marginLeft: 6, fontSize: "0.85em", color: "#666" }}>
                                    {step.stage}
                                  </code>
                                ) : null}
                              </span>
                            )}
                            {step.kind === "tool_result" && (
                              <span>
                                ✓ <strong>{step.tool}</strong>
                                {(() => {
                                  const r = step.result as { error?: string } | null;
                                  return r && typeof r === "object" && "error" in r && r.error
                                    ? <em style={{ color: "#b00020", marginLeft: 6 }}>{String(r.error).slice(0, 120)}</em>
                                    : <span style={{ marginLeft: 6, color: "#2e7d32" }}>done</span>;
                                })()}
                              </span>
                            )}
                            {step.kind === "workflow_budget" && (
                              <span>
                                <strong>Workflow budget</strong>
                                <span style={{ marginLeft: 6, color: "#4b5563" }}>{step.text}</span>
                              </span>
                            )}
                            {step.kind === "workflow_checkpoint" && (
                              <span>
                                <strong>Checkpoint</strong>
                                <span style={{ marginLeft: 6, color: "#4b5563" }}>{step.text}</span>
                                {step.cacheRefs && step.cacheRefs.length > 0 ? (
                                  <code style={{ marginLeft: 6, fontSize: "0.85em", color: "#666" }}>
                                    {step.cacheRefs.join(", ")}
                                  </code>
                                ) : null}
                              </span>
                            )}
                            {step.kind === "tools_disabled" && step.disabled && step.disabled.length > 0 && (
                              <span style={{ color: "#b8860b" }}>
                                🚫 <strong>Tools disabled this turn:</strong>{" "}
                                <code>{step.disabled.join(", ")}</code>
                                <em style={{ marginLeft: 6, fontSize: "0.85em", color: "#8a6a00" }}>
                                  — failed ≥2× this turn, removed from toolkit
                                </em>
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ) : msg.role === "assistant" ? (
                  msg._abstention ? (
                    <HonestAbstentionCard
                      abstention={msg._abstention}
                      onRetry={(suggestion) => { setInput(suggestion); }}
                    />
                  ) : (
                    <>
                      {/* F3.3: ⚠ preamble when any auto-executed tool failed
                          or returned empty, even if the prose passed the
                          claim gate.  Keeps the validation signal visible. */}
                      <ToolTurnSummary actions={msg.actions} />
                      <MarkdownText content={msg.content} />
                      {/* R11-NEW-1: payload_too_large 错误专属 CTA —
                          一键新开聊天清空当前会话历史. */}
                      {msg._action_hint === "new_chat" && (
                        <button
                          className="btn-primary btn-small"
                          style={{ marginTop: 10 }}
                          onClick={handleNewChat}
                        >
                          🔄 开始新聊天 (Start fresh chat)
                        </button>
                      )}
                    </>
                  )
                ) : (
                  msg.content.split("\n").map((line, i) => (
                    <p key={i}>{line || "\u00A0"}</p>
                  ))
                )}
                {msg._pending && Date.now() - msg._pending.started_at > 60_000 && (
                  <button
                    className="btn-secondary btn-small"
                    style={{ marginTop: 8 }}
                    onClick={() => {
                      // H0.4: the old onClick did
                      //   setMessages(prev.filter((m) => m.id !== msg.id))
                      // BEFORE handleSend, which removed the pending
                      // message from the list — and with it all the
                      // prior successful tool_results / figures via the
                      // localStorage flush.  Paper 1 reviewer lost an
                      // entire Pleiades analysis to this.
                      // Fix: DON'T remove the pending message.  Let
                      // handleSend append a NEW pending marker + new
                      // reply; the old stuck pending bubble stays as
                      // context ("previous attempt got stuck") and the
                      // figures above it are untouched.
                      const priorUser = messages
                        .slice(0, messages.indexOf(msg))
                        .reverse()
                        .find((m) => m.role === "user");
                      if (!priorUser) return;
                      // Clear _pending on the stuck message so it stops
                      // showing the spinner and the Retry button — but
                      // keep the bubble in the list.
                      setMessages((prev) => prev.map((m) =>
                        m.id === msg.id
                          ? { ...m, _pending: undefined, content: m.content || "⚠ Previous attempt timed out; retrying below." }
                          : m,
                      ));
                      void handleSend(priorUser.content);
                    }}
                  >
                    Retry
                  </button>
                )}
              </div>
              {msg.actions && msg.actions.length > 0 && (
                <div className="chat-actions-list">
                  <span className="chat-actions-label">
                    Suggested actions:
                  </span>
                  {msg.actions.map((action, idx) => (
                    <ActionCard
                      key={idx}
                      action={action}
                      index={idx}
                      result={msg.actionResults?.get(idx)}
                      executing={executingActions.has(
                        `${msg.id}-${idx}`
                      )}
                      conversationProvenance={conversationProvenance}
                      onCopyAcknowledgement={() => showToast("Copied", "success")}
                      onExecute={(i, a) =>
                        handleExecuteAction(msg.id, i, a)
                      }
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="chat-message assistant">
            <div className="chat-message-avatar">AI</div>
            <div className="chat-message-body">
              <div className="chat-loading">
                <span className="chat-loading-dot" />
                <span className="chat-loading-dot" />
                <span className="chat-loading-dot" />
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {paperModalOpen && (
        <div className="viz-overlay" onClick={() => setPaperModalOpen(false)}>
          <div
            className="viz-overlay-content"
            style={{ maxWidth: 980, width: "min(980px, 92vw)", maxHeight: "88vh", overflow: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0 }}>Paper Draft</h3>
                <div style={{ color: "var(--color-text-secondary)", fontSize: "0.9rem", marginTop: 4 }}>
                  Validate the session, generate a draft, edit sections, and download LaTeX/BibTeX.
                </div>
              </div>
              <button className="btn-secondary btn-small" onClick={() => setPaperModalOpen(false)}>Close</button>
            </div>

            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontWeight: 600 }}>Journal</span>
                <select
                  value={paperFormat}
                  onChange={(e) => setPaperFormat(e.target.value as JournalFormat)}
                  className="search-input"
                  style={{ width: 160 }}
                >
                  <option value="aastex">AASTeX</option>
                  <option value="mnras">MNRAS</option>
                  <option value="aa">A&amp;A</option>
                </select>
              </label>
              <button
                className="btn-primary btn-small"
                disabled={paperLoading || paperGenerating || !paperSessionId}
                onClick={() => { void handleGeneratePaper(paperValidation?.overall_status === "FAIL"); }}
              >
                {paperGenerating ? "Generating..." : paperValidation?.overall_status === "FAIL" ? "Generate Anyway" : "Generate Draft"}
              </button>
              {paperDraft && (
                <>
                  <button
                    className="btn-secondary btn-small"
                    disabled={paperSaving || !paperEditorJson}
                    onClick={() => { void handleSavePaperDraft(); }}
                  >
                    {paperSaving ? "Saving..." : "Save Changes"}
                  </button>
                  <button
                    className={paperDraft.is_public ? "btn-secondary btn-small" : "btn-primary btn-small"}
                    disabled={paperSaving}
                    onClick={() => { void handleTogglePaperPublish(); }}
                    title={paperDraft.is_public ? "Remove the public draft link" : "Create a public read-only draft link"}
                  >
                    {paperDraft.is_public ? "Unpublish Draft" : "Publish Draft"}
                  </button>
                  <button
                    className="btn-secondary btn-small"
                    onClick={() => {
                      downloadBlob(
                        new Blob([paperDraft.latex_source], { type: "application/x-tex" }),
                        `${(paperDraft.paper_json.title as string || "standard_astro_draft").replace(/\s+/g, "_")}.tex`,
                      );
                    }}
                  >
                    Download LaTeX
                  </button>
                  <button
                    className="btn-secondary btn-small"
                    onClick={() => {
                      downloadBlob(
                        new Blob([paperDraft.bibtex], { type: "application/x-bibtex" }),
                        `${(paperDraft.paper_json.title as string || "standard_astro_references").replace(/\s+/g, "_")}.bib`,
                      );
                    }}
                  >
                    Download BibTeX
                  </button>
                </>
              )}
            </div>

            {paperDraft?.is_public && paperDraft.public_url && (
              <div style={{
                marginBottom: 16,
                padding: "0.6rem 0.8rem",
                borderRadius: 6,
                border: "1px solid rgba(46,106,78,0.28)",
                background: "rgba(46,106,78,0.08)",
                fontSize: "0.85rem",
              }}>
                Published read-only draft:{" "}
                <a href={paperDraft.public_url} target="_blank" rel="noopener noreferrer">
                  {new URL(paperDraft.public_url, window.location.origin).toString()}
                </a>
              </div>
            )}

            {paperLoading && (
              <div className="fits-loading" style={{ marginBottom: 16 }}>Inspecting session and running validation...</div>
            )}

            {paperValidation && (
              <div style={{ marginBottom: 18, padding: 14, borderRadius: 10, background: "rgba(15,23,42,0.05)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 10 }}>
                  <strong>
                    Validation: {paperValidation.overall_status} ({Math.round(paperValidation.score * 100)}%)
                  </strong>
                  <span style={{
                    padding: "4px 8px",
                    borderRadius: 999,
                    background:
                      paperValidation.overall_status === "FAIL" ? "#fee2e2" :
                      paperValidation.overall_status === "WARN" ? "#fef3c7" : "#dcfce7",
                    color:
                      paperValidation.overall_status === "FAIL" ? "#b91c1c" :
                      paperValidation.overall_status === "WARN" ? "#a16207" : "#166534",
                    fontWeight: 700,
                    fontSize: "0.75rem",
                  }}>
                    {paperValidation.overall_status}
                  </span>
                </div>
                <div style={{ display: "grid", gap: 10 }}>
                  {paperValidation.checks.map((check) => (
                    <div key={check.name} style={{ border: "1px solid rgba(15,23,42,0.08)", borderRadius: 8, padding: 10, background: "#fff" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                        <strong>{check.name.replace(/_/g, " ")}</strong>
                        <span style={{
                          fontSize: "0.72rem",
                          fontWeight: 700,
                          color: check.status === "FAIL" ? "#b91c1c" : check.status === "WARN" ? "#a16207" : "#166534",
                        }}>
                          {check.status}
                        </span>
                      </div>
                      <div style={{ fontSize: "0.88rem", marginTop: 6 }}>{check.details}</div>
                      <div style={{ fontSize: "0.82rem", color: "var(--color-text-secondary)", marginTop: 4 }}>
                        Recommendation: {check.recommendation}
                      </div>
                      <div style={{ marginTop: 8 }}>
                        <button
                          className="btn-secondary btn-small"
                          onClick={() => {
                            setInput(`Help me address this analysis validation issue in my current session: ${check.recommendation}`);
                            setPaperModalOpen(false);
                          }}
                        >
                          Send Fix Prompt to AI
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {paperDraft && paperEditorJson && (
              <>
                <div style={{ marginBottom: 12 }}>
                  <input
                    className="search-input"
                    style={{ width: "100%", fontSize: "1.05rem", fontWeight: 700 }}
                    value={String(paperEditorJson.title || "")}
                    onChange={(e) => setPaperEditorJson({ ...paperEditorJson, title: e.target.value })}
                    placeholder="Paper title"
                  />
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                  {[
                    ["abstract", "Abstract"],
                    ["introduction", "Introduction"],
                    ["data_sources", "Data"],
                    ["analysis_methods", "Methods"],
                    ["results", "Results"],
                    ["discussion", "Discussion"],
                    ["conclusions", "Conclusions"],
                    ["acknowledgments", "Acknowledgments"],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      className={`btn-small ${paperTab === key ? "btn-primary" : "btn-secondary"}`}
                      onClick={() => setPaperTab(key as PaperTab)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div style={{ marginBottom: 10 }}>
                  <textarea
                    className="chat-input"
                    style={{ minHeight: 260, width: "100%" }}
                    value={getPaperSectionText(paperEditorJson, paperTab)}
                    onChange={(e) => setPaperEditorJson(setPaperSectionText(paperEditorJson, paperTab, e.target.value))}
                  />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div style={{ color: "var(--color-text-secondary)", fontSize: "0.85rem" }}>
                    Figures: {Array.isArray(((paperEditorJson.results as Record<string, unknown> | undefined)?.figures))
                      ? ((((paperEditorJson.results as Record<string, unknown>).figures as unknown[]) || []).length)
                      : 0}
                    {" · "}
                    Tables: {Array.isArray(((paperEditorJson.results as Record<string, unknown> | undefined)?.tables))
                      ? ((((paperEditorJson.results as Record<string, unknown>).tables as unknown[]) || []).length)
                      : 0}
                  </div>
                  <button
                    className="btn-secondary btn-small"
                    disabled={paperGenerating}
                    onClick={() => { void handleRegeneratePaperSection(); }}
                  >
                    {paperGenerating ? "Regenerating..." : "Regenerate Section"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {messages.length > 0 && !loading && (
        <NextStepsPanel onSend={(msg) => {
          pendingSendRef.current = true;
          setInput(msg);
        }} />
      )}

      <div
        className={`chat-input-area${dragOver ? " drag-over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFitsDrop(e.dataTransfer.files); }}
      >
        <div className="chat-input-wrapper">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={dragOver ? "Drop FITS file here..." : t("chat.placeholder")}
            rows={1}
            disabled={loading}
            aria-label="Message input"
          />
          {loading ? (
            <button
              className="btn-secondary"
              onClick={handleStop}
              style={{ background: "#b00020", color: "#fff", marginRight: 4 }}
              title="Abort this reply (R0d)"
              aria-label="Stop"
            >
              ■ Stop
            </button>
          ) : null}
          <button
            className="btn-chat-send"
            onClick={() => {
              // H0.5: pass the current input value explicitly instead of
              // relying on handleSend's closure over `input`.  Reviewer
              // reported the first-click failing (input cleared but no
              // request sent) — suspected cause is a rapid re-render
              // race where the handleSend closure captured an empty
              // string.  Passing via `overrideText` sidesteps the closure.
              void handleSend(input);
            }}
            disabled={!input.trim() || loading || !aiBackendReady}
            title={
              !aiBackendReady
                ? "No AI backend configured — add an API key in Settings"
                : "Send message (Enter)"
            }
            aria-label="Send message"
          >
            &#x2191;
          </button>
        </div>
        <span className="chat-input-hint">
          {t("chat.hint")}
        </span>
      </div>
      </div>
    </div>
  );
}
