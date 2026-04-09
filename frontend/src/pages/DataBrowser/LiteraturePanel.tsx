import { useState, useCallback } from "react";
import { searchLiterature } from "../../api/client";
import type { LiteratureResult } from "../../api/client";
import { useI18n } from "../../i18n";

function adsLink(bibcode: string): string {
  if (bibcode.startsWith("arXiv:")) {
    const id = bibcode.replace("arXiv:", "");
    return `https://arxiv.org/abs/${id}`;
  }
  return `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(bibcode)}`;
}

function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + "\u2026";
}

export default function LiteraturePanel() {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LiteratureResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [source, setSource] = useState<string>("");
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const handleSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setExpandedIdx(null);
    try {
      const resp = await searchLiterature(q);
      setResults(resp.results);
      setSource(resp.source);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Literature search failed";
      setError(msg);
      setResults([]);
    } finally {
      setLoading(false);
      setSearched(true);
    }
  }, [query]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSearch();
      }
    },
    [handleSearch]
  );

  return (
    <div className="literature-panel">
      <div className="literature-search-bar">
        <input
          type="text"
          className="literature-input"
          placeholder={t("data.lit_placeholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={loading}
        />
        <button
          className="btn-primary"
          onClick={handleSearch}
          disabled={loading || !query.trim()}
        >
          {loading ? (
            <><span className="spinner" /> Searching...</>
          ) : (
            t("data.lit_search")
          )}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {searched && results.length === 0 && !loading && !error && (
        <div className="empty-state">
          <p>No papers found. Try different keywords.</p>
        </div>
      )}

      {results.length > 0 && (
        <div className="literature-results">
          <div className="literature-results-header">
            <span>{results.length} papers from {source.toUpperCase()}</span>
          </div>
          {results.map((paper, idx) => {
            const isExpanded = expandedIdx === idx;
            return (
              <div key={paper.bibcode || idx} className="literature-card">
                <div className="literature-card-header">
                  <a
                    href={adsLink(paper.bibcode)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="literature-title-link"
                  >
                    {paper.title || "(untitled)"}
                  </a>
                  <span className="literature-year">{paper.year}</span>
                </div>
                <div className="literature-authors">
                  {paper.authors.slice(0, 5).join(", ")}
                  {paper.authors.length > 5 && ` +${paper.authors.length - 5} more`}
                </div>
                {paper.pub && (
                  <span className="literature-pub">{paper.pub}</span>
                )}
                {paper.abstract && (
                  <div className="literature-abstract">
                    {isExpanded ? paper.abstract : truncate(paper.abstract, 200)}
                    {paper.abstract.length > 200 && (
                      <button
                        className="btn-link literature-toggle"
                        onClick={() => setExpandedIdx(isExpanded ? null : idx)}
                      >
                        {isExpanded ? "Show less" : "Show more"}
                      </button>
                    )}
                  </div>
                )}
                <div className="literature-actions">
                  <a
                    href={adsLink(paper.bibcode)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-secondary btn-small"
                  >
                    {paper.bibcode.startsWith("arXiv:") ? "arXiv" : "ADS"}
                  </a>
                  {paper.doi && (
                    <a
                      href={`https://doi.org/${paper.doi}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn-secondary btn-small"
                    >
                      DOI
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {!searched && !loading && (
        <div className="empty-state">
          <p>Search NASA ADS and arXiv for astronomical literature.</p>
          <p style={{ fontSize: "0.8rem", opacity: 0.7, marginTop: "0.5rem" }}>
            Examples: "JWST high-redshift galaxies", "gravitational lensing review", "M87 black hole"
          </p>
        </div>
      )}
    </div>
  );
}
