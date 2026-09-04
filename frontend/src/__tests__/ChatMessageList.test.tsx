/**
 * Thinking-timeline rendering contract (2026-09-02, H5).
 *
 * `agent_text` prose is streamed BEFORE the output gate runs, so the timeline
 * must not present it the way it presents a gated reply. When the backend
 * marks a step `draft`, the list renders a visible "draft · unverified" label
 * ahead of the prose; an unmarked step renders exactly as before (no label).
 *
 * Corrected 2026-09-03: an earlier note here said the streamed prose is
 * "persisted into the session audit trail". It is not — `chatStorage`'s
 * `serializeStored` drops `_thinking` entirely, and the backend's
 * `ChatSession.audit_log` holds server-owned signed evidence records, not
 * streamed events. The reason to label it is that it is un-gated on the wire,
 * which is reason enough.
 *
 * Also pinned here (2026-09-03 review): `redacted_count` reaches this list as
 * `ThinkingStep.redactedCount` and is SHOWN. It used to be parsed in
 * `client.ts` and then silently dropped on the way into the step, so a reader
 * saw `[withheld]` in the prose with no idea how many values were removed.
 */
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { I18nProvider } from "../i18n";
import type { ConversationProvenance } from "../hooks/useConversationProvenance";
import { ChatMessageList } from "../pages/Chat/ChatMessageList";
import type { DisplayMessage, ThinkingStep } from "../pages/Chat/chatStorage";

const EMPTY_PROVENANCE: ConversationProvenance = {
  datasets: [],
  fieldBibcodesByColumn: {},
  field_bibcodes: {},
  fieldBibcodeCount: 0,
  measurementReferenceCount: 0,
  fullyCovered: false,
  fully_covered: false,
  acknowledgementText: "",
};

function renderTimeline(thinking: ThinkingStep[]) {
  const message: DisplayMessage = {
    id: "pending-1",
    role: "assistant",
    content: "",
    _pending: { started_at: Date.now() },
    _thinking: thinking,
  };
  return render(
    <I18nProvider>
      <ChatMessageList
        messages={[message]}
        loading
        executingActions={new Set()}
        conversationProvenance={EMPTY_PROVENANCE}
        messagesEndRef={createRef<HTMLDivElement>()}
        setMessages={vi.fn()}
        setInput={vi.fn()}
        showToast={vi.fn()}
        handleSend={vi.fn()}
        handleNewChat={vi.fn()}
        handleExecuteAction={vi.fn()}
      />
    </I18nProvider>,
  );
}

describe("ChatMessageList thinking timeline", () => {
  it("labels a pre-gate agent_text draft as unverified", () => {
    const { container } = renderTimeline([
      {
        kind: "agent_text",
        text: "Chain finished: H0 = [withheld] km/s/Mpc.",
        draft: true,
      },
    ]);

    expect(screen.getByText("draft · unverified")).toBeInTheDocument();
    expect(
      container.querySelector(".chat-thinking-draft-label"),
    ).not.toBeNull();
    // The prose itself still renders next to the label.
    expect(
      screen.getByText(/Chain finished: H0 = \[withheld\] km\/s\/Mpc\./),
    ).toBeInTheDocument();
  });

  it("renders an unmarked agent_text step without a draft label", () => {
    const { container } = renderTimeline([
      { kind: "agent_text", text: "Looking at the extracted tables." },
    ]);

    expect(screen.queryByText("draft · unverified")).toBeNull();
    expect(container.querySelector(".chat-thinking-draft-label")).toBeNull();
    expect(
      screen.getByText(/Looking at the extracted tables\./),
    ).toBeInTheDocument();
  });

  it("shows how many values the honesty gates withheld from the draft", () => {
    const { container } = renderTimeline([
      {
        kind: "agent_text",
        text: "Chain finished: H0 = [withheld] +/- [withheld] km/s/Mpc.",
        draft: true,
        redactedCount: 2,
      },
    ]);

    expect(screen.getByText("draft · unverified")).toBeInTheDocument();
    const note = container.querySelector(".chat-thinking-redacted-count");
    expect(note).not.toBeNull();
    expect(note?.textContent).toContain("2");
    expect(note?.textContent).toContain("value(s) withheld");
  });

  it("shows no withheld-count note when nothing was redacted", () => {
    // A draft the gates left alone must not grow a "0 value(s) withheld"
    // badge — the note exists to distinguish a redacted draft from a clean
    // one, so an always-on badge would defeat it.
    const { container } = renderTimeline([
      {
        kind: "agent_text",
        text: "Looking at the extracted tables.",
        draft: true,
        redactedCount: 0,
      },
    ]);

    expect(screen.getByText("draft · unverified")).toBeInTheDocument();
    expect(container.querySelector(".chat-thinking-redacted-count")).toBeNull();
  });

  it("does not label non-agent_text steps", () => {
    renderTimeline([{ kind: "status", text: "Running the fact check…" }]);

    expect(screen.queryByText("draft · unverified")).toBeNull();
  });
});
