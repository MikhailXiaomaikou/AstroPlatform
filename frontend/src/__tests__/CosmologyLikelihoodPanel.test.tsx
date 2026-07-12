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
              execution_mode: "external_cobaya",
              covariance: { kind: "block covariance", provided: true },
              citations: [{ label: "DESI Collaboration", year: 2024, arxiv: "2404.03002" }],
              data_products: [
                {
                  role: "measurement_vector",
                  url: "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/desi_2024_gaussian_bao_ALL_GCcomb_mean.txt",
                },
                {
                  role: "covariance",
                  url: "https://raw.githubusercontent.com/CobayaSampler/bao_data/master/desi_2024_gaussian_bao_ALL_GCcomb_cov.txt",
                },
              ],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Cosmology Dataset Registry")).toBeInTheDocument();
    expect(screen.getByText("DESI DR1 BAO")).toBeInTheDocument();
    expect(screen.getByText("external likelihood")).toBeInTheDocument();
    expect(screen.getByText("external cobaya")).toBeInTheDocument();
    expect(screen.getByText(/block covariance/)).toBeInTheDocument();
    expect(screen.getByText(/machine-readable products: 2/)).toBeInTheDocument();
    expect(screen.getByText(/measurement_vector, covariance/)).toBeInTheDocument();
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

  it("labels registered posterior summaries as literature context", () => {
    render(
      <CosmologyLikelihoodPanel
        result={{
          dataset_count: 1,
          datasets: [
            {
              key: "kids1000_wl",
              display_name: "KiDS-1000",
              execution_mode: "compressed_gaussian",
              compressed_likelihood: {
                parameters: ["S8"],
                statistical_role: "published_posterior_summary",
                source_prior: "KiDS source-analysis prior",
              },
            },
          ],
        }}
      />,
    );

    expect(screen.getByText(/literature posterior context: S8/)).toBeInTheDocument();
    expect(screen.queryByText(/executable compressed params/)).not.toBeInTheDocument();
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

  it("shows partial robustness cells without calling context datasets executed", () => {
    render(
      <CosmologyLikelihoodPanel
        result={{
          model: "lcdm",
          analysis_status: "ROBUSTNESS_MATRIX_DIAGNOSTIC",
          matrix_size: 1,
          matrix: [
            {
              label: "BAO + CMB + weak lensing",
              dataset_keys: ["desi_dr1_bao", "planck2018_compressed", "kids1000_wl"],
              publication_ready: false,
              execution_level: "partial_dataset_run",
              result: {
                datasets_used: [{ key: "planck2018_compressed" }],
                datasets_not_run: [{ key: "kids1000_wl" }],
              },
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("diagnostic matrix")).toBeInTheDocument();
    expect(screen.getByText(/partial posterior; some datasets not included/)).toBeInTheDocument();
    expect(screen.getByText(/used 1 dataset/)).toBeInTheDocument();
    expect(screen.getByText(/not numerically included: kids1000_wl/)).toBeInTheDocument();
  });
});
