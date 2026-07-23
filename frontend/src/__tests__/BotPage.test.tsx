import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BotPage from "../pages/Bot/BotPage";

const api = vi.hoisted(() => ({
  chatWithAutomationBot: vi.fn(),
  getAutomationResearchStatus: vi.fn(),
  getAutomationResearchReport: vi.fn(),
  triggerAutomationResearch: vi.fn(),
}));

vi.mock("../api/client", () => api);

const labels: Record<string, string> = {
  "bot.eyebrow": "Local automation",
  "bot.title": "Linked Research Bot",
  "bot.subtitle": "Local linked research console",
  "bot.connected": "Local bridge connected",
  "bot.connecting": "Connecting to local bridge",
  "bot.offline": "Local bridge unavailable",
  "bot.trigger": "Start this week's research",
  "bot.triggering": "Submitting…",
  "bot.running": "Research in progress",
  "bot.trigger_unavailable": "This week is up to date",
  "bot.refresh": "Refresh",
  "bot.mobile_tabs": "Bot console sections",
  "bot.tab.chat": "Conversation",
  "bot.tab.status": "Status",
  "bot.tab.report": "Report",
  "bot.chat.title": "SOL conversation",
  "bot.chat.subtitle": "Same local model",
  "bot.chat.unverified_notice": "Operator convenience only, not reviewed by the platform.",
  "bot.chat.empty_title": "Ask SOL about the research",
  "bot.chat.empty_body": "Web and Telegram use the same model.",
  "bot.chat.you": "You · Web",
  "bot.chat.thinking": "SOL is thinking…",
  "bot.chat.retry": "Retry",
  "bot.chat.input_label": "Message for SOL",
  "bot.chat.placeholder": "Ask a question",
  "bot.chat.sending": "Sending…",
  "bot.chat.send": "Send",
  "bot.chat.hint": "Enter to send",
  "bot.status.title": "Bot & research status",
  "bot.status.loading": "Loading status",
  "bot.status.bot": "Local Bot bridge",
  "bot.status.online": "online",
  "bot.status.offline": "offline",
  "bot.status.model": "Configured model",
  "bot.status.research": "Weekly research",
  "bot.status.primary_schedule": "Main schedule",
  "bot.status.recovery_schedule": "Recovery schedule",
  "bot.status.week": "Week",
  "bot.status.llm_calls": "Local model calls",
  "bot.status.executed": "Successful platform runs",
  "bot.status.review": "Needs review",
  "bot.status.notification": "Notification",
  "bot.status.none": "none",
  "bot.report.title": "Latest weekly report",
  "bot.report.loading": "Loading report",
  "bot.report.empty_title": "No weekly report yet",
  "bot.report.empty_body": "Start one research run.",
  "bot.report.truncated": "Report shortened",
  "bot.error.chat": "Chat failed",
  "bot.error.status": "Status failed",
  "bot.error.report": "Report failed",
  "bot.error.trigger": "Trigger failed",
  "bot.state.pending": "Waiting to run",
  "bot.state.running": "Running",
  "bot.state.completed": "Completed",
  "bot.disabled_title": "Local only",
  "bot.disabled_body": "Open locally",
};

vi.mock("../i18n", () => ({
  useI18n: () => ({
    lang: "en" as const,
    setLang: vi.fn(),
    t: (key: string) => labels[key] || key,
  }),
}));

const STATUS = {
  week_id: "2026-W28",
  status: "pending",
  status_label: "等待运行",
  bot_online: true,
  model: "gpt-5.6-sol" as const,
  schedule_primary: "Friday 18:30",
  schedule_recovery: "Saturday 10:30",
  llm_calls_used: 2,
  llm_calls_limit: 10,
  executed_success: 3,
  needs_review: 1,
  report_available: true,
  report_name: "2026-07-11.md",
  notification_status: "sent",
  can_trigger: true,
};

const REPORT = {
  week_id: "2026-W28",
  report_name: "2026-07-11.md",
  markdown: "# Weekly result\n\nThree platform runs completed.",
  truncated: false,
  updated_at: "2026-07-11T10:30:00+08:00",
};

describe("BotPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAutomationResearchStatus.mockResolvedValue(STATUS);
    api.getAutomationResearchReport.mockResolvedValue(REPORT);
    api.chatWithAutomationBot.mockResolvedValue({
      reply: "SOL answer",
      model: "gpt-5.6-sol",
    });
    api.triggerAutomationResearch.mockResolvedValue({
      week_id: "2026-W28",
      submitted: true,
      status: "pending",
      message: "Research submitted.",
    });
  });

  it("loads Bot status and the latest report independently", async () => {
    render(<BotPage />);

    expect(await screen.findByText("2026-W28")).toBeInTheDocument();
    expect(screen.getByText("Friday 18:30")).toBeInTheDocument();
    expect(screen.getByText("Weekly result")).toBeInTheDocument();
    expect(screen.getByText("Three platform runs completed.")).toBeInTheDocument();
  });

  it("treats a missing report as an empty state, not a page failure", async () => {
    api.getAutomationResearchReport.mockRejectedValue({ response: { status: 404 } });
    render(<BotPage />);

    expect(await screen.findByText("No weekly report yet")).toBeInTheDocument();
    expect(screen.queryByText("Report failed")).not.toBeInTheDocument();
    expect(await screen.findByText("2026-W28")).toBeInTheDocument();
  });

  it("sends the conversation to SOL and renders its Markdown reply", async () => {
    api.chatWithAutomationBot.mockResolvedValue({
      reply: "**Evidence:** ready",
      model: "gpt-5.6-sol",
    });
    render(<BotPage />);

    fireEvent.change(screen.getByLabelText("Message for SOL"), {
      target: { value: "What changed?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(api.chatWithAutomationBot).toHaveBeenCalledWith([
      { role: "user", content: "What changed?" },
    ]));
    expect(await screen.findByText("Evidence:")).toBeInTheDocument();
    expect(screen.getByText("What changed?")).toBeInTheDocument();
  });

  it("keeps a failed message visible and retries without duplicating it", async () => {
    api.chatWithAutomationBot
      .mockRejectedValueOnce(new Error("SOL unavailable"))
      .mockResolvedValueOnce({ reply: "Recovered", model: "gpt-5.6-sol" });
    render(<BotPage />);

    fireEvent.change(screen.getByLabelText("Message for SOL"), {
      target: { value: "Retry this question" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("SOL unavailable");
    expect(screen.getAllByText("Retry this question")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Recovered")).toBeInTheDocument();
    expect(api.chatWithAutomationBot).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("Retry this question")).toHaveLength(1);
  });

  it("never resends a reply above the server's per-message limit", async () => {
    // Regression (2026-07-23): a linked-model reply can exceed the backend's
    // 8000-character per-message cap; resending it made every later request
    // 422 until the turn left the 16-message window.
    const oversized = "x".repeat(8_001);
    api.chatWithAutomationBot
      .mockResolvedValueOnce({ reply: oversized, model: "gpt-5.6-sol" })
      .mockResolvedValueOnce({ reply: "Short answer", model: "gpt-5.6-sol" });
    render(<BotPage />);

    fireEvent.change(screen.getByLabelText("Message for SOL"), {
      target: { value: "First question" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.chatWithAutomationBot).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("Message for SOL"), {
      target: { value: "Second question" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.chatWithAutomationBot).toHaveBeenCalledTimes(2));

    const secondPayload = api.chatWithAutomationBot.mock.calls[1][0];
    expect(secondPayload).toEqual([
      { role: "user", content: "First question" },
      { role: "user", content: "Second question" },
    ]);
  });

  it("shows FastAPI validation errors as readable text", async () => {
    api.chatWithAutomationBot.mockRejectedValueOnce({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ["body", 0, "content"], msg: "String should have at most 8000 characters" },
          ],
        },
      },
    });
    render(<BotPage />);

    fireEvent.change(screen.getByLabelText("Message for SOL"), {
      target: { value: "Trigger validation" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "String should have at most 8000 characters",
    );
  });

  it("submits the weekly research trigger only once while it is pending", async () => {
    let resolveTrigger: ((value: {
      week_id: string;
      submitted: boolean;
      status: string;
      message: string;
    }) => void) | undefined;
    api.triggerAutomationResearch.mockReturnValue(new Promise((resolve) => {
      resolveTrigger = resolve;
    }));
    render(<BotPage />);

    const trigger = await screen.findByRole("button", { name: "Start this week's research" });
    fireEvent.click(trigger);
    fireEvent.click(trigger);
    expect(api.triggerAutomationResearch).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Submitting…" })).toBeDisabled();

    resolveTrigger?.({
      week_id: "2026-W28",
      submitted: true,
      status: "pending",
      message: "Research submitted.",
    });
    expect(await screen.findByText("Research submitted.")).toBeInTheDocument();
  });

  it("redacts unexpected absolute paths from reports", async () => {
    api.getAutomationResearchReport.mockResolvedValue({
      ...REPORT,
      markdown: "Saved at /Users/operator/private/report.md",
    });
    render(<BotPage />);

    expect(await screen.findByText(/Saved at \[local file\]/)).toBeInTheDocument();
    expect(screen.queryByText(/\/Users\/operator/)).not.toBeInTheDocument();
  });
});
