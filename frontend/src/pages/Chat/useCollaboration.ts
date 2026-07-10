// Share-link and snapshot collaboration cluster. State and handlers
// moved verbatim from ChatPage.tsx.
import { useState, useCallback, type Dispatch, type SetStateAction } from "react";
import {
  createSessionShare,
  listSessionShares,
  revokeSessionShare,
  createSessionSnapshot,
  listSessionSnapshots,
  restoreSessionSnapshot,
  diffSessionSnapshots,
  loadChatSession,
  type SessionShareItem,
  type SessionSnapshotItem,
  type SessionSnapshotDiff,
  type UserProfile,
} from "../../api/client";
import { saveChatHistory, type DisplayMessage } from "./chatStorage";
import { deserializeDisplayMessage, type ShareAccessLevel, type ToastState } from "./chatHelpers";

export function useCollaboration({
  user,
  currentSessionId,
  ensurePersistedSession,
  showToast,
  setMessages,
  storageScope,
}: {
  user: UserProfile | null;
  currentSessionId: string | null;
  ensurePersistedSession: () => Promise<string>;
  showToast: (message: string, tone?: ToastState["tone"]) => void;
  setMessages: Dispatch<SetStateAction<DisplayMessage[]>>;
  storageScope: string;
}) {
  const [shareModalOpen, setShareModalOpen] = useState(false);
  const [shareAccessLevel, setShareAccessLevel] = useState<ShareAccessLevel>("view");
  const [shareExpiryHours, setShareExpiryHours] = useState<number>(72);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sessionShares, setSessionShares] = useState<SessionShareItem[]>([]);
  const [sessionSnapshots, setSessionSnapshots] = useState<SessionSnapshotItem[]>([]);
  const [snapshotName, setSnapshotName] = useState("");
  const [snapshotCompareSelection, setSnapshotCompareSelection] = useState<string[]>([]);
  const [snapshotDiff, setSnapshotDiff] = useState<SessionSnapshotDiff | null>(null);
  const [shareLoading, setShareLoading] = useState(false);

  const loadCollaborationState = useCallback(async (sessionId: string) => {
    if (!user) return;
    const [shares, snapshots] = await Promise.all([
      listSessionShares(sessionId),
      listSessionSnapshots(sessionId),
    ]);
    setSessionShares(shares);
    setSessionSnapshots(snapshots);
  }, [user]);

  const handleOpenCollaboration = useCallback(async () => {
    if (!user) {
      showToast("Sign in to share sessions and manage snapshots", "info");
      return;
    }
    try {
      const sessionId = await ensurePersistedSession();
      setShareLoading(true);
      await loadCollaborationState(sessionId);
      setShareModalOpen(true);
      setShareUrl(null);
      setSnapshotDiff(null);
      setSnapshotCompareSelection([]);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to prepare the session", "error");
    } finally {
      setShareLoading(false);
    }
  }, [ensurePersistedSession, loadCollaborationState, showToast, user]);

  const handleCreateShare = useCallback(async () => {
    try {
      const sessionId = await ensurePersistedSession();
      setShareLoading(true);
      const created = await createSessionShare(
        sessionId,
        shareAccessLevel,
        Number.isFinite(shareExpiryHours) && shareExpiryHours > 0 ? shareExpiryHours : undefined,
      );
      setShareUrl(created.share_url);
      await loadCollaborationState(sessionId);
      if (navigator.clipboard?.writeText) {
        void navigator.clipboard.writeText(created.share_url).catch(() => {});
      }
      showToast("Share link created and copied to clipboard", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create share link", "error");
    } finally {
      setShareLoading(false);
    }
  }, [ensurePersistedSession, loadCollaborationState, shareAccessLevel, shareExpiryHours, showToast]);

  const handleRevokeShare = useCallback(async (shareId: string) => {
    if (!currentSessionId) return;
    try {
      await revokeSessionShare(currentSessionId, shareId);
      await loadCollaborationState(currentSessionId);
      showToast("Share link revoked", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to revoke share", "error");
    }
  }, [currentSessionId, loadCollaborationState, showToast]);

  const handleCreateSnapshot = useCallback(async () => {
    try {
      const sessionId = await ensurePersistedSession();
      const label = snapshotName.trim() || `Snapshot ${new Date().toLocaleString()}`;
      await createSessionSnapshot(sessionId, label);
      setSnapshotName("");
      await loadCollaborationState(sessionId);
      showToast("Snapshot created", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to create snapshot", "error");
    }
  }, [ensurePersistedSession, loadCollaborationState, showToast, snapshotName]);

  const handleRestoreSnapshot = useCallback(async (snapshotId: string) => {
    if (!currentSessionId) return;
    try {
      await restoreSessionSnapshot(currentSessionId, snapshotId);
      const session = await loadChatSession(currentSessionId);
      const loaded: DisplayMessage[] = session.messages.map(deserializeDisplayMessage);
      setMessages(loaded);
      saveChatHistory(loaded, storageScope);
      await loadCollaborationState(currentSessionId);
      showToast("Snapshot restored", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to restore snapshot", "error");
    }
    // setMessages is ChatPage's useState setter (stable identity); dep array preserved verbatim from the pre-split code.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId, loadCollaborationState, showToast, storageScope]);

  const handleCompareSnapshots = useCallback(async () => {
    if (!currentSessionId || snapshotCompareSelection.length !== 2) return;
    try {
      const diff = await diffSessionSnapshots(
        currentSessionId,
        snapshotCompareSelection[0],
        snapshotCompareSelection[1],
      );
      setSnapshotDiff(diff);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to compare snapshots", "error");
    }
  }, [currentSessionId, showToast, snapshotCompareSelection]);

  return {
    shareModalOpen,
    setShareModalOpen,
    shareAccessLevel,
    setShareAccessLevel,
    shareExpiryHours,
    setShareExpiryHours,
    shareUrl,
    sessionShares,
    sessionSnapshots,
    snapshotName,
    setSnapshotName,
    snapshotCompareSelection,
    setSnapshotCompareSelection,
    snapshotDiff,
    shareLoading,
    handleOpenCollaboration,
    handleCreateShare,
    handleRevokeShare,
    handleCreateSnapshot,
    handleRestoreSnapshot,
    handleCompareSnapshots,
  };
}
