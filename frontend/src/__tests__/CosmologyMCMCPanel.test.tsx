import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CosmologyMCMCPanel from "../components/chat/CosmologyMCMCPanel";

describe("CosmologyMCMCPanel", () => {
  it("shows posterior parameters and diagnostics", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "emcee",
          model: "flat_w0wa_cdm",
          publication_ready: false,
          random_seed: 1234,
          data_hash: "abcdef1234567890",
          n_samples: 320,
          acceptance_fraction: 0.41,
          chain_diagnostics: { overall_status: "check_required" },
          parameters: {
            H0: {
              median: 70.1234,
              hdi_low_94: 65.1,
              hdi_high_94: 75.2,
              rhat: 1.12,
              ess_bulk: 80,
              status: "not_converged",
            },
            w0: {
              median: -1.05,
              hdi_low_94: -1.6,
              hdi_high_94: -0.7,
              rhat: 1.03,
              ess_bulk: 250,
              status: "marginal",
            },
          },
        }}
      />,
    );

    expect(screen.getByText("Cosmology MCMC")).toBeInTheDocument();
    expect(screen.getByText(/flat_w0wa_cdm/)).toBeInTheDocument();
    expect(screen.getByText("not publication-ready")).toBeInTheDocument();
    expect(screen.getByText("H0")).toBeInTheDocument();
    expect(screen.getByText("w0")).toBeInTheDocument();
    expect(screen.getByText("70.1234")).toBeInTheDocument();
    expect(screen.getByText("seed=1234")).toBeInTheDocument();
    expect(screen.getByText("data_hash=abcdef123456")).toBeInTheDocument();
    expect(screen.getByText("diagnostics=check_required")).toBeInTheDocument();
  });

  it("shows compressed likelihood caveat and dataset coverage", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "compressed_gaussian_analytic",
          model: "lcdm",
          publication_ready: true,
          compressed_likelihood_preliminary: true,
          datasets_used: [{ key: "planck2018_compressed" }],
          datasets_not_run: [{ key: "sdss_6df_bao" }],
          chain_diagnostics: { overall_status: "analytic_gaussian" },
          parameters: {
            S8: {
              median: 0.831,
              hdi_low_94: 0.79,
              hdi_high_94: 0.87,
              rhat: 1,
              ess_bulk: 4000,
              status: "analytic_gaussian",
            },
          },
        }}
      />,
    );

    expect(screen.getByText("publication-ready")).toBeInTheDocument();
    expect(screen.getByText("compressed likelihood")).toBeInTheDocument();
    expect(screen.getByText(/Preliminary compressed-Gaussian result/)).toBeInTheDocument();
    expect(screen.getByText(/1 selected dataset/)).toBeInTheDocument();
  });
});
