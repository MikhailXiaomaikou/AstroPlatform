import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  createResearchUpdateRecord,
  deleteResearchUpdateRecord,
  deleteResearchMemory,
  getResearchProfile,
  listResearchHistory,
  listResearchUpdateRecords,
  refreshResearchProfile,
  updateResearchUpdateRecord,
  updateResearchProfile,
  type ResearchHistoryItem,
  type ResearchProfile,
  type ResearchUpdateRecord,
} from "../../api/client";

export default function ResearchHistoryPage() {
  const navigate = useNavigate();
  const [profile, setProfile] = useState<ResearchProfile | null>(null);
  const [history, setHistory] = useState<ResearchHistoryItem[]>([]);
  const [updates, setUpdates] = useState<ResearchUpdateRecord[]>([]);
  const [query, setQuery] = useState("");
  const [updateQuery, setUpdateQuery] = useState("");
  const [newUpdateTitle, setNewUpdateTitle] = useState("");
  const [newUpdateBody, setNewUpdateBody] = useState("");
  const [newUpdateTags, setNewUpdateTags] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingUpdate, setSavingUpdate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAll = async (search?: string, updateSearch?: string) => {
    setLoading(true);
    try {
      const [nextProfile, nextHistory, nextUpdates] = await Promise.all([
        getResearchProfile(),
        listResearchHistory(search),
        listResearchUpdateRecords(updateSearch),
      ]);
      setProfile(nextProfile);
      setHistory(nextHistory);
      setUpdates(nextUpdates);
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
      await loadAll(query || undefined, updateQuery || undefined);
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
      await loadAll(query || undefined, updateQuery || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete memory");
    }
  };

  const updateTags = (raw: string) => raw
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

  const handleCreateUpdate = async () => {
    if (!newUpdateTitle.trim()) {
      setError("Update title is required");
      return;
    }
    setSavingUpdate(true);
    try {
      await createResearchUpdateRecord({
        title: newUpdateTitle.trim(),
        body: newUpdateBody.trim(),
        tags: updateTags(newUpdateTags),
        status: "active",
      });
      setNewUpdateTitle("");
      setNewUpdateBody("");
      setNewUpdateTags("");
      await loadAll(query || undefined, updateQuery || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save update record");
    } finally {
      setSavingUpdate(false);
    }
  };

  const handleStatusChange = async (record: ResearchUpdateRecord, status: string) => {
    try {
      const updated = await updateResearchUpdateRecord(record.id, { status });
      setUpdates((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to update record");
    }
  };

  const handleDeleteUpdate = async (recordId: string) => {
    if (!confirm("Delete this private update record?")) return;
    try {
      await deleteResearchUpdateRecord(recordId);
      setUpdates((current) => current.filter((item) => item.id !== recordId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete update record");
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
          <button className="btn-secondary" onClick={() => { void refreshResearchProfile(); void loadAll(query || undefined, updateQuery || undefined); }}>
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

          <div className="detail-section" style={{ display: "grid", gap: 10, marginTop: 16 }}>
            <h3>Private Update Record</h3>
            <input
              className="search-input"
              value={newUpdateTitle}
              onChange={(event) => setNewUpdateTitle(event.target.value)}
              placeholder="Short update title"
            />
            <textarea
              className="note-textarea"
              rows={4}
              value={newUpdateBody}
              onChange={(event) => setNewUpdateBody(event.target.value)}
              placeholder="What changed, what was tested, or what needs attention"
            />
            <input
              className="search-input"
              value={newUpdateTags}
              onChange={(event) => setNewUpdateTags(event.target.value)}
              placeholder="tags, comma separated"
            />
            <button className="btn-primary" onClick={() => { void handleCreateUpdate(); }} disabled={savingUpdate}>
              {savingUpdate ? "Saving..." : "Add Private Update"}
            </button>
          </div>
        </div>

        <div className="workspace-detail" style={{ display: "block" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, marginBottom: 12 }}>
            <h3>Private Update Log</h3>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="search-input"
                value={updateQuery}
                onChange={(event) => setUpdateQuery(event.target.value)}
                placeholder="Search updates..."
                style={{ minWidth: 220 }}
              />
              <button className="btn-secondary" onClick={() => { void loadAll(query || undefined, updateQuery || undefined); }}>
                Search
              </button>
            </div>
          </div>

          {loading ? (
            <div className="fits-loading">Loading updates...</div>
          ) : updates.length === 0 ? (
            <div className="fits-hint" style={{ marginBottom: 20 }}>
              No private update records yet.
            </div>
          ) : (
            <div style={{ display: "grid", gap: 12, marginBottom: 24 }}>
              {updates.map((item) => (
                <div key={item.id} className="note-card">
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                    <div>
                      <strong>{item.title}</strong>
                      <p style={{ marginTop: 8, whiteSpace: "pre-wrap" }}>{item.body || "No details."}</p>
                      <div style={{ color: "var(--color-text-secondary)", fontSize: "0.88rem" }}>
                        {item.created_at ? new Date(item.created_at).toLocaleString() : "Just now"}
                        {" · "}
                        {item.owner_locked ? "locked to your account" : "unlocked"}
                        {item.tags.length > 0 ? ` · ${item.tags.join(", ")}` : ""}
                      </div>
                    </div>
                    <div style={{ display: "grid", gap: 8, minWidth: 120 }}>
                      <select
                        className="search-input"
                        value={item.status}
                        onChange={(event) => { void handleStatusChange(item, event.target.value); }}
                      >
                        <option value="active">Active</option>
                        <option value="resolved">Resolved</option>
                        <option value="blocked">Blocked</option>
                        <option value="note">Note</option>
                      </select>
                      <button className="btn-secondary btn-small" onClick={() => { void handleDeleteUpdate(item.id); }}>
                        Delete
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

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
    </div>
  );
}
