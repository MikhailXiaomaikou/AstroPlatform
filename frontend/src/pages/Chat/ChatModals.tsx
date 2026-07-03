// Share/snapshots modal and paper-draft modal. JSX moved verbatim from
// ChatPage.tsx; all state lives in useCollaboration / usePaperDraft.
import type { Dispatch, SetStateAction } from "react";
import type {
  AnalysisValidationResult,
  PaperDraftResponse,
  SessionShareItem,
  SessionSnapshotItem,
  SessionSnapshotDiff,
} from "../../api/client";
import {
  downloadBlob,
  getPaperSectionText,
  setPaperSectionText,
  type JournalFormat,
  type PaperTab,
  type ShareAccessLevel,
} from "./chatHelpers";

export function ShareModal({
  setShareModalOpen,
  shareAccessLevel,
  setShareAccessLevel,
  shareExpiryHours,
  setShareExpiryHours,
  shareLoading,
  shareUrl,
  sessionShares,
  sessionSnapshots,
  snapshotName,
  setSnapshotName,
  snapshotCompareSelection,
  setSnapshotCompareSelection,
  snapshotDiff,
  handleCreateShare,
  handleRevokeShare,
  handleCreateSnapshot,
  handleRestoreSnapshot,
  handleCompareSnapshots,
}: {
  setShareModalOpen: (open: boolean) => void;
  shareAccessLevel: ShareAccessLevel;
  setShareAccessLevel: (level: ShareAccessLevel) => void;
  shareExpiryHours: number;
  setShareExpiryHours: (hours: number) => void;
  shareLoading: boolean;
  shareUrl: string | null;
  sessionShares: SessionShareItem[];
  sessionSnapshots: SessionSnapshotItem[];
  snapshotName: string;
  setSnapshotName: (name: string) => void;
  snapshotCompareSelection: string[];
  setSnapshotCompareSelection: Dispatch<SetStateAction<string[]>>;
  snapshotDiff: SessionSnapshotDiff | null;
  handleCreateShare: () => Promise<void>;
  handleRevokeShare: (shareId: string) => Promise<void>;
  handleCreateSnapshot: () => Promise<void>;
  handleRestoreSnapshot: (snapshotId: string) => Promise<void>;
  handleCompareSnapshots: () => Promise<void>;
}) {
  return (
        <div className="viz-overlay" onClick={() => setShareModalOpen(false)}>
          <div
            className="viz-overlay-content"
            style={{ maxWidth: 920, width: "min(920px, 92vw)", maxHeight: "88vh", overflow: "auto" }}
            onClick={(event) => event.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0 }}>Share And Snapshots</h3>
                <div style={{ color: "var(--color-text-secondary)", marginTop: 4 }}>
                  Manage share links, forks, and point-in-time session restores.
                </div>
              </div>
              <button className="btn-secondary btn-small" onClick={() => setShareModalOpen(false)}>Close</button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}>
              <div style={{ padding: 14, borderRadius: 12, background: "rgba(15,23,42,0.04)" }}>
                <h4 style={{ marginTop: 0 }}>Create Share Link</h4>
                <div style={{ display: "grid", gap: 10 }}>
                  <label>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Access Level</div>
                    <select
                      className="search-input"
                      value={shareAccessLevel}
                      onChange={(event) => setShareAccessLevel(event.target.value as ShareAccessLevel)}
                    >
                      <option value="view">View</option>
                      <option value="fork">Fork</option>
                      <option value="comment">Comment</option>
                    </select>
                  </label>
                  <label>
                    <div style={{ fontWeight: 600, marginBottom: 6 }}>Expiry (hours)</div>
                    <input
                      className="search-input"
                      type="number"
                      min={1}
                      value={shareExpiryHours}
                      onChange={(event) => setShareExpiryHours(Number(event.target.value) || 0)}
                    />
                  </label>
                  <button className="btn-primary" disabled={shareLoading} onClick={() => { void handleCreateShare(); }}>
                    {shareLoading ? "Creating..." : "Create Share Link"}
                  </button>
                  {shareUrl && (
                    <div className="fits-hint" style={{ wordBreak: "break-all" }}>
                      Latest link: {shareUrl}
                    </div>
                  )}
                </div>

                <div style={{ marginTop: 18 }}>
                  <h4>Active Shares</h4>
                  {sessionShares.length === 0 ? (
                    <div className="fits-hint">No active share links yet.</div>
                  ) : (
                    sessionShares.map((share) => (
                      <div key={share.id} className="note-card" style={{ marginBottom: 10 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                          <div>
                            <strong>{share.access_level}</strong>
                            <div style={{ color: "var(--color-text-secondary)", fontSize: "0.85rem", marginTop: 4 }}>
                              {share.expires_at ? `Expires ${new Date(share.expires_at).toLocaleString()}` : "No expiry"}
                            </div>
                            <div className="mono" style={{ marginTop: 6 }}>.../{share.share_token}</div>
                          </div>
                          <button className="btn-secondary btn-small" onClick={() => { void handleRevokeShare(share.id); }}>
                            Revoke
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div style={{ padding: 14, borderRadius: 12, background: "rgba(15,23,42,0.04)" }}>
                <h4 style={{ marginTop: 0 }}>Version Snapshots</h4>
                <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                  <input
                    className="search-input"
                    value={snapshotName}
                    onChange={(event) => setSnapshotName(event.target.value)}
                    placeholder='e.g. "before extinction correction"'
                  />
                  <button className="btn-secondary" onClick={() => { void handleCreateSnapshot(); }}>
                    Snapshot
                  </button>
                </div>
                {sessionSnapshots.length === 0 ? (
                  <div className="fits-hint">No snapshots yet.</div>
                ) : (
                  <div style={{ display: "grid", gap: 10 }}>
                    {sessionSnapshots.map((snapshot) => {
                      const selected = snapshotCompareSelection.includes(snapshot.id);
                      return (
                        <div key={snapshot.id} className="note-card">
                          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                            <div>
                              <strong>{snapshot.name}</strong>
                              <div className="note-date" style={{ marginTop: 4 }}>
                                {snapshot.created_at ? new Date(snapshot.created_at).toLocaleString() : "Unknown time"}
                              </div>
                            </div>
                            <div style={{ display: "flex", gap: 6 }}>
                              <button
                                className={`btn-secondary btn-small${selected ? " active" : ""}`}
                                onClick={() => {
                                  setSnapshotCompareSelection((prev) => {
                                    if (prev.includes(snapshot.id)) return prev.filter((id) => id !== snapshot.id);
                                    if (prev.length === 2) return [prev[1], snapshot.id];
                                    return [...prev, snapshot.id];
                                  });
                                }}
                              >
                                Compare
                              </button>
                              <button className="btn-secondary btn-small" onClick={() => { void handleRestoreSnapshot(snapshot.id); }}>
                                Restore
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <button
                    className="btn-secondary btn-small"
                    disabled={snapshotCompareSelection.length !== 2}
                    onClick={() => { void handleCompareSnapshots(); }}
                  >
                    Compare Selected
                  </button>
                </div>
                {snapshotDiff && (
                  <div className="fits-hint" style={{ marginTop: 12 }}>
                    Title changed: {snapshotDiff.updated_title ? "yes" : "no"} · Added messages: {snapshotDiff.added_messages} · Removed messages: {snapshotDiff.removed_messages}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
  );
}

export function PaperDraftModal({
  setPaperModalOpen,
  paperFormat,
  setPaperFormat,
  paperTab,
  setPaperTab,
  paperSessionId,
  paperValidation,
  paperDraft,
  paperEditorJson,
  setPaperEditorJson,
  paperLoading,
  paperGenerating,
  paperSaving,
  handleGeneratePaper,
  handleSavePaperDraft,
  handleTogglePaperPublish,
  handleRegeneratePaperSection,
  setInput,
}: {
  setPaperModalOpen: (open: boolean) => void;
  paperFormat: JournalFormat;
  setPaperFormat: (format: JournalFormat) => void;
  paperTab: PaperTab;
  setPaperTab: (tab: PaperTab) => void;
  paperSessionId: string | null;
  paperValidation: AnalysisValidationResult | null;
  paperDraft: PaperDraftResponse | null;
  paperEditorJson: Record<string, unknown> | null;
  setPaperEditorJson: (json: Record<string, unknown> | null) => void;
  paperLoading: boolean;
  paperGenerating: boolean;
  paperSaving: boolean;
  handleGeneratePaper: (overrideValidation?: boolean) => Promise<void>;
  handleSavePaperDraft: () => Promise<void>;
  handleTogglePaperPublish: () => Promise<void>;
  handleRegeneratePaperSection: () => Promise<void>;
  setInput: (value: string) => void;
}) {
  return (
        <div className="viz-overlay" onClick={() => setPaperModalOpen(false)}>
          <div
            className="viz-overlay-content"
            style={{ maxWidth: 980, width: "min(980px, 92vw)", maxHeight: "88vh", overflow: "auto" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, marginBottom: 16 }}>
              <div>
                <h3 style={{ margin: 0 }}>Paper Draft</h3>
                <div style={{ color: "var(--color-text-secondary)", fontSize: "0.9rem", marginTop: 4 }}>
                  Validate the session, generate a draft, edit sections, and download LaTeX/BibTeX.
                </div>
              </div>
              <button className="btn-secondary btn-small" onClick={() => setPaperModalOpen(false)}>Close</button>
            </div>

            <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 16 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontWeight: 600 }}>Journal</span>
                <select
                  value={paperFormat}
                  onChange={(e) => setPaperFormat(e.target.value as JournalFormat)}
                  className="search-input"
                  style={{ width: 160 }}
                >
                  <option value="aastex">AASTeX</option>
                  <option value="mnras">MNRAS</option>
                  <option value="aa">A&amp;A</option>
                </select>
              </label>
              <button
                className="btn-primary btn-small"
                disabled={paperLoading || paperGenerating || !paperSessionId}
                onClick={() => { void handleGeneratePaper(paperValidation?.overall_status === "FAIL"); }}
              >
                {paperGenerating ? "Generating..." : paperValidation?.overall_status === "FAIL" ? "Generate Anyway" : "Generate Draft"}
              </button>
              {paperDraft && (
                <>
                  <button
                    className="btn-secondary btn-small"
                    disabled={paperSaving || !paperEditorJson}
                    onClick={() => { void handleSavePaperDraft(); }}
                  >
                    {paperSaving ? "Saving..." : "Save Changes"}
                  </button>
                  <button
                    className={paperDraft.is_public ? "btn-secondary btn-small" : "btn-primary btn-small"}
                    disabled={paperSaving}
                    onClick={() => { void handleTogglePaperPublish(); }}
                    title={paperDraft.is_public ? "Remove the public draft link" : "Create a public read-only draft link"}
                  >
                    {paperDraft.is_public ? "Unpublish Draft" : "Publish Draft"}
                  </button>
                  <button
                    className="btn-secondary btn-small"
                    onClick={() => {
                      downloadBlob(
                        new Blob([paperDraft.latex_source], { type: "application/x-tex" }),
                        `${(paperDraft.paper_json.title as string || "standard_astro_draft").replace(/\s+/g, "_")}.tex`,
                      );
                    }}
                  >
                    Download LaTeX
                  </button>
                  <button
                    className="btn-secondary btn-small"
                    onClick={() => {
                      downloadBlob(
                        new Blob([paperDraft.bibtex], { type: "application/x-bibtex" }),
                        `${(paperDraft.paper_json.title as string || "standard_astro_references").replace(/\s+/g, "_")}.bib`,
                      );
                    }}
                  >
                    Download BibTeX
                  </button>
                </>
              )}
            </div>

            {paperDraft?.is_public && paperDraft.public_url && (
              <div style={{
                marginBottom: 16,
                padding: "0.6rem 0.8rem",
                borderRadius: 6,
                border: "1px solid rgba(46,106,78,0.28)",
                background: "rgba(46,106,78,0.08)",
                fontSize: "0.85rem",
              }}>
                Published read-only draft:{" "}
                <a href={paperDraft.public_url} target="_blank" rel="noopener noreferrer">
                  {new URL(paperDraft.public_url, window.location.origin).toString()}
                </a>
              </div>
            )}

            {paperLoading && (
              <div className="fits-loading" style={{ marginBottom: 16 }}>Inspecting session and running validation...</div>
            )}

            {paperValidation && (
              <div style={{ marginBottom: 18, padding: 14, borderRadius: 10, background: "rgba(15,23,42,0.05)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 10 }}>
                  <strong>
                    Validation: {paperValidation.overall_status} ({Math.round(paperValidation.score * 100)}%)
                  </strong>
                  <span style={{
                    padding: "4px 8px",
                    borderRadius: 999,
                    background:
                      paperValidation.overall_status === "FAIL" ? "#fee2e2" :
                      paperValidation.overall_status === "WARN" ? "#fef3c7" : "#dcfce7",
                    color:
                      paperValidation.overall_status === "FAIL" ? "#b91c1c" :
                      paperValidation.overall_status === "WARN" ? "#a16207" : "#166534",
                    fontWeight: 700,
                    fontSize: "0.75rem",
                  }}>
                    {paperValidation.overall_status}
                  </span>
                </div>
                <div style={{ display: "grid", gap: 10 }}>
                  {paperValidation.checks.map((check) => (
                    <div key={check.name} style={{ border: "1px solid rgba(15,23,42,0.08)", borderRadius: 8, padding: 10, background: "#fff" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
                        <strong>{check.name.replace(/_/g, " ")}</strong>
                        <span style={{
                          fontSize: "0.72rem",
                          fontWeight: 700,
                          color: check.status === "FAIL" ? "#b91c1c" : check.status === "WARN" ? "#a16207" : "#166534",
                        }}>
                          {check.status}
                        </span>
                      </div>
                      <div style={{ fontSize: "0.88rem", marginTop: 6 }}>{check.details}</div>
                      <div style={{ fontSize: "0.82rem", color: "var(--color-text-secondary)", marginTop: 4 }}>
                        Recommendation: {check.recommendation}
                      </div>
                      <div style={{ marginTop: 8 }}>
                        <button
                          className="btn-secondary btn-small"
                          onClick={() => {
                            setInput(`Help me address this analysis validation issue in my current session: ${check.recommendation}`);
                            setPaperModalOpen(false);
                          }}
                        >
                          Send Fix Prompt to AI
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {paperDraft && paperEditorJson && (
              <>
                <div style={{ marginBottom: 12 }}>
                  <input
                    className="search-input"
                    style={{ width: "100%", fontSize: "1.05rem", fontWeight: 700 }}
                    value={String(paperEditorJson.title || "")}
                    onChange={(e) => setPaperEditorJson({ ...paperEditorJson, title: e.target.value })}
                    placeholder="Paper title"
                  />
                </div>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
                  {[
                    ["abstract", "Abstract"],
                    ["introduction", "Introduction"],
                    ["data_sources", "Data"],
                    ["analysis_methods", "Methods"],
                    ["results", "Results"],
                    ["discussion", "Discussion"],
                    ["conclusions", "Conclusions"],
                    ["acknowledgments", "Acknowledgments"],
                  ].map(([key, label]) => (
                    <button
                      key={key}
                      className={`btn-small ${paperTab === key ? "btn-primary" : "btn-secondary"}`}
                      onClick={() => setPaperTab(key as PaperTab)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div style={{ marginBottom: 10 }}>
                  <textarea
                    className="chat-input"
                    style={{ minHeight: 260, width: "100%" }}
                    value={getPaperSectionText(paperEditorJson, paperTab)}
                    onChange={(e) => setPaperEditorJson(setPaperSectionText(paperEditorJson, paperTab, e.target.value))}
                  />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <div style={{ color: "var(--color-text-secondary)", fontSize: "0.85rem" }}>
                    Figures: {Array.isArray(((paperEditorJson.results as Record<string, unknown> | undefined)?.figures))
                      ? ((((paperEditorJson.results as Record<string, unknown>).figures as unknown[]) || []).length)
                      : 0}
                    {" · "}
                    Tables: {Array.isArray(((paperEditorJson.results as Record<string, unknown> | undefined)?.tables))
                      ? ((((paperEditorJson.results as Record<string, unknown>).tables as unknown[]) || []).length)
                      : 0}
                  </div>
                  <button
                    className="btn-secondary btn-small"
                    disabled={paperGenerating}
                    onClick={() => { void handleRegeneratePaperSection(); }}
                  >
                    {paperGenerating ? "Regenerating..." : "Regenerate Section"}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
  );
}
