/**
 * Tests for PlotBuilder — the interactive visualization component.
 *
 * Tests cover chart type selection, available chart filtering based
 * on data, custom scatter column selectors, and export controls.
 * Plotly is mocked since it requires a real browser canvas.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ── Mock react-plotly.js (no canvas in jsdom) ──
vi.mock("react-plotly.js", () => ({
  __esModule: true,
  default: (props: Record<string, unknown>) => (
    <div data-testid="mock-plot" data-chart-type={JSON.stringify(props.data)} />
  ),
}));

import PlotBuilder from "../components/viz/PlotBuilder";

/* ── Test data ── */

const SKY_DATA: Record<string, unknown> = {
  ra: [10.0, 20.0, 30.0, 40.0],
  dec: [41.0, 42.0, 43.0, 44.0],
  names: ["Star A", "Star B", "Star C", "Star D"],
  redshift: [0.01, 0.02, 0.03, 0.04],
  magnitude: [12.5, 13.0, 14.2, 11.8],
};

const HR_DATA: Record<string, unknown> = {
  ra: [10.0, 20.0],
  dec: [41.0, 42.0],
  names: ["Star A", "Star B"],
  redshift: [null, null],
  magnitude: [12.5, 13.0],
  bp_rp: [0.8, 1.2],
  phot_g_mean_mag: [5.0, 6.0],
  abs_g_mag: [4.0, 5.5],
  parallax: [10.0, 8.0],
  teff_gspphot: [5800, 4200],
};

const SPECTRUM_DATA: Record<string, unknown> = {
  ra: [10.0],
  dec: [41.0],
  names: ["Spec Star"],
  redshift: [null],
  magnitude: [null],
  wavelength: Array.from({ length: 100 }, (_, i) => 3800 + i * 50),
  flux: Array.from({ length: 100 }, (_, i) => Math.sin(i / 10) + 2),
};

const LIGHTCURVE_DATA: Record<string, unknown> = {
  ra: [10.0],
  dec: [41.0],
  names: ["Variable Star"],
  redshift: [null],
  magnitude: [null],
  mjd: Array.from({ length: 50 }, (_, i) => 59000 + i),
  mag: Array.from({ length: 50 }, (_, i) => 12.0 + Math.sin(i / 5) * 0.3),
  magerr: Array.from({ length: 50 }, () => 0.02),
};

/* ── Tests ── */

describe("PlotBuilder", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ── Basic rendering ──

  it("renders with header and chart type selector", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    expect(screen.getByText("Interactive Visualization")).toBeInTheDocument();
    // Chart type selector should be present
    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    expect(select).toBeInTheDocument();
  });

  it("renders the plot when data is provided", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);
    expect(screen.getByTestId("mock-plot")).toBeInTheDocument();
  });

  it("shows empty message when no data is provided", () => {
    render(<PlotBuilder />);
    expect(screen.getByText("No data to visualize")).toBeInTheDocument();
  });

  // ── Chart type filtering ──

  it("hides redshift charts when no redshift data available", () => {
    const noRedshiftData: Record<string, unknown> = {
      ra: [10.0, 20.0],
      dec: [41.0, 42.0],
      names: ["A", "B"],
      redshift: [null, null],
      magnitude: [12.0, 13.0],
    };
    render(<PlotBuilder initialData={noRedshiftData} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).not.toContain("redshift_histogram");
    expect(options).not.toContain("ra_dec_redshift");
    expect(options).toContain("sky_coverage");
  });

  it("hides magnitude histogram when no magnitude data", () => {
    const noMagData: Record<string, unknown> = {
      ra: [10.0],
      dec: [41.0],
      names: ["A"],
      redshift: [0.01],
      magnitude: [null],
    };
    render(<PlotBuilder initialData={noMagData} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).not.toContain("magnitude_histogram");
  });

  it("shows HR diagram option when bp_rp column exists", () => {
    render(<PlotBuilder initialData={HR_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("hr_diagram");
  });

  it("hides HR diagram when no color data", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).not.toContain("hr_diagram");
  });

  it("shows spectrum option when wavelength data exists", () => {
    render(<PlotBuilder initialData={SPECTRUM_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("spectrum");
  });

  it("shows lightcurve option when time + mag data exists", () => {
    render(<PlotBuilder initialData={LIGHTCURVE_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("lightcurve");
  });

  it("always includes sky_coverage and custom scatter", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toContain("sky_coverage");
    expect(options).toContain("scatter_custom");
  });

  // ── Chart type switching ──

  it("changes chart type via select", async () => {
    const user = userEvent.setup();
    render(<PlotBuilder initialData={SKY_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    await user.selectOptions(select, "redshift_histogram");
    expect(select.value).toBe("redshift_histogram");
  });

  it("respects initialChartType prop", () => {
    render(
      <PlotBuilder
        initialData={SKY_DATA}
        initialChartType="redshift_histogram"
      />,
    );

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    expect(select.value).toBe("redshift_histogram");
  });

  // ── Fit & Stats toggles ──

  it("has Fit and Stats toggle checkboxes", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    expect(screen.getByLabelText("Fit")).toBeInTheDocument();
    expect(screen.getByLabelText("Stats")).toBeInTheDocument();
  });

  it("Fit is off by default, Stats is on by default", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    const fitCheckbox = screen.getByLabelText("Fit") as HTMLInputElement;
    const statsCheckbox = screen.getByLabelText("Stats") as HTMLInputElement;
    expect(fitCheckbox.checked).toBe(false);
    expect(statsCheckbox.checked).toBe(true);
  });

  it("toggles Fit checkbox", async () => {
    const user = userEvent.setup();
    render(<PlotBuilder initialData={SKY_DATA} />);

    const fitCheckbox = screen.getByLabelText("Fit") as HTMLInputElement;
    await user.click(fitCheckbox);
    expect(fitCheckbox.checked).toBe(true);
  });

  // ── Custom scatter selectors ──

  it("shows column selectors when scatter_custom is chosen", async () => {
    const user = userEvent.setup();
    render(<PlotBuilder initialData={SKY_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    await user.selectOptions(select, "scatter_custom");

    // X axis and Y axis labels should appear
    expect(screen.getByText("X axis")).toBeInTheDocument();
    expect(screen.getByText("Y axis")).toBeInTheDocument();
    expect(screen.getByText("Color")).toBeInTheDocument();
  });

  it("has Flip Y toggle in custom scatter mode", async () => {
    const user = userEvent.setup();
    render(<PlotBuilder initialData={SKY_DATA} />);

    const select = screen.getAllByRole("combobox")[0] as HTMLSelectElement;
    await user.selectOptions(select, "scatter_custom");

    expect(screen.getByLabelText("Flip Y")).toBeInTheDocument();
  });

  // ── Export buttons ──

  it("renders export buttons", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    expect(screen.getByText("Export SVG")).toBeInTheDocument();
    expect(screen.getByText("Export PDF")).toBeInTheDocument();
    expect(screen.getByText("Export Hi-Res PNG")).toBeInTheDocument();
  });

  // ── Close button ──

  it("shows close button when onClose is provided", () => {
    const onClose = vi.fn();
    render(<PlotBuilder initialData={SKY_DATA} onClose={onClose} />);

    expect(screen.getByText("Close")).toBeInTheDocument();
  });

  it("calls onClose when close button is clicked", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<PlotBuilder initialData={SKY_DATA} onClose={onClose} />);

    await user.click(screen.getByText("Close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not show close button when onClose is absent", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);
    expect(screen.queryByText("Close")).not.toBeInTheDocument();
  });

  // ── Axis range inputs ──

  it("renders axis range inputs", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    expect(screen.getByText("X min")).toBeInTheDocument();
    expect(screen.getByText("X max")).toBeInTheDocument();
    expect(screen.getByText("Y min")).toBeInTheDocument();
    expect(screen.getByText("Y max")).toBeInTheDocument();
  });

  // ── Coordinate format selector ──

  it("has coordinate format dropdown with decimal and HMS/DMS", () => {
    render(<PlotBuilder initialData={SKY_DATA} />);

    expect(screen.getByText("Decimal")).toBeInTheDocument();
    expect(screen.getByText("HMS/DMS")).toBeInTheDocument();
  });

  // ── Empty data for selected chart type ──

  it("shows empty message when chart type has no matching data", () => {
    const sparseData: Record<string, unknown> = {
      ra: [10.0],
      dec: [41.0],
      names: ["A"],
      redshift: [0.01],
      magnitude: [12.0],
    };
    render(
      <PlotBuilder initialData={sparseData} initialChartType="spectrum" />,
    );

    // spectrum is not available, should fall back to another chart type
    // or show "No data available for this chart type"
    // Since spectrum option won't be in the select (hidden), the component
    // will still try to build a spectrum plot and get empty data
    expect(
      screen.getByText("No data available for this chart type"),
    ).toBeInTheDocument();
  });
});
