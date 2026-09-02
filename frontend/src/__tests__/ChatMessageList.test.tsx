/**
 * Thinking-timeline rendering contract (2026-09-02, H5).
 *
 * `agent_text` prose is streamed BEFORE the output gate runs and is
 * persisted into the session audit trail, so the timeline must not present
 * it the way it presents a gated reply. When the backend marks a step
 * `draft`, the list renders a visible "draft · unverified" label ahead of
 * the prose; an unmarked step renders exactly as before (no label).
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

  it("does not label non-agent_text steps", () => {
    renderTimeline([{ kind: "status", text: "Running the fact check…" }]);

    expect(screen.queryByText("draft · unverified")).toBeNull();
  });
});
