// Chat export pipeline: per-format busy flags, workspace sync, and the
// shared export handler. Moved verbatim from ChatPage.tsx.
import { useState, useCallback } from "react";
import { uploadGeneralFile, type UserProfile } from "../../api/client";
import { registerWorkspaceExport } from "../../utils/workspaceCache";
import { downloadBlob, type ExportAction, type ToastState } from "./chatHelpers";
import type { DisplayMessage } from "./chatStorage";

export function useChatExports({
  user,
  messages,
  storageScope,
  showToast,
  track,
}: {
  user: UserProfile | null;
  messages: DisplayMessage[];
  storageScope: string;
  showToast: (message: string, tone?: ToastState["tone"]) => void;
  track: (eventType: string, data?: Record<string, unknown>) => void;
}) {
  const [exporting, setExporting] = useState<Record<ExportAction, boolean>>({
    markdown: false,
    notebook: false,
    html: false,
    latex: false,
    bibtex: false,
  });

  const rememberExportInWorkspace = useCallback(async (
    blob: Blob,
    filename: string,
    exportKind: ExportAction,
  ): Promise<boolean> => {
    if (!user) return false;
    try {
      const upload = await uploadGeneralFile(
        new File([blob], filename, { type: blob.type || "application/octet-stream" })
      );
      registerWorkspaceExport({
        id: upload.id,
        filename: upload.filename,
        storagePath: upload.path,
        exportKind,
        contentType: blob.type || "application/octet-stream",
        sizeBytes: blob.size,
        localOnly: false,
      }, storageScope);
      return true;
    } catch {
      return false;
    }
  }, [user, storageScope]);

  const handleExport = useCallback(async (
    exportKind: ExportAction,
    label: string,
    filename: string,
    exporter: () => Promise<Blob>,
    options?: { emptyMessage?: string; fallback?: () => Blob; skipDownloadWhenEmpty?: boolean },
  ) => {
    setExporting((prev) => ({ ...prev, [exportKind]: true }));
    try {
      let blob = await exporter();
      if (blob.size === 0 && options?.skipDownloadWhenEmpty) {
        showToast(options.emptyMessage || `No ${label} content was available to export`, "info");
        return;
      }
      if (blob.size === 0) {
        if (options?.fallback) {
          blob = options.fallback();
        } else {
          throw new Error(options?.emptyMessage || `${label} export returned an empty file`);
        }
      }

      downloadBlob(blob, filename);
      const savedToWorkspace = await rememberExportInWorkspace(blob, filename, exportKind);
      const exportEventMap: Record<ExportAction, string> = {
        markdown: "export.paper_draft",
        notebook: "export.notebook",
        html: "export.html",
        latex: "export.latex",
        bibtex: "export.paper_draft",
      };
      const combinedText = messages.map((msg) => msg.content).join(" ");
      const bibcodeMatches = combinedText.match(/\b\d{4}[A-Za-z][A-Za-z&.]+\.+\S+/g) || [];
      track(exportEventMap[exportKind], {
        journal_format: exportKind === "latex" ? "aastex" : undefined,
        sections: exportKind === "latex" ? ["chat_export"] : undefined,
        figures_count: messages.filter((msg) => (msg.actions || []).some((action) => action.action === "plot")).length,
        citations_count: exportKind === "bibtex" ? bibcodeMatches.length : undefined,
        cell_count: exportKind === "notebook" ? messages.length + 2 : undefined,
        word_count: combinedText.split(/\s+/).filter(Boolean).length,
      });

      if (savedToWorkspace) {
        showToast(`Exported ${label} successfully — saved to Workspace`, "success");
      } else if (user) {
        showToast(`Exported ${label} successfully — downloaded locally, but Workspace sync failed`, "success");
      } else {
        showToast(`Exported ${label} successfully — downloaded locally. Sign in to sync it to Workspace.`, "success");
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : `${label} export failed`;
      showToast(`Export failed: ${detail}`, "error");
    } finally {
      setExporting((prev) => ({ ...prev, [exportKind]: false }));
    }
  }, [messages, rememberExportInWorkspace, showToast, track, user]);

  return { exporting, handleExport };
}
