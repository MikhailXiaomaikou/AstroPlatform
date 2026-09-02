// Chat display/storage types and localStorage persistence helpers.
// Moved verbatim from ChatPage.tsx (behavior-preserving split).
import type { ChatAction, ChatSessionSummary, ValidationSummary } from "../../api/client";

export interface ThinkingStep {
  kind: "agent_text" | "tool_call" | "tool_progress" | "tool_result" | "status" | "tools_disabled" | "workflow_budget" | "workflow_checkpoint";
  agent?: string;
  tool?: string;
  text?: string;
  input?: unknown;
  stage?: string;
  result?: unknown;
  mode?: string;
  cacheRefs?: string[];
  // agent_text only: the prose was streamed before the output gate ran, so
  // the timeline labels it as an unverified draft (2026-09-02, H5).
  draft?: boolean;
  // G3.5 — backend stripped these tools from the toolkit this iteration.
  disabled?: string[];
  iteration?: number;
  maxIterations?: number;
}

export interface DisplayMessage {
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
  // R11-NEW-1: Populated when the error bubble needs an action button
  // (e.g. payload_too_large prompting a "Start new chat"). The UI renders the
  // corresponding CTA based on the value. Leaving it undefined has no effect on regular bubbles.
  _action_hint?: "new_chat";
  // 2026-07-03 honesty surfacing: per-reply validation summary from the
  // backend gate stack. Optional — old messages lack it and render
  // unchanged (no badge).
  _validation?: ValidationSummary;
  // True when the agent loop hit its iteration cap — the reply is a
  // truncated workflow, not a complete answer.
  _truncated?: boolean;
}

export interface StoredMessage {
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
  _validation?: ValidationSummary;
  _truncated?: boolean;
}

const ANON_CHAT_SCOPE = "anon";
const CHAT_HISTORY_STORAGE_KEY = "astro_chat_history";
export const CHAT_DRAFT_STORAGE_KEY = "astro_chat_draft";
export const CHAT_AUTOSAVE_DRAFT_STORAGE_KEY = "astro_chat_autosave_draft";
export const CURRENT_CHAT_SESSION_STORAGE_KEY = "astro_current_chat_session_id";
const LOCAL_CHAT_SESSIONS_STORAGE_KEY = "astro_local_chat_sessions";

type ChatStorageUser = { id?: string | null } | null | undefined;

export function chatStorageScope(user: ChatStorageUser): string {
  const id = String(user?.id || "").trim();
  return id ? `user:${id}` : ANON_CHAT_SCOPE;
}

export function scopedChatStorageKey(baseKey: string, scope: string): string {
  return `${baseKey}:${scope}`;
}

export function clearFreshChatStorage(scope: string): void {
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

export function loadChatHistory(scope: string): DisplayMessage[] {
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
    // _pruneToolResults mutates tool_result objects in place (delete heavy
    // fields, replace figures with a marker).  Clone the actions array and
    // each action's tool_result so the pruner never touches the live React
    // state objects we are still rendering — otherwise an over-cap save
    // would blank out tool cards mid-session.
    actions: m.actions?.map((a) => {
      const rec = a as Record<string, unknown>;
      const tr = rec.tool_result;
      if (tr && typeof tr === "object") {
        return { ...rec, tool_result: { ...(tr as Record<string, unknown>) } } as unknown as ChatAction;
      }
      return { ...rec } as unknown as ChatAction;
    }),
    actionResults: m.actionResults ? Array.from(m.actionResults.entries()) : undefined,
    _pending: m._pending,
    _validation: m._validation,
    _truncated: m._truncated,
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
              // Mark that a heavy non-figure field was offloaded so the
              // boot-time server rehydrate fires (it gates on __offloaded__
              // / __figures_offloaded__ / __fields_offloaded__).  Without
              // this marker, rows/data/results stripped here would render as
              // empty after reload and never be refetched from the server.
              trObj.__fields_offloaded__ = true;
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

export function safeSetChatHistory(messages: DisplayMessage[], scope: string): { written: boolean; droppedMessages: number } {
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

export function saveChatHistory(messages: DisplayMessage[], scope: string): void {
  safeSetChatHistory(messages, scope);
}

export interface LocalChatSession {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
  messages: Array<{ role: string; content: string; actions?: unknown[] }>;
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

export function summarizeLocalSessions(scope: string): ChatSessionSummary[] {
  return readLocalChatSessions(scope).map(({ id, title, message_count, updated_at }) => ({
    id,
    title,
    message_count,
    updated_at,
  }));
}

export function saveLocalChatSession(
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

export function loadLocalChatSession(id: string, scope: string): LocalChatSession | null {
  return readLocalChatSessions(scope).find((session) => session.id === id) || null;
}

export function deleteLocalChatSession(id: string, scope: string): void {
  writeLocalChatSessions(readLocalChatSessions(scope).filter((session) => session.id !== id), scope);
}
