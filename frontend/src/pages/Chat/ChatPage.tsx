import { useState, useRef, useEffect, useCallback, lazy, Suspense } from "react";
import {
  sendChatMessage,
  executeChatAction,
  getStoredApiKey,
  type ChatMessage,
  type ChatAction,
} from "../../api/client";
const PlotBuilder = lazy(() => import("../../components/viz/PlotBuilder"));

interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: Map<number, Record<string, unknown>>;
}

function ActionCard({
  action,
  index,
  result,
  onExecute,
  executing,
}: {
  action: ChatAction;
  index: number;
  result?: Record<string, unknown>;
  onExecute: (index: number, action: ChatAction) => void;
  executing: boolean;
}) {
  const labels: Record<string, string> = {
    search: "Search databases",
    adql: "Run ADQL query",
    run_pipeline: "Run pipeline",
    explain: "Explanation",
    plot: "Interactive Plot",
  };

  const icons: Record<string, string> = {
    search: "🔍",
    adql: "📊",
    run_pipeline: "⚙️",
    explain: "📖",
    plot: "📈",
  };

  return (
    <div className="chat-action-card">
      <div className="chat-action-header">
        <span className="chat-action-icon">
          {icons[action.action] || "▶"}
        </span>
        <span className="chat-action-label">
          {labels[action.action] || action.action}
        </span>
        {action.action !== "explain" && !result && (
          <button
            className="btn-chat-action"
            onClick={() => onExecute(index, action)}
            disabled={executing}
          >
            {executing ? "Running..." : "Execute"}
          </button>
        )}
      </div>
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
      </div>
      {action.action === "plot" && (
        <Suspense fallback={<div className="fits-loading">Loading plot...</div>}>
          <PlotBuilder
            initialData={action.data as Record<string, unknown>}
            initialChartType={action.chart_type as string}
          />
        </Suspense>
      )}
      {result && <ActionResult result={result} />}
    </div>
  );
}

function SearchResultTable({ data }: { data: Array<Record<string, unknown>> }) {
  const [selected, setSelected] = useState<Set<number>>(new Set());

  if (!data || data.length === 0) {
    return (
      <div className="chat-action-result">
        <p className="chat-result-empty">No results found.</p>
      </div>
    );
  }

  const displayed = data.slice(0, 50);
  const allSelected = displayed.length > 0 && displayed.every((_, i) => selected.has(i));
  const someSelected = displayed.some((_, i) => selected.has(i));

  function toggleAll() {
    if (allSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(displayed.map((_, i) => i)));
    }
  }

  function toggleOne(i: number) {
    const next = new Set(selected);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setSelected(next);
  }

  function getSelected() {
    return displayed.filter((_, i) => selected.has(i));
  }

  function handleDownload() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const header = "source,name,ra,dec,type,magnitude,redshift";
    const csv = [
      header,
      ...rows.map((r) =>
        [
          r.source,
          `"${String(r.name || "").replace(/"/g, '""')}"`,
          r.ra,
          r.dec,
          r.object_type || "",
          r.magnitude ?? "",
          r.redshift ?? "",
        ].join(",")
      ),
    ].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `astro_chat_results_${rows.length}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleVisualize() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const vizData = {
      ra: rows.map((r) => r.ra),
      dec: rows.map((r) => r.dec),
      magnitude: rows.filter((r) => r.magnitude != null).map((r) => r.magnitude),
      redshift: rows.filter((r) => r.redshift != null).map((r) => r.redshift),
      names: rows.map((r) => r.name),
      sources: rows.map((r) => r.source),
    };
    // Open in new tab with data in sessionStorage
    sessionStorage.setItem("astro_viz_data", JSON.stringify(vizData));
    window.open("/?viz=1", "_blank");
  }

  return (
    <div className="chat-action-result">
      {selected.size > 0 && (
        <div className="chat-result-actions">
          <span className="chat-result-count">{selected.size} selected</span>
          <button className="btn-chat-action" onClick={handleDownload}>
            Download CSV
          </button>
          <button className="btn-chat-action" onClick={handleVisualize}>
            Visualize
          </button>
          <button className="btn-chat-action" onClick={() => setSelected(new Set())}>
            Clear
          </button>
        </div>
      )}
      <table className="chat-result-table">
        <thead>
          <tr>
            <th className="th-check">
              <input
                type="checkbox"
                checked={allSelected}
                ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                onChange={toggleAll}
                className="row-checkbox"
              />
            </th>
            <th>Source</th>
            <th>Name</th>
            <th>RA</th>
            <th>Dec</th>
            <th>Type</th>
            <th>Mag</th>
          </tr>
        </thead>
        <tbody>
          {displayed.map((row, i) => (
            <tr key={i} className={selected.has(i) ? "row-selected" : ""}>
              <td className="td-check">
                <input
                  type="checkbox"
                  checked={selected.has(i)}
                  onChange={() => toggleOne(i)}
                  className="row-checkbox"
                />
              </td>
              <td>
                <span className="source-chip">{row.source as string}</span>
              </td>
              <td>{row.name as string}</td>
              <td>{(row.ra as number)?.toFixed(4)}</td>
              <td>{(row.dec as number)?.toFixed(4)}</td>
              <td>{(row.object_type as string) || "—"}</td>
              <td>
                {row.magnitude != null
                  ? (row.magnitude as number).toFixed(2)
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.length > 50 && (
        <p className="chat-result-more">
          Showing 50 of {data.length} results
        </p>
      )}
    </div>
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
        <table className="chat-result-table">
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col}>{col}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: numRows }).map((_, rowIdx) => (
              <tr key={rowIdx}>
                {columns.map((col) => (
                  <td key={col}>
                    {tableData[col]?.[rowIdx] != null
                      ? String(tableData[col][rowIdx])
                      : "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
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

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    const key = keyInput.trim();
    if (!key) return;
    try {
      const keys = JSON.parse(localStorage.getItem("astro_api_keys") || "{}");
      keys.anthropic = key;
      localStorage.setItem("astro_api_keys", JSON.stringify(keys));
    } catch {
      localStorage.setItem("astro_api_keys", JSON.stringify({ anthropic: key }));
    }
    onSaved();
  }

  return (
    <div className="chat-apikey-prompt">
      <h3>Configure API Key</h3>
      <p>To use the AI assistant, enter your Anthropic API key.</p>
      <p className="chat-apikey-hint">
        Get one at{" "}
        <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noopener noreferrer">
          console.anthropic.com
        </a>
        {" "}(new accounts get $5 free credit)
      </p>
      <form className="chat-apikey-form" onSubmit={handleSave}>
        <input
          type="text"
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          placeholder="sk-ant-..."
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

export default function ChatPage() {
  const [hasKey, setHasKey] = useState(() => !!getStoredApiKey("anthropic"));
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [executingActions, setExecutingActions] = useState<Set<string>>(
    new Set()
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading, scrollToBottom]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;

    const userMsg: DisplayMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };

    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInput("");
    setLoading(true);

    try {
      const chatHistory: ChatMessage[] = updatedMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const response = await sendChatMessage(chatHistory);

      const assistantMsg: DisplayMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: response.reply,
        actions:
          response.actions.length > 0 ? response.actions : undefined,
        actionResults: new Map(),
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err: unknown) {
      const errorDetail =
        err instanceof Error ? err.message : "Unknown error";
      const errorMsg: DisplayMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Sorry, I encountered an error: ${errorDetail}. Please try again.`,
      };
      setMessages((prev) => [...prev, errorMsg]);
    } finally {
      setLoading(false);
    }
  };

  const handleExecuteAction = async (
    msgId: string,
    actionIndex: number,
    action: ChatAction
  ) => {
    const key = `${msgId}-${actionIndex}`;
    setExecutingActions((prev) => new Set(prev).add(key));

    try {
      const result = await executeChatAction(
        action as Record<string, unknown>
      );
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

  return (
    <div className="chat-page">
      <div className="chat-header">
        <h2>AI Research Assistant</h2>
        <p>
          Ask about astronomical objects, build pipelines, or run ADQL queries
        </p>
      </div>

      <div className="chat-messages">
        {!hasKey && (
          <ApiKeyPrompt onSaved={() => setHasKey(true)} />
        )}
        {hasKey && messages.length === 0 && !loading && (
          <div className="chat-empty">
            <div className="chat-empty-icon">&#x2728;</div>
            <h3>How can I help with your research?</h3>
            <div className="chat-suggestions">
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput("Search for quasars near RA=180, Dec=45")
                }
              >
                Search for quasars near RA=180, Dec=45
              </button>
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput(
                    "Write an ADQL query to find bright stars in Gaia DR3"
                  )
                }
              >
                Write an ADQL query for bright Gaia stars
              </button>
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput("How do I denoise a spectrum and fit emission lines?")
                }
              >
                How to denoise a spectrum and fit lines?
              </button>
              <button
                className="chat-suggestion"
                onClick={() =>
                  setInput("Explain the difference between ICRS and Galactic coordinates")
                }
              >
                Explain ICRS vs Galactic coordinates
              </button>
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
                {msg.content.split("\n").map((line, i) => (
                  <p key={i}>{line || "\u00A0"}</p>
                ))}
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

      <div className="chat-input-area">
        <div className="chat-input-wrapper">
          <textarea
            ref={inputRef}
            className="chat-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about astronomical data, pipelines, or ADQL queries..."
            rows={1}
            disabled={loading}
          />
          <button
            className="btn-chat-send"
            onClick={handleSend}
            disabled={!input.trim() || loading}
            title="Send message (Enter)"
          >
            &#x2191;
          </button>
        </div>
        <span className="chat-input-hint">
          Press Enter to send, Shift+Enter for new line
        </span>
      </div>
    </div>
  );
}
