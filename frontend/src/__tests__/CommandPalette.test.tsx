/**
 * Tests for CommandPalette — the Ctrl+K command palette overlay.
 *
 * Tests cover open/close behaviour, keyboard shortcuts, filtering,
 * navigation, and empty-results state.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";
import CommandPalette from "../components/CommandPalette";

// ── Mock react-router-dom navigate ──
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// ── Mock i18n — pass keys through as-is ──
vi.mock("../i18n", async () => {
  const actual = await vi.importActual<typeof import("../i18n")>("../i18n");
  return {
    ...actual,
    useI18n: () => ({
      lang: "en" as const,
      setLang: () => {},
      t: (key: string) => key,
    }),
  };
});

function renderPalette() {
  return render(
    <BrowserRouter>
      <I18nProvider>
        <CommandPalette />
      </I18nProvider>
    </BrowserRouter>,
  );
}

describe("CommandPalette", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Closed state ──

  it("does not render when closed (returns null)", () => {
    const { container } = renderPalette();
    expect(container.querySelector(".cmd-palette-backdrop")).toBeNull();
  });

  // ── Open via keyboard shortcut ──

  it("opens on Ctrl+K keyboard shortcut", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("opens on Meta+K keyboard shortcut", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  // ── Content when open ──

  it("shows input and command list when open", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    expect(screen.getByLabelText("Search commands")).toBeInTheDocument();
    // Should show at least one command option
    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThan(0);
  });

  // ── Filtering ──

  it("filters commands by query text", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    const input = screen.getByLabelText("Search commands");
    // All commands visible initially
    const allOptions = screen.getAllByRole("option");
    const initialCount = allOptions.length;

    // Type a query that matches only a subset (cmd.ai_assistant — Chat
    // is one of the surviving primary routes after the M3 deletion).
    fireEvent.change(input, { target: { value: "cmd.ai_assistant" } });
    const filteredOptions = screen.getAllByRole("option");
    expect(filteredOptions.length).toBeLessThan(initialCount);
    expect(filteredOptions.length).toBeGreaterThanOrEqual(1);
  });

  // ── NAV_ROUTES integrity: no command should navigate to a deleted route ──

  it("does not list commands targeting deleted M3 routes", async () => {
    const { NAV_ROUTES } = await import("../routes");
    const deletedPaths = ["/search", "/pipeline", "/adql", "/workspace"];
    for (const dead of deletedPaths) {
      expect(NAV_ROUTES.find((r) => r.path === dead)).toBeUndefined();
    }
  });

  it("lists the local linked Bot route in the test environment", async () => {
    const { NAV_ROUTES } = await import("../routes");
    expect(NAV_ROUTES.find((route) => route.path === "/bot")).toMatchObject({
      id: "nav-bot",
      labelKey: "cmd.research_bot",
    });
  });

  // ── Close on Escape ──

  it("closes on Escape key", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    const input = screen.getByLabelText("Search commands");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // ── Navigate on Enter ──

  it("navigates on Enter key", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    const input = screen.getByLabelText("Search commands");
    // The first command is "nav-search" which navigates to /search
    fireEvent.keyDown(input, { key: "Enter" });

    expect(mockNavigate).toHaveBeenCalled();
    // Palette should close after selection
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  // ── No match ──

  it("shows 'no match' when query has no results", () => {
    renderPalette();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });

    const input = screen.getByLabelText("Search commands");
    fireEvent.change(input, { target: { value: "xyznonexistent999" } });

    expect(screen.getByText("cmd.no_match")).toBeInTheDocument();
    expect(screen.queryAllByRole("option")).toHaveLength(0);
  });
});
