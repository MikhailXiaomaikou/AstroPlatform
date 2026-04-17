import { useState, useEffect, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  uploadFITS,
  browseFITS,
  deleteFITS,
  downloadFITSUrl,
} from "../../api/client";
import type { FITSFileInfo } from "../../api/client";
import FITSPreviewComponent from "./FITSPreview";
import { useI18n } from "../../i18n";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface Props {
  onSelectFile?: (fitsPath: string) => void;
}

export default function FITSBrowser({ onSelectFile }: Props) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [files, setFiles] = useState<FITSFileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [lastUploadType, setLastUploadType] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  // H18: monotonic counter guards against stale-fetch races when the user
  // changes the filter quickly — only the most recent load's response may
  // update state.  (browseFITS does not currently accept an AbortSignal,
  // so we drop the stale response instead of cancelling the request.)
  const loadSeqRef = useRef(0);

  const loadFiles = useCallback(async () => {
    const mySeq = ++loadSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await browseFITS(filter || undefined);
      if (mySeq !== loadSeqRef.current) return;  // stale — newer request in flight
      setFiles(data);
    } catch (e) {
      if (mySeq !== loadSeqRef.current) return;
      const msg = e instanceof Error ? e.message : t("fits.failed_to_load");
      if (msg.includes("401") || msg.includes("Unauthorized")) {
        setError(t("fits.sign_in_required"));
      } else {
        setError(msg);
      }
    } finally {
      if (mySeq === loadSeqRef.current) setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const handleUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setUploadProgress(0);
    setError(null);
    try {
      const fileArr = Array.from(fileList);
      for (let i = 0; i < fileArr.length; i++) {
        await uploadFITS(fileArr[i], undefined, (pct) => {
          // For multi-file: blend per-file progress with overall progress
          const base = Math.round((i / fileArr.length) * 100);
          const portion = Math.round(pct / fileArr.length);
          setUploadProgress(base + portion);
        });
      }
      setUploadProgress(100);
      await loadFiles();
      // Detect FITS type from filename for auto-suggestion
      const lastName = fileArr[fileArr.length - 1].name.toLowerCase();
      if (lastName.includes("spec") || lastName.includes("spectrum")) {
        setLastUploadType("Spectrum");
      } else if (lastName.includes("image") || lastName.includes("img")) {
        setLastUploadType("Image");
      } else if (lastName.includes("cube")) {
        setLastUploadType("Data Cube");
      } else if (lastName.includes("table") || lastName.includes("cat")) {
        setLastUploadType("Catalog/Table");
      } else {
        setLastUploadType("FITS Data");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("fits.upload_failed"));
    } finally {
      setUploading(false);
      setUploadProgress(null);
    }
  };

  const handleDelete = async (fileId: string) => {
    if (!confirm(t("fits.delete_confirm"))) return;
    try {
      await deleteFITS(fileId);
      setFiles((prev) => prev.filter((f) => f.id !== fileId));
      if (previewPath) {
        const deleted = files.find((f) => f.id === fileId);
        if (deleted && deleted.fits_path === previewPath) {
          setPreviewPath(null);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t("fits.delete_failed"));
    }
  };

  const handlePreview = (fitsPath: string) => {
    setPreviewPath(previewPath === fitsPath ? null : fitsPath);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleUpload(e.dataTransfer.files);
  };

  return (
    <div style={{ padding: "1rem" }}>
      <h3 style={{ margin: "0 0 1rem", color: "#e0e0e0" }}>{t("fits.manager_title")}</h3>

      {/* Upload area */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        style={{
          border: `2px dashed ${dragOver ? "#4fc3f7" : "#555"}`,
          borderRadius: 8,
          padding: "2rem",
          textAlign: "center",
          marginBottom: "1rem",
          background: dragOver ? "rgba(79,195,247,0.05)" : "transparent",
          cursor: "pointer",
          transition: "all 0.2s",
        }}
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".fits,.fit,.fts,.fits.gz"
          multiple
          style={{ display: "none" }}
          onChange={(e) => handleUpload(e.target.files)}
        />
        {uploading ? (
          <div style={{ width: "100%" }}>
            <span style={{ color: "#4fc3f7" }}>{t("fits.uploading")} {uploadProgress != null ? `${uploadProgress}%` : ""}</span>
            {uploadProgress != null && (
              <div style={{ width: "100%", background: "#333", borderRadius: 4, height: 6, marginTop: 8, overflow: "hidden" }}>
                <div style={{ width: `${uploadProgress}%`, background: "#4fc3f7", height: "100%", borderRadius: 4, transition: "width 0.3s ease" }} />
              </div>
            )}
          </div>
        ) : (
          <span style={{ color: "#999" }}>
            {t("fits.drop_files")}
          </span>
        )}
      </div>

      {error && (
        <div style={{ color: "#f44", marginBottom: "0.5rem", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      {lastUploadType && (
        <div style={{ padding: "8px 12px", background: "var(--color-accent-subtle, rgba(79,195,247,0.08))", borderRadius: 8, margin: "8px 0", fontSize: "0.85rem" }}>
          Detected: <strong>{lastUploadType}</strong>
          {" — "}
          <button className="btn-primary btn-small" onClick={() => {
            localStorage.setItem("astro_chat_draft", "Analyze the uploaded file");
            navigate("/chat");
          }}>
            Auto-analyze
          </button>
        </div>
      )}

      {/* Filter */}
      <div style={{ marginBottom: "1rem", display: "flex", gap: "0.5rem" }}>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            background: "#2a2a2e",
            color: "#e0e0e0",
            border: "1px solid #555",
            borderRadius: 4,
            padding: "0.3rem 0.5rem",
          }}
        >
          <option value="">{t("fits.all_sources")}</option>
          <option value="upload">{t("fits.uploaded")}</option>
          <option value="sdss">SDSS</option>
          <option value="gaia">Gaia</option>
          <option value="simbad">SIMBAD</option>
          <option value="mast">MAST</option>
        </select>
        <button
          onClick={loadFiles}
          disabled={loading}
          style={{
            background: "#333",
            color: "#e0e0e0",
            border: "1px solid #555",
            borderRadius: 4,
            padding: "0.3rem 0.8rem",
            cursor: "pointer",
          }}
        >
          {loading ? t("common.loading") : t("fits.refresh")}
        </button>
      </div>

      {/* File list */}
      <div style={{ maxHeight: 400, overflowY: "auto" }}>
        {files.length === 0 && !loading && (
          <div style={{ color: "#777", textAlign: "center", padding: "2rem" }}>
            {t("fits.no_files")}
          </div>
        )}
        {files.map((f) => (
          <div
            key={f.id}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "0.5rem 0.75rem",
              borderBottom: "1px solid #333",
              background: previewPath === f.fits_path ? "rgba(79,195,247,0.08)" : "transparent",
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                style={{
                  color: "#e0e0e0",
                  fontSize: "0.9rem",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  cursor: "pointer",
                }}
                onClick={() => handlePreview(f.fits_path)}
                title={f.fits_path}
              >
                {f.filename}
              </div>
              <div style={{ color: "#777", fontSize: "0.75rem" }}>
                {f.source} &middot; {formatBytes(f.size_bytes)}
                {f.created_at && ` \u00b7 ${new Date(f.created_at).toLocaleDateString()}`}
              </div>
            </div>
            <div style={{ display: "flex", gap: "0.3rem" }}>
              {onSelectFile && (
                <button
                  onClick={() => onSelectFile(f.fits_path)}
                  style={{
                    background: "#1a6b3a",
                    color: "#fff",
                    border: "none",
                    borderRadius: 4,
                    padding: "0.2rem 0.5rem",
                    fontSize: "0.75rem",
                    cursor: "pointer",
                  }}
                >
                  {t("fits.use")}
                </button>
              )}
              <button
                onClick={() => handlePreview(f.fits_path)}
                style={{
                  background: "#2a4a6b",
                  color: "#fff",
                  border: "none",
                  borderRadius: 4,
                  padding: "0.2rem 0.5rem",
                  fontSize: "0.75rem",
                  cursor: "pointer",
                }}
              >
                {t("fits.preview")}
              </button>
              <a
                href={downloadFITSUrl(f.fits_path)}
                download
                style={{
                  background: "#2a5a3b",
                  color: "#fff",
                  border: "none",
                  borderRadius: 4,
                  padding: "0.2rem 0.5rem",
                  fontSize: "0.75rem",
                  cursor: "pointer",
                  textDecoration: "none",
                  display: "inline-block",
                }}
              >
                {t("common.download")}
              </a>
              <button
                onClick={() => handleDelete(f.id)}
                style={{
                  background: "#6b2a2a",
                  color: "#fff",
                  border: "none",
                  borderRadius: 4,
                  padding: "0.2rem 0.5rem",
                  fontSize: "0.75rem",
                  cursor: "pointer",
                }}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Full FITS Preview */}
      {previewPath && (
        <div style={{ marginTop: "1rem" }}>
          <FITSPreviewComponent
            filename={previewPath.split("/").pop() || ""}
            fitsPath={previewPath}
            source="upload"
            objectId={previewPath.split("/").pop() || ""}
          />
        </div>
      )}
    </div>
  );
}
