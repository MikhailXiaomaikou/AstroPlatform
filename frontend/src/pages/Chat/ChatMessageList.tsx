// Chat message list: user/assistant bubbles, live thinking timeline,
// abstention cards, retry CTA, action cards, and the loading dots.
// JSX moved verbatim from ChatPage.tsx; all state stays in ChatPage.
import type { Dispatch, MouseEvent, RefObject, SetStateAction } from "react";
import type { ChatAction } from "../../api/client";
import MarkdownText from "../../components/chat/MarkdownText";
import { EvidenceReceiptCards } from "../../components/chat/EvidenceReceiptCard";
import ResearchStepsCard, { isResearchTurn } from "../../components/chat/ResearchStepsCard";
import type { ConversationProvenance } from "../../hooks/useConversationProvenance";
import { useI18n } from "../../i18n";
import { ActionCard, ToolTurnSummary, VisibleResearchDiagnostics } from "./ActionCard";
import { HonestAbstentionCard } from "./ChatPanels";
import { ValidationBadge } from "./ValidationBadge";
import type { DisplayMessage } from "./chatStorage";
import type { ToastState } from "./chatHelpers";

export function ChatMessageList({
  messages,
  loading,
  executingActions,
  conversationProvenance,
  messagesEndRef,
  setMessages,
  setInput,
  showToast,
  handleSend,
  handleNewChat,
  handleExecuteAction,
}: {
  messages: DisplayMessage[];
  loading: boolean;
  executingActions: Set<string>;
  conversationProvenance: ConversationProvenance;
  messagesEndRef: RefObject<HTMLDivElement | null>;
  setMessages: Dispatch<SetStateAction<DisplayMessage[]>>;
  setInput: (value: string) => void;
  showToast: (message: string, tone?: ToastState["tone"]) => void;
  handleSend: (overrideText?: string) => Promise<void>;
  handleNewChat: (event?: MouseEvent<HTMLAnchorElement | HTMLButtonElement>) => void;
  handleExecuteAction: (msgId: string, actionIndex: number, action: ChatAction) => Promise<void>;
}) {
  const { t } = useI18n();

  return (
    <>
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
                        // eslint-disable-next-line react-hooks/purity -- pre-split behavior kept verbatim: ChatPage.tsx used Date.now() here; the old god component was too complex for this lint pass to analyze, so the call was never flagged.
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
                              <span>
                                💭{" "}
                                {/* H5 (2026-09-02): agent_text is streamed
                                    before the output gate runs, so it is
                                    labelled as an unverified draft rather
                                    than reading as a settled answer. */}
                                {step.draft && (
                                  <em
                                    className="chat-thinking-draft-label"
                                    style={{ color: "#92400e", marginRight: 4 }}
                                  >
                                    {t("chat.thinking.draft_label")}
                                  </em>
                                )}
                                {step.text}
                              </span>
                            )}
                            {step.kind === "status" && (
                              <span>
                                ⏳ <span style={{ color: "#4b5563" }}>{step.text}</span>
                              </span>
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
                    <>
                      {/* Honest abstention is useful only if the user can
                          see what was attempted first. The backend already
                          preserves auto-executed tool results in actions;
                          keep the same turn summary visible here instead
                          of showing a context-free failure card. */}
                      <ToolTurnSummary actions={msg.actions} />
                      {msg.actions && msg.actions.length > 0 && (
                        <div
                          className="chat-abstention-process-note"
                          style={{
                            margin: "0.45rem 0",
                            padding: "0.55rem 0.7rem",
                            border: "1px solid #f4d7a1",
                            borderRadius: 6,
                            background: "#fff8ea",
                            color: "#6b4b12",
                            fontSize: "0.85rem",
                          }}
                        >
                          <strong>Process before honest failure:</strong>{" "}
                          {msg.actions.length} tool attempt{msg.actions.length === 1 ? "" : "s"} are shown below.
                        </div>
                      )}
                      <HonestAbstentionCard
                        abstention={msg._abstention}
                        onRetry={(suggestion) => { setInput(suggestion); }}
                      />
                    </>
                  ) : (
                    <>
                      {/* F3.3: ⚠ preamble when any auto-executed tool failed
                          or returned empty, even if the prose passed the
                          claim gate.  Keeps the validation signal visible. */}
                      <ToolTurnSummary actions={msg.actions} />
                      <MarkdownText content={msg.content} />
                      {/* R11-NEW-1: CTA specific to payload_too_large errors —
                          one click to start a fresh chat and clear the current session history. */}
                      {msg._action_hint === "new_chat" && (
                        <button
                          className="btn-primary btn-small"
                          style={{ marginTop: 10 }}
                          onClick={handleNewChat}
                        >
                          🔄 {t("chat.new_chat")}
                        </button>
                      )}
                    </>
                  )
                ) : (
                  msg.content.split("\n").map((line, i) => (
                    <p key={i}>{line || "\u00A0"}</p>
                  ))
                )}
                {/* 2026-07-03 honesty surfacing: per-reply validation badge.
                    Old messages without _validation render nothing here. */}
                {msg.role === "assistant" && !msg._pending && (
                  <>
                    <EvidenceReceiptCards receipts={msg._validation?.evidence_receipts} />
                    <ValidationBadge summary={msg._validation} truncated={msg._truncated} />
                  </>
                )}
                {/* eslint-disable-next-line react-hooks/purity -- pre-split behavior kept verbatim: ChatPage.tsx used Date.now() here; the old god component was too complex for this lint pass to analyze, so the call was never flagged. */}
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
                  {isResearchTurn(msg.actions) ? (
                    <>
                      <ResearchStepsCard actions={msg.actions} />
                      <VisibleResearchDiagnostics
                        actions={msg.actions}
                        actionResults={msg.actionResults}
                      />
                    </>
                  ) : (
                    <span className="chat-actions-label">
                      Suggested actions:
                    </span>
                  )}
                  {(() => {
                    const cards = msg.actions!.map((action, idx) => (
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
                    ));
                    // Research turns: fold the raw tool cards behind a collapsed
                    // disclosure so the clean step card leads, audit trail stays.
                    return isResearchTurn(msg.actions) ? (
                      <details className="chat-raw-actions">
                        <summary style={{ cursor: "pointer", color: "#6b7280", fontSize: 12, margin: "4px 0" }}>
                          Show raw tool cards ({msg.actions!.length})
                        </summary>
                        {cards}
                      </details>
                    ) : (
                      cards
                    );
                  })()}
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
    </>
  );
}
