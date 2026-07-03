// Auto-executed tool result renderer: one formatted card per tool name,
// plus the figure modal / line-relation plot helpers it uses.
// Moved verbatim from ChatPage.tsx (behavior-preserving split).
import { useState } from "react";
import CosmologyMCMCPanel from "../../components/chat/CosmologyMCMCPanel";
import CosmologyLikelihoodPanel from "../../components/chat/CosmologyLikelihoodPanel";
import ResearchProgramPanel from "../../components/chat/ResearchProgramPanel";
import DefaultToolResultPanel from "../../components/chat/DefaultToolResultPanel";

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

export function AutoToolResult({ toolName, result }: { toolName: string; result: Record<string, unknown> }) {
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
    // X4 (PART X): Prominent non-collapsible banner for radius auto-shrink. Fixes B6 Pleiades
    // radius 0.75 deg -> 0.375 deg silently reducing without any warning.
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
    // Stage 5 (2026-05-19): inline link-chip helper. backend `_build_paper_links`
    // returns optional fields {pdf_url, arxiv_url, doi_url, ads_url}; render
    // any present field as a small chip the user can click directly.
    const renderLinkChip = (
      url: string | undefined,
      label: string,
      icon: string,
    ) => {
      if (!url) return null;
      return (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 3,
            padding: "1px 6px",
            marginRight: 4,
            fontSize: "0.68rem",
            borderRadius: 3,
            border: "1px solid var(--color-border)",
            color: "var(--color-text-secondary)",
            textDecoration: "none",
            background: "var(--color-bg-secondary, transparent)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {icon} {label}
        </a>
      );
    };
    return (
      <div>
        {refs.slice(0, 8).map((r, i) => {
          const adsUrl = (r.ads_url as string | undefined)
            || (r.bibcode ? `https://ui.adsabs.harvard.edu/abs/${String(r.bibcode)}` : undefined);
          const pdfUrl = r.pdf_url as string | undefined;
          const arxivUrl = r.arxiv_url as string | undefined;
          const doiUrl = r.doi_url as string | undefined;
          // Stage 6 P0c-B (2026-05-19): ADS RETRACTED flag — grey out the entire paper entry and show a red banner
          const isRetracted = Boolean(r.retracted);
          return (
            <div
              key={i}
              style={{
                fontSize: "0.75rem",
                padding: "4px 0",
                borderBottom: "1px solid var(--color-border)",
                opacity: isRetracted ? 0.55 : 1,
              }}
            >
              {isRetracted ? (
                <div
                  style={{
                    display: "inline-block",
                    background: "#b00020",
                    color: "white",
                    fontWeight: 700,
                    fontSize: "0.7rem",
                    padding: "1px 6px",
                    borderRadius: 3,
                    marginBottom: 3,
                    letterSpacing: "0.04em",
                  }}
                >
                  🚫 RETRACTED — DO NOT CITE
                </div>
              ) : null}
              <div>
                <a href={adsUrl} target="_blank" rel="noopener noreferrer"
                  style={{
                    color: isRetracted ? "var(--color-text-tertiary)" : "var(--color-accent)",
                    textDecoration: isRetracted ? "line-through" : "none",
                  }}>
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
              <div style={{ marginTop: 4 }}>
                {renderLinkChip(pdfUrl, "PDF", "📄")}
                {renderLinkChip(arxivUrl, "arXiv", "🅰")}
                {renderLinkChip(adsUrl, "ADS", "📚")}
                {renderLinkChip(doiUrl, "DOI", "🔗")}
              </div>
            </div>
          );
        })}
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
    || toolName === "run_cmb_rotation_likelihood"
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
    || toolName === "load_cosmology_data_product"
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
    // R6 post: stderr is a top-level field (present even when empty); stderr_note is set
    // when "stderr is empty but exit code is non-zero", indicating the subprocess crashed
    // during Python startup — check /api/admin/sandbox/health for the real Python error.
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

        {/* stderr / warnings should also be shown on success; warnings.warn and
            print(..., file=sys.stderr) do not mean the tool failed, but they are
            critical for user debugging. */}
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

  // Default fallback: tool name not matched by any specialised panel above.
  // Replaces an old 500-char JSON pre dump with a structured card that
  // shows toolName, status chip, message, warnings, and a collapsible
  // raw payload. Keeps unknown / new tools from rendering as a wall of
  // unstructured text. See components/chat/DefaultToolResultPanel.tsx.
  return <DefaultToolResultPanel toolName={toolName} result={result} />;
}
