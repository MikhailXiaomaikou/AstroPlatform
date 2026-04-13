/**
 * Tests for ADQLPage — the ADQL query editor and result viewer.
 *
 * Tests cover rendering, service selector, templates, query editor,
 * execute button, and history section. API calls are mocked.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BrowserRouter } from "react-router-dom";

// ── Mock tracking hook ──
vi.mock("../hooks/useTracking", () => ({
  useTracking: () => ({
    track: vi.fn(),
    sessionId: "test-session-id",
    setCurrentPage: vi.fn(),
    getEventCount: () => 0,
  }),
}));

// ── Mock react-plotly.js (no canvas in jsdom) ──
vi.mock("react-plotly.js", () => ({
  __esModule: true,
  default: () => <div data-testid="mock-plot" />,
}));

// ── Mock API client ──
vi.mock("../api/client", () => ({
  adqlQuery: vi.fn(),
  listADQLServices: vi.fn(() =>
    Promise.resolve([
      { id: "gaia", name: "Gaia" },
      { id: "simbad", name: "SIMBAD" },
      { id: "vizier", name: "VizieR" },
      { id: "cadc", name: "CADC" },
    ]),
  ),
  logOperation: vi.fn(),
  exportSearchNotebook: vi.fn(),
}));

// ── Import component under test (after mocks) ──
import ADQLPage from "../pages/ADQL/ADQLPage";
import { listADQLServices } from "../api/client";

/* ── Helper to render with providers ── */

function renderADQLPage() {
  return render(
    <BrowserRouter>
      <ADQLPage />
    </BrowserRouter>,
  );
}

/* ── Tests ── */

describe("ADQLPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  // ── Basic rendering ──

  it("renders without crashing", () => {
    renderADQLPage();
    expect(screen.getByText("ADQL Query")).toBeInTheDocument();
  });

  it("renders the page heading", () => {
    renderADQLPage();
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("ADQL Query");
  });

  // ── Service selector ──

  it("renders service selector buttons after services load", async () => {
    renderADQLPage();

    await waitFor(() => {
      expect(screen.getByText("Gaia")).toBeInTheDocument();
    });
    expect(screen.getByText("SIMBAD")).toBeInTheDocument();
    expect(screen.getByText("VizieR")).toBeInTheDocument();
    expect(screen.getByText("CADC")).toBeInTheDocument();
  });

  it("highlights Gaia as the default active service", async () => {
    renderADQLPage();

    await waitFor(() => {
      expect(screen.getByText("Gaia")).toBeInTheDocument();
    });

    const gaiaBtn = screen.getByText("Gaia");
    expect(gaiaBtn.classList.contains("active")).toBe(true);
  });

  it("switches active service on click", async () => {
    const user = userEvent.setup();
    renderADQLPage();

    await waitFor(() => {
      expect(screen.getByText("SIMBAD")).toBeInTheDocument();
    });

    await user.click(screen.getByText("SIMBAD"));
    const simbadBtn = screen.getByText("SIMBAD");
    expect(simbadBtn.classList.contains("active")).toBe(true);
  });

  // ── Template buttons ──

  it("renders Gaia template buttons by default", async () => {
    renderADQLPage();

    await waitFor(() => {
      expect(screen.getByText("Gaia")).toBeInTheDocument();
    });

    // Gaia templates
    expect(screen.getByText("Photometry")).toBeInTheDocument();
    expect(screen.getByText("Stellar params")).toBeInTheDocument();
    expect(screen.getByText("Radial velocity")).toBeInTheDocument();
    expect(screen.getByText("Nearby (plx>50)")).toBeInTheDocument();
  });

  it("shows SIMBAD templates when SIMBAD is selected", async () => {
    const user = userEvent.setup();
    renderADQLPage();

    await waitFor(() => {
      expect(screen.getByText("SIMBAD")).toBeInTheDocument();
    });

    await user.click(screen.getByText("SIMBAD"));

    expect(screen.getByText("z>4 galaxies")).toBeInTheDocument();
    expect(screen.getByText("QSOs")).toBeInTheDocument();
    expect(screen.getByText("Seyfert galaxies")).toBeInTheDocument();
  });

  // ── Query editor ──

  it("renders the ADQL editor textarea", () => {
    renderADQLPage();

    const textarea = document.querySelector(".adql-syn-textarea");
    expect(textarea).toBeTruthy();
  });

  it("loads default Gaia query into the editor", () => {
    renderADQLPage();

    const textarea = document.querySelector(
      ".adql-syn-textarea",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toContain("gaiadr3.gaia_source");
  });

  // ── Execute button ──

  it("renders the Run Query button", () => {
    renderADQLPage();

    const runBtn = screen.getByText("Run Query");
    expect(runBtn).toBeInTheDocument();
    expect(runBtn).not.toBeDisabled();
  });

  it("disables Run Query button when editor is empty", async () => {
    const user = userEvent.setup();
    renderADQLPage();

    const textarea = document.querySelector(
      ".adql-syn-textarea",
    ) as HTMLTextAreaElement;

    // Clear the textarea
    await user.clear(textarea);

    const runBtn = screen.getByText("Run Query");
    expect(runBtn).toBeDisabled();
  });

  // ── History section ──

  it("does not show history section when history is empty", () => {
    renderADQLPage();

    expect(screen.queryByText("Recent:")).not.toBeInTheDocument();
  });

  it("shows history entries when queries exist in localStorage", () => {
    localStorage.setItem(
      "astro_adql_history",
      JSON.stringify([
        "SELECT TOP 10 * FROM gaiadr3.gaia_source",
        "SELECT main_id FROM basic",
      ]),
    );

    renderADQLPage();

    expect(screen.getByText("Recent:")).toBeInTheDocument();
  });

  // ── API call ──

  it("calls listADQLServices on mount", () => {
    renderADQLPage();
    expect(listADQLServices).toHaveBeenCalledTimes(1);
  });
});
