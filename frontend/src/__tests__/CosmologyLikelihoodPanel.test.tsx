import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CosmologyLikelihoodPanel from "../components/chat/CosmologyLikelihoodPanel";

describe("CosmologyLikelihoodPanel", () => {
  it("shows registry datasets with covariance and status", () => {
    render(
      <CosmologyLikelihoodPanel
        result={{
          dataset_count: 1,
          datasets: [
            {
              key: "desi_dr1_bao",
              display_name: "DESI DR1 BAO",
              version: "DR1 2024 BAO likelihood",
              probe: "bao",
              status: "external_likelihood",
              covariance: { kind: "block covariance", provided: true },
              citations: [{ label: "DESI Collaboration", year: 2024, arxiv: "2404.03002" }],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Cosmology Dataset Registry")).toBeInTheDocument();
    expect(screen.getByText("DESI DR1 BAO")).toBeInTheDocument();
    expect(screen.getByText("external likelihood")).toBeInTheDocument();
    expect(screen.getByText(/block covariance/)).toBeInTheDocument();
    expect(screen.getByText(/arXiv:2404.03002/)).toBeInTheDocument();
  });

  it("shows likelihood config guardrail", () => {
    render(
      <CosmologyLikelihoodPanel
        result={{
          model: "w0wa_cdm",
          config_hash: "abcdef1234567890",
          datasets: [{ key: "pantheon_plus", display_name: "Pantheon+", status: "external_likelihood" }],
          warnings: ["One or more selected datasets require external likelihood/data files."],
        }}
      />,
    );

    expect(screen.getByText("Cosmology Likelihood Config")).toBeInTheDocument();
    expect(screen.getByText(/Config hash abcdef123456/)).toBeInTheDocument();
    expect(screen.getByText(/Posterior, tension, AIC\/BIC/)).toBeInTheDocument();
    expect(screen.getByText(/external likelihood\/data files/)).toBeInTheDocument();
  });

  it("shows robustness matrix rows", () => {
    render(
      <CosmologyLikelihoodPanel
        result={{
          model: "lcdm",
          matrix_size: 2,
          matrix: [
            { label: "BAO only", dataset_keys: ["desi_dr1_bao"], config_hash: "hash-one" },
            { label: "BAO + Pantheon+ + CMB", dataset_keys: ["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"], config_hash: "hash-two" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Cosmology Robustness Matrix")).toBeInTheDocument();
    expect(screen.getByText("BAO only")).toBeInTheDocument();
    expect(screen.getByText("BAO + Pantheon+ + CMB")).toBeInTheDocument();
    expect(screen.getByText(/desi_dr1_bao \+ pantheon_plus \+ planck2018_compressed/)).toBeInTheDocument();
  });
});
