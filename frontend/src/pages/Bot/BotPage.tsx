import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import {
  chatWithAutomationBot,
  getAutomationResearchReport,
  getAutomationResearchStatus,
  triggerAutomationResearch,
  type AutomationBotMessage,
  type AutomationResearchReport,
  type AutomationResearchStatus,
} from "../../api/client";
import MarkdownText from "../../components/chat/MarkdownText";
import { useI18n } from "../../i18n";
import { BOT_CONSOLE_ENABLED } from "../../routes";
import "./BotPage.css";

type MobileTab = "chat" | "status" | "report";

interface DisplayBotMessage extends AutomationBotMessage {
  id: string;
}

const MAX_CHAT_MESSAGES = 16;
const MAX_CHAT_CHARACTERS = 39_000;
const MAX_INPUT_CHARACTERS = 8_000;
// Mirrors _MAX_CHAT_MESSAGE_CHARS in backend/app/api/bot_console.py. A linked
// model reply can exceed it; resending such a turn would 422 every later
// request until it leaves the history window.
const MAX_MESSAGE_CHARACTERS = 8_000;

function safeDisplayText(value: string): string {
  // The automation API already returns safe basenames. This final UI guard
  // prevents an unexpected local exception from exposing a home-directory
  // path in a reply, error, or report.
  return value
    .replace(/\/(?:Users|home)\/[^\s'"<>]+/g, "[local file]");
}

function errorMessage(error: unknown, fallback: string): string {
  const candidate = error as {
    response?: { data?: { detail?: unknown } };
    message?: unknown;
  };
  const detail = candidate?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return safeDisplayText(detail.trim());
  if (Array.isArray(detail)) {
    // FastAPI validation errors carry a list of {msg, ...} objects.
    const parts = detail
      .map((item) => (item as { msg?: unknown })?.msg)
      .filter((msg): msg is string => typeof msg === "string" && msg.trim().length > 0);
    if (parts.length) return safeDisplayText(parts.join("; "));
  }
  if (typeof candidate?.message === "string" && candidate.message.trim()) {
    return safeDisplayText(candidate.message.trim());
  }
  return fallback;
}

function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404;
}

function requestMessages(messages: DisplayBotMessage[]): AutomationBotMessage[] {
  const selected: AutomationBotMessage[] = [];
  let characters = 0;

  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (selected.length >= MAX_CHAT_MESSAGES) break;
    const message = messages[index];
    if (!message.content.trim()) continue;
    if (message.content.length > MAX_MESSAGE_CHARACTERS) {
      // Skipped as a whole turn (never truncated into a fake quote); the
      // server rejects any single message above this limit.
      continue;
    }
    const remaining = MAX_CHAT_CHARACTERS - characters;
    if (remaining <= 0) break;
    if (message.content.length > remaining) {
      // The newest user message is capped well below this limit. Older turns
      // are dropped as whole turns so the server never receives a fake,
      // partially-trimmed quote from the linked model.
      break;
    }
    selected.unshift({ role: message.role, content: message.content });
    characters += message.content.length;
  }

  return selected;
}

function statusTone(status: string): "verified" | "warn" | "idle" {
  const normalized = status.toLowerCase();
  if (["completed", "completed_with_warnings", "no_new_data"].includes(normalized)) {
    return normalized === "completed" || normalized === "no_new_data" ? "verified" : "warn";
  }
  if (["running", "pending", "interrupted", "failed"].includes(normalized)) return "warn";
  return "idle";
}

function translatedStatusLabel(
  status: AutomationResearchStatus,
  translate: (key: string) => string,
): string {
  const key = `bot.state.${status.status.toLowerCase()}`;
  const translated = translate(key);
  return translated === key ? safeDisplayText(status.status_label) : translated;
}

function formatTimestamp(value: string, language: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const locale = language === "zh" ? "zh-CN" : language;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(parsed);
}

function BotConsole() {
  const { lang, t } = useI18n();
  const [mobileTab, setMobileTab] = useState<MobileTab>("chat");
  const [messages, setMessages] = useState<DisplayBotMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [failedMessage, setFailedMessage] = useState<string | null>(null);
  const [model, setModel] = useState("");
  const [status, setStatus] = useState<AutomationResearchStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [report, setReport] = useState<AutomationResearchReport | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [reportError, setReportError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [triggerNotice, setTriggerNotice] = useState<{ text: string; error: boolean } | null>(null);
  const messageId = useRef(0);
  const messagesRef = useRef<HTMLDivElement>(null);

  const nextMessageId = useCallback((role: AutomationBotMessage["role"]) => {
    messageId.current += 1;
    return `${role}-${messageId.current}`;
  }, []);

  const refreshStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const nextStatus = await getAutomationResearchStatus();
      setStatus(nextStatus);
      setModel(nextStatus.model);
      setStatusError(null);
    } catch (error) {
      setStatusError(errorMessage(error, t("bot.error.status")));
    } finally {
      setStatusLoading(false);
    }
  }, [t]);

  const refreshReport = useCallback(async () => {
    setReportLoading(true);
    try {
      const nextReport = await getAutomationResearchReport();
      setReport(nextReport);
      setReportError(null);
    } catch (error) {
      if (isNotFound(error)) {
        setReport(null);
        setReportError(null);
      } else {
        setReportError(errorMessage(error, t("bot.error.report")));
      }
    } finally {
      setReportLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refreshStatus();
    void refreshReport();
    const timer = window.setInterval(() => {
      void refreshStatus();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [refreshReport, refreshStatus]);

  useEffect(() => {
    const container = messagesRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages, sending]);

  const completeChatRequest = async (
    nextMessages: DisplayBotMessage[],
    retryText: string,
  ) => {
    setSending(true);
    setChatError(null);
    setFailedMessage(null);
    try {
      const response = await chatWithAutomationBot(requestMessages(nextMessages));
      setModel(response.model);
      setMessages((current) => [
        ...current,
        {
          id: nextMessageId("assistant"),
          role: "assistant",
          content: safeDisplayText(response.reply),
        },
      ]);
    } catch (error) {
      setChatError(errorMessage(error, t("bot.error.chat")));
      setFailedMessage(retryText);
    } finally {
      setSending(false);
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    const nextMessages = [
      ...messages,
      { id: nextMessageId("user"), role: "user" as const, content: text },
    ];
    setMessages(nextMessages);
    setInput("");
    void completeChatRequest(nextMessages, text);
  };

  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  };

  const handleRetryMessage = () => {
    if (!failedMessage || sending) return;
    void completeChatRequest(messages, failedMessage);
  };

  const handleTrigger = async () => {
    if (triggering || status?.can_trigger === false) return;
    setTriggering(true);
    setTriggerNotice(null);
    try {
      const result = await triggerAutomationResearch();
      setTriggerNotice({ text: safeDisplayText(result.message), error: false });
      await Promise.all([refreshStatus(), refreshReport()]);
    } catch (error) {
      setTriggerNotice({ text: errorMessage(error, t("bot.error.trigger")), error: true });
    } finally {
      setTriggering(false);
    }
  };

  const connected = Boolean(status) && !statusError;
  const running = status?.status.toLowerCase() === "running";
  const triggerDisabled = triggering || statusLoading || !status || running || status.can_trigger === false;
  const reportMarkdown = report ? safeDisplayText(report.markdown) : "";
  const connectionLabel = statusLoading && !status
    ? t("bot.connecting")
    : connected
      ? t("bot.connected")
      : t("bot.offline");

  return (
    <div className="journal-page bot-page" data-mobile-tab={mobileTab}>
      <header className="page-head bot-page-head">
        <div>
          <div className="hero-eyebrow">{t("bot.eyebrow")}</div>
          <h1>{t("bot.title")}</h1>
          <p className="page-sub">{t("bot.subtitle")}</p>
          <div className="badge-row bot-badge-row" aria-live="polite">
            <span className={`j-tag ${connected ? "verified" : "warn"}`}>
              {connectionLabel}
            </span>
            {model && <span className="j-tag">{model}</span>}
            {status && (
              <span className={`j-tag ${statusTone(status.status)}`}>
                {translatedStatusLabel(status, t)}
              </span>
            )}
          </div>
        </div>
        <button
          type="button"
          className="btn-journal-primary bot-trigger-button"
          onClick={() => { void handleTrigger(); }}
          disabled={triggerDisabled}
        >
          {triggering
            ? t("bot.triggering")
            : running
              ? t("bot.running")
              : status?.can_trigger === false
                ? t("bot.trigger_unavailable")
                : t("bot.trigger")}
        </button>
      </header>

      {triggerNotice && (
        <div
          className={`${triggerNotice.error ? "error-banner" : "success-banner"} bot-trigger-notice`}
          role={triggerNotice.error ? "alert" : "status"}
        >
          {triggerNotice.text}
        </div>
      )}

      <div className="bot-mobile-tabs" role="tablist" aria-label={t("bot.mobile_tabs")}>
        {(["chat", "status", "report"] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            aria-selected={mobileTab === tab}
            className={mobileTab === tab ? "active" : ""}
            onClick={() => setMobileTab(tab)}
          >
            {t(`bot.tab.${tab}`)}
          </button>
        ))}
      </div>

      <div className="bot-layout">
        <section className="bot-chat-panel bot-panel" data-panel="chat" aria-labelledby="bot-chat-title">
          <div className="bot-panel-heading">
            <div>
              <h2 id="bot-chat-title">{t("bot.chat.title")}</h2>
              <p>{t("bot.chat.subtitle")}</p>
            </div>
            {model && <span className="j-tag">{model}</span>}
          </div>
          <p className="abstract">{t("bot.chat.unverified_notice")}</p>

          <div
            ref={messagesRef}
            className="bot-messages"
            role="log"
            aria-live="polite"
            aria-relevant="additions"
          >
            {messages.length === 0 && (
              <div className="bot-empty-chat">
                <span aria-hidden="true">✦</span>
                <h3>{t("bot.chat.empty_title")}</h3>
                <p>{t("bot.chat.empty_body")}</p>
              </div>
            )}
            {messages.map((message) => (
              <article key={message.id} className={`bot-message ${message.role}`}>
                <div className="bot-message-meta">
                  {message.role === "user" ? t("bot.chat.you") : (model || t("bot.chat.assistant"))}
                </div>
                <div className="bot-message-content">
                  {message.role === "assistant"
                    ? <MarkdownText content={message.content} />
                    : message.content}
                </div>
              </article>
            ))}
            {sending && (
              <div className="bot-message assistant bot-message-pending" role="status">
                <div className="bot-message-meta">{model || t("bot.chat.assistant")}</div>
                <div>{t("bot.chat.thinking")}</div>
              </div>
            )}
          </div>

          {chatError && (
            <div className="error-banner bot-inline-error" role="alert">
              <span>{chatError}</span>
              {failedMessage && (
                <button
                  type="button"
                  className="btn-journal-ghost small"
                  onClick={handleRetryMessage}
                  disabled={sending}
                >
                  {t("bot.chat.retry")}
                </button>
              )}
            </div>
          )}

          <form className="bot-composer" onSubmit={handleSubmit}>
            <label className="sr-only" htmlFor="bot-message-input">{t("bot.chat.input_label")}</label>
            <textarea
              id="bot-message-input"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder={t("bot.chat.placeholder")}
              rows={2}
              maxLength={MAX_INPUT_CHARACTERS}
              disabled={sending}
            />
            <button
              type="submit"
              className="btn-journal-primary small"
              disabled={sending || !input.trim()}
            >
              {sending ? t("bot.chat.sending") : t("bot.chat.send")}
            </button>
          </form>
          <div className="bot-composer-hint">{t("bot.chat.hint")}</div>
        </section>

        <aside className="bot-sidebar">
          <section className="bot-status-panel bot-panel" data-panel="status" aria-labelledby="bot-status-title">
            <div className="bot-panel-heading">
              <h2 id="bot-status-title">{t("bot.status.title")}</h2>
              <button
                type="button"
                className="btn-journal-ghost small"
                onClick={() => { void refreshStatus(); }}
                disabled={statusLoading}
              >
                {t("bot.refresh")}
              </button>
            </div>

            {statusLoading && !status && <div className="fits-loading">{t("bot.status.loading")}</div>}
            {statusError && (
              <div className="error-banner bot-inline-error" role="alert">{statusError}</div>
            )}
            {status && (
              <div className="j-side-block bot-status-block" aria-live="polite">
                <div className="timeline bot-connections">
                  <div className="tl-row">
                    <span className={`tl-dot ${status.bot_online ? "ok" : "idle"}`} />
                    <span>{t("bot.status.bot")}</span>
                    <em>{status.bot_online ? t("bot.status.online") : t("bot.status.offline")}</em>
                  </div>
                  <div className="tl-row">
                    <span className="tl-dot ok" />
                    <span>{t("bot.status.model")}</span>
                    <em>{model || t("bot.status.none")}</em>
                  </div>
                  <div className="tl-row">
                    <span className={`tl-dot ${running ? "run" : "ok"}`} />
                    <span>{t("bot.status.research")}</span>
                    <em>{translatedStatusLabel(status, t)}</em>
                  </div>
                </div>

                <div className="bot-status-rows">
                  <div className="row"><span className="k">{t("bot.status.week")}</span><span className="v">{status.week_id}</span></div>
                  <div className="row"><span className="k">{t("bot.status.primary_schedule")}</span><span className="v">{status.schedule_primary ? safeDisplayText(status.schedule_primary) : t("bot.status.none")}</span></div>
                  <div className="row"><span className="k">{t("bot.status.recovery_schedule")}</span><span className="v">{status.schedule_recovery ? safeDisplayText(status.schedule_recovery) : t("bot.status.none")}</span></div>
                  <div className="row"><span className="k">{t("bot.status.llm_calls")}</span><span className="v">{status.llm_calls_used}/{status.llm_calls_limit}</span></div>
                  <div className="row"><span className="k">{t("bot.status.executed")}</span><span className="v">{status.executed_success}</span></div>
                  <div className="row"><span className="k">{t("bot.status.review")}</span><span className="v">{status.needs_review}</span></div>
                  <div className="row"><span className="k">{t("bot.status.notification")}</span><span className="v">{status.notification_status || t("bot.status.none")}</span></div>
                </div>
              </div>
            )}
          </section>

          <section className="bot-report-panel bot-panel" data-panel="report" aria-labelledby="bot-report-title">
            <div className="bot-panel-heading">
              <h2 id="bot-report-title">{t("bot.report.title")}</h2>
              <button
                type="button"
                className="btn-journal-ghost small"
                onClick={() => { void refreshReport(); }}
                disabled={reportLoading}
              >
                {t("bot.refresh")}
              </button>
            </div>

            {reportLoading && !report && <div className="fits-loading">{t("bot.report.loading")}</div>}
            {reportError && (
              <div className="error-banner bot-inline-error" role="alert">{reportError}</div>
            )}
            {!reportLoading && !report && !reportError && (
              <div className="fits-hint bot-report-empty">
                <strong>{t("bot.report.empty_title")}</strong>
                <p>{t("bot.report.empty_body")}</p>
              </div>
            )}
            {report && (
              <article className="bot-report">
                <div className="bot-report-meta">
                  <strong>{safeDisplayText(report.report_name)}</strong>
                  <span>{formatTimestamp(report.updated_at, lang)}</span>
                </div>
                {report.truncated && (
                  <div className="abstract bot-report-truncated">{t("bot.report.truncated")}</div>
                )}
                <div className="bot-report-markdown">
                  <MarkdownText content={reportMarkdown} />
                </div>
              </article>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

export default function BotPage() {
  const { t } = useI18n();
  if (BOT_CONSOLE_ENABLED) return <BotConsole />;

  return (
    <div className="journal-page bot-page">
      <header className="page-head">
        <div>
          <div className="hero-eyebrow">{t("bot.eyebrow")}</div>
          <h1>{t("bot.title")}</h1>
          <p className="page-sub">{t("bot.subtitle")}</p>
        </div>
      </header>
      <div className="abstract bot-disabled-notice" role="status">
        <strong>{t("bot.disabled_title")}</strong>{" "}{t("bot.disabled_body")}
      </div>
    </div>
  );
}
