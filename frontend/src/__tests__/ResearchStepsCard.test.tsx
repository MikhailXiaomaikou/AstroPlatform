import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ResearchStepsCard, { isResearchTurn } from "../components/chat/ResearchStepsCard";
import { type ChatAction } from "../api/client";

describe("ResearchStepsCard", () => {
  it("isResearchTurn detects cosmology/research tools only", () => {
    expect(isResearchTurn([{ action: "run_cosmology_likelihood_chain" }])).toBe(true);
    expect(isResearchTurn([{ action: "assess_bao_bin_anomaly" }])).toBe(true);
    expect(isResearchTurn([{ action: "search_literature" }])).toBe(false);
    expect(isResearchTurn(undefined)).toBe(false);
    expect(isResearchTurn([])).toBe(false);
  });

  it("renders ordered method steps with tool-grounded result lines", () => {
    const actions: ChatAction[] = [
      {
        action: "list_cosmology_datasets",
        tool_result: { datasets: [{ display_name: "DESI DR1 BAO" }, { display_name: "Planck 2018 compressed" }] },
      },
      {
        action: "run_cosmology_likelihood_chain",
        tool_result: {
          __tool_status__: "COMPLETED",
          chain_tier: "publication",
          parameters: { H0: { median: 67.4 }, omegam: { median: 0.31 } },
        },
      },
      {
        action: "assess_bao_bin_anomaly",
        tool_result: { omega_m_best: 0.314, omega_m_1sigma_low: 0.283, omega_m_1sigma_high: 0.348 },
      },
    ];
    render(<ResearchStepsCard actions={actions} />);
    expect(screen.getByText(/Research method/)).toBeTruthy();
    expect(screen.getByText("Data selection")).toBeTruthy();
    expect(screen.getByText("Likelihood chain")).toBeTruthy();
    expect(screen.getByText("Alcock-Paczynski geometric test")).toBeTruthy();
    expect(screen.getByText(/2 dataset\(s\): DESI DR1 BAO/)).toBeTruthy();
    expect(screen.getByText(/H0=67.4/)).toBeTruthy();
    expect(screen.getByText(/Ωm = 0.314/)).toBeTruthy();
  });

  it("tones a failed tool result with the burgundy ✕ glyph", () => {
    const actions: ChatAction[] = [
      { action: "compare_luminosity_distances", tool_result: { __tool_status__: "EMPTY", success: false } },
    ];
    render(<ResearchStepsCard actions={actions} />);
    expect(screen.getByText("Luminosity-distance comparison")).toBeTruthy();
    expect(screen.getByText("✕")).toBeTruthy();
  });
});
