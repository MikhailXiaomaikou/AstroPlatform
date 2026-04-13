import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  addSharedSessionComment,
  forkSharedSession,
  getSharedSession,
  type SessionCommentItem,
  type SharedSessionPayload,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";

export default function SharedSessionPage() {
  const { token = "" } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [payload, setPayload] = useState<SharedSessionPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [savingComment, setSavingComment] = useState(false);
  const [forking, setForking] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("Share token is missing");
      setLoading(false);
      return;
    }
    setLoading(true);
    getSharedSession(token)
      .then(setPayload)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Failed to load shared session"))
      .finally(() => setLoading(false));
  }, [token]);

  const sortedComments = useMemo(
    () => [...(payload?.comments || [])].sort((a, b) => (a.created_at || "").localeCompare(b.created_at || "")),
    [payload?.comments],
  );

  const handleFork = async () => {
    if (!token) return;
    setForking(true);
    try {
      const fork = await forkSharedSession(token);
      localStorage.setItem("astro_chat_open_session", fork.id);
      navigate("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fork the shared session");
    } finally {
      setForking(false);
    }
  };

  const handleComment = async () => {
    if (!token || !comment.trim()) return;
    setSavingComment(true);
    try {
      await addSharedSessionComment(token, { content: comment.trim(), target_type: "general" });
      setPayload((prev) => prev ? {
        ...prev,
        comments: [
          ...prev.comments,
          {
            id: crypto.randomUUID(),
            user_id: user?.id || "me",
            target_type: "general",
            target_id: null,
            content: comment.trim(),
            parent_id: null,
            created_at: new Date().toISOString(),
          } satisfies SessionCommentItem,
        ],
      } : prev);
      setComment("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add comment");
    } finally {
      setSavingComment(false);
    }
  };

  if (loading) {
    return <div className="fits-loading" style={{ padding: "2rem" }}>Loading shared session...</div>;
  }
  if (error) {
    return <div className="error-banner" style={{ margin: "1rem" }}>{error}</div>;
  }
  if (!payload) {
    return <div className="fits-hint" style={{ padding: "2rem" }}>Shared session not found.</div>;
  }

  return (
    <div className="workspace-page">
      <div className="workspace-header">
        <div>
          <h1 style={{ marginBottom: 6 }}>{payload.session.title}</h1>
          <div style={{ color: "var(--color-text-secondary)" }}>
            Shared access: <strong>{payload.share.access_level}</strong>
            {payload.share.expires_at ? ` · Expires ${new Date(payload.share.expires_at).toLocaleString()}` : " · No expiry"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {payload.can_fork && (
            <button className="btn-primary" onClick={() => { void handleFork(); }} disabled={forking}>
              {forking ? "Forking..." : "Fork To My Workspace"}
            </button>
          )}
        </div>
      </div>

      <div className="workspace-layout">
        <div className="workspace-detail" style={{ display: "block" }}>
          <h3>Session Replay</h3>
          <div className="chat-messages" style={{ minHeight: 320 }}>
            {payload.session.messages.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`chat-message ${message.role}`}>
                <div className="chat-message-avatar">{message.role === "user" ? "U" : "AI"}</div>
                <div className="chat-message-body">
                  <div className="chat-message-content">
                    {String(message.content || "").split("\n").map((line, i) => (
                      <p key={i}>{line || "\u00A0"}</p>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="workspace-file-list" style={{ maxWidth: 360 }}>
          <h3>Comments</h3>
          {sortedComments.length === 0 ? (
            <div className="fits-hint">No comments yet.</div>
          ) : (
            sortedComments.map((item) => (
              <div key={item.id} className="note-card" style={{ marginBottom: 10 }}>
                <p style={{ marginBottom: 6 }}>{item.content}</p>
                <span className="note-date">{item.created_at ? new Date(item.created_at).toLocaleString() : "Unknown time"}</span>
              </div>
            ))
          )}

          {payload.can_comment ? (
            <div style={{ marginTop: 16 }}>
              <textarea
                className="note-textarea"
                rows={4}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="Add a comment for the session owner..."
              />
              <button className="btn-secondary" disabled={savingComment || !comment.trim()} onClick={() => { void handleComment(); }}>
                {savingComment ? "Posting..." : "Post Comment"}
              </button>
            </div>
          ) : (
            <div className="fits-hint" style={{ marginTop: 16 }}>
              {user ? "This share link is read-only." : "Sign in to fork or comment when the share link allows it."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
