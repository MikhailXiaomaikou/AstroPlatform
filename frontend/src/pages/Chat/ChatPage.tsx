import { useState, useMemo, useRef, useEffect, useCallback, lazy, Suspense } from "react";
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
    arxiv: "Extract arXiv tables",
    run_pipeline: "Run pipeline",
    explain: "Explanation",
    plot: "Interactive Plot",
  };

  const icons: Record<string, string> = {
    search: "🔍",
    arxiv: "📄",
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
        {action.action === "arxiv" && (
          <span>Paper: {action.arxiv_id as string}</span>
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
  const [vizData, setVizData] = useState<Record<string, unknown> | null>(null);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 15;

  if (!data || data.length === 0) {
    return (
      <div className="chat-action-result">
        <p className="chat-result-empty">No results found.</p>
      </div>
    );
  }

  // Deduplicate by name+source
  const unique = useMemo(() => {
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

  // Dynamically discover extra columns that have data
  const extraCols = useMemo(() => {
    const colSet = new Set<string>();
    for (const row of unique) {
      const extra = row.extra as Record<string, unknown> | undefined;
      if (extra) {
        for (const k of Object.keys(extra)) {
          if (extra[k] != null) colSet.add(k);
        }
      }
    }
    return Array.from(colSet).sort();
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

  function handleDownload() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const cols = ["source", "name", "ra", "dec", "object_type", "magnitude", "redshift", ...extraCols];
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
  }

  function handleDownloadVOTable() {
    const rows = getSelected();
    if (rows.length === 0) return;
    const cols = ["source", "name", "ra", "dec", "object_type", "magnitude", "redshift", ...extraCols];
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
          <button className="btn-chat-action" onClick={() => setSelected(new Set())}>Clear</button>
        </div>
      )}
      <div className="chat-result-table-scroll">
        <table className="chat-result-table">
          <thead>
            <tr>
              <th className="th-check">
                <input type="checkbox" checked={allSelected}
                  ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                  onChange={toggleAll} className="row-checkbox" />
              </th>
              <th>Source</th>
              <th>Name</th>
              <th>RA</th>
              <th>Dec</th>
              <th>Type</th>
              <th>Mag</th>
              <th>z</th>
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
                  <td><span className="source-chip">{row.source as string}</span></td>
                  <td>{row.name as string}</td>
                  <td>{(row.ra as number)?.toFixed(5)}</td>
                  <td>{(row.dec as number)?.toFixed(5)}</td>
                  <td>{(row.object_type as string) || "—"}</td>
                  <td>{row.magnitude != null ? (row.magnitude as number).toFixed(2) : "—"}</td>
                  <td>{row.redshift != null ? (row.redshift as number).toFixed(4) : "—"}</td>
                  {extraCols.map((c) => (
                    <td key={c}>{fmt(extra[c])}</td>
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
            <Suspense fallback={<div className="fits-loading">Loading visualization...</div>}>
              <PlotBuilder
                initialData={vizData}
                initialChartType="sky_coverage"
                onClose={() => setVizData(null)}
              />
            </Suspense>
          </div>
        </div>
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

interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: ChatAction[];
  actionResults?: [number, Record<string, unknown>][];
}

function loadChatHistory(): DisplayMessage[] {
  try {
    const raw = localStorage.getItem("astro_chat_history");
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

function saveChatHistory(messages: DisplayMessage[]) {
  try {
    const stored: StoredMessage[] = messages.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      actions: m.actions,
      actionResults: m.actionResults ? Array.from(m.actionResults.entries()) : undefined,
    }));
    localStorage.setItem("astro_chat_history", JSON.stringify(stored));
  } catch {
    // storage full or unavailable — silently ignore
  }
}

export default function ChatPage() {
  const [hasKey, setHasKey] = useState(() => !!getStoredApiKey("anthropic"));
  const [messages, setMessages] = useState<DisplayMessage[]>(loadChatHistory);
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

  useEffect(() => {
    saveChatHistory(messages);
  }, [messages]);

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
      let errorDetail = "Unknown error";
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response?: { data?: { detail?: string }; status?: number } }).response;
        errorDetail = resp?.data?.detail || `Request failed (${resp?.status})`;
        // If auth error, prompt to fix key
        if (resp?.status === 401) {
          setHasKey(false);
        }
      } else if (err instanceof Error) {
        errorDetail = err.message;
      }
      const errorMsg: DisplayMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: `Sorry, I encountered an error: ${errorDetail}`,
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
        <div className="chat-header-row">
          <div>
            <h2>AI Research Assistant</h2>
            <p>
              Ask about astronomical objects, build pipelines, or run ADQL queries
            </p>
          </div>
          {messages.length > 0 && (
            <button
              className="btn-secondary btn-small"
              onClick={() => {
                setMessages([]);
                localStorage.removeItem("astro_chat_history");
              }}
            >
              Clear Chat
            </button>
          )}
        </div>
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
