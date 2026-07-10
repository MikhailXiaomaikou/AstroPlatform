// Pure chat-page helpers: failure-message builders, export utilities,
// paper-draft section accessors, and user-tool argument scaffolding.
// Moved verbatim from ChatPage.tsx (behavior-preserving split).
import {
  getStoredApiKeys,
  getPreferredAiProvider,
  getPreferredAiModelProfile,
  AI_MODEL_OPTIONS,
  DEFAULT_AI_PROVIDER,
  type ChatMessage,
  type ChatAction,
  type AIModelProfile,
} from "../../api/client";
import type { UserToolDefinition } from "../../api/userTools";
import type { DisplayMessage } from "./chatStorage";

export type ExportAction = "markdown" | "notebook" | "html" | "latex" | "bibtex";
export type JournalFormat = "aastex" | "mnras" | "aa";
export type ShareAccessLevel = "view" | "fork" | "comment";
export type PaperTab =
  | "abstract"
  | "introduction"
  | "data_sources"
  | "analysis_methods"
  | "results"
  | "discussion"
  | "conclusions"
  | "acknowledgments";

export interface ToastState {
  message: string;
  tone: "success" | "error" | "info";
}

function sanitizeAiFailureForUser(errorDetail: string): string {
  const detail = String(errorDetail || "").trim();
  if (/All configured AI backends failed|backend failed|OpenAI CLI backend failed|deepseek|anthropic|openai/i.test(detail)) {
    return "The selected AI backend failed before it could produce a verified final answer. No scientific result should be inferred from this failed turn.";
  }
  return detail || "The selected AI backend failed before it could produce a verified final answer.";
}

export function buildBackendFailureMessage(errorDetail: string, hint = ""): string {
  return [
    sanitizeAiFailureForUser(errorDetail),
    "",
    "No tool-grounded scientific conclusion was produced. Please retry, switch model/provider, or narrow the request.",
    hint,
  ].filter(Boolean).join("\n");
}

export function buildToolGroundedErrorFallback(errorDetail: string, actions: ChatAction[]): string {
  const toolNames = Array.from(new Set(
    actions
      .map((action) => String(action.action || "").trim())
      .filter(Boolean),
  ));
  const visibleTools = toolNames.length
    ? toolNames.slice(0, 8).join(", ") + (toolNames.length > 8 ? `, +${toolNames.length - 8} more` : "")
    : "the streamed tool cards";
  return [
    "The research tools streamed results, but the final language synthesis failed before a normal prose answer could be produced.",
    "",
    `Executed tool cards are still visible below: ${visibleTools}. Treat those tool cards as the source of truth for this turn.`,
    "",
    "No additional scientific conclusion is being added by this fallback. Rerun the missing evidence path before quoting posterior, fit, tension, or significance values.",
    "",
    `Technical failure: ${sanitizeAiFailureForUser(errorDetail)}`,
  ].join("\n");
}

// Stage 3 Bug 4 fix: the server's actions field is typed unknown[], so it cannot be
// directly cast to ChatAction[]. Filter out dirty entries that are missing the `action`
// field or have the wrong shape, preventing ActionCard from receiving incomplete objects
// that would render as blank cards. Used in both refresh-resume and figure-rehydrate paths.
export function validateActions(raw: unknown): ChatAction[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (a): a is ChatAction =>
      a !== null
      && typeof a === "object"
      && typeof (a as { action?: unknown }).action === "string",
  );
}

/** One evidence-preserving decoder used by every session/snapshot/import path. */
export function deserializeDisplayMessage(raw: Record<string, unknown>): DisplayMessage {
  const actions = validateActions(raw.actions);
  return {
    id: typeof raw.id === "string" && raw.id ? raw.id : crypto.randomUUID(),
    role: raw.role === "assistant" ? "assistant" : "user",
    content: typeof raw.content === "string" ? raw.content : String(raw.content ?? ""),
    actions: actions.length > 0 ? actions : undefined,
    _validation: raw._validation && typeof raw._validation === "object"
      ? raw._validation as DisplayMessage["_validation"]
      : undefined,
    _truncated: raw._truncated === true || undefined,
  };
}

/** Keep validation evidence attached when saving, exporting, or snapshotting. */
export function serializeDisplayMessage(message: DisplayMessage) {
  return {
    role: message.role,
    content: message.content,
    actions: message.actions,
    _validation: message._validation,
    _truncated: message._truncated,
  };
}

export function hasStoredAiKey(): boolean {
  const keys = getStoredApiKeys();
  return Object.values(keys).some((v) => typeof v === "string" && v.trim().length > 0);
}

export function modelDisplayLabel(profile: AIModelProfile | null): string {
  const provider = getPreferredAiProvider() || DEFAULT_AI_PROVIDER;
  const profileId = getPreferredAiModelProfile(provider);
  const localOption = (AI_MODEL_OPTIONS[provider] || []).find((option) => option.id === profileId);
  const label = profile?.display_name || localOption?.label || profileId || "DeepSeek V4 Pro";
  const resolved = profile?.resolved_model_id;
  if (profileId === "openai:gpt-5.5" && resolved && resolved !== "gpt-5.5") {
    return `${label} -> ${resolved} fallback`;
  }
  return label;
}

export function buildMinimalChatHistory(messages: DisplayMessage[]): ChatMessage[] {
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

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function generateLatexFallback(msgs: DisplayMessage[]): string {
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

export function getPaperSectionText(paperJson: Record<string, unknown>, tab: PaperTab): string {
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

export function setPaperSectionText(
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

function defaultValueForJsonSchema(schema: unknown): unknown {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) return "";
  const record = schema as Record<string, unknown>;
  const type = String(record.type || "string");
  if (type === "number" || type === "integer") return 0;
  if (type === "boolean") return false;
  if (type === "array") return [];
  if (type === "object") return {};
  return "";
}

export function exampleArgsForUserTool(tool: UserToolDefinition): Record<string, unknown> {
  const schema = tool.input_schema || {};
  const props = schema.properties;
  if (!props || typeof props !== "object" || Array.isArray(props)) return {};
  return Object.fromEntries(
    Object.entries(props as Record<string, unknown>).map(([key, value]) => [
      key,
      defaultValueForJsonSchema(value),
    ]),
  );
}
