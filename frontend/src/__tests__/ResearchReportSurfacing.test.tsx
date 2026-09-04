import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n";
import { ChatMessageList } from "../pages/Chat/ChatMessageList";
import type { ConversationProvenance } from "../hooks/useConversationProvenance";
import type { DisplayMessage } from "../pages/Chat/chatStorage";

const REPORT_MARKDOWN = [
  "# Research Report",
  "",
  "## Scientific Question",
  "Does w deviate from -1 under DESI DR2 + Pantheon+?",
  "",
  "## Execution Trace",
  "| Cell | Model |",
  "|---|---|",
  "| lcdm_bao_sn | lcdm |",
  "",
].join("\n");

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

function researchTurnWithReport(): DisplayMessage {
  return {
    id: "m1",
    role: "assistant",
    content: "The report is ready.",
    actions: [
      {
        action: "export_research_report",
        label: "Report export",
        _auto_executed: true,
        tool_result: {
          analysis_status: "RESEARCH_REPORT_READY",
          markdown: REPORT_MARKDOWN,
        },
      },
    ],
  } as unknown as DisplayMessage;
}

function renderMessages(messages: DisplayMessage[]) {
  return render(
    <I18nProvider>
      <ChatMessageList
        messages={messages}
        loading={false}
        executingActions={new Set()}
        conversationProvenance={EMPTY_PROVENANCE}
        messagesEndRef={createRef<HTMLDivElement>()}
        setMessages={() => {}}
        setInput={() => {}}
        showToast={() => {}}
        handleSend={async () => {}}
        handleNewChat={() => {}}
        handleExecuteAction={async () => {}}
      />
    </I18nProvider>,
  );
}

describe("research report surfacing in a chat turn", () => {
  it("renders the report outside the collapsed 'Show raw tool cards' disclosure", () => {
    // A research turn folds every tool card into a collapsed <details>. The
    // report is the deliverable of the turn, not audit trail, so at least one
    // copy has to sit outside that disclosure — otherwise the only document
    // the reader takes away is hidden behind a control labelled "raw".
    const { container } = renderMessages([researchTurnWithReport()]);

    const rawCardsDisclosure = screen.getByText(/Show raw tool cards/).closest("details");
    expect(rawCardsDisclosure).not.toBeNull();

    const reportBodies = Array.from(
      container.querySelectorAll('[data-testid="research-report-markdown"]'),
    );
    expect(reportBodies.length).toBeGreaterThan(0);

    const outside = reportBodies.filter((body) => !rawCardsDisclosure!.contains(body));
    expect(outside.length).toBeGreaterThan(0);
    expect(outside[0].textContent).toContain("Does w deviate from -1");
    expect(container.querySelector(".chat-visible-research-report")).not.toBeNull();
  });

  it("surfaces nothing when the turn has no report markdown", () => {
    const message = researchTurnWithReport();
    (message.actions![0] as Record<string, unknown>).tool_result = {
      analysis_status: "RESEARCH_REPORT_READY",
      markdown: "   ",
    };
    const { container } = renderMessages([message]);
    expect(container.querySelector(".chat-visible-research-report")).toBeNull();
  });
});
