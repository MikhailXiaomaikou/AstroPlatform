import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  getPublicPaperDraft,
  listPaperDrafts,
  publishPaperDraft,
  unpublishPaperDraft,
  type PaperDraftResponse,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../i18n";

function paperTitle(draft: PaperDraftResponse | null): string {
  return String(draft?.paper_json?.title || "Untitled paper draft");
}

function publicAbsoluteUrl(path: string | null | undefined): string | null {
  if (!path) return null;
  return new URL(path, window.location.origin).toString();
}

function downloadText(filename: string, content: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function PapersPage() {
  const { token } = useParams();
  const { user, loading: authLoading } = useAuth();
  const { t } = useI18n();
  const [drafts, setDrafts] = useState<PaperDraftResponse[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [publicDraft, setPublicDraft] = useState<PaperDraftResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const selected = useMemo(() => {
    if (token) return publicDraft;
    return drafts.find((draft) => draft.id === selectedId) || drafts[0] || null;
  }, [drafts, publicDraft, selectedId, token]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        if (token) {
          const draft = await getPublicPaperDraft(token);
          if (!cancelled) setPublicDraft(draft);
          return;
        }
        if (!user) {
          if (!cancelled) setDrafts([]);
          return;
        }
        const owned = await listPaperDrafts();
        if (cancelled) return;
        setDrafts(owned);
        setSelectedId((current) => current || owned[0]?.id || null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load paper drafts");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    if (!authLoading || token) {
      void load();
    }
    return () => { cancelled = true; };
  }, [authLoading, token, user]);

  async function handleTogglePublish(draft: PaperDraftResponse) {
    setBusyId(draft.id);
    try {
      const updated = draft.is_public
        ? await unpublishPaperDraft(draft.id)
        : await publishPaperDraft(draft.id);
      setDrafts((prev) => prev.map((item) => item.id === updated.id ? updated : item));
      if (updated.is_public && updated.public_url && navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(publicAbsoluteUrl(updated.public_url) || updated.public_url).catch(() => {});
      }
    } finally {
      setBusyId(null);
    }
  }

  const publicUrl = publicAbsoluteUrl(selected?.public_url);

  return (
    <div className="journal-page">
      <div className="page-head">
        <div>
          <h1>{token ? "Published Draft" : t("nav.papers")}</h1>
          <div className="page-sub">
            <em>
              {token
                ? "read-only public manuscript draft"
                : "private manuscript drafts from your saved chat sessions"}
            </em>
          </div>
        </div>
        <div className="badge-row">
          <span className={selected?.is_public ? "j-tag verified" : "j-tag"}>
            {selected?.is_public ? "published" : "private"}
          </span>
        </div>
      </div>

      {loading && <div className="fits-loading">Loading paper drafts...</div>}
      {error && <div className="error-banner">{error}</div>}
      {!loading && !token && !user && (
        <div className="note-card">
          <strong>Sign in to view your paper drafts.</strong>
          <p className="fits-hint">
            Drafts are private to your account by default. Use Publish Draft to create a public read-only link.
          </p>
        </div>
      )}
      {!loading && !token && user && drafts.length === 0 && (
        <div className="note-card">
          <strong>No paper drafts yet.</strong>
          <p className="fits-hint">Generate a draft from an AI Assistant session to see it here.</p>
        </div>
      )}

      {selected && (
        <div className="three-col">
          {!token && (
            <aside className="side-left">
              <div className="j-side-block">
                <h4>Your Drafts</h4>
                {drafts.map((draft) => (
                  <button
                    key={draft.id}
                    type="button"
                    className={`j-side-item ${selected?.id === draft.id ? "active" : ""}`}
                    onClick={() => setSelectedId(draft.id)}
                    style={{ width: "100%", textAlign: "left" }}
                  >
                    <span>{paperTitle(draft)}</span>
                    <em>{draft.journal_format || "aastex"} · {draft.is_public ? "published" : "private"}</em>
                  </button>
                ))}
              </div>
            </aside>
          )}

          <main>
            <div className="page-head">
              <div>
                <h1>{paperTitle(selected)}</h1>
                <div className="page-sub">
                  <em>
                    {selected.journal_format || "aastex"}
                    {selected.updated_at ? ` · updated ${new Date(selected.updated_at).toLocaleString()}` : ""}
                  </em>
                </div>
              </div>
              <div className="badge-row">
                {!token && (
                  <button
                    className={selected.is_public ? "btn-journal-ghost" : "btn-journal-primary"}
                    disabled={busyId === selected.id}
                    onClick={() => { void handleTogglePublish(selected); }}
                  >
                    {selected.is_public ? "Unpublish Draft" : "Publish Draft"}
                  </button>
                )}
                <button
                  className="btn-journal-ghost"
                  onClick={() => downloadText(`${paperTitle(selected).replace(/\s+/g, "_")}.tex`, selected.latex_source, "application/x-tex")}
                >
                  Download .tex
                </button>
                <button
                  className="btn-journal-ghost"
                  onClick={() => downloadText(`${paperTitle(selected).replace(/\s+/g, "_")}.bib`, selected.bibtex, "application/x-bibtex")}
                >
                  Download BibTeX
                </button>
              </div>
            </div>

            {publicUrl && (
              <div className="note-card" style={{ marginBottom: 16 }}>
                <strong>Public read-only link</strong>
                <p className="fits-hint" style={{ marginTop: 6 }}>
                  <a href={selected.public_url || "#"} target="_blank" rel="noopener noreferrer">{publicUrl}</a>
                </p>
              </div>
            )}

            <div className="manuscript">
              <div className="ms-page">
                <div className="ms-title">{paperTitle(selected)}</div>
                <div className="ms-abstract">
                  <strong>Abstract -- </strong>
                  {String(selected.paper_json.abstract || "No abstract generated yet.")}
                </div>
                <pre className="cl-body" style={{ whiteSpace: "pre-wrap", maxHeight: 520, overflow: "auto" }}>
                  {selected.latex_source}
                </pre>
              </div>
            </div>
          </main>

          <aside className="side-right">
            <div className="j-side-block">
              <h4>Validation</h4>
              <div className="row"><span className="k">status</span><span className="v">{selected.validation?.overall_status || "unknown"}</span></div>
              <div className="row"><span className="k">score</span><span className="v">{selected.validation?.score ?? "n/a"}</span></div>
            </div>
            <div className="j-side-block">
              <h4>Privacy</h4>
              <p className="fits-hint">
                Drafts are account-scoped by default. Publishing creates a read-only public link; unpublishing revokes it.
              </p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
