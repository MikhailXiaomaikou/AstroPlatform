import { useState, useMemo } from "react";
import type { SearchResult } from "../../api/client";
import { useI18n } from "../../i18n";

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

  const colCount = onSelectionChange ? 9 : 8;

  return (
    <>
      <div className="results-table-wrap">
        <table className="results-table">
          <colgroup>
            {onSelectionChange && <col className="col-check" />}
            <col className="col-source" />
            <col className="col-name" />
            <col className="col-ra" />
            <col className="col-dec" />
            <col className="col-type" />
            <col className="col-mag" />
            <col className="col-redshift" />
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
              <th className="th-sortable" onClick={() => handleSort("source")}>{t("data.source")}{sortIndicator("source")}</th>
              <th className="th-sortable" onClick={() => handleSort("name")}>{t("data.name")}{sortIndicator("name")}</th>
              <th className="th-sortable" onClick={() => handleSort("ra")}>RA (&deg;){sortIndicator("ra")}</th>
              <th className="th-sortable" onClick={() => handleSort("dec")}>Dec (&deg;){sortIndicator("dec")}</th>
              <th className="th-sortable" onClick={() => handleSort("object_type")}>{t("data.type")}{sortIndicator("object_type")}</th>
              <th className="th-sortable" onClick={() => handleSort("magnitude")}>{t("data.magnitude")}{sortIndicator("magnitude")}</th>
              <th className="th-sortable" onClick={() => handleSort("redshift")}>{t("data.redshift")}{sortIndicator("redshift")}</th>
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
                    <td>
                      <span className={`badge badge-${r.source}`}>
                        {r.source.toUpperCase()}
                      </span>
                    </td>
                    <td className="name-cell" title={r.object_id}>
                      {onObjectClick ? (
                        <button className="name-link" onClick={() => onObjectClick(r.name, r.ra, r.dec)}>
                          {r.name}
                        </button>
                      ) : r.name}
                    </td>
                    <td>{r.ra.toFixed(5)}</td>
                    <td>{r.dec.toFixed(5)}</td>
                    <td>{r.object_type || "\u2014"}</td>
                    <td>{r.magnitude?.toFixed(2) ?? "\u2014"}</td>
                    <td>{r.redshift?.toFixed(4) ?? "\u2014"}</td>
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
                              const dag = {
                                nodes: [
                                  { id: "n1", type: "LoadData", position: { x: 0, y: 150 }, data: { label: "Load Data", params: { fits_path: `${r.source}/${r.object_id}` }, nodeType: "LoadData" } },
                                  { id: "n2", type: "Denoise", position: { x: 300, y: 150 }, data: { label: "Denoise", params: { sigma: 3 }, nodeType: "Denoise" } },
                                  { id: "n3", type: "InteractivePlot", position: { x: 600, y: 150 }, data: { label: "Plot", params: {}, nodeType: "InteractivePlot" } },
                                ],
                                edges: [
                                  { id: "e1-2", source: "n1", target: "n2" },
                                  { id: "e2-3", source: "n2", target: "n3" },
                                ],
                                inputDataId: `${r.source}/${r.object_id}`,
                              };
                              localStorage.setItem("pipeline_autosave", JSON.stringify(dag));
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
