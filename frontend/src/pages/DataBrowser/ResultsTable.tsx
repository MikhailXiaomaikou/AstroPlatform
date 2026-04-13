import { useState, useMemo, useCallback, useEffect, useRef } from "react";
import type { SearchResult } from "../../api/client";
import { useI18n } from "../../i18n";
import { buildPipelineDraft, findWorkspaceFile } from "../../utils/workspaceCache";

interface Props {
  results: SearchResult[];
  onFetch: (source: string, objectId: string) => void;
  fetchingId: string | null;
  loading?: boolean;
  searched?: boolean;
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  onObjectClick?: (name: string, ra: number, dec: number) => void;
}

const PAGE_SIZE = 25;
const CORE_COLUMNS = ["source", "name", "ra", "dec", "type", "magnitude", "redshift"] as const;
const STORAGE_KEY = "astro_column_visibility";

type SortKey = "source" | "name" | "ra" | "dec" | "object_type" | "magnitude" | "redshift";

function resultKey(r: SearchResult, i: number): string {
  return `${r.source}-${r.object_id}-${i}`;
}

function SkeletonRows({ colCount }: { colCount: number }) {
  return (
    <>
      {Array.from({ length: 5 }).map((_, i) => (
        <tr key={i}>
          <td colSpan={colCount}>
            <div className="skeleton skeleton-row" />
          </td>
        </tr>
      ))}
    </>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 15.803a7.5 7.5 0 0010.607 0z"
        />
      </svg>
      <p>{message}</p>
    </div>
  );
}

function compareFn(key: SortKey, a: SearchResult, b: SearchResult): number {
  const va = a[key];
  const vb = b[key];
  if (va == null && vb == null) return 0;
  if (va == null) return 1;
  if (vb == null) return -1;
  if (typeof va === "number" && typeof vb === "number") return va - vb;
  return String(va).localeCompare(String(vb));
}

export default function ResultsTable({
  results,
  onFetch,
  fetchingId,
  loading,
  searched,
  selectedKeys,
  onSelectionChange,
  onObjectClick,
}: Props) {
  const { t } = useI18n();
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortAsc, setSortAsc] = useState(true);
  const [page, setPage] = useState(0);
  const [showColumnHelp, setShowColumnHelp] = useState(false);

  // ── Column visibility ──
  const [visibleColumns, setVisibleColumns] = useState<Set<string>>(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try { return new Set(JSON.parse(saved) as string[]); } catch { /* ignore */ }
    }
    return new Set<string>(CORE_COLUMNS);
  });
  const [showColumnPanel, setShowColumnPanel] = useState(false);
  const columnPanelRef = useRef<HTMLDivElement>(null);

  const extraColumns = useMemo(() => {
    const keys = new Set<string>();
    for (const obj of results) {
      if (obj.extra) {
        for (const k of Object.keys(obj.extra)) keys.add(k);
      }
    }
    return Array.from(keys).sort();
  }, [results]);

  const allColumns = useMemo(
    () => [...CORE_COLUMNS, ...extraColumns],
    [extraColumns],
  );

  const persistVisibility = useCallback((next: Set<string>) => {
    setVisibleColumns(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(next)));
  }, []);

  const toggleColumn = useCallback((col: string) => {
    const next = new Set(visibleColumns);
    if (next.has(col)) { next.delete(col); } else { next.add(col); }
    persistVisibility(next);
  }, [visibleColumns, persistVisibility]);

  const selectAllColumns = useCallback(() => {
    persistVisibility(new Set(allColumns));
  }, [allColumns, persistVisibility]);

  const deselectAllColumns = useCallback(() => {
    persistVisibility(new Set<string>());
  }, [persistVisibility]);

  // Close panel when clicking outside
  useEffect(() => {
    if (!showColumnPanel) return;
    function handleClick(e: MouseEvent) {
      if (columnPanelRef.current && !columnPanelRef.current.contains(e.target as Node)) {
        setShowColumnPanel(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [showColumnPanel]);

  const sorted = useMemo(() => {
    if (!sortKey) return results;
    const copy = [...results];
    copy.sort((a, b) => {
      const c = compareFn(sortKey, a, b);
      return sortAsc ? c : -c;
    });
    return copy;
  }, [results, sortKey, sortAsc]);

  const totalPages = Math.ceil(sorted.length / PAGE_SIZE);
  const displayed = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Reset page when results change
  useMemo(() => { setPage(0); }, [results]);

  if (!loading && results.length === 0) {
    if (searched) {
      return <EmptyState message={t("search.no_results")} />;
    }
    return <EmptyState message={t("search.empty")} />;
  }

  const allKeys = displayed.map((r, i) => resultKey(r, page * PAGE_SIZE + i));
  const allSelected = allKeys.length > 0 && allKeys.every((k) => selectedKeys?.has(k));
  const someSelected = allKeys.some((k) => selectedKeys?.has(k));

  function toggleAll() {
    if (!onSelectionChange) return;
    if (allSelected) {
      const next = new Set(selectedKeys);
      for (const k of allKeys) next.delete(k);
      onSelectionChange(next);
    } else {
      const next = new Set(selectedKeys);
      for (const k of allKeys) next.add(k);
      onSelectionChange(next);
    }
  }

  function toggleOne(key: string) {
    if (!onSelectionChange || !selectedKeys) return;
    const next = new Set(selectedKeys);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    onSelectionChange(next);
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  }

  function sortIndicator(key: SortKey) {
    if (sortKey !== key) return null;
    return <span className="sort-indicator">{sortAsc ? " \u25B2" : " \u25BC"}</span>;
  }

  // Core columns visible + extra columns visible + actions column + optional checkbox
  const visibleCoreCount = CORE_COLUMNS.filter((c) => visibleColumns.has(c)).length;
  const visibleExtraCount = extraColumns.filter((c) => visibleColumns.has(c)).length;
  const colCount = (onSelectionChange ? 1 : 0) + visibleCoreCount + visibleExtraCount + 1;

  return (
    <>
      <div className="column-controls-row">
        <div className="column-explainer">
          <button
            type="button"
            className="column-explainer-toggle"
            onClick={() => setShowColumnHelp(!showColumnHelp)}
          >
            {t("columns.explainer_toggle")}
            <svg
              className={`chevron${showColumnHelp ? " open" : ""}`}
              viewBox="0 0 20 20"
              fill="currentColor"
              width="14"
              height="14"
            >
              <path
                fillRule="evenodd"
                d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z"
                clipRule="evenodd"
              />
            </svg>
          </button>
          {showColumnHelp && (
            <div className="column-explainer-content">
              <dl className="column-explainer-list">
                <div><dt>{t("data.name")}</dt><dd>{t("columns.name_desc")}</dd></div>
                <div><dt>RA / Dec</dt><dd>{t("columns.ra_desc")}</dd></div>
                <div><dt>{t("data.magnitude")}</dt><dd>{t("columns.mag_desc")}</dd></div>
                <div><dt>{t("data.redshift")}</dt><dd>{t("columns.redshift_desc")}</dd></div>
                <div><dt>{t("data.type")}</dt><dd>{t("columns.type_desc")}</dd></div>
                <div><dt>{t("data.source")}</dt><dd>{t("columns.source_desc")}</dd></div>
              </dl>
            </div>
          )}
        </div>

        {/* Column visibility toggle */}
        <div className="column-panel-anchor" ref={columnPanelRef}>
          <button
            type="button"
            className="btn-column-toggle"
            onClick={() => setShowColumnPanel(!showColumnPanel)}
            title="Toggle columns"
          >
            <svg viewBox="0 0 20 20" fill="currentColor" width="14" height="14">
              <path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.062 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd" />
            </svg>
            Columns
          </button>
          {showColumnPanel && (
            <div className="column-panel">
              <div className="column-panel-header">
                <button type="button" className="btn-small btn-secondary" onClick={selectAllColumns}>All</button>
                <button type="button" className="btn-small btn-secondary" onClick={deselectAllColumns}>None</button>
              </div>
              <div className="column-panel-group">Core</div>
              {CORE_COLUMNS.map((col) => (
                <label key={col}>
                  <input type="checkbox" checked={visibleColumns.has(col)} onChange={() => toggleColumn(col)} />
                  {col === "type" ? t("data.type") : col === "magnitude" ? t("data.magnitude") : col === "redshift" ? t("data.redshift") : col === "source" ? t("data.source") : col === "name" ? t("data.name") : col.toUpperCase()}
                </label>
              ))}
              {extraColumns.length > 0 && (
                <>
                  <div className="column-panel-group">Extra</div>
                  {extraColumns.map((col) => (
                    <label key={col}>
                      <input type="checkbox" checked={visibleColumns.has(col)} onChange={() => toggleColumn(col)} />
                      {col}
                    </label>
                  ))}
                </>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="results-table-wrap">
        <table className="results-table">
          <colgroup>
            {onSelectionChange && <col className="col-check" />}
            {visibleColumns.has("source") && <col className="col-source" />}
            {visibleColumns.has("name") && <col className="col-name" />}
            {visibleColumns.has("ra") && <col className="col-ra" />}
            {visibleColumns.has("dec") && <col className="col-dec" />}
            {visibleColumns.has("type") && <col className="col-type" />}
            {visibleColumns.has("magnitude") && <col className="col-mag" />}
            {visibleColumns.has("redshift") && <col className="col-redshift" />}
            {extraColumns.filter((c) => visibleColumns.has(c)).map((c) => (
              <col key={c} className="col-extra" />
            ))}
            <col className="col-action" />
          </colgroup>
          <thead>
            <tr>
              {onSelectionChange && (
                <th className="th-check">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => { if (el) el.indeterminate = someSelected && !allSelected; }}
                    onChange={toggleAll}
                    className="row-checkbox"
                  />
                </th>
              )}
              {visibleColumns.has("source") && <th className="th-sortable" onClick={() => handleSort("source")}>{t("data.source")}{sortIndicator("source")}</th>}
              {visibleColumns.has("name") && <th className="th-sortable" onClick={() => handleSort("name")}>{t("data.name")}{sortIndicator("name")}</th>}
              {visibleColumns.has("ra") && <th className="th-sortable" onClick={() => handleSort("ra")}>RA (&deg;){sortIndicator("ra")}</th>}
              {visibleColumns.has("dec") && <th className="th-sortable" onClick={() => handleSort("dec")}>Dec (&deg;){sortIndicator("dec")}</th>}
              {visibleColumns.has("type") && <th className="th-sortable" onClick={() => handleSort("object_type")}>{t("data.type")}{sortIndicator("object_type")}</th>}
              {visibleColumns.has("magnitude") && <th className="th-sortable" onClick={() => handleSort("magnitude")}>{t("data.magnitude")}{sortIndicator("magnitude")}</th>}
              {visibleColumns.has("redshift") && <th className="th-sortable" onClick={() => handleSort("redshift")}>{t("data.redshift")}{sortIndicator("redshift")}</th>}
              {extraColumns.filter((c) => visibleColumns.has(c)).map((c) => (
                <th key={c}>{c}</th>
              ))}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <SkeletonRows colCount={colCount} />
            ) : (
              displayed.map((r, i) => {
                const globalIdx = page * PAGE_SIZE + i;
                const key = resultKey(r, globalIdx);
                const isFetching = fetchingId === `${r.source}-${r.object_id}`;
                const isSelected = selectedKeys?.has(key) ?? false;
                return (
                  <tr key={key} className={isSelected ? "row-selected" : ""}>
                    {onSelectionChange && (
                      <td className="td-check">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleOne(key)}
                          className="row-checkbox"
                        />
                      </td>
                    )}
                    {visibleColumns.has("source") && (
                      <td>
                        <span className={`badge badge-${r.source}`}>
                          {r.source.toUpperCase()}
                        </span>
                      </td>
                    )}
                    {visibleColumns.has("name") && (
                      <td className="name-cell" title={r.object_id}>
                        {onObjectClick ? (
                          <button className="name-link" onClick={() => onObjectClick(r.name, r.ra, r.dec)}>
                            {r.name}
                          </button>
                        ) : r.name}
                      </td>
                    )}
                    {visibleColumns.has("ra") && <td>{r.ra.toFixed(5)}</td>}
                    {visibleColumns.has("dec") && <td>{r.dec.toFixed(5)}</td>}
                    {visibleColumns.has("type") && <td>{r.object_type || "\u2014"}</td>}
                    {visibleColumns.has("magnitude") && <td>{r.magnitude?.toFixed(2) ?? "\u2014"}</td>}
                    {visibleColumns.has("redshift") && (
                      <td>
                        {r.redshift != null ? (
                          <>
                            {r.redshift.toFixed(4)}
                            {r.z_source === "spectroscopic" && (
                              <span className="badge badge-spec" style={{ marginLeft: 4, fontSize: "0.7em", padding: "1px 4px", borderRadius: 3, background: "rgba(59,130,246,0.18)", color: "var(--color-blue, #3b82f6)" }}>spec</span>
                            )}
                            {r.z_source === "photometric" && (
                              <span
                                className="badge badge-phot"
                                title={r.photo_z_err != null ? `\u00b1${r.photo_z_err.toFixed(4)}` : "photometric estimate"}
                                style={{ marginLeft: 4, fontSize: "0.7em", padding: "1px 4px", borderRadius: 3, background: "rgba(249,115,22,0.18)", color: "var(--color-orange, #f97316)", cursor: "help" }}
                              >phot</span>
                            )}
                          </>
                        ) : "\u2014"}
                      </td>
                    )}
                    {extraColumns.filter((c) => visibleColumns.has(c)).map((c) => (
                      <td key={c}>{r.extra?.[c] != null ? String(r.extra[c]) : "\u2014"}</td>
                    ))}
                    <td>
                      {r.object_id !== "error" && (
                        <div style={{ display: "flex", gap: 3 }}>
                          <button
                            className="btn-fetch"
                            disabled={isFetching}
                            onClick={() => onFetch(r.source, r.object_id)}
                          >
                            {isFetching ? (
                              <span className="spinner spinner-blue" />
                            ) : (
                              t("data.fetch_fits")
                            )}
                          </button>
                          <button
                            className="btn-fetch"
                            style={{ background: "rgba(48,209,88,0.15)", color: "var(--color-green)" }}
                            onClick={() => {
                              const inputDataId = findWorkspaceFile(r.source, r.object_id)?.fits_path || `${r.source}/${r.object_id}`;
                              localStorage.setItem("pipeline_autosave", JSON.stringify(buildPipelineDraft(inputDataId)));
                              window.location.href = "/pipeline";
                            }}
                            title="Open in Pipeline Editor"
                          >
                            Pipeline
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="results-pagination">
          <button className="btn-secondary btn-small" disabled={page === 0} onClick={() => setPage(page - 1)}>
            {t("common.previous")}
          </button>
          <span className="results-page-info">
            {page + 1} / {totalPages} ({sorted.length} {t("search.results")})
          </span>
          <button className="btn-secondary btn-small" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>
            {t("common.next")}
          </button>
        </div>
      )}
    </>
  );
}
