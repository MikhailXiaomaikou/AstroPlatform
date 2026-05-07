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

  it("shows fact-check claims and safe rewrites", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "FACT_CHECK_READY",
          fact_check_report: {
            status: "warning",
            verified_claim_count: 1,
            unsupported_claim_count: 1,
            checked_sources: { dataset_count: 2, arxiv_ids: ["2112.03863"], dois: [], bibcodes: [] },
            claims: [
              {
                text: "H0 = 70",
                kind: "numeric",
                status: "unsupported",
                support_level: "not_applicable",
                safe_rewrite: "Do not quote H0 until a publication-ready chain exists.",
              },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText("Fact Check")).toBeInTheDocument();
    expect(screen.getByText("warning")).toBeInTheDocument();
    expect(screen.getByText(/H0 = 70/)).toBeInTheDocument();
    expect(screen.getByText(/Safe rewrite/)).toBeInTheDocument();
  });

  it("shows mined paper tools with implementation status", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "PAPER_TOOL_MINING_READY",
          tool_count: 2,
          paper_metadata: { title: "Mock BAO likelihood paper", arxiv_id: "2601.00001" },
          category_counts: { likelihood: 1, sampler: 1 },
          implementation_counts: { partial: 1, missing: 1 },
          tool_specs: [
            {
              tool_id: "tool_spec_1",
              method_name: "Evaluate Gaussian/covariance likelihood",
              tool_category: "likelihood",
              implementation_status: "partial",
              confidence: 0.86,
              datasets: ["DESI BAO"],
              source_spans: [{ section: "Methods", text: "likelihood with covariance matrix" }],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Paper Tool Mining")).toBeInTheDocument();
    expect(screen.getByText(/Mock BAO likelihood paper/)).toBeInTheDocument();
    expect(screen.getByText("Evaluate Gaussian/covariance likelihood")).toBeInTheDocument();
    expect(screen.getByText("partial")).toBeInTheDocument();
  });

  it("shows ontology, gap matrix, and implementation queue states", () => {
    const { rerender } = render(
      <ResearchProgramPanel
        result={{
          analysis_status: "TOOL_ONTOLOGY_READY",
          cluster_count: 1,
          ontology: {
            categories: {
              likelihood: [
                { canonical_capability: "compressed_cosmology_likelihood", paper_count: 3, status: "partial" },
              ],
            },
          },
        }}
      />,
    );
    expect(screen.getByText("Tool Ontology")).toBeInTheDocument();
    expect(screen.getByText(/compressed_cosmology_likelihood/)).toBeInTheDocument();

    rerender(
      <ResearchProgramPanel
        result={{
          analysis_status: "TOOL_GAP_MATRIX_READY",
          gap_count: 1,
          gap_matrix: [
            {
              capability: "nested_sampler",
              tool_category: "sampler",
              current_status: "missing",
              priority: "P1",
              research_value: "Appears in 2 mined papers.",
              implementation_gap: "No controlled nested sampler yet.",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Tool Gap Matrix")).toBeInTheDocument();
    expect(screen.getByText("nested_sampler")).toBeInTheDocument();

    rerender(
      <ResearchProgramPanel
        result={{
          analysis_status: "TOOL_IMPLEMENTATION_QUEUE_READY",
          queue_size: 1,
          implementation_queue: [
            {
              rank: 1,
              capability: "external_likelihood_runner",
              priority: "P0",
              current_status: "missing",
              next_engineering_step: "Add controlled Cobaya job runner.",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Implementation Queue")).toBeInTheDocument();
    expect(screen.getByText(/external_likelihood_runner/)).toBeInTheDocument();
  });

  it("shows continuous paper-mining loop rounds and local bundle state", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "PAPER_TOOL_MINING_LOOP_READY",
          rounds_run: 1,
          rounds: [
            {
              round_index: 1,
              batch_size: 20,
              batch_result: { paper_count: 20, tool_spec_count: 42 },
            },
          ],
          updated_state: {
            round_index: 1,
            round_history: [{ round_index: 1, paper_ids: ["2601.00001"], tool_spec_count: 42 }],
          },
          aggregate_implementation_queue: [
            {
              capability: "pantheon_plus_runner",
              priority: "P0",
              next_engineering_step: "Implement SN covariance ingestion.",
            },
          ],
          bundle_path: ".local/paper_tool_mining/round_0001.json",
        }}
      />,
    );

    expect(screen.getByText("Paper Mining Loop")).toBeInTheDocument();
    expect(screen.getByText(/Round 1 · 20 paper/)).toBeInTheDocument();
    expect(screen.getByText("pantheon_plus_runner")).toBeInTheDocument();
    expect(screen.getByText(/Local bundle/)).toBeInTheDocument();
  });

  it("shows paper candidate pools before mining rounds", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "PAPER_MINING_CANDIDATE_POOL_READY",
          candidate_count: 1,
          live_search_enabled: false,
          attempted_queries: ["cat:astro-ph.CO AND BAO"],
          candidate_papers: [
            {
              arxiv_id: "2604.00001",
              title: "DESI BAO covariance likelihood",
              relevance_score: 0.72,
              mining_readiness: "metadata_or_abstract_only",
              relevance_terms: ["bao", "desi"],
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Paper Candidate Pool")).toBeInTheDocument();
    expect(screen.getByText("DESI BAO covariance likelihood")).toBeInTheDocument();
    expect(screen.getByText("arXiv:2604.00001")).toBeInTheDocument();
    expect(screen.getByText(/cat:astro-ph.CO/)).toBeInTheDocument();
  });
});
