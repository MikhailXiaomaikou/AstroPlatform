import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  addSharedSessionComment,
  deleteSharedSessionComment,
  forkSharedSession,
  getSharedSession,
  type ChatAction,
  type SessionCommentItem,
  type SharedSessionPayload,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import MarkdownText from "../../components/chat/MarkdownText";
// Reuse the Chat page's extracted tool-evidence components (2026-07-03):
// shared read-only links must show the same tool cards + validation badge
// as the owner's chat, not bare AI prose.
import { ActionCard, ToolTurnSummary } from "../Chat/ActionCard";
import { ValidationBadge } from "../Chat/ValidationBadge";
import { validateActions } from "../Chat/chatHelpers";

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
  const [deletingCommentId, setDeletingCommentId] = useState<string | null>(null);

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
            can_delete: true,
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

  const handleDeleteComment = async (commentId: string) => {
    if (!token) return;
    setDeletingCommentId(commentId);
    try {
      await deleteSharedSessionComment(token, commentId);
      setPayload((prev) => prev ? {
        ...prev,
        comments: prev.comments.filter((item) => item.id !== commentId),
      } : prev);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete comment");
    } finally {
      setDeletingCommentId(null);
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
            {payload.session.messages.map((message, index) => {
              const isAssistant = message.role === "assistant";
              // Server-stored actions are unknown[]; keep only well-shaped
              // entries. Old shares without actions render as before.
              const actions = isAssistant ? validateActions(message.actions) : [];
              return (
                <div key={`${message.role}-${index}`} className={`chat-message ${message.role}`}>
                  <div className="chat-message-avatar">{message.role === "user" ? "U" : "AI"}</div>
                  <div className="chat-message-body">
                    <div className="chat-message-content">
                      {isAssistant ? (
                        <>
                          <ToolTurnSummary actions={actions.length > 0 ? actions : undefined} />
                          <MarkdownText content={String(message.content || "")} />
                          <ValidationBadge
                            summary={message._validation}
                            truncated={message._truncated}
                          />
                        </>
                      ) : (
                        String(message.content || "").split("\n").map((line, i) => (
                          <p key={i}>{line || "\u00A0"}</p>
                        ))
                      )}
                    </div>
                    {actions.length > 0 && (
                      <div className="chat-actions-list">
                        {actions.map((action: ChatAction, idx: number) => (
                          <ActionCard
                            key={idx}
                            action={action}
                            index={idx}
                            executing={false}
                            readOnly
                            onExecute={() => { /* read-only share view */ }}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          {payload.session.paper_drafts && payload.session.paper_drafts.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <h3>Paper Drafts</h3>
              {payload.session.paper_drafts.map((draft) => (
                <div key={draft.id} className="note-card" style={{ marginBottom: 10 }}>
                  <strong>{String(draft.paper_json?.title || "Untitled Draft")}</strong>
                  <div className="fits-hint" style={{ marginTop: 6 }}>
                    Format: {draft.journal_format.toUpperCase()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="workspace-file-list" style={{ maxWidth: 360 }}>
          <h3>Comments</h3>
          {sortedComments.length === 0 ? (
            <div className="fits-hint">No comments yet.</div>
          ) : (
            sortedComments.map((item) => (
              <div key={item.id} className="note-card" style={{ marginBottom: 10 }}>
                <p style={{ marginBottom: 6 }}>{item.content}</p>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}>
                  <span className="note-date">{item.created_at ? new Date(item.created_at).toLocaleString() : "Unknown time"}</span>
                  {item.can_delete && (
                    <button
                      className="btn-ghost"
                      disabled={deletingCommentId === item.id}
                      onClick={() => { void handleDeleteComment(item.id); }}
                    >
                      {deletingCommentId === item.id ? "Deleting..." : "Delete"}
                    </button>
                  )}
                </div>
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
