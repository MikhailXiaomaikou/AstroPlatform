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
    // chain_tier not emitted by the fixture and publication_ready=false →
    // panel falls back to "blocked".
    expect(screen.getByText("blocked — do not cite")).toBeInTheDocument();
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

    expect(screen.getByText("Cosmology MCMC")).toBeInTheDocument();
    expect(screen.getByText("publication-ready")).toBeInTheDocument();
    expect(screen.getByText("compressed likelihood")).toBeInTheDocument();
    expect(screen.getByText(/Preliminary compressed-Gaussian result/)).toBeInTheDocument();
    expect(screen.getByText(/1 selected dataset/)).toBeInTheDocument();
  });

  it("renders CMB rotation beta posterior as a publication-ready compressed result", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "compressed_beta_gaussian",
          model: "isotropic_beta",
          publication_ready: true,
          chain_tier: "publication",
          compressed_rotation_preliminary: true,
          datasets_used: [{ key: "unit_test_cmb_rotation" }],
          chain_diagnostics: { overall_status: "analytic_gaussian" },
          parameters: {
            beta_deg: {
              median: 0.35,
              hdi_low_94: 0.16,
              hdi_high_94: 0.54,
              rhat: 1,
              ess_bulk: 1024,
              status: "analytic_gaussian",
            },
          },
        }}
      />,
    );

    expect(screen.getByText("CMB Rotation")).toBeInTheDocument();
    expect(screen.getByText("publication-ready")).toBeInTheDocument();
    expect(screen.getByText("beta_deg")).toBeInTheDocument();
    expect(screen.getByText("0.35")).toBeInTheDocument();
  });

  it("shows no-executable-likelihood reason instead of convergence warning when no parameters exist", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "compressed_gaussian_analytic",
          model: "lcdm",
          publication_ready: false,
          analysis_status: "NO_COMPRESSED_LIKELIHOOD",
          warnings: ["No selected dataset has a registered compressed Gaussian likelihood."],
          parameters: {},
        }}
      />,
    );

    expect(screen.getByText("blocked — do not cite")).toBeInTheDocument();
    expect(screen.getByText(/No selected dataset has a registered compressed Gaussian likelihood/)).toBeInTheDocument();
    expect(screen.queryByText(/ESS\/R-hat diagnostics pass/)).not.toBeInTheDocument();
  });

  it("renders exploratory tier with warning, not a blocked banner", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "emcee",
          model: "flat_lcdm",
          publication_ready: false,
          chain_tier: "exploratory",
          __tool_status__: "EXPLORATORY",
          __exploratory_warning__:
            "Chain min ESS=180 (publication threshold 400), max R-hat=1.07.",
          parameters: {
            H0: {
              median: 71.5,
              hdi_low_94: 67.0,
              hdi_high_94: 76.0,
              rhat: 1.07,
              ess_bulk: 180,
              status: "marginal",
            },
          },
        }}
      />,
    );

    expect(screen.getByText("exploratory")).toBeInTheDocument();
    expect(screen.queryByText("blocked — do not cite")).not.toBeInTheDocument();
    expect(screen.getByText(/min ESS=180/)).toBeInTheDocument();
  });

  it("renders explicit chain_tier=blocked with the do-not-cite banner", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "emcee",
          model: "flat_lcdm",
          publication_ready: false,
          chain_tier: "blocked",
          __do_not_claim__: true,
          warnings: [
            "Cosmology MCMC used inline/unverified rows. Inline rows are audit-only.",
          ],
          parameters: {
            H0: { median: 70.0, hdi_low_94: 50, hdi_high_94: 90, rhat: 2.5, ess_bulk: 30, status: "not_converged" },
          },
        }}
      />,
    );

    expect(screen.getByText("blocked — do not cite")).toBeInTheDocument();
    expect(screen.getByText(/inline\/unverified rows/)).toBeInTheDocument();
  });

  it("renders chain_tier=publication as publication-ready when explicit", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "emcee",
          model: "flat_lcdm",
          publication_ready: true,
          chain_tier: "publication",
          parameters: {
            H0: { median: 67.4, hdi_low_94: 66.5, hdi_high_94: 68.3, rhat: 1.01, ess_bulk: 1200, status: "good" },
          },
        }}
      />,
    );

    expect(screen.getByText("publication-ready")).toBeInTheDocument();
    expect(screen.queryByText("blocked — do not cite")).not.toBeInTheDocument();
    expect(screen.queryByText("exploratory")).not.toBeInTheDocument();
  });

  it("renders pairwise tensions sorted by sigma with >=3sigma highlighted", () => {
    render(
      <CosmologyMCMCPanel
        result={{
          sampler: "compressed_gaussian_analytic",
          model: "lcdm",
          publication_ready: true,
          chain_tier: "publication",
          parameters: {
            H0: { median: 67.4, hdi_low_94: 66.5, hdi_high_94: 68.3, rhat: 1, ess_bulk: 1200, status: "good" },
          },
          pairwise_tensions: [
            { parameter: "omegam", dataset_a: "act_dr6_lensing", dataset_b: "kids1000_wl", delta: 0.02, sigma: 1.4, value_a: 0.31, value_b: 0.29 },
            { parameter: "H0", dataset_a: "planck2018_compressed", dataset_b: "shoes_h0_riess22", delta: -5.7, sigma: 5.2, value_a: 67.4, value_b: 73.0 },
            { parameter: "sigma8", dataset_a: "planck2018_compressed", dataset_b: "kids1000_wl", delta: 0.04, sigma: 2.6, value_a: 0.81, value_b: 0.77 },
          ],
        }}
      />,
    );

    expect(screen.getByText("Parameter tensions (pairwise)")).toBeInTheDocument();
    // H0 5.2σ should appear first (sorted by sigma desc) and be flagged
    const sigmaCells = screen.getAllByText(/\d+(\.\d+)?σ/);
    expect(sigmaCells[0].textContent).toMatch(/5\.2σ/);
    expect(sigmaCells[0].textContent).toContain("⚠");
    // omegam 1.4σ should not be flagged
    const lowCell = sigmaCells[sigmaCells.length - 1];
    expect(lowCell.textContent).toMatch(/1\.4σ/);
    expect(lowCell.textContent).not.toContain("⚠");
  });


  it("offers getdist chain downloads only for persisted artifacts", () => {
    const base = {
      sampler: "importance_bao",
      model: "lcdm",
      parameters: { H0: { median: 67.4 } },
    };
    const { rerender } = render(
      <CosmologyMCMCPanel
        result={{
          ...base,
          chain_artifacts: {
            status: "persisted",
            run_id: "3f1b52aa-0000-4000-8000-000000000000",
            files: [
              {
                name: "chain_1.txt",
                output_path: "chains/u/3f1b52aa/chain_1.txt",
                sha256: "ab".repeat(32),
                size_bytes: 1024,
              },
              {
                name: "chain.paramnames",
                output_path: "chains/u/3f1b52aa/chain.paramnames",
                sha256: "cd".repeat(32),
                size_bytes: 64,
              },
            ],
          },
        }}
      />,
    );
    expect(screen.getByRole("button", { name: "chain_1.txt" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "chain.paramnames" })).toBeInTheDocument();

    rerender(
      <CosmologyMCMCPanel
        result={{
          ...base,
          chain_artifacts: { status: "persist_failed", files: [] },
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: "chain_1.txt" })).not.toBeInTheDocument();
  });

});
