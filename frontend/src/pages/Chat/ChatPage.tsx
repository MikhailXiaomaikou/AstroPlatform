import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import type { MouseEvent } from "react";
import {
  sendChatMessage,
  executeChatAction,
  logOperation,
  uploadFITS,
  uploadGeneralFile,
  saveChatSession,
  renameChatSession,
  listChatSessions,
  loadChatSession,
  deleteChatSession,
  importChatSession,
  exportChatMarkdown,
  exportChatNotebook,
  exportChatHtml,
  exportChatLatex,
  exportChatBibTeX,
  type ChatAction,
  type ThinkingEvent,
  type ChatSessionSummary,
} from "../../api/client";
import { useI18n } from "../../i18n";
import { useAuth } from "../../context/AuthContext";
import { useNavigate } from "react-router-dom";
import { useTracking } from "../../hooks/useTracking";
import { useConversationProvenance } from "../../hooks/useConversationProvenance";
import { readWorkspaceCache } from "../../utils/workspaceCache";
import {
  chatStorageScope,
  scopedChatStorageKey,
  clearFreshChatStorage,
  loadChatHistory,
  safeSetChatHistory,
  saveChatHistory,
  summarizeLocalSessions,
  saveLocalChatSession,
  loadLocalChatSession,
  deleteLocalChatSession,
  CHAT_DRAFT_STORAGE_KEY,
  CHAT_AUTOSAVE_DRAFT_STORAGE_KEY,
  CURRENT_CHAT_SESSION_STORAGE_KEY,
  type DisplayMessage,
  type ThinkingStep,
} from "./chatStorage";
import {
  buildBackendFailureMessage,
  buildToolGroundedErrorFallback,
  buildMinimalChatHistory,
  modelDisplayLabel,
  generateLatexFallback,
  validateActions,
  type ToastState,
} from "./chatHelpers";
import { ApiKeyPrompt } from "./ChatPanels";
import { ChatSidebar } from "./ChatSidebar";
import { ChatMessageList } from "./ChatMessageList";
import { ShareModal, PaperDraftModal } from "./ChatModals";
import { useAiBackendStatus } from "./useAiBackendStatus";
import { useUserTools } from "./useUserTools";
import { useChatExports } from "./useChatExports";
import { usePaperDraft } from "./usePaperDraft";
import { useCollaboration } from "./useCollaboration";

// W6 (PART W): "Validate assumptions first" is the new first item in NextStepsPanel.
// The B3/B4 regression surfaced a missing entry point for proactively verifying the
// tool attribution of every numeric claim before writing up results. Clicking it asks the
// AI to enumerate each claim's tool_result source, or explicitly state "not measured this
// turn". This is the zero-hallucination gate's companion UX: the AI self-checks which
// numbers lack tool backing instead of waiting for the validator to block them.
// Exported so ChatPage.test.tsx can run regression tests against it.
// PART Y Q3: HMR fast-refresh not working for this file is an accepted known trade-off —
// splitting the constant to a separate file would require updating test import paths for minimal gain.
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
  const navigate = useNavigate();
  // PART Y Q3: depend on user?.id, not the user object — chatStorageScope
  // is a function of identity, not of the wrapping object's reference.
  // Re-computing on every user-object reference change would invalidate
  // localStorage scope on every Auth refresh.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const storageScope = useMemo(() => chatStorageScope(user), [user?.id]);
  const freshChatRequestedAtBoot = new URLSearchParams(window.location.search).has("fresh_chat");
  const { t } = useI18n();
  const { track } = useTracking();
  const {
    hasKey,
    setHasKey,
    serverBackendReady,
    serverBackendList,
    selectedModelStatus,
    aiBackendReady,
  } = useAiBackendStatus();
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

  const {
    userTools,
    userToolsLoading,
    userToolsError,
    refreshUserTools,
    handleUseUserTool,
  } = useUserTools({ user, setInput, showToast, inputRef });

  const { exporting, handleExport } = useChatExports({ user, messages, storageScope, showToast, track });

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
      // Persist the validation badge state so shared/read-only views of
      // this session can render the same honesty signal.
      _validation: message._validation,
      _truncated: message._truncated,
    }));
    const saved = await saveChatSession(data, currentSessionId || undefined);
    setCurrentSessionId(saved.id);
    refreshSessions();
    return saved.id;
  }, [currentSessionId, messages, refreshSessions, user]);

  const {
    paperModalOpen,
    setPaperModalOpen,
    paperSessionId,
    paperFormat,
    setPaperFormat,
    paperValidation,
    paperDraft,
    paperEditorJson,
    setPaperEditorJson,
    paperTab,
    setPaperTab,
    paperLoading,
    paperGenerating,
    paperSaving,
    handleOpenPaperDraft,
    handleGeneratePaper,
    handleSavePaperDraft,
    handleTogglePaperPublish,
    handleRegeneratePaperSection,
  } = usePaperDraft({ user, messages, currentSessionId, setCurrentSessionId, refreshSessions, showToast, track });

  const {
    shareModalOpen,
    setShareModalOpen,
    shareAccessLevel,
    setShareAccessLevel,
    shareExpiryHours,
    setShareExpiryHours,
    shareUrl,
    sessionShares,
    sessionSnapshots,
    snapshotName,
    setSnapshotName,
    snapshotCompareSelection,
    setSnapshotCompareSelection,
    snapshotDiff,
    shareLoading,
    handleOpenCollaboration,
    handleCreateShare,
    handleRevokeShare,
    handleCreateSnapshot,
    handleRestoreSnapshot,
    handleCompareSnapshots,
  } = useCollaboration({ user, currentSessionId, ensurePersistedSession, showToast, setMessages, storageScope });

  useEffect(() => {
    // Stage 3 Bug 5 fix: the original mount effect ran 4-5 setMessages calls, two of which
    // were async (refresh-resume + figure-rehydrate) fire-and-forget whose ordering was
    // determined by the network, occasionally causing message flicker / out-of-order renders.
    // Refactored to:
    //   Phase 1 (sync): determine initialMessages and call setMessages once
    //   Phase 2 (async): a single loadChatSession handles both refresh-resume and
    //                    figure-rehydrate, then merges with one final setMessages
    // Total setMessages calls <= 2; no concurrent fetches.
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

    // The scoped draft key is written by ChatPage itself; the legacy unscoped
    // key (CHAT_DRAFT_STORAGE_KEY) is what cross-page "Send to AI Assistant"
    // writers (AlertDashboard, AnomalyExplorer, FITSBrowser) still use.  Read
    // the scoped key first, fall back to the unscoped one so those prefills are
    // not silently discarded, then clear both.
    const draft = localStorage.getItem(draftKey) ?? localStorage.getItem(CHAT_DRAFT_STORAGE_KEY);
    if (draft) {
      setInput(draft);
    }
    localStorage.removeItem(draftKey);
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
        return tr.__offloaded__ === true
          || tr.__figures_offloaded__ !== undefined
          || tr.__fields_offloaded__ === true;
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
                // Stage 3 Bug 4: validateActions filters out dirty entries
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
          // Stage 3 Bug 3: Use tail-aligned array index matching with a 50-character content sanity check.
          if (needFigureRehydrate) {
            const offset = srvMsgs.length - initialMessages.length;
            nextMessages = nextMessages.map((m, localIdx) => {
              if (m.role !== "assistant" || !m.actions?.length) return m;
              const locallyOffloaded = m.actions.some((a) => {
                const tr = (a as Record<string, unknown>).tool_result as Record<string, unknown> | undefined;
                return tr && typeof tr === "object" && (
                  tr.__offloaded__ === true
                  || tr.__figures_offloaded__ !== undefined
                  || tr.__fields_offloaded__ === true
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
      const data = messages.map(m => ({
        role: m.role,
        content: m.content,
        actions: m.actions,
        _validation: m._validation,
        _truncated: m._truncated,
      }));
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
        _validation: (m._validation && typeof m._validation === "object")
          ? m._validation as DisplayMessage["_validation"]
          : undefined,
        _truncated: m._truncated === true || undefined,
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

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    // Clicking "New Chat" calls setMessages([]) — skip scroll for an empty list
    // to prevent the page jumping to the bottom anchor when a new session starts.
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
  const attachInputRef = useRef<HTMLInputElement>(null);

  // Auto-send when input is set by FITS drop
  useEffect(() => {
    if (pendingSendRef.current && input.trim()) {
      pendingSendRef.current = false;
      handleSend();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  const handleFileDrop = async (files: FileList) => {
    for (const file of Array.from(files)) {
      const lower = file.name.toLowerCase();
      // 3B (2026-06-11): CSV = the user's OWN measurement table → upload via
      // the general-file endpoint, then steer the model to the fit_line_lfr
      // user_file path (results are labeled user-provided, not literature).
      if (lower.endsWith(".csv")) {
        try {
          const result = await uploadGeneralFile(file);
          const msg = `I uploaded a CSV file with my own measurements: ${file.name} (stored at ${result.path}). Please fit the line luminosity-FWHM relation from it with fit_line_lfr (user_file="${result.path}").`;
          pendingSendRef.current = true;
          setInput(msg);
        } catch {
          showToast(t("chat.csv_upload_failed"), "error");
        }
        break;
      }
      if (!lower.match(/\.(fits|fit|fts)$/)) continue;
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
      const statusMessage = evt.type === "status" ? String(evt.message || "") : "";
      if (evt.type === "status" && !/(fact|guardrail|fallback|summary|deadline|matrix|likelihood|research|evidence|checkpoint|resume|reconnect)/i.test(statusMessage)) {
        return; // heartbeat, ignore in UI
      }
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
          evt.type === "agent_text" || evt.type === "tool_progress" || evt.type === "status"
            ? (evt.type === "agent_text" ? evt.content : evt.type === "tool_progress" ? evt.message : evt.message)
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
      // Stage 3 Bug 2 fix: 5 localStorage keys were removed in M3 (2026-05-18)
      // after the /search /adql /pipeline /workspace pages were deleted. Nobody
      // writes to them anymore, so anything still stored there can only be stale
      // data left by a previous user on the same machine — a privacy leak.
      // All deleted. The still-active astro_workspace_files goes through
      // readWorkspaceCache(scope) with an explicit user scope instead of raw getItem.
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
          // 2026-07-03 honesty surfacing: per-reply validation summary +
          // iteration-cap truncation flag from the final SSE text frame.
          _validation: response.validation_summary,
          _truncated: response.hit_iteration_cap || undefined,
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
        // R11-NEW-1: error_class is propagated from the 'error' SSE event in the stream (client.ts attaches it to the Error instance).
        errorClass = (err as Error & { error_class?: string }).error_class;
      }
      // Classification is by structured error_class ONLY (backend SSE
      // error events set e.g. "payload_too_large"; client.ts tags genuine
      // outages "backend_unreachable"). Never match on display text — it
      // is locale-dependent, and rewriting a real outage into payload
      // advice told users to clear their history during downtime.
      // R11-NEW-1: When the payload is too large, add a "Start new chat" button to the
      // error bubble. Lightweight approach: append guidance text to the error message tail
      // and surface the button via DisplayMessage._action_hint, letting the UI render it.
      const hint = errorClass === "payload_too_large"
        ? `\n\n${t("chat.new_chat_hint")}`
        : "";
      const hasStreamedToolResults = streamedActions.length > 0;
      const errorMsg: DisplayMessage = {
        id: pendingId,
        role: "assistant",
	        content: hasStreamedToolResults
	          ? buildToolGroundedErrorFallback(errorDetail, streamedActions)
	          : buildBackendFailureMessage(errorDetail, hint),
        actions: streamedActions.length > 0 ? streamedActions : undefined,
        _action_hint: !hasStreamedToolResults && errorClass === "payload_too_large" ? "new_chat" : undefined,
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
      <ChatSidebar
        sidebarCollapsed={sidebarCollapsed}
        setSidebarCollapsed={setSidebarCollapsed}
        sessionSearch={sessionSearch}
        setSessionSearch={setSessionSearch}
        sessions={sessions}
        filteredSessions={filteredSessions}
        currentSessionId={currentSessionId}
        user={user}
        userTools={userTools}
        userToolsLoading={userToolsLoading}
        userToolsError={userToolsError}
        refreshUserTools={refreshUserTools}
        handleUseUserTool={handleUseUserTool}
        handleLoadSession={handleLoadSession}
        handleDeleteSession={handleDeleteSession}
        handleNewChat={handleNewChat}
        navigate={navigate}
      />

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
        <ShareModal
          setShareModalOpen={setShareModalOpen}
          shareAccessLevel={shareAccessLevel}
          setShareAccessLevel={setShareAccessLevel}
          shareExpiryHours={shareExpiryHours}
          setShareExpiryHours={setShareExpiryHours}
          shareLoading={shareLoading}
          shareUrl={shareUrl}
          sessionShares={sessionShares}
          sessionSnapshots={sessionSnapshots}
          snapshotName={snapshotName}
          setSnapshotName={setSnapshotName}
          snapshotCompareSelection={snapshotCompareSelection}
          setSnapshotCompareSelection={setSnapshotCompareSelection}
          snapshotDiff={snapshotDiff}
          handleCreateShare={handleCreateShare}
          handleRevokeShare={handleRevokeShare}
          handleCreateSnapshot={handleCreateSnapshot}
          handleRestoreSnapshot={handleRestoreSnapshot}
          handleCompareSnapshots={handleCompareSnapshots}
        />
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
              below (stays in this browser by default) or configure one
              server-side before sending messages. Anthropic, OpenAI, and
              DeepSeek are all supported.
            </div>
            <button
              type="button"
              onClick={() => navigate("/account")}
              style={{
                padding: "0.35rem 0.8rem",
                background: "var(--color-red)",
                color: "white",
                border: "none",
                borderRadius: 4,
                fontSize: "0.82rem",
                whiteSpace: "nowrap",
                cursor: "pointer",
              }}
            >
              Open Account → API keys
            </button>
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

        <ChatMessageList
          messages={messages}
          loading={loading}
          executingActions={executingActions}
          conversationProvenance={conversationProvenance}
          messagesEndRef={messagesEndRef}
          setMessages={setMessages}
          setInput={setInput}
          showToast={showToast}
          handleSend={handleSend}
          handleNewChat={handleNewChat}
          handleExecuteAction={handleExecuteAction}
        />
      </div>

      {paperModalOpen && (
        <PaperDraftModal
          setPaperModalOpen={setPaperModalOpen}
          paperFormat={paperFormat}
          setPaperFormat={setPaperFormat}
          paperTab={paperTab}
          setPaperTab={setPaperTab}
          paperSessionId={paperSessionId}
          paperValidation={paperValidation}
          paperDraft={paperDraft}
          paperEditorJson={paperEditorJson}
          setPaperEditorJson={setPaperEditorJson}
          paperLoading={paperLoading}
          paperGenerating={paperGenerating}
          paperSaving={paperSaving}
          handleGeneratePaper={handleGeneratePaper}
          handleSavePaperDraft={handleSavePaperDraft}
          handleTogglePaperPublish={handleTogglePaperPublish}
          handleRegeneratePaperSection={handleRegeneratePaperSection}
          setInput={setInput}
        />
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
        onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFileDrop(e.dataTransfer.files); }}
      >
        <div className="chat-input-wrapper">
          <input
            ref={attachInputRef}
            type="file"
            accept=".csv,.fits,.fit,.fts"
            style={{ display: "none" }}
            onChange={(e) => {
              if (e.target.files && e.target.files.length > 0) {
                void handleFileDrop(e.target.files);
              }
              e.target.value = "";
            }}
            tabIndex={-1}
            aria-hidden="true"
          />
          <button
            className="btn-secondary chat-attach-btn"
            onClick={() => attachInputRef.current?.click()}
            disabled={loading || !user}
            title={user ? t("chat.attach_file") : t("chat.attach_sign_in")}
            aria-label={t("chat.attach_file")}
            style={{ marginRight: 4 }}
          >
            &#x1F4CE;
          </button>
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={dragOver ? "Drop FITS or CSV file here..." : t("chat.placeholder")}
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
