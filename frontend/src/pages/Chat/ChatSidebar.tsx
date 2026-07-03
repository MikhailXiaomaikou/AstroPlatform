// Persistent session sidebar (like Claude desktop) with the user-tools
// panel. JSX moved verbatim from ChatPage.tsx; all state stays in ChatPage.
import type { MouseEvent } from "react";
import type { ChatSessionSummary, UserProfile } from "../../api/client";
import type { UserToolDefinition } from "../../api/userTools";

export function ChatSidebar({
  sidebarCollapsed,
  setSidebarCollapsed,
  sessionSearch,
  setSessionSearch,
  sessions,
  filteredSessions,
  currentSessionId,
  user,
  userTools,
  userToolsLoading,
  userToolsError,
  refreshUserTools,
  handleUseUserTool,
  handleLoadSession,
  handleDeleteSession,
  handleNewChat,
  navigate,
}: {
  sidebarCollapsed: boolean;
  setSidebarCollapsed: (collapsed: boolean) => void;
  sessionSearch: string;
  setSessionSearch: (value: string) => void;
  sessions: ChatSessionSummary[];
  filteredSessions: ChatSessionSummary[];
  currentSessionId: string | null;
  user: UserProfile | null;
  userTools: UserToolDefinition[];
  userToolsLoading: boolean;
  userToolsError: string | null;
  refreshUserTools: () => Promise<void>;
  handleUseUserTool: (tool: UserToolDefinition) => void;
  handleLoadSession: (id: string) => Promise<void>;
  handleDeleteSession: (id: string) => Promise<void>;
  handleNewChat: (event?: MouseEvent<HTMLAnchorElement | HTMLButtonElement>) => void;
  navigate: (to: string) => void;
}) {
  return (
      <aside className="chat-sidebar" aria-label="Chat sessions">
        <div className="chat-sidebar-header">
          <a
            href="/chat?fresh_chat=1"
            role="button"
            className="chat-sidebar-new"
            data-fresh-chat="true"
            title="New chat"
            onClick={handleNewChat}
          >
            <span style={{ fontSize: "1.1rem" }}>+</span> New chat
          </a>
          <button
            type="button"
            className="chat-sidebar-toggle"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? "»" : "«"}
          </button>
        </div>
        {!sidebarCollapsed && (
          <>
            <div className="chat-sidebar-search">
              <input
                type="search"
                placeholder="Search chats..."
                value={sessionSearch}
                onChange={(e) => setSessionSearch(e.target.value)}
                className="chat-sidebar-search-input"
              />
            </div>
            <div className="chat-sidebar-list" role="list">
              {sessions.length === 0 && (
                <p className="chat-sidebar-empty">
                  {user ? "No saved chats yet." : "Sign in to sync chats across devices."}
                </p>
              )}
              {filteredSessions.map((s) => (
                <div
                  key={s.id}
                  className={`chat-sidebar-item${s.id === currentSessionId ? " active" : ""}`}
                  role="listitem"
                >
                  <button
                    className="chat-sidebar-item-load"
                    onClick={() => handleLoadSession(s.id)}
                    title={s.title}
                  >
                    <span className="chat-sidebar-item-title">{s.title || "New Chat"}</span>
                    <span className="chat-sidebar-item-meta">
                      {s.message_count} msg · {new Date(s.updated_at).toLocaleDateString()}
                    </span>
                  </button>
                  <button
                    className="chat-sidebar-item-delete"
                    onClick={(e) => { e.stopPropagation(); handleDeleteSession(s.id); }}
                    title="Delete"
                    aria-label={`Delete ${s.title}`}
                  >
                    ×
                  </button>
                </div>
              ))}
              {filteredSessions.length === 0 && sessions.length > 0 && (
                <p className="chat-sidebar-empty">No chats match your search.</p>
              )}
            </div>
            <div className="chat-sidebar-tools" aria-label="User tools">
              <div className="chat-sidebar-tools-header">
                <span>User Tools</span>
                <div className="chat-sidebar-tools-actions">
                  <button
                    type="button"
                    className="chat-sidebar-tools-link"
                    onClick={() => { void refreshUserTools(); }}
                    disabled={!user || userToolsLoading}
                    title="Refresh user tools"
                  >
                    Refresh
                  </button>
                  <button
                    type="button"
                    className="chat-sidebar-tools-link"
                    onClick={() => navigate("/account?tab=tools")}
                  >
                    Manage
                  </button>
                </div>
              </div>
              {!user ? (
                <p className="chat-sidebar-empty">Sign in to create reusable tools.</p>
              ) : userToolsLoading ? (
                <p className="chat-sidebar-empty">Loading tools...</p>
              ) : userToolsError ? (
                <p className="chat-sidebar-empty">{userToolsError}</p>
              ) : userTools.length === 0 ? (
                <p className="chat-sidebar-empty">No user tools yet.</p>
              ) : (
                <div className="chat-sidebar-tools-list">
                  {userTools.map((tool) => (
                    <button
                      type="button"
                      key={tool.tool_id}
                      className="chat-sidebar-tool-item"
                      onClick={() => handleUseUserTool(tool)}
                      title={tool.description}
                    >
                      <span className="chat-sidebar-item-title">
                        {tool.display_name || tool.tool_id}
                      </span>
                      <span className="chat-sidebar-item-meta">
                        {tool.tool_id} · {tool.steps?.length || 0} step{tool.steps?.length === 1 ? "" : "s"}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </>
        )}
      </aside>
  );
}
