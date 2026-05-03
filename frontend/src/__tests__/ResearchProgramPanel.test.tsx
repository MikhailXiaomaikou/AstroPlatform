import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ResearchProgramPanel from "../components/chat/ResearchProgramPanel";

describe("ResearchProgramPanel", () => {
  it("shows research plan hypotheses, datasets, and matrix", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_PLAN_READY",
          research_plan: {
            research_question: "Test DESI BAO + SN + CMB dark-energy robustness.",
            executable_level: "mixed",
            required_probes: ["BAO", "SN", "CMB"],
            hypotheses: ["Test whether extended dark-energy models are supported."],
            candidate_datasets: [
              { key: "desi_dr1_bao", display_name: "DESI DR1 BAO", execution_level: "compressed_preliminary" },
              { key: "pantheon_plus", display_name: "Pantheon+", execution_level: "config_only" },
            ],
            proposed_experiment_matrix: [
              { label: "BAO + SN + CMB", dataset_keys: ["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"], model: "lcdm" },
            ],
            blocking_gaps: ["Pantheon+ requires external chains."],
          },
        }}
      />,
    );

    expect(screen.getByText("Research Plan")).toBeInTheDocument();
    expect(screen.getByText("DESI DR1 BAO")).toBeInTheDocument();
    expect(screen.getByText("Pantheon+")).toBeInTheDocument();
    expect(screen.getAllByText(/BAO \+ SN \+ CMB/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Pantheon\+ requires external chains/)).toBeInTheDocument();
  });

  it("shows research matrix ready cell counts", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_MATRIX_READY",
          publication_ready: true,
          ready_cells: 1,
          matrix_size: 2,
          matrix: [
            { label: "BAO only", model: "lcdm", dataset_keys: ["desi_dr1_bao"], publication_ready: true },
            { label: "BAO + SN", model: "lcdm", dataset_keys: ["desi_dr1_bao", "pantheon_plus"], execution_level: "config_only" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Research Matrix")).toBeInTheDocument();
    expect(screen.getByText("runnable cells ready")).toBeInTheDocument();
    expect(screen.getByText(/1 \/ 2 ready/)).toBeInTheDocument();
    expect(screen.getByText(/BAO only/)).toBeInTheDocument();
  });

  it("shows evidence graph claimability", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "EVIDENCE_GRAPH_READY",
          evidence_graph: {
            claimable_parameters: ["H0", "omegam"],
            supported_claims: [{ parameter: "H0" }],
            unsupported_claims: [],
          },
        }}
      />,
    );

    expect(screen.getByText("Evidence Graph")).toBeInTheDocument();
    expect(screen.getByText(/H0, omegam/)).toBeInTheDocument();
    expect(screen.getByText(/1 supported claim/)).toBeInTheDocument();
  });
});
