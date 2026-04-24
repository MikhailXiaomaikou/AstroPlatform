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
const authState = vi.hoisted(() => ({
  user: null as null | {
    id: string;
    username: string;
    email: string;
    subscription_tier: string;
    stripe_customer_id: string | null;
    display_name: string | null;
    avatar_url: string | null;
    google_linked: boolean;
  },
}));

vi.mock("../context/AuthContext", () => ({
  useAuth: () => ({
    user: authState.user,
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
  writeStoredAiProvider: vi.fn(),
  getPreferredAiProvider: vi.fn(() => "anthropic"),
  getPreferredAiModelProfile: vi.fn(() => "anthropic:default"),
  getAIBackendStatus: vi.fn(() => Promise.resolve({
    configured_backends: [],
    needs_setup: true,
    selected_model_status: {
      id: "anthropic:default",
      provider: "anthropic",
      model_id: "default",
      display_name: "Claude default",
      api_ready: true,
      resolved_model_id: "claude-sonnet-4-20250514",
      supports_tools: true,
      supports_reasoning: true,
    },
  })),
  AI_MODEL_OPTIONS: {
    anthropic: [{ id: "anthropic:default", label: "Claude default" }],
    openai: [{ id: "openai:gpt-5.5", label: "GPT-5.5" }],
    deepseek: [{ id: "deepseek:v4-pro", label: "DeepSeek V4 Pro" }],
  },
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
  publishPaperDraft: vi.fn(),
  unpublishPaperDraft: vi.fn(),
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
    authState.user = null;
    localStorage.clear();
    sessionStorage.clear();
  });

  // ── Basic rendering ──

  it("renders without crashing", () => {
    renderChatPage();
    // The page should render something visible
    expect(document.querySelector(".chat-input-area")).toBeTruthy();
  });

  it("does not show legacy global chat history to a signed-in account", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    authState.user = {
      id: "user-b",
      username: "user-b",
      email: "user-b@example.com",
      subscription_tier: "solo",
      stripe_customer_id: null,
      display_name: "User B",
      avatar_url: null,
      google_linked: false,
    };
    localStorage.setItem(
      "astro_chat_history",
      JSON.stringify([{ id: "legacy", role: "user", content: "other account secret" }]),
    );
    localStorage.setItem(
      "astro_chat_history:user:user-b",
      JSON.stringify([{ id: "own", role: "user", content: "my account chat" }]),
    );

    renderChatPage();

    expect(screen.queryByText("other account secret")).not.toBeInTheDocument();
    expect(await screen.findByText("my account chat")).toBeInTheDocument();
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
      const raw = localStorage.getItem("astro_chat_history:anon");
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
    await screen.findByText("Done");
  });

  it("does not drop a later streamed ADQL success when earlier final actions replay", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockImplementation(async (
      _messages,
      _context,
      _onThinking,
      _signal,
      onActions,
    ) => {
      onActions?.([
        {
          action: "run_adql",
          tool_result: { success: false, error: "timeout" },
          _auto_executed: true,
          _stream_preview: true,
          _tool_call_id: "adql-1",
        },
        {
          action: "run_adql",
          tool_result: { row_count: 1000, columns: ["ra"] },
          _auto_executed: true,
          _stream_preview: true,
          _tool_call_id: "adql-2",
        },
      ]);
      onActions?.([
        {
          action: "run_adql",
          tool_result: { success: false, error: "timeout", __tool_status__: "FAILED" },
          _auto_executed: true,
          _tool_call_id: "adql-1",
        },
        {
          action: "run_adql",
          tool_result: { row_count: 1000, columns: ["ra"], attempt_log: [{ stage: "mirror_success", message: "fallback succeeded" }] },
          _auto_executed: true,
          _tool_call_id: "adql-2",
        },
      ]);
      await new Promise((resolve) => setTimeout(resolve, 10));
      return {
        reply: "Done",
        actions: [
          {
            action: "run_adql",
            tool_result: { success: false, error: "timeout", __tool_status__: "FAILED" },
            _auto_executed: true,
            _tool_call_id: "adql-1",
          },
          {
            action: "run_adql",
            tool_result: { row_count: 1000, columns: ["ra"], attempt_log: [{ stage: "mirror_success", message: "fallback succeeded" }] },
            _auto_executed: true,
            _tool_call_id: "adql-2",
          },
        ],
      };
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "query Gaia" } });
    fireEvent.click(sendBtn);

    await waitFor(() => {
      const raw = localStorage.getItem("astro_chat_history:anon");
      const stored = JSON.parse(raw || "[]") as Array<{
        role?: string;
        actions?: Array<{ _tool_call_id?: string; tool_result?: { row_count?: number } }>;
      }>;
      const assistant = stored.find((m) => m.role === "assistant" && m.actions?.length === 2);
      expect(assistant?.actions?.[1]).toEqual(expect.objectContaining({
        _tool_call_id: "adql-2",
        tool_result: expect.objectContaining({ row_count: 1000 }),
      }));
    });
    await screen.findByText("Done");
  });

  it("renders run_python stderr even when the tool succeeds", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "Done",
      actions: [{
        action: "run_python",
        tool_result: {
          success: true,
          stdout: "stdout value=42\n",
          stderr: "UserWarning: check this warning\n",
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "show stderr" } });
    fireEvent.click(sendBtn);

    await screen.findByText("STDERR / WARNINGS");
    expect(screen.getByText(/UserWarning: check this warning/)).toBeInTheDocument();
    expect(document.querySelectorAll("[class*='stderr']").length).toBeGreaterThan(0);
  });

  it("folds repeated run_python stderr warnings", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "Done",
      actions: [{
        action: "run_python",
        tool_result: {
          success: true,
          stdout: "ok\n",
          stderr: [
            "UserWarning: duplicate warning",
            "UserWarning: duplicate warning",
            "UserWarning: duplicate warning",
          ].join("\n"),
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "show repeated stderr" } });
    fireEvent.click(sendBtn);

    await screen.findByText(/Folded repeated warnings/);
    expect(screen.getByText(/repeated 3 times/)).toBeInTheDocument();
  });

  it("does not render literal undefined in search_objects cards", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "Done",
      actions: [{
        action: "search_objects",
        tool_result: {
          total: 1,
          results: [{ source: "sdss", object_id: "SDSS-J001" }],
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "search sdss" } });
    fireEvent.click(sendBtn);

    await screen.findByText("SDSS-J001");
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
  });

  it("labels synthetic run_python stdout as audit-only", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "I cannot cite that output.",
      actions: [{
        action: "run_python",
        tool_result: {
          success: true,
          stdout: "mean=3.0\n",
          __tool_status__: "SYNTHETIC",
          __do_not_claim__: true,
          data_origin: "synthetic",
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "synthetic diagnostic" } });
    fireEvent.click(sendBtn);

    await screen.findByText(/Synthetic stdout is shown for audit only/);
    expect(screen.getByText(/Facts, numbers, and conclusions from synthetic tools/)).toBeInTheDocument();
    expect(screen.getByText(/mean=3.0/)).toBeInTheDocument();
  });

  it("shows literature table measurements as fit-ready and renders line-fit results", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "Fit completed.",
      actions: [
        {
          action: "extract_literature_tables",
          tool_result: {
            success: true,
            cache_key: "latest_literature_tables:test",
            tables: [{
              name: "Table 2",
              columns: ["Source", "log L[CII]", "FWHM"],
              rows: [["ALPINE_001", "8.1", "240"]],
              row_count: 1,
            }],
            line_measurements: [
              { source_name: "ALPINE_001", log_luminosity: 8.1, fwhm_km_s: 240 },
            ],
            line_measurement_count: 1,
          },
          _auto_executed: true,
          _tool_call_id: "tables-1",
        },
        {
          action: "fit_line_lfr",
          tool_result: {
            success: true,
            publication_ready: true,
            n_used: 6,
            cache_key: "latest_literature_tables:test",
            alpha: 8.0,
            beta: 0.5,
            pearson_r: 0.92,
            pearson_p: 0.001,
            scatter_dex: 0.12,
            citation_summary: { citations: ["arXiv:2211.04968"] },
          },
          _auto_executed: true,
          _tool_call_id: "fit-1",
        },
      ],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "fit cii relation" } });
    fireEvent.click(sendBtn);

    await screen.findByText("Ready for fitting");
    expect(screen.getByText("Publication-ready")).toBeInTheDocument();
    expect(screen.getByText(/log L = 8\.000 \+ 0\.500/)).toBeInTheDocument();
    expect(screen.getByText(/arXiv:2211\.04968/)).toBeInTheDocument();
  });

  it("does not render literal undefined in object info cards", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "Done",
      actions: [{
        action: "get_object_info",
        tool_result: {
          name: "Pleiades",
          object_type: "undefined",
          cross_ids: ["M 45"],
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "object info pleiades" } });
    fireEvent.click(sendBtn);

    await screen.findByText("Pleiades");
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
  });

  it("marks malformed run_python crash output as failed", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "The sandbox crashed.",
      actions: [{
        action: "run_python",
        tool_result: {
          error: "run_python returned an empty response (tool response malformed; check backend logs)",
          error_class: "SubprocessCrash",
          stdout: "",
          stderr: "",
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "crash python" } });
    fireEvent.click(sendBtn);

    await screen.findByText("❌ Failed");
    expect(screen.queryByText("auto")).not.toBeInTheDocument();
  });

  it("marks synthetic-declared sandbox crashes as failed", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "The sandbox failed.",
      actions: [{
        action: "run_python",
        tool_result: {
          success: false,
          error: "MemoryError: process hit its address-space limit",
          error_class: "oom",
          __tool_status__: "FAILED",
          data_origin: "synthetic",
          __do_not_claim__: true,
          stdout: "",
          stderr: "",
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "synthetic crash" } });
    fireEvent.click(sendBtn);

    await screen.findByText("❌ Failed");
    expect(screen.queryByText("⚠ SYNTHETIC")).not.toBeInTheDocument();
    expect(screen.getByText(/MemoryError/)).toBeInTheDocument();
  });

  it("marks suppressed run_python fallback as empty", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "No citeable data was returned.",
      actions: [{
        action: "run_python",
        tool_result: {
          __tool_status__: "EMPTY",
          __do_not_claim__: true,
          data_origin: "unavailable",
          analysis_status: "empty",
          success: true,
          stdout: "",
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "fallback after failed data" } });
    fireEvent.click(sendBtn);

    await screen.findByText("∅ Empty");
    expect(screen.getByText("Tool returned no data")).toBeInTheDocument();
  });

  it("surfaces provenance controls on auto-executed tool results", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "Gaia result ready.",
      actions: [{
        action: "run_adql",
        tool_result: {
          row_count: 1,
          columns: ["source_id", "parallax"],
          provenance: {
            datasets: [{
              service_key: "gaia",
              service_name: "Gaia DR3",
              archive_version: "Gaia DR3",
              ivoid: "ivo://esavo/gaia/dr3",
              article: "2023A&A...674A...1G",
              publisher: "European Space Agency / Gaia Archive",
              credits_page_url: "https://www.cosmos.esa.int/web/gaia-users/archive/credits",
              source_authority: "datacenter_non_standard_tag",
            }],
            field_bibcodes: {
              columns: {
                plx_bibcode: ["2020yCat.1350....0G"],
              },
            },
          },
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "query Gaia with provenance" } });
    fireEvent.click(sendBtn);

    await screen.findByText("Data Sources");
    expect(screen.getAllByText("Gaia DR3").length).toBeGreaterThan(0);
    expect(screen.getByText("2023A&A...674A...1G")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy Acknowledgement" })).toBeInTheDocument();
  });

  it("renders unavailable sources as maintenance rather than failed", async () => {
    vi.mocked(getStoredApiKeys).mockReturnValue({ anthropic: "sk-ant-test" });
    vi.mocked(sendChatMessage).mockResolvedValueOnce({
      reply: "SDSS is temporarily unavailable.",
      actions: [{
        action: "run_sdss_sql",
        tool_result: {
          __tool_status__: "UNAVAILABLE",
          __do_not_claim__: true,
          data_origin: "unavailable",
          analysis_status: "failed",
          error: "Connector(s) under maintenance during the provenance v2 rollout: sdss.",
          unavailable_sources: ["sdss"],
          available_alternatives: ["vizier", "gaia", "simbad", "ned", "2mass", "alma"],
        },
        _auto_executed: true,
      }],
    });

    renderChatPage();

    const textarea = document.querySelector("textarea.chat-input") as HTMLTextAreaElement;
    const sendBtn = document.querySelector(".btn-chat-send") as HTMLButtonElement;
    fireEvent.change(textarea, { target: { value: "query SDSS" } });
    fireEvent.click(sendBtn);

    await screen.findByText("Maintenance");
    expect(screen.getByText("Source under maintenance")).toBeInTheDocument();
    expect(screen.getByText(/Available alternatives: vizier, gaia, simbad, ned, 2mass, alma/)).toBeInTheDocument();
    expect(screen.queryByText("❌ Failed")).not.toBeInTheDocument();
    expect(screen.queryByText(/Error: Connector/)).not.toBeInTheDocument();
  });
});


describe("NextStepsPanel (W6 PART W) next-step actions", () => {
  it("exports NEXT_STEPS_PANEL_ACTIONS with Validate assumptions first as the leading action", async () => {
    const { NEXT_STEPS_PANEL_ACTIONS } = await import("../pages/Chat/ChatPage");
    expect(NEXT_STEPS_PANEL_ACTIONS.length).toBe(4);
    expect(NEXT_STEPS_PANEL_ACTIONS[0].label).toBe("Validate assumptions first");
    const prompt = NEXT_STEPS_PANEL_ACTIONS[0].prompt;
    expect(prompt).toMatch(/re-verify each of our key assumptions/i);
    expect(prompt).toMatch(/tool_result supplied the value/);
    expect(prompt).toMatch(/not measured this turn/);
  });

  it("keeps the three legacy next-step actions in place", async () => {
    const { NEXT_STEPS_PANEL_ACTIONS } = await import("../pages/Chat/ChatPage");
    const labels = NEXT_STEPS_PANEL_ACTIONS.map((s) => s.label);
    expect(labels).toContain("Export as notebook");
    expect(labels).toContain("Run sensitivity analysis");
    expect(labels).toContain("Search related literature");
  });
});
