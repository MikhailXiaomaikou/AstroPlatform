import { useState, useEffect, useCallback, useRef } from "react";
import {
  uploadFITS,
  browseFITS,
  deleteFITS,
  downloadFITSUrl,
} from "../../api/client";
import type { FITSFileInfo } from "../../api/client";
import FITSPreviewComponent from "./FITSPreview";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface Props {
  onSelectFile?: (fitsPath: string) => void;
}

export default function FITSBrowser({ onSelectFile }: Props) {
  const [files, setFiles] = useState<FITSFileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [previewPath, setPreviewPath] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await browseFITS(filter || undefined);
      setFiles(data);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to load files";
      if (msg.includes("401") || msg.includes("Unauthorized")) {
        setError("Sign in to manage your FITS files.");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  const handleUpload = async (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(fileList)) {
        await uploadFITS(file);
      }
      await loadFiles();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (fileId: string) => {
    if (!confirm("Delete this file?")) return;
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
      setError(e instanceof Error ? e.message : "Delete failed");
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
      <h3 style={{ margin: "0 0 1rem", color: "#e0e0e0" }}>FITS File Manager</h3>

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
          <span style={{ color: "#4fc3f7" }}>Uploading...</span>
        ) : (
          <span style={{ color: "#999" }}>
            Drop FITS files here or click to browse
          </span>
        )}
      </div>

      {error && (
        <div style={{ color: "#f44", marginBottom: "0.5rem", fontSize: "0.85rem" }}>
          {error}
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
          <option value="">All sources</option>
          <option value="upload">Uploaded</option>
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
          {loading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {/* File list */}
      <div style={{ maxHeight: 400, overflowY: "auto" }}>
        {files.length === 0 && !loading && (
          <div style={{ color: "#777", textAlign: "center", padding: "2rem" }}>
            No FITS files yet. Upload one to get started.
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
                  Use
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
                Preview
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
                Download
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
                Delete
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
