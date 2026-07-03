/**
 * Shared read-only session page must render TOOL EVIDENCE, not bare AI
 * prose (2026-07-03 honesty surfacing).
 *
 * Locks:
 * - assistant messages with stored auto-executed actions render the reused
 *   Chat ActionCard tool cards (read-only: no Execute button, no
 *   per-process provenance link);
 * - the per-message validation badge renders when the share payload
 *   carries _validation;
 * - old shares (messages without actions/_validation) render unchanged —
 *   prose only, no badge, no crash.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

Element.prototype.scrollIntoView = vi.fn();

// Pass i18n keys through so assertions are locale-independent.
vi.mock("../i18n", () => ({
  I18nProvider: ({ children }: { children: React.ReactNode }) => children,
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

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

// No canvas in jsdom.
vi.mock("react-plotly.js", () => ({
  __esModule: true,
  default: () => <div data-testid="mock-plot" />,
}));

vi.mock("../components/chat/MarkdownText", () => ({
  __esModule: true,
  default: ({ content }: { content: string }) => <div>{content}</div>,
}));

const sharedPayload = vi.hoisted(() => ({
  share: { access_level: "view" as const, expires_at: null },
  session: {
    schema_version: 2,
    id: "sess-1",
    title: "BAO cross-check session",
    messages: [
      { role: "user", content: "Fetch the parallax of HD 12345." },
      {
        role: "assistant",
        content: "The archive query returned a parallax of 5.0 mas.",
        actions: [
          {
            action: "run_adql",
            _auto_executed: true,
            _tool_call_id: "c1",
            tool_result: {
              success: true,
              rows: [{ target: "HD 12345", parallax: 5.0 }],
              row_count: 1,
              columns: ["target", "parallax"],
              analysis_status: "COMPLETED",
              data_origin: "archive",
              reproducibility: { run_id: "run-abc-123", query_hash: "ff00" },
            },
          },
        ],
        _validation: {
          schema_version: 1,
          numeric_gate: "passed",
          citation_gate: "passed",
          regen_count: 0,
          blocked: false,
          interventions: [],
        },
      },
      // Legacy share shape: assistant message with no actions / validation.
      { role: "assistant", content: "Older prose-only reply." },
    ],
    created_at: null,
    updated_at: null,
    paper_drafts: [],
  },
  comments: [],
  can_fork: false,
  can_comment: false,
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getSharedSession: vi.fn(() => Promise.resolve(sharedPayload)),
  };
});

import SharedSessionPage from "../pages/SharedSession/SharedSessionPage";

function renderSharedPage() {
  return render(
    <MemoryRouter initialEntries={["/shared/tok123"]}>
      <Routes>
        <Route path="/shared/:token" element={<SharedSessionPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("SharedSessionPage tool evidence", () => {
  it("renders stored tool-result cards alongside the AI prose", async () => {
    renderSharedPage();
    await waitFor(() => {
      expect(screen.getByText("BAO cross-check session")).toBeTruthy();
    });
    // Reused ActionCard renders the tool label + auto badge.
    expect(screen.getByText("Run ADQL query")).toBeTruthy();
    expect(screen.getByText("auto")).toBeTruthy();
    // The prose itself is still there.
    expect(
      screen.getByText(/The archive query returned a parallax of 5\.0 mas\./),
    ).toBeTruthy();
  });

  it("is read-only: no Execute button and no per-process provenance link", async () => {
    renderSharedPage();
    await waitFor(() => {
      expect(screen.getByText("Run ADQL query")).toBeTruthy();
    });
    expect(screen.queryByText("Execute")).toBeNull();
    expect(screen.queryByText(/chat\.provenance\.button/)).toBeNull();
  });

  it("renders the validation badge from the share payload", async () => {
    renderSharedPage();
    await waitFor(() => {
      expect(screen.getByText(/chat\.validation\.badge_passed/)).toBeTruthy();
    });
  });

  it("renders legacy messages without actions or validation unchanged", async () => {
    renderSharedPage();
    await waitFor(() => {
      expect(screen.getByText("Older prose-only reply.")).toBeTruthy();
    });
    // Exactly one badge on the page — the legacy message has none.
    expect(screen.getAllByText(/chat\.validation\.badge_/).length).toBe(1);
  });
});
