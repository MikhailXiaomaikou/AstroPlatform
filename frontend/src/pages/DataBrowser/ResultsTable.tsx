import type { SearchResult } from "../../api/client";

interface Props {
  results: SearchResult[];
  onFetch: (source: string, objectId: string) => void;
  fetchingId: string | null;
  loading?: boolean;
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
}

function resultKey(r: SearchResult, i: number): string {
  return `${r.source}-${r.object_id}-${i}`;
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 5 }).map((_, i) => (
        <tr key={i}>
          <td colSpan={9}>
            <div className="skeleton skeleton-row" />
          </td>
        </tr>
      ))}
    </>
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 15.803a7.5 7.5 0 0010.607 0z"
        />
      </svg>
      <p>Search for astronomical objects to see results</p>
    </div>
  );
}

export default function ResultsTable({
  results,
  onFetch,
  fetchingId,
  loading,
  selectedKeys,
  onSelectionChange,
}: Props) {
  if (!loading && results.length === 0) return <EmptyState />;

  const allKeys = results.map((r, i) => resultKey(r, i));
  const allSelected = allKeys.length > 0 && allKeys.every((k) => selectedKeys?.has(k));
  const someSelected = allKeys.some((k) => selectedKeys?.has(k));

  function toggleAll() {
    if (!onSelectionChange) return;
    if (allSelected) {
      onSelectionChange(new Set());
    } else {
      onSelectionChange(new Set(allKeys));
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

  return (
    <div className="results-table-wrap">
      <table className="results-table">
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
            <th>Source</th>
            <th>Name</th>
            <th>RA (°)</th>
            <th>Dec (°)</th>
            <th>Type</th>
            <th>Mag</th>
            <th>Redshift</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <SkeletonRows />
          ) : (
            results.map((r, i) => {
              const key = resultKey(r, i);
              const isFetching = fetchingId === key;
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
                    {r.name}
                  </td>
                  <td>{r.ra.toFixed(5)}</td>
                  <td>{r.dec.toFixed(5)}</td>
                  <td>{r.object_type || "—"}</td>
                  <td>{r.magnitude?.toFixed(2) ?? "—"}</td>
                  <td>{r.redshift?.toFixed(4) ?? "—"}</td>
                  <td>
                    {r.object_id !== "error" && (
                      <button
                        className="btn-fetch"
                        disabled={isFetching}
                        onClick={() => onFetch(r.source, r.object_id)}
                      >
                        {isFetching ? (
                          <span className="spinner spinner-blue" />
                        ) : (
                          "Fetch FITS"
                        )}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
