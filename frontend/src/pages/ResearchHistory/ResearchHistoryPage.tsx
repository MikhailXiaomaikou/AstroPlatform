import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  browseFITS,
  cancelResearchJob,
  deleteResearchMemory,
  fetchResearchArtifact,
  getResearchProfile,
  listResearchJobs,
  listResearchHistory,
  refreshResearchProfile,
  retryResearchJob,
  updateResearchProfile,
  type FITSFileInfo,
  type ResearchJobSummary,
  type ResearchHistoryItem,
  type ResearchProfile,
} from "../../api/client";

export default function ResearchHistoryPage() {
  const navigate = useNavigate();
  const [profile, setProfile] = useState<ResearchProfile | null>(null);
  const [history, setHistory] = useState<ResearchHistoryItem[]>([]);
  const [jobs, setJobs] = useState<ResearchJobSummary[]>([]);
  const [artifacts, setArtifacts] = useState<FITSFileInfo[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadAll = async (search?: string) => {
    setLoading(true);
    try {
      const [nextProfile, nextHistory, nextJobs, nextArtifacts] = await Promise.all([
        getResearchProfile(),
        listResearchHistory(search),
        listResearchJobs(),
        browseFITS(),
      ]);
      setProfile(nextProfile);
      setHistory(nextHistory);
      setJobs(nextJobs.items);
      setArtifacts(nextArtifacts);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load research history");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  const interestText = useMemo(
    () => (profile?.research_interests || []).join(", "),
    [profile?.research_interests],
  );

  const handleSaveProfile = async () => {
    if (!profile) return;
    setSaving(true);
    try {
      await updateResearchProfile({
        memory_enabled: profile.memory_enabled,
        expertise_level: profile.expertise_level,
        research_interests: interestText
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      await loadAll(query || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save profile");
    } finally {
      setSaving(false);
    }
  };

  const handleContinue = (sessionId: string) => {
    localStorage.setItem("astro_chat_open_session", sessionId);
    navigate("/chat");
  };

  const handleDeleteMemory = async () => {
    if (!confirm("Delete all stored research memory for this account?")) return;
    try {
      await deleteResearchMemory();
      await loadAll(query || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory");
    }
  };

  const handleJobAction = async (job: ResearchJobSummary, action: "cancel" | "retry") => {
    setActiveJob(job.job_id);
    try {
      if (action === "cancel") await cancelResearchJob(job.job_id);
      else await retryResearchJob(job.job_id);
      await loadAll(query || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} job`);
    } finally {
      setActiveJob(null);
    }
  };

  const handleDownload = async (artifact: FITSFileInfo) => {
    try {
      const blob = await fetchResearchArtifact(artifact.fits_path);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = artifact.filename || artifact.object_id;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to download artifact");
    }
  };

  return (
    <div className="workspace-page">
      <div className="workspace-header">
        <div>
          <h1>Research History</h1>
          <div style={{ color: "var(--color-text-secondary)" }}>
            Persistent scientific memory, profile tuning, and session continuity.
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-secondary" onClick={() => { void refreshResearchProfile(); void loadAll(query || undefined); }}>
            Refresh Memory
          </button>
          <button className="btn-secondary" onClick={() => { void handleDeleteMemory(); }}>
            Delete All Memory
          </button>
        </div>
      </div>

      {error && <div className="error-banner" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="workspace-layout">
        <div className="workspace-file-list" style={{ maxWidth: 360 }}>
          <h3>Research Profile</h3>
          {loading || !profile ? (
            <div className="fits-loading">Loading profile...</div>
          ) : (
            <div className="detail-section" style={{ display: "grid", gap: 12 }}>
              <label>
                <div style={{ marginBottom: 6, fontWeight: 600 }}>Memory Enabled</div>
                <input
                  type="checkbox"
                  checked={profile.memory_enabled}
                  onChange={(event) => setProfile({ ...profile, memory_enabled: event.target.checked })}
                />
              </label>

              <label>
                <div style={{ marginBottom: 6, fontWeight: 600 }}>Expertise Level</div>
                <select
                  className="search-input"
                  value={profile.expertise_level}
                  onChange={(event) => setProfile({ ...profile, expertise_level: event.target.value })}
                >
                  <option value="beginner">Beginner</option>
                  <option value="intermediate">Intermediate</option>
                  <option value="advanced">Advanced</option>
                </select>
              </label>

              <label>
                <div style={{ marginBottom: 6, fontWeight: 600 }}>Research Interests</div>
                <textarea
                  className="note-textarea"
                  rows={4}
                  value={interestText}
                  onChange={(event) => setProfile({ ...profile, research_interests: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) })}
                  placeholder="stellar evolution, open clusters, spectroscopy"
                />
              </label>

              <div className="fits-meta">
                <dt>Preferred Databases</dt>
                <dd>{profile.preferred_databases.join(", ") || "None yet"}</dd>
                <dt>Preferred Methods</dt>
                <dd>{profile.preferred_analysis_methods.join(", ") || "None yet"}</dd>
                <dt>Frequently Queried Objects</dt>
                <dd>
                  {profile.frequently_queried_objects.length > 0
                    ? profile.frequently_queried_objects.map((item) => `${item.name} (${item.count}x)`).join(", ")
                    : "None yet"}
                </dd>
              </div>

              <button className="btn-primary" onClick={() => { void handleSaveProfile(); }} disabled={saving}>
                {saving ? "Saving..." : "Save Profile"}
              </button>
            </div>
          )}
        </div>

        <div className="workspace-detail" style={{ display: "block" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
            <h3>Session Timeline</h3>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="search-input"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search summaries..."
                style={{ minWidth: 220 }}
              />
              <button className="btn-secondary" onClick={() => { void loadAll(query || undefined); }}>
                Search
              </button>
            </div>
          </div>

          {loading ? (
            <div className="fits-loading">Loading history...</div>
          ) : history.length === 0 ? (
            <div className="fits-hint">No remembered sessions yet. Enable memory and save some chats first.</div>
          ) : (
            <div style={{ display: "grid", gap: 12 }}>
              {history.map((item) => (
                <div key={item.id} className="note-card">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                    <div>
                      <strong>{item.created_at ? new Date(item.created_at).toLocaleString() : "Session"}</strong>
                      <p style={{ marginTop: 8 }}>{item.summary}</p>
                      <div style={{ color: "var(--color-text-secondary)", fontSize: "0.88rem" }}>
                        Objects: {item.objects.join(", ") || "n/a"} · Methods: {item.methods.join(", ") || "n/a"}
                      </div>
                    </div>
                    <button className="btn-secondary btn-small" onClick={() => handleContinue(item.session_id)}>
                      Continue This Research
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
          <div>
            <h2 style={{ marginBottom: 4 }}>Artifacts &amp; Long Jobs</h2>
            <div style={{ color: "var(--color-text-secondary)" }}>
              Durable outputs, integrity hashes, and work that continues across page refreshes.
            </div>
          </div>
          <button className="btn-secondary" onClick={() => { void loadAll(query || undefined); }}>
            Refresh
          </button>
        </div>

        <div className="workspace-layout">
          <div className="workspace-file-list" style={{ maxWidth: 520 }}>
            <h3>Long-running jobs</h3>
            {jobs.length === 0 ? (
              <div className="fits-hint">No background research jobs yet.</div>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                {jobs.map((job) => (
                  <div key={job.job_id} className="note-card">
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                      <strong>{job.tool_name}</strong>
                      <span>{job.status}</span>
                    </div>
                    <div style={{ color: "var(--color-text-secondary)", fontSize: "0.82rem", overflowWrap: "anywhere", marginTop: 6 }}>
                      {job.job_id}
                    </div>
                    {job.progress != null && (
                      <div style={{ marginTop: 8 }}>{Math.round(job.progress)}% {job.progress_message || ""}</div>
                    )}
                    {job.error && <div className="error-banner" style={{ marginTop: 8 }}>{job.error}</div>}
                    {(job.can_cancel || job.can_retry) && (
                      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                        {job.can_cancel && (
                          <button className="btn-secondary btn-small" disabled={activeJob === job.job_id} onClick={() => { void handleJobAction(job, "cancel"); }}>
                            Cancel
                          </button>
                        )}
                        {job.can_retry && (
                          <button className="btn-secondary btn-small" disabled={activeJob === job.job_id} onClick={() => { void handleJobAction(job, "retry"); }}>
                            Retry
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="workspace-detail" style={{ display: "block" }}>
            <h3>Research artifacts</h3>
            {artifacts.length === 0 ? (
              <div className="fits-hint">No saved files or exports yet.</div>
            ) : (
              <div style={{ display: "grid", gap: 10 }}>
                {artifacts.map((artifact) => {
                  const sha = typeof artifact.metadata?.sha256 === "string" ? artifact.metadata.sha256 : null;
                  return (
                    <div key={artifact.id} className="note-card">
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                        <div style={{ minWidth: 0 }}>
                          <strong style={{ overflowWrap: "anywhere" }}>{artifact.filename || artifact.object_id}</strong>
                          <div style={{ color: "var(--color-text-secondary)", fontSize: "0.86rem", marginTop: 5 }}>
                            {artifact.source} · {artifact.size_bytes.toLocaleString()} bytes
                          </div>
                          <div style={{ color: "var(--color-text-secondary)", fontSize: "0.78rem", marginTop: 5, overflowWrap: "anywhere" }}>
                            SHA-256: {sha || "legacy object — hash not recorded"}
                          </div>
                        </div>
                        <button className="btn-secondary btn-small" onClick={() => { void handleDownload(artifact); }}>
                          Download
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
