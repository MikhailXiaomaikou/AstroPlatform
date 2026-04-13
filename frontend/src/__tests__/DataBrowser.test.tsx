/**
 * Tests for ResultsTable — the main table component in DataBrowser.
 *
 * Tests cover rendering with data, empty/loading states, sorting,
 * pagination, and row selection.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SearchResult } from "../api/client";

// ── Mock i18n — pass keys through as-is ──
vi.mock("../i18n", () => ({
  useI18n: () => ({
    lang: "en" as const,
    setLang: () => {},
    t: (key: string) => key,
  }),
}));

// ── Mock workspace cache utilities ──
vi.mock("../utils/workspaceCache", () => ({
  findWorkspaceFile: () => undefined,
  buildPipelineDraft: () => ({ nodes: [], edges: [] }),
}));

// ── Import component under test (after mocks) ──
import ResultsTable from "../pages/DataBrowser/ResultsTable";

/* ── Test data factories ── */

function makeResult(overrides: Partial<SearchResult> = {}): SearchResult {
  return {
    source: "simbad",
    object_id: "M31",
    name: "Andromeda Galaxy",
    ra: 10.6847,
    dec: 41.2687,
    object_type: "galaxy",
    magnitude: 3.44,
    redshift: -0.001,
    extra: {},
    error_type: null,
    z_source: null,
    photo_z: null,
    photo_z_err: null,
    ...overrides,
  };
}

function makeResults(n: number): SearchResult[] {
  return Array.from({ length: n }, (_, i) =>
    makeResult({
      object_id: `OBJ-${i}`,
      name: `Star ${i}`,
      ra: 10 + i,
      dec: 20 + i,
      magnitude: 5 + i * 0.1,
      redshift: 0.01 * i,
    }),
  );
}

/* ── Tests ── */

describe("ResultsTable", () => {
  const defaultProps = {
    results: [] as SearchResult[],
    onFetch: vi.fn(),
    fetchingId: null,
    loading: false,
    searched: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.removeItem("astro_column_visibility");
  });

  // ── Empty / loading states ──

  it("shows empty state when no results and not searched", () => {
    render(<ResultsTable {...defaultProps} />);
    // The component renders an empty state with the i18n key
    expect(screen.getByText("search.empty")).toBeInTheDocument();
  });

  it("shows 'no results' state after searching with zero results", () => {
    render(<ResultsTable {...defaultProps} searched={true} />);
    expect(screen.getByText("search.no_results")).toBeInTheDocument();
  });

  it("shows skeleton rows while loading", () => {
    const results = [makeResult()];
    const { container } = render(
      <ResultsTable {...defaultProps} results={results} loading={true} />,
    );
    const skeletons = container.querySelectorAll(".skeleton-row");
    expect(skeletons.length).toBe(5);
  });

  // ── Rendering data ──

  it("renders table rows with result data", () => {
    const results = [
      makeResult({ name: "Sirius", object_id: "SIR-1", source: "gaia" }),
      makeResult({ name: "Betelgeuse", object_id: "BET-2", source: "sdss" }),
    ];
    render(<ResultsTable {...defaultProps} results={results} />);

    expect(screen.getByText("Sirius")).toBeInTheDocument();
    expect(screen.getByText("Betelgeuse")).toBeInTheDocument();
    expect(screen.getByText("GAIA")).toBeInTheDocument();
    expect(screen.getByText("SDSS")).toBeInTheDocument();
  });

  it("displays formatted RA/Dec values", () => {
    const results = [makeResult({ ra: 123.45678, dec: -45.12345 })];
    render(<ResultsTable {...defaultProps} results={results} />);

    expect(screen.getByText("123.45678")).toBeInTheDocument();
    expect(screen.getByText("-45.12345")).toBeInTheDocument();
  });

  it("shows dash for null magnitude and redshift", () => {
    const results = [makeResult({ magnitude: null, redshift: null })];
    const { container } = render(
      <ResultsTable {...defaultProps} results={results} />,
    );
    // Dashes are em-dashes (U+2014)
    const cells = container.querySelectorAll("td");
    const dashCells = Array.from(cells).filter(
      (td) => td.textContent === "\u2014",
    );
    expect(dashCells.length).toBeGreaterThanOrEqual(2);
  });

  // ── Fetch button ──

  it("calls onFetch when fetch button is clicked", async () => {
    const user = userEvent.setup();
    const onFetch = vi.fn();
    const results = [
      makeResult({ source: "sdss", object_id: "SDSS-J001" }),
    ];
    render(
      <ResultsTable {...defaultProps} results={results} onFetch={onFetch} />,
    );

    const fetchBtn = screen.getByText("data.fetch_fits");
    await user.click(fetchBtn);
    expect(onFetch).toHaveBeenCalledWith("sdss", "SDSS-J001");
  });

  it("disables fetch button when that object is being fetched", () => {
    const results = [
      makeResult({ source: "sdss", object_id: "SDSS-J001" }),
    ];
    render(
      <ResultsTable
        {...defaultProps}
        results={results}
        fetchingId="sdss-SDSS-J001"
      />,
    );

    // The button should be disabled and show a spinner instead of text
    const buttons = screen.getAllByRole("button");
    const fetchBtn = buttons.find(
      (b) =>
        b.classList.contains("btn-fetch") &&
        (b as HTMLButtonElement).disabled,
    ) as HTMLButtonElement | undefined;
    expect(fetchBtn).toBeDefined();
    expect(fetchBtn!.disabled).toBe(true);
  });

  // ── Sorting ──

  it("sorts by name when name column header is clicked", async () => {
    const user = userEvent.setup();
    const results = [
      makeResult({ name: "Zeta Ori", object_id: "Z1" }),
      makeResult({ name: "Alpha Cen", object_id: "A1" }),
      makeResult({ name: "Mizar", object_id: "M1" }),
    ];
    render(<ResultsTable {...defaultProps} results={results} />);

    // Click the Name column header
    const nameHeader = screen.getByText("data.name");
    await user.click(nameHeader);

    // After sorting ascending, Alpha Cen should be first
    const nameCells = screen
      .getAllByRole("cell")
      .filter((c) => c.classList.contains("name-cell"));
    expect(nameCells[0]).toHaveTextContent("Alpha Cen");
    expect(nameCells[2]).toHaveTextContent("Zeta Ori");
  });

  it("reverses sort direction on second click", async () => {
    const user = userEvent.setup();
    const results = [
      makeResult({ name: "Zeta Ori", object_id: "Z1" }),
      makeResult({ name: "Alpha Cen", object_id: "A1" }),
    ];
    render(<ResultsTable {...defaultProps} results={results} />);

    const nameHeader = screen.getByText("data.name");
    await user.click(nameHeader); // ascending
    await user.click(nameHeader); // descending

    const nameCells = screen
      .getAllByRole("cell")
      .filter((c) => c.classList.contains("name-cell"));
    expect(nameCells[0]).toHaveTextContent("Zeta Ori");
    expect(nameCells[1]).toHaveTextContent("Alpha Cen");
  });

  // ── Pagination ──

  it("paginates results at 25 per page", () => {
    const results = makeResults(30);
    render(<ResultsTable {...defaultProps} results={results} />);

    // Should show pagination controls
    expect(screen.getByText(/1 \/ 2/)).toBeInTheDocument();
    expect(screen.getByText("common.previous")).toBeInTheDocument();
    expect(screen.getByText("common.next")).toBeInTheDocument();

    // Previous should be disabled on page 1
    expect(screen.getByText("common.previous")).toBeDisabled();
    expect(screen.getByText("common.next")).not.toBeDisabled();
  });

  it("navigates to next page", async () => {
    const user = userEvent.setup();
    const results = makeResults(30);
    render(<ResultsTable {...defaultProps} results={results} />);

    await user.click(screen.getByText("common.next"));

    // Should now be on page 2
    expect(screen.getByText(/2 \/ 2/)).toBeInTheDocument();
    // Next should be disabled on last page
    expect(screen.getByText("common.next")).toBeDisabled();
    expect(screen.getByText("common.previous")).not.toBeDisabled();
  });

  it("does not show pagination when results fit in one page", () => {
    const results = makeResults(5);
    render(<ResultsTable {...defaultProps} results={results} />);

    expect(screen.queryByText("common.previous")).not.toBeInTheDocument();
    expect(screen.queryByText("common.next")).not.toBeInTheDocument();
  });

  // ── Row selection ──

  it("renders checkboxes when onSelectionChange is provided", () => {
    const results = [makeResult()];
    render(
      <ResultsTable
        {...defaultProps}
        results={results}
        selectedKeys={new Set()}
        onSelectionChange={vi.fn()}
      />,
    );

    const checkboxes = screen.getAllByRole("checkbox");
    // 1 header checkbox + 1 row checkbox
    expect(checkboxes.length).toBe(2);
  });

  it("calls onSelectionChange when a row checkbox is toggled", async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    const results = [makeResult()];
    render(
      <ResultsTable
        {...defaultProps}
        results={results}
        selectedKeys={new Set<string>()}
        onSelectionChange={onSelectionChange}
      />,
    );

    const checkboxes = screen.getAllByRole("checkbox");
    // Click the row checkbox (not the header)
    await user.click(checkboxes[1]);
    expect(onSelectionChange).toHaveBeenCalled();
    // The new set should contain the row key
    const newSet = onSelectionChange.mock.calls[0][0] as Set<string>;
    expect(newSet.size).toBe(1);
  });

  it("select-all toggles all visible rows", async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    const results = makeResults(3);
    render(
      <ResultsTable
        {...defaultProps}
        results={results}
        selectedKeys={new Set<string>()}
        onSelectionChange={onSelectionChange}
      />,
    );

    const checkboxes = screen.getAllByRole("checkbox");
    // Click the header (select-all) checkbox
    await user.click(checkboxes[0]);
    expect(onSelectionChange).toHaveBeenCalled();
    const newSet = onSelectionChange.mock.calls[0][0] as Set<string>;
    expect(newSet.size).toBe(3);
  });

  // ── Extra columns ──

  it("renders extra columns from result data", () => {
    const results = [
      makeResult({
        extra: { survey: "DR3", distance_pc: 1234 },
      }),
    ];
    // Pre-set column visibility to include extras
    localStorage.setItem(
      "astro_column_visibility",
      JSON.stringify([
        "source",
        "name",
        "ra",
        "dec",
        "type",
        "magnitude",
        "redshift",
        "survey",
        "distance_pc",
      ]),
    );
    render(<ResultsTable {...defaultProps} results={results} />);

    expect(screen.getByText("survey")).toBeInTheDocument();
    expect(screen.getByText("distance_pc")).toBeInTheDocument();
    expect(screen.getByText("DR3")).toBeInTheDocument();
    expect(screen.getByText("1234")).toBeInTheDocument();
  });

  // ── Redshift badges ──

  it("shows spec badge for spectroscopic redshift", () => {
    const results = [
      makeResult({ redshift: 0.05, z_source: "spectroscopic" }),
    ];
    render(<ResultsTable {...defaultProps} results={results} />);

    expect(screen.getByText("spec")).toBeInTheDocument();
  });

  it("shows phot badge for photometric redshift", () => {
    const results = [
      makeResult({
        redshift: 0.12,
        z_source: "photometric",
        photo_z_err: 0.02,
      }),
    ];
    render(<ResultsTable {...defaultProps} results={results} />);

    const photBadge = screen.getByText("phot");
    expect(photBadge).toBeInTheDocument();
    expect(photBadge.getAttribute("title")).toContain("0.0200");
  });

  // ── Column visibility panel ──

  it("toggles column visibility panel on button click", async () => {
    const user = userEvent.setup();
    const results = [makeResult()];
    render(<ResultsTable {...defaultProps} results={results} />);

    const colBtn = screen.getByText("Columns");
    await user.click(colBtn);

    // Panel should appear with Core group label
    expect(screen.getByText("Core")).toBeInTheDocument();
  });

  // ── Object click ──

  it("calls onObjectClick when name link is clicked", async () => {
    const user = userEvent.setup();
    const onObjectClick = vi.fn();
    const results = [
      makeResult({ name: "Sirius", ra: 101.28, dec: -16.71 }),
    ];
    render(
      <ResultsTable
        {...defaultProps}
        results={results}
        onObjectClick={onObjectClick}
      />,
    );

    const nameLink = screen.getByText("Sirius");
    await user.click(nameLink);
    expect(onObjectClick).toHaveBeenCalledWith("Sirius", 101.28, -16.71);
  });

  // ── Error row ──

  it("does not render fetch button for error results", () => {
    const results = [
      makeResult({ object_id: "error", error_type: "timeout" }),
    ];
    const { container } = render(
      <ResultsTable {...defaultProps} results={results} />,
    );

    // There should be no btn-fetch in the error row
    const fetchButtons = container.querySelectorAll(".btn-fetch");
    expect(fetchButtons.length).toBe(0);
  });
});
