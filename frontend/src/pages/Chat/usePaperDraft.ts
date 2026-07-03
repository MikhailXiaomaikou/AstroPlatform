// Paper-draft modal state cluster: validation, generation, editing,
// publishing. State and handlers moved verbatim from ChatPage.tsx.
import { useState, useCallback } from "react";
import {
  saveChatSession,
  validatePaperSession,
  generatePaperDraft,
  updatePaperDraft,
  publishPaperDraft,
  unpublishPaperDraft,
  type AnalysisValidationResult,
  type PaperDraftResponse,
  type UserProfile,
} from "../../api/client";
import type { DisplayMessage } from "./chatStorage";
import {
  getPaperSectionText,
  setPaperSectionText,
  type JournalFormat,
  type PaperTab,
  type ToastState,
} from "./chatHelpers";

export function usePaperDraft({
  user,
  messages,
  currentSessionId,
  setCurrentSessionId,
  refreshSessions,
  showToast,
  track,
}: {
  user: UserProfile | null;
  messages: DisplayMessage[];
  currentSessionId: string | null;
  setCurrentSessionId: (id: string | null) => void;
  refreshSessions: () => void;
  showToast: (message: string, tone?: ToastState["tone"]) => void;
  track: (eventType: string, data?: Record<string, unknown>) => void;
}) {
  const [paperModalOpen, setPaperModalOpen] = useState(false);
  const [paperSessionId, setPaperSessionId] = useState<string | null>(null);
  const [paperFormat, setPaperFormat] = useState<JournalFormat>("aastex");
  const [paperValidation, setPaperValidation] = useState<AnalysisValidationResult | null>(null);
  const [paperDraft, setPaperDraft] = useState<PaperDraftResponse | null>(null);
  const [paperEditorJson, setPaperEditorJson] = useState<Record<string, unknown> | null>(null);
  const [paperTab, setPaperTab] = useState<PaperTab>("abstract");
  const [paperLoading, setPaperLoading] = useState(false);
  const [paperGenerating, setPaperGenerating] = useState(false);
  const [paperSaving, setPaperSaving] = useState(false);

  const handleOpenPaperDraft = useCallback(async () => {
    if (!user) {
      showToast("Sign in to generate a paper draft", "info");
      return;
    }
    if (messages.length === 0) {
      showToast("Add some analysis messages before generating a paper draft", "info");
      return;
    }

    setPaperModalOpen(true);
    setPaperLoading(true);
    setPaperDraft(null);
    setPaperEditorJson(null);
    setPaperValidation(null);
    try {
      const sessionData = messages.map((m) => ({
        role: m.role,
        content: m.content,
        actions: m.actions,
        // Keep the per-reply validation summary in the server session copy —
        // this save path must not silently strip the honesty signal that the
        // auto-save / share paths persist.
        _validation: m._validation,
        _truncated: m._truncated,
      }));
      const saved = await saveChatSession(sessionData, currentSessionId || undefined);
      setCurrentSessionId(saved.id);
      setPaperSessionId(saved.id);
      refreshSessions();

      const validation = await validatePaperSession(saved.id);
      setPaperValidation(validation);
      setPaperTab("abstract");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper validation failed";
      showToast(detail, "error");
      setPaperModalOpen(false);
    } finally {
      setPaperLoading(false);
    }
    // setCurrentSessionId is ChatPage's useState setter (stable identity); dep array preserved verbatim from the pre-split code.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId, messages, refreshSessions, showToast, user]);

  const handleGeneratePaper = useCallback(async (overrideValidation = false) => {
    if (!paperSessionId) {
      showToast("Save the session before generating a paper draft", "error");
      return;
    }

    setPaperGenerating(true);
    try {
      const draft = await generatePaperDraft(paperSessionId, paperFormat, overrideValidation);
      setPaperDraft(draft);
      setPaperEditorJson(draft.paper_json);
      setPaperValidation(draft.validation);
      track("export.paper_draft", {
        journal_format: paperFormat,
        word_count: String(draft.paper_json.abstract || "").split(/\s+/).filter(Boolean).length,
        figures_count: Array.isArray((draft.paper_json.results as Record<string, unknown> | undefined)?.figures)
          ? (((draft.paper_json.results as Record<string, unknown>).figures as unknown[]) || []).length
          : 0,
        citations_count: (draft.bibtex.match(/@\w+\{/g) || []).length,
      });
      showToast("Paper draft generated", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper generation failed";
      showToast(detail, "error");
    } finally {
      setPaperGenerating(false);
    }
  }, [paperFormat, paperSessionId, showToast, track]);

  const handleSavePaperDraft = useCallback(async () => {
    if (!paperDraft || !paperEditorJson) return;
    setPaperSaving(true);
    try {
      const updated = await updatePaperDraft(paperDraft.id, paperEditorJson);
      setPaperDraft(updated);
      setPaperEditorJson(updated.paper_json);
      showToast("Paper draft saved", "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Saving paper draft failed";
      showToast(detail, "error");
    } finally {
      setPaperSaving(false);
    }
  }, [paperDraft, paperEditorJson, showToast]);

  const handleTogglePaperPublish = useCallback(async () => {
    if (!paperDraft) return;
    setPaperSaving(true);
    try {
      const updated = paperDraft.is_public
        ? await unpublishPaperDraft(paperDraft.id)
        : await publishPaperDraft(paperDraft.id);
      setPaperDraft(updated);
      setPaperEditorJson(updated.paper_json);
      if (updated.is_public && updated.public_url) {
        const absolute = new URL(updated.public_url, window.location.origin).toString();
        if (navigator.clipboard?.writeText) {
          void navigator.clipboard.writeText(absolute).catch(() => {});
        }
        showToast("Paper draft published and link copied", "success");
      } else {
        showToast("Paper draft unpublished", "success");
      }
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Paper publication update failed";
      showToast(detail, "error");
    } finally {
      setPaperSaving(false);
    }
  }, [paperDraft, showToast]);

  const handleRegeneratePaperSection = useCallback(async () => {
    if (!paperSessionId || !paperEditorJson) return;
    setPaperGenerating(true);
    try {
      const regenerated = await generatePaperDraft(
        paperSessionId,
        paperFormat,
        paperValidation?.overall_status === "FAIL",
      );
      const nextPaperJson = setPaperSectionText(
        paperEditorJson,
        paperTab,
        getPaperSectionText(regenerated.paper_json, paperTab),
      );
      setPaperEditorJson(nextPaperJson);
      if (paperDraft) {
        const updated = await updatePaperDraft(paperDraft.id, nextPaperJson);
        setPaperDraft(updated);
        setPaperEditorJson(updated.paper_json);
      }
      showToast(`Regenerated ${paperTab.replace(/_/g, " ")}`, "success");
    } catch (err) {
      const detail = err instanceof Error ? err.message : "Section regeneration failed";
      showToast(detail, "error");
    } finally {
      setPaperGenerating(false);
    }
  }, [paperDraft, paperEditorJson, paperFormat, paperSessionId, paperTab, paperValidation?.overall_status, showToast]);

  return {
    paperModalOpen,
    setPaperModalOpen,
    paperSessionId,
    paperFormat,
    setPaperFormat,
    paperValidation,
    paperDraft,
    paperEditorJson,
    setPaperEditorJson,
    paperTab,
    setPaperTab,
    paperLoading,
    paperGenerating,
    paperSaving,
    handleOpenPaperDraft,
    handleGeneratePaper,
    handleSavePaperDraft,
    handleTogglePaperPublish,
    handleRegeneratePaperSection,
  };
}
