import { useEffect, useRef, useState } from "react";
import {
  getWorkspace,
  batchSearch,
  uploadBatchTargets,
  addTag,
  getTags,
  deleteTag,
  addNote,
  getNotes,
  exportData,
  sampSend,
  sampStatus,
  downloadFileUrl,
  type BatchTarget,
} from "../../api/client";
import FITSPreview from "../../components/fits/FITSPreview";
import { useAuth } from "../../context/AuthContext";
import { buildPipelineDraft, mergeWorkspaceFiles, readWorkspaceCache, type WorkspaceCacheFile } from "../../utils/workspaceCache";

type WorkspaceFile = WorkspaceCacheFile;

interface FileTag {
  id: string;
  tag: string;
}

interface FileNote {
  id: string;
  content: string;
  created_at: string;
}

export default function WorkspacePage() {
  const { user } = useAuth();
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedFile, setSelectedFile] = useState<WorkspaceFile | null>(null);
  const [tags, setTags] = useState<FileTag[]>([]);
  const [notes, setNotes] = useState<FileNote[]>([]);
  const [newTag, setNewTag] = useState("");
  const [newNote, setNewNote] = useState("");
  const [batchMode, setBatchMode] = useState(false);
  const [batchTargets, setBatchTargets] = useState<BatchTarget[]>([]);
  const [batchResults, setBatchResults] = useState<Record<string, unknown[]> | null>(null);
  const [batchLoading, setBatchLoading] = useState(false);
  const [sampConnected, setSampConnected] = useState(false);
  const [showPreview, setShowPreview] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const selectedMetadata = selectedFile?.metadata || null;
  const selectedContentType = String(selectedMetadata?.["content_type"] || "");
  const selectedFilename = String(
    selectedMetadata?.["original_filename"] ||
    selectedMetadata?.["filename"] ||
    selectedFile?.object_id ||
    ""
  );
  const isGenericWorkspaceFile = !!selectedFile && (
    selectedMetadata?.["workspace_kind"] === "generic" ||
    (!!selectedContentType && !selectedContentType.includes("fits"))
  );

  useEffect(() => {
    getWorkspace()
      .then((data) => setFiles(mergeWorkspaceFiles([...(data as unknown as WorkspaceFile[]), ...readWorkspaceCache()])))
      .catch(() => setFiles(readWorkspaceCache()))
      .finally(() => setLoading(false));
    sampStatus().then((s) => setSampConnected(s.connected)).catch(() => {});
  }, []);

  const selectFile = async (f: WorkspaceFile) => {
    setSelectedFile(f);
    try {
      const [t, n] = await Promise.all([getTags(f.id), getNotes(f.id)]);
      setTags(t);
      setNotes(n);
    } catch {
      setTags([]);
      setNotes([]);
    }
  };

  const handleAddTag = async () => {
    if (!selectedFile || !newTag.trim()) return;
    const t = await addTag(selectedFile.id, newTag.trim());
    setTags((prev) => [...prev, t]);
    setNewTag("");
  };

  const handleDeleteTag = async (tagId: string) => {
    if (!selectedFile) return;
    await deleteTag(selectedFile.id, tagId);
    setTags((prev) => prev.filter((t) => t.id !== tagId));
  };

  const handleAddNote = async () => {
    if (!selectedFile || !newNote.trim()) return;
    await addNote(selectedFile.id, newNote.trim());
    const n = await getNotes(selectedFile.id);
    setNotes(n);
    setNewNote("");
  };

  const handleExport = async (format: "csv" | "votable" | "latex") => {
    if (!selectedFile) return;
    const blob = await exportData(selectedFile.id, format);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `export-${selectedFile.object_id}.${format === "votable" ? "xml" : format === "latex" ? "tex" : "csv"}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleSAMPSend = async () => {
    if (!selectedFile?.fits_path) return;
    await sampSend(selectedFile.fits_path);
  };

  const handleBatchUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const targets = await uploadBatchTargets(file);
    setBatchTargets(targets);
  };

  const handleBatchSearch = async () => {
    if (batchTargets.length === 0) return;
    setBatchLoading(true);
    try {
      const results = await batchSearch(batchTargets);
      setBatchResults(results);
    } catch {
      setBatchResults(null);
    } finally {
      setBatchLoading(false);
    }
  };

  return (
    <div className="workspace-page">
      <div className="workspace-header">
        <h1>Workspace</h1>
        <div className="workspace-actions-top">
          <button
            className={`btn-secondary${batchMode ? " active" : ""}`}
            onClick={() => setBatchMode(!batchMode)}
          >
            Batch Search
          </button>
          {sampConnected && <span className="samp-badge">SAMP Connected</span>}
        </div>
      </div>

      {batchMode && (
        <div className="batch-section">
          <h3>Batch Search</h3>
          <p className="batch-hint">Upload a CSV with columns: name, ra, dec</p>
          <div className="batch-controls">
            <input
              type="file"
              accept=".csv"
              ref={fileInputRef}
              onChange={handleBatchUpload}
              style={{ display: "none" }}
            />
            <button className="btn-secondary" onClick={() => fileInputRef.current?.click()}>
              Upload Target List
            </button>
            {batchTargets.length > 0 && (
              <span className="batch-count">{batchTargets.length} targets loaded</span>
            )}
            <button
              className="btn-primary"
              disabled={batchTargets.length === 0 || batchLoading}
              onClick={handleBatchSearch}
            >
              {batchLoading ? "Searching..." : "Search All"}
            </button>
          </div>
          {batchResults && (
            <div className="batch-results">
              {Object.entries(batchResults).map(([target, results]) => (
                <div key={target} className="batch-result-group">
                  <h4>{target} ({(results as unknown[]).length} results)</h4>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="workspace-layout">
        <div className="workspace-file-list">
          <h3>Saved Files ({files.length})</h3>
          {loading ? (
            <div className="fits-loading"><span className="spinner spinner-blue" /> Loading...</div>
          ) : files.length === 0 ? (
            <div className="fits-hint">
              <p>No saved files. Fetch data from the Data Browser to get started.</p>
              {!user && <p>Sign in if you want these files synced to your server-side workspace and history.</p>}
            </div>
          ) : (
            files.map((f) => (
              <div
                key={f.id}
                className={`workspace-file-card${selectedFile?.id === f.id ? " selected" : ""}`}
                onClick={() => selectFile(f)}
              >
                <span className={`badge badge-${f.source}`}>{f.source.toUpperCase()}</span>
                <span className="file-object-id">{f.object_id}</span>
                {f.created_at && (
                  <span className="file-date">{new Date(f.created_at).toLocaleDateString()}</span>
                )}
              </div>
            ))
          )}
        </div>

        {selectedFile && (
          <div className="workspace-detail">
            <h3>{selectedFile.object_id}</h3>
            <dl className="fits-meta">
              <dt>Source</dt>
              <dd><span className={`badge badge-${selectedFile.source}`}>{selectedFile.source.toUpperCase()}</span></dd>
              <dt>{isGenericWorkspaceFile ? "Storage Path" : "FITS Path"}</dt>
              <dd className="mono">{selectedFile.fits_path}</dd>
              {selectedFilename && selectedFilename !== selectedFile.object_id && (
                <>
                  <dt>Filename</dt>
                  <dd>{selectedFilename}</dd>
                </>
              )}
            </dl>

            {/* Action buttons */}
            <div style={{ display: "flex", gap: "0.4rem", margin: "0.75rem 0" }}>
              {selectedFile.fits_path && !isGenericWorkspaceFile && (
                <>
                  <button className="btn-secondary btn-small" onClick={() => setShowPreview(!showPreview)}>
                    {showPreview ? "Hide Preview" : "Preview FITS"}
                  </button>
                  <button className="btn-secondary btn-small" style={{ background: "rgba(48,209,88,0.15)", color: "var(--color-green)" }}
                    onClick={() => {
                      localStorage.setItem("pipeline_autosave", JSON.stringify(buildPipelineDraft(selectedFile.fits_path)));
                      window.location.href = "/pipeline";
                    }}>
                    Open in Pipeline
                  </button>
                  <a href={`${import.meta.env.VITE_API_URL || (import.meta.env.DEV || import.meta.env.MODE === "test" ? "http://localhost:8000" : "")}/api/data/fits/download?fits_path=${encodeURIComponent(selectedFile.fits_path)}`}
                    download className="btn-secondary btn-small" style={{ textDecoration: "none" }}>
                    Download
                  </a>
                </>
              )}
              {selectedFile.fits_path && isGenericWorkspaceFile && (
                <a
                  href={downloadFileUrl(selectedFile.fits_path)}
                  download={selectedFilename || undefined}
                  className="btn-secondary btn-small"
                  style={{ textDecoration: "none" }}
                >
                  Download
                </a>
              )}
            </div>

            {/* FITS Preview */}
            {showPreview && selectedFile.fits_path && !isGenericWorkspaceFile && (
              <div style={{ marginBottom: "1rem" }}>
                <FITSPreview
                  filename={selectedFile.object_id}
                  fitsPath={selectedFile.fits_path}
                  source={selectedFile.source}
                  objectId={selectedFile.object_id}
                />
              </div>
            )}

            {/* Tags */}
            <div className="detail-section">
              <h4>Tags</h4>
              <div className="tags-list">
                {tags.map((t) => (
                  <span key={t.id} className="tag-chip">
                    {t.tag}
                    <button className="tag-remove" onClick={() => handleDeleteTag(t.id)}>&times;</button>
                  </span>
                ))}
              </div>
              <div className="tag-add">
                <input
                  type="text" value={newTag} onChange={(e) => setNewTag(e.target.value)}
                  placeholder="Add tag..." className="input-field" style={{ flex: 1 }}
                  onKeyDown={(e) => e.key === "Enter" && handleAddTag()}
                />
                <button className="btn-secondary" onClick={handleAddTag}>Add</button>
              </div>
            </div>

            {/* Notes */}
            <div className="detail-section">
              <h4>Notes</h4>
              <div className="notes-list">
                {notes.map((n) => (
                  <div key={n.id} className="note-card">
                    <p>{n.content}</p>
                    <span className="note-date">{new Date(n.created_at).toLocaleString()}</span>
                  </div>
                ))}
              </div>
              <textarea
                value={newNote} onChange={(e) => setNewNote(e.target.value)}
                placeholder="Write a note..." className="note-textarea" rows={3}
              />
              <button className="btn-secondary" onClick={handleAddNote} disabled={!newNote.trim()}>
                Add Note
              </button>
            </div>

            {/* Export & Integration */}
            <div className="detail-section">
              <h4>Export & Send</h4>
              <div className="export-buttons">
                <button className="btn-secondary" onClick={() => handleExport("csv")}>CSV</button>
                <button className="btn-secondary" onClick={() => handleExport("votable")}>VOTable</button>
                <button className="btn-secondary" onClick={() => handleExport("latex")}>LaTeX</button>
                {sampConnected && (
                  <button className="btn-secondary" onClick={handleSAMPSend}>
                    Send to DS9/TOPCAT
                  </button>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
