import { useEffect, useState } from "react";
import {
  inviteTeamMember,
  getTeamMembers,
  removeTeamMember,
  updateMemberRole,
  getSharedPipelines,
  getSharedDatasets,
  getPipelineComments,
  addPipelineComment,
  getFriends,
  sendFriendRequest,
  acceptFriend,
  rejectFriend,
  removeFriend,
  getSearchHistory,
  deleteSearchHistoryItem,
  clearSearchHistory,
  type TeamMember,
  type SharedPipelineItem,
  type SharedDatasetItem,
  type PipelineCommentItem,
  type FriendItem,
  type SearchHistoryItem,
} from "../../api/client";
import { useAuth } from "../../context/AuthContext";

type Tab = "friends" | "members" | "pipelines" | "datasets" | "history";

export default function TeamPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("friends");

  // Friends state
  const [friends, setFriends] = useState<FriendItem[]>([]);
  const [friendEmail, setFriendEmail] = useState("");
  const [friendsLoading, setFriendsLoading] = useState(true);
  const [friendError, setFriendError] = useState("");

  // Members state
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [membersLoading, setMembersLoading] = useState(false);
  const [inviteError, setInviteError] = useState("");

  // Shared pipelines state
  const [sharedPipelines, setSharedPipelines] = useState<SharedPipelineItem[]>([]);
  const [pipelinesLoading, setPipelinesLoading] = useState(false);
  const [selectedPipelineId, setSelectedPipelineId] = useState<string | null>(null);
  const [comments, setComments] = useState<PipelineCommentItem[]>([]);
  const [newComment, setNewComment] = useState("");
  const [commentsLoading, setCommentsLoading] = useState(false);

  // Shared datasets state
  const [sharedDatasets, setSharedDatasets] = useState<SharedDatasetItem[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);

  // Search history state
  const [searchHistory, setSearchHistory] = useState<SearchHistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Load friends on mount
  useEffect(() => {
    loadFriends();
  }, []);

  // Load tab data when tab changes
  useEffect(() => {
    if (tab === "members") loadMembers();
    if (tab === "pipelines") loadPipelines();
    if (tab === "datasets") loadDatasets();
    if (tab === "history") loadHistory();
  }, [tab]);

  async function loadFriends() {
    setFriendsLoading(true);
    try {
      const data = await getFriends();
      setFriends(data);
    } catch {
      // not logged in or no friends yet
    } finally {
      setFriendsLoading(false);
    }
  }

  async function loadMembers() {
    setMembersLoading(true);
    try {
      const data = await getTeamMembers();
      setMembers(data);
    } catch {
      // not on a team yet
    } finally {
      setMembersLoading(false);
    }
  }

  async function loadPipelines() {
    setPipelinesLoading(true);
    try {
      const data = await getSharedPipelines();
      setSharedPipelines(data);
    } catch {
      // ignore
    } finally {
      setPipelinesLoading(false);
    }
  }

  async function loadDatasets() {
    setDatasetsLoading(true);
    try {
      const data = await getSharedDatasets();
      setSharedDatasets(data);
    } catch {
      // ignore
    } finally {
      setDatasetsLoading(false);
    }
  }

  async function loadHistory() {
    setHistoryLoading(true);
    try {
      const data = await getSearchHistory();
      setSearchHistory(data);
    } catch {
      // ignore
    } finally {
      setHistoryLoading(false);
    }
  }

  // ── Friend handlers ──

  async function handleAddFriend(e: React.FormEvent) {
    e.preventDefault();
    setFriendError("");
    if (!friendEmail.trim()) return;
    try {
      await sendFriendRequest(friendEmail.trim());
      setFriendEmail("");
      loadFriends();
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "response" in err
          ? (err as { response: { data: { detail: string } } }).response?.data?.detail
          : "Failed to send friend request";
      setFriendError(msg || "Failed to send friend request");
    }
  }

  async function handleAccept(id: string) {
    try {
      await acceptFriend(id);
      loadFriends();
    } catch {
      // ignore
    }
  }

  async function handleReject(id: string) {
    try {
      await rejectFriend(id);
      loadFriends();
    } catch {
      // ignore
    }
  }

  async function handleRemoveFriend(id: string) {
    if (!confirm("Remove this friend?")) return;
    try {
      await removeFriend(id);
      loadFriends();
    } catch {
      // ignore
    }
  }

  // ── Team handlers ──

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    setInviteError("");
    if (!inviteEmail.trim()) return;
    try {
      await inviteTeamMember(inviteEmail.trim(), inviteRole);
      setInviteEmail("");
      setInviteRole("member");
      loadMembers();
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "response" in err
          ? (err as { response: { data: { detail: string } } }).response?.data?.detail
          : "Failed to invite member";
      setInviteError(msg || "Failed to invite member");
    }
  }

  async function handleRemove(memberId: string) {
    if (!confirm("Remove this team member?")) return;
    try {
      await removeTeamMember(memberId);
      loadMembers();
    } catch {
      // ignore
    }
  }

  async function handleRoleChange(memberId: string, newRole: string) {
    try {
      await updateMemberRole(memberId, newRole);
      loadMembers();
    } catch {
      // ignore
    }
  }

  async function openComments(templateId: string) {
    setSelectedPipelineId(templateId);
    setCommentsLoading(true);
    try {
      const data = await getPipelineComments(templateId);
      setComments(data);
    } catch {
      setComments([]);
    } finally {
      setCommentsLoading(false);
    }
  }

  async function handleAddComment(e: React.FormEvent) {
    e.preventDefault();
    if (!newComment.trim() || !selectedPipelineId) return;
    try {
      await addPipelineComment(selectedPipelineId, newComment.trim());
      setNewComment("");
      openComments(selectedPipelineId);
    } catch {
      // ignore
    }
  }

  // ── Search history handlers ──

  async function handleDeleteHistory(id: string) {
    try {
      await deleteSearchHistoryItem(id);
      setSearchHistory((prev) => prev.filter((h) => h.id !== id));
    } catch {
      // ignore
    }
  }

  async function handleClearHistory() {
    if (!confirm("Clear all search history?")) return;
    try {
      await clearSearchHistory();
      setSearchHistory([]);
    } catch {
      // ignore
    }
  }

  if (!user) {
    return (
      <div className="team-page">
        <h1>Team Collaboration</h1>
        <p className="empty-msg">Sign in to access team features.</p>
      </div>
    );
  }

  const isTeamTier = true; // beta: all users can use team features
  const pendingReceived = friends.filter((f) => f.status === "pending" && f.direction === "received");
  const pendingSent = friends.filter((f) => f.status === "pending" && f.direction === "sent");
  const accepted = friends.filter((f) => f.status === "accepted");

  return (
    <div className="team-page">
      <h1>Team Collaboration</h1>

      <div className="team-tabs">
        <button
          className={`team-tab ${tab === "friends" ? "active" : ""}`}
          onClick={() => setTab("friends")}
        >
          Friends{pendingReceived.length > 0 ? ` (${pendingReceived.length})` : ""}
        </button>
        <button
          className={`team-tab ${tab === "members" ? "active" : ""}`}
          onClick={() => setTab("members")}
        >
          Members
        </button>
        <button
          className={`team-tab ${tab === "pipelines" ? "active" : ""}`}
          onClick={() => setTab("pipelines")}
        >
          Shared Pipelines
        </button>
        <button
          className={`team-tab ${tab === "datasets" ? "active" : ""}`}
          onClick={() => setTab("datasets")}
        >
          Shared Datasets
        </button>
        <button
          className={`team-tab ${tab === "history" ? "active" : ""}`}
          onClick={() => setTab("history")}
        >
          Search History
        </button>
      </div>

      {/* ── Friends Tab ── */}
      {tab === "friends" && (
        <div className="team-section">
          <form className="team-invite-form" onSubmit={handleAddFriend}>
            <input
              type="email"
              placeholder="Add friend by email"
              value={friendEmail}
              onChange={(e) => setFriendEmail(e.target.value)}
              className="team-input"
            />
            <button type="submit" className="btn-primary">
              Add Friend
            </button>
          </form>
          {friendError && <p className="team-error">{friendError}</p>}

          {friendsLoading ? (
            <p className="empty-msg">Loading...</p>
          ) : (
            <>
              {/* Pending received */}
              {pendingReceived.length > 0 && (
                <div className="friend-section">
                  <h3 className="friend-section-title">Friend Requests</h3>
                  <ul className="team-member-list">
                    {pendingReceived.map((f) => (
                      <li key={f.id} className="team-member-item">
                        <div className="team-member-info">
                          <span className="team-member-email">{f.email}</span>
                          <span className="role-badge role-pending">pending</span>
                        </div>
                        <div className="team-member-actions">
                          <button className="btn-primary btn-sm" onClick={() => handleAccept(f.id)}>
                            Accept
                          </button>
                          <button className="btn-danger-sm" onClick={() => handleReject(f.id)}>
                            Reject
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Accepted friends */}
              {accepted.length > 0 && (
                <div className="friend-section">
                  <h3 className="friend-section-title">Friends</h3>
                  <ul className="team-member-list">
                    {accepted.map((f) => (
                      <li key={f.id} className="team-member-item">
                        <div className="team-member-info">
                          <span className="team-member-email">{f.email}</span>
                          <span className="role-badge role-member">friend</span>
                        </div>
                        <div className="team-member-actions">
                          <button className="btn-danger-sm" onClick={() => handleRemoveFriend(f.id)}>
                            Remove
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Pending sent */}
              {pendingSent.length > 0 && (
                <div className="friend-section">
                  <h3 className="friend-section-title">Sent Requests</h3>
                  <ul className="team-member-list">
                    {pendingSent.map((f) => (
                      <li key={f.id} className="team-member-item">
                        <div className="team-member-info">
                          <span className="team-member-email">{f.email}</span>
                          <span className="role-badge role-pending">pending</span>
                        </div>
                        <div className="team-member-actions">
                          <button className="btn-danger-sm" onClick={() => handleRemoveFriend(f.id)}>
                            Cancel
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {accepted.length === 0 && pendingReceived.length === 0 && pendingSent.length === 0 && (
                <p className="empty-msg">No friends yet. Add friends by email to start collaborating.</p>
              )}
            </>
          )}
        </div>
      )}

      {/* ── Members Tab ── */}
      {tab === "members" && (
        <div className="team-section">
          {!isTeamTier && (
            <div className="team-upgrade-banner">
              Team features require a <strong>Lab</strong> or <strong>Institution</strong> subscription.
              Upgrade to invite members and collaborate on pipelines.
            </div>
          )}
          {isTeamTier && (
            <form className="team-invite-form" onSubmit={handleInvite}>
              <input
                type="email"
                placeholder="Email address"
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                className="team-input"
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="team-select"
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
              <button type="submit" className="btn-primary">
                Invite
              </button>
            </form>
          )}
          {inviteError && <p className="team-error">{inviteError}</p>}

          {membersLoading ? (
            <p className="empty-msg">Loading members...</p>
          ) : members.length === 0 ? (
            <p className="empty-msg">No team members yet.</p>
          ) : (
            <ul className="team-member-list">
              {members.map((m) => (
                <li key={m.id} className="team-member-item">
                  <div className="team-member-info">
                    <span className="team-member-email">{m.email}</span>
                    <span className={`role-badge role-${m.role}`}>{m.role}</span>
                  </div>
                  <div className="team-member-actions">
                    <select
                      value={m.role}
                      onChange={(e) => handleRoleChange(m.id, e.target.value)}
                      className="team-select team-select-sm"
                    >
                      <option value="member">Member</option>
                      <option value="admin">Admin</option>
                    </select>
                    <button className="btn-danger-sm" onClick={() => handleRemove(m.id)}>
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ── Shared Pipelines Tab ── */}
      {tab === "pipelines" && (
        <div className="team-section">
          {pipelinesLoading ? (
            <p className="empty-msg">Loading shared pipelines...</p>
          ) : sharedPipelines.length === 0 ? (
            <p className="empty-msg">No pipelines have been shared with you.</p>
          ) : (
            <div className="team-shared-grid">
              {sharedPipelines.map((sp) => (
                <div key={sp.id} className="team-shared-card">
                  <div className="team-shared-card-header">
                    <h3>{sp.template_name}</h3>
                    <span className={`permission-badge perm-${sp.permission}`}>
                      {sp.permission}
                    </span>
                  </div>
                  <p className="team-shared-meta">
                    Shared by {sp.shared_by_email}
                    {sp.created_at && (
                      <> on {new Date(sp.created_at).toLocaleDateString()}</>
                    )}
                  </p>
                  <button
                    className="btn-secondary-sm"
                    onClick={() => openComments(sp.template_id)}
                  >
                    Comments
                  </button>
                </div>
              ))}
            </div>
          )}

          {selectedPipelineId && (
            <div className="team-comments-panel">
              <div className="team-comments-header">
                <h3>Comments</h3>
                <button className="btn-close" onClick={() => setSelectedPipelineId(null)}>
                  Close
                </button>
              </div>
              {commentsLoading ? (
                <p className="empty-msg">Loading comments...</p>
              ) : comments.length === 0 ? (
                <p className="empty-msg">No comments yet.</p>
              ) : (
                <ul className="team-comment-list">
                  {comments.map((c) => (
                    <li key={c.id} className="team-comment-item">
                      <div className="team-comment-meta">
                        <strong>{c.email}</strong>
                        {c.created_at && (
                          <span className="team-comment-date">
                            {new Date(c.created_at).toLocaleString()}
                          </span>
                        )}
                      </div>
                      <p className="team-comment-content">{c.content}</p>
                    </li>
                  ))}
                </ul>
              )}
              <form className="team-comment-form" onSubmit={handleAddComment}>
                <input
                  type="text"
                  placeholder="Add a comment..."
                  value={newComment}
                  onChange={(e) => setNewComment(e.target.value)}
                  className="team-input"
                />
                <button type="submit" className="btn-primary">
                  Post
                </button>
              </form>
            </div>
          )}
        </div>
      )}

      {/* ── Shared Datasets Tab ── */}
      {tab === "datasets" && (
        <div className="team-section">
          {datasetsLoading ? (
            <p className="empty-msg">Loading shared datasets...</p>
          ) : sharedDatasets.length === 0 ? (
            <p className="empty-msg">No datasets have been shared with you.</p>
          ) : (
            <ul className="team-dataset-list">
              {sharedDatasets.map((sd) => (
                <li key={sd.id} className="team-dataset-item">
                  <div className="team-dataset-info">
                    <span className="team-dataset-source">{sd.source}</span>
                    <span className="team-dataset-object">{sd.object_id}</span>
                  </div>
                  <p className="team-shared-meta">
                    Shared by {sd.shared_by_email}
                    {sd.created_at && (
                      <> on {new Date(sd.created_at).toLocaleDateString()}</>
                    )}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* ── Search History Tab ── */}
      {tab === "history" && (
        <div className="team-section">
          {searchHistory.length > 0 && (
            <div className="history-header">
              <button className="btn-danger-sm" onClick={handleClearHistory}>
                Clear All
              </button>
            </div>
          )}
          {historyLoading ? (
            <p className="empty-msg">Loading search history...</p>
          ) : searchHistory.length === 0 ? (
            <p className="empty-msg">No search history yet. Your searches will appear here.</p>
          ) : (
            <ul className="team-member-list">
              {searchHistory.map((h) => (
                <li key={h.id} className="team-member-item">
                  <div className="team-member-info" style={{ flex: 1 }}>
                    <span className="team-member-email">{h.query}</span>
                    <span className="role-badge role-member">
                      {h.result_count} results
                    </span>
                    {h.sources && (
                      <span className="history-sources">
                        {h.sources.split(",").map((s) => (
                          <span key={s} className="source-chip-sm">{s.trim()}</span>
                        ))}
                      </span>
                    )}
                  </div>
                  <div className="team-member-actions">
                    <span className="history-date">
                      {h.created_at ? new Date(h.created_at).toLocaleString() : ""}
                    </span>
                    <button className="btn-danger-sm" onClick={() => handleDeleteHistory(h.id)}>
                      Delete
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
