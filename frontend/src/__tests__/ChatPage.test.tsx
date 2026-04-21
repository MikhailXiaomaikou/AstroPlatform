/**
 * Tests for ChatPage — the AI research assistant interface.
 *
 * Tests cover rendering, API key prompt, template cards, message input,
 * and basic UI elements. All API calls and context providers are mocked.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";

// ── Mock scrollIntoView (not available in jsdom) ──
Element.prototype.scrollIntoView = vi.fn();

// ── Mock i18n — pass keys through as-is ──
vi.mock("../i18n", () => ({
  I18nProvider: ({ children }: { children: React.ReactNode }) => children,
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

// ── Mock auth context ──
vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    login: vi.fn(),
    register: vi.fn(),
    setupKeyLogin: vi.fn(),
    googleLogin: vi.fn(),
    logout: vi.fn(),
  }),
}));

// ── Mock tracking hook ──
vi.mock("../hooks/useTracking", () => ({
  useTracking: () => ({
    track: vi.fn(),
    sessionId: "test-session-id",
    setCurrentPage: vi.fn(),
    getEventCount: () => 0,
  }),
}));

// ── Mock workspace cache utilities ──
vi.mock("../utils/workspaceCache", () => ({
  findWorkspaceFile: () => undefined,
  buildPipelineDraft: () => ({ nodes: [], edges: [] }),
  registerWorkspaceExport: () => [],
}));

// ── Mock react-plotly.js (no canvas in jsdom) ──
vi.mock("react-plotly.js", () => ({
  __esModule: true,
  default: () => <div data-testid="mock-plot" />,
}));

// ── Mock MarkdownText — just render content as text ──
vi.mock("../components/chat/MarkdownText", () => ({
  __esModule: true,
  default: ({ content }: { content: string }) => <div>{content}</div>,
}));

// ── Mock API client ──
vi.mock("../api/client", () => ({
  sendChatMessage: vi.fn(),
  executeChatAction: vi.fn(),
  getStoredApiKeys: vi.fn(() => ({})),
  searchADS: vi.fn(),
  getBibTeX: vi.fn(),
  logOperation: vi.fn(),
  uploadFITS: vi.fn(),
  uploadGeneralFile: vi.fn(),
  saveChatSession: vi.fn(),
  listChatSessions: vi.fn(() => Promise.resolve([])),
  loadChatSession: vi.fn(),
  deleteChatSession: vi.fn(),
  importChatSession: vi.fn(),
  createSessionShare: vi.fn(),
  listSessionShares: vi.fn(() => Promise.resolve([])),
  revokeSessionShare: vi.fn(),
  createSessionSnapshot: vi.fn(),
  listSessionSnapshots: vi.fn(() => Promise.resolve([])),
  restoreSessionSnapshot: vi.fn(),
  diffSessionSnapshots: vi.fn(),
  exportChatMarkdown: vi.fn(),
  exportChatNotebook: vi.fn(),
  exportChatLatex: vi.fn(),
  exportChatBibTeX: vi.fn(),
  generatePaperDraft: vi.fn(),
  updatePaperDraft: vi.fn(),
  validatePaperSession: vi.fn(),
}));

// ── Import component under test (after mocks) ──
import ChatPage from "../pages/Chat/ChatPage";
import { getStoredApiKeys, sendChatMessage } from "../api/client";

/* ── Helper to render with providers ── */

function renderChatPage() {
  return render(
    <BrowserRouter>
      <ChatPage />
    </BrowserRouter>,
  );
}

/* ── Tests ── */

describe("ChatPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  // ── Basic rendering ──

  it("renders without crashing", () => {
    renderChatPage();
    // The page should render something visible
    expect(document.querySelector(".chat-input-area")).toBeTruthy();
  });

  // ── API key prompt ──

  it("shows API key prompt when no key is stored", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({});
    renderChatPage();

    expect(screen.getByText("Configure API Key")).toBeInTheDocument();
    expect(
      screen.getByText(
        "To use the AI assistant, enter an API key from any supported provider.",
      ),
    ).toBeInTheDocument();
  });

  it("shows provider selection buttons in API key prompt", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({});
    renderChatPage();

    expect(
      screen.getByText(/Anthropic \(Claude\)/),
    ).toBeInTheDocument();
    expect(screen.getByText(/OpenAI \(GPT\)/)).toBeInTheDocument();
    expect(screen.getByText(/DeepSeek/)).toBeInTheDocument();
  });

  it("shows Save & Start button in API key prompt", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({});
    renderChatPage();

    const saveBtn = screen.getByRole("button", { name: /Save & Start/i });
    expect(saveBtn).toBeInTheDocument();
    expect(saveBtn).toBeDisabled(); // empty input = disabled
  });

  // ── New chat / Import buttons ──

  it("shows new chat button", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    renderChatPage();

    expect(screen.getByText("chat.new_chat")).toBeInTheDocument();
  });

  it("shows import button", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    renderChatPage();

    expect(screen.getByText("chat.import")).toBeInTheDocument();
  });

  // ── Template cards ──

  it("renders template cards when key is configured and no messages", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    renderChatPage();

    // Should show the research assistant prompt
    expect(
      screen.getByText("How can I help with your research?"),
    ).toBeInTheDocument();

    // Template titles should be rendered (as i18n keys)
    expect(screen.getByText("template.hr_diagram")).toBeInTheDocument();
    expect(screen.getByText("template.galaxy_redshift")).toBeInTheDocument();
    expect(screen.getByText("template.variable_star")).toBeInTheDocument();
    expect(screen.getByText("template.spectral")).toBeInTheDocument();
    expect(screen.getByText("template.highz")).toBeInTheDocument();
    expect(screen.getByText("template.supernova")).toBeInTheDocument();
  });

  it("renders difficulty badges on template cards", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    const { container } = renderChatPage();

    // Check that difficulty badges exist (6 templates = 6 badges)
    const badges = container.querySelectorAll("[class*='chat-template-badge']");
    expect(badges.length).toBe(6);
  });

  // ── Message input ──

  it("renders message input textarea when key is configured", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input");
    expect(textarea).toBeTruthy();
    expect(textarea).not.toBeDisabled();
  });

  it("renders send button (disabled when input is empty)", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    renderChatPage();

    const sendBtn = document.querySelector(".btn-chat-send");
    expect(sendBtn).toBeTruthy();
    expect((sendBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows chat hint text", () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    renderChatPage();

    // The hint is rendered with the i18n key
    expect(screen.getByText("chat.hint")).toBeInTheDocument();
  });

  it("persists streamed tool actions to localStorage before the turn finishes", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockImplementation(async (
      _messages,
      _context,
      _onThinking,
      _signal,
      onActions,
    ) => {
      onActions?.([{
        action: "run_adql",
        tool_result: { row_count: 3 },
        _auto_executed: true,
        _stream_preview: true,
      }]);
      await new Promise((resolve) => setTimeout(resolve, 25));
      return {
        reply: "Done",
        actions: [{
          action: "run_adql",
          tool_result: { row_count: 3, rows: [{ id: 1 }] },
          _auto_executed: true,
        }],
      };
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "query Gaia" } });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      const raw = localStorage.getItem("astro_chat_history");
      expect(raw).toBeTruthy();
      const stored = JSON.parse(raw || "[]") as Array<{
        role?: string;
        actions?: Array<{ action?: string; _stream_preview?: boolean }>;
      }>;
      const assistant = stored.find((m) => m.role === "assistant" && m.actions?.length);
      expect(assistant?.actions?.[0]).toEqual(expect.objectContaining({
        action: "run_adql",
        _stream_preview: true,
      }));
    });
  });
});
