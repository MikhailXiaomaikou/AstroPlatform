import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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
            alpha_test_protocol: {
              supported_scope: "Exploratory registered-data research.",
            },
            blocking_gaps: ["Pantheon+ requires external chains."],
          },
        }}
      />,
    );

    expect(screen.getByText("Research Plan")).toBeInTheDocument();
    // `hypotheses` is a rule-derived keyword checklist in the backend, so the
    // panel must not present it as model-generated hypotheses.
    expect(screen.getByText("Platform checklist (rule-derived)")).toBeInTheDocument();
    expect(screen.queryByText("Hypotheses")).not.toBeInTheDocument();
    expect(screen.getByText("DESI DR1 BAO")).toBeInTheDocument();
    expect(screen.getByText("Pantheon+")).toBeInTheDocument();
    expect(screen.getAllByText(/BAO \+ SN \+ CMB/).length).toBeGreaterThan(0);
    expect(screen.getByText("Alpha test protocol")).toBeInTheDocument();
    expect(screen.getByText(/Pantheon\+ requires external chains/)).toBeInTheDocument();
  });

  it("shows research matrix ready cell counts", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_MATRIX_READY",
          publication_ready: true,
          ready_cells: 2,
          matrix_size: 3,
          research_charts: {
            matrix_status: [
              { label: "BAO only", status: "ready", execution_level: "compressed_preliminary", publication_ready: true },
              { label: "BAO + CMB", status: "ready", execution_level: "compressed_preliminary", publication_ready: true },
              { label: "BAO + SN", status: "config_only", execution_level: "config_only", publication_ready: false },
            ],
            posterior_forest: [
              { label: "BAO + CMB", parameter: "H0", median: 67.31, low: 66.4, high: 68.2, publication_ready: true },
            ],
            diagnostics: [
              { label: "BAO + CMB", ess: 471.3, rhat: 1.0, ess_threshold: 400, rhat_threshold: 1.05, publication_ready: true },
            ],
            notes: ["Charts are deterministic renderings of current-turn Research Matrix cells."],
          },
          matrix: [
            { label: "BAO only", model: "lcdm", dataset_keys: ["desi_dr1_bao"], publication_ready: true },
            {
              label: "BAO + CMB",
              model: "lcdm",
              dataset_keys: ["desi_dr1_bao", "planck2018_compressed"],
              publication_ready: true,
              execution_level: "compressed_preliminary",
              result: {
                parameters: { H0: { median: 67.31 } },
                // 2026-06-12 payload shape: rhat is null on the in-process
                // runner (not computed) — the panel must render a dash, never
                // coerce null to a fabricated "0.000".
                chain_diagnostics: {
                  proposal_ess: 471.3,
                  rhat: null,
                  rhat_note: "not computed on the in-process runner",
                  ess_source: "importance_weights",
                },
              },
            },
            { label: "BAO + SN", model: "lcdm", dataset_keys: ["desi_dr1_bao", "pantheon_plus"], execution_level: "config_only" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Research Matrix")).toBeInTheDocument();
    expect(screen.getByTestId("research-visual-diagnostics")).toBeInTheDocument();
    expect(screen.getByText("Visual Diagnostics")).toBeInTheDocument();
    expect(screen.getByText("Matrix status map")).toBeInTheDocument();
    expect(screen.getByText("Posterior forest: H0")).toBeInTheDocument();
    expect(screen.getByText("Chain diagnostics")).toBeInTheDocument();
    expect(screen.getByText("runnable cells ready")).toBeInTheDocument();
    expect(screen.getByText(/2 \/ 3 ready/)).toBeInTheDocument();
    expect(screen.getAllByText(/BAO only/).length).toBeGreaterThan(0);
    // rhat:null renders as a dash — the honest "not computed", never a
    // coerced 0.000 (and never the old fabricated 1.000).
    expect(screen.getByText(/H0 median 67.310 · ESS 471 · Rhat —/)).toBeInTheDocument();
    expect(screen.getByText(/configuration only, no posterior run yet/)).toBeInTheDocument();
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

  it("renders the research report as markdown with a copy control and package files", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const markdown = [
      "# Research Report",
      "",
      "## 1. Scientific Question",
      "",
      "Does w deviate from -1 under DESI DR2 + Pantheon+?",
      "",
      "## 2. Why it matters",
      "",
      "Not generated by the platform; add the motivation by hand.",
      "",
    ].join("\n");

    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_REPORT_READY",
          markdown,
          paper_draft_markdown: "## 4. Results\n\nNo publication-ready cell.",
          report_package: {
            files: [
              { path: "research_report.md", content_type: "text/markdown", bytes: 1234 },
              { path: "references.bib", content_type: "text/x-bibtex", bytes: 0 },
            ],
          },
        }}
      />,
    );

    // The section titles arrive as real headings — the report is rendered
    // markdown now, not raw text dumped into a <pre>.
    const questionHeading = screen.getByRole("heading", { name: "1. Scientific Question" });
    expect(questionHeading.tagName).toBe("H3");
    const body = screen.getByTestId("research-report-markdown");
    expect(body.querySelector("pre")).toBeNull();
    expect(body.textContent).toContain("Does w deviate from -1");
    // Scaffold sections stay exactly as the backend wrote them.
    expect(screen.getByText(/Not generated by the platform/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Copy as Markdown" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(markdown));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();

    // Reproducibility bundle is visible with real byte counts.
    expect(screen.getByTestId("research-report-package")).toBeInTheDocument();
    expect(screen.getByText("research_report.md")).toBeInTheDocument();
    expect(screen.getByText("1234 bytes")).toBeInTheDocument();
    expect(screen.getByText("references.bib")).toBeInTheDocument();
    expect(screen.getByText("0 bytes")).toBeInTheDocument();

    // The paper draft keeps its own collapsed block.
    expect(screen.getByText("Paper Draft")).toBeInTheDocument();
  });

  it("shows a visible failure state when the clipboard refuses the copy", async () => {
    // A denied clipboard that leaves the label on "Copy as Markdown" is
    // indistinguishable from a click that never registered — the reader walks
    // away believing the report is on their clipboard when it is not.
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn(() => Promise.reject(new Error("permission denied"))) },
    });

    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_REPORT_READY",
          markdown: "# Research Report\n\n## Scientific Question\n\nIs H0 tension real?\n",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy as Markdown" }));
    expect(await screen.findByRole("button", { name: "Copy failed" })).toBeInTheDocument();
    expect(screen.getByTestId("research-report-copy-error").textContent)
      .toMatch(/nothing was copied/i);
    expect(screen.queryByRole("button", { name: "Copied" })).not.toBeInTheDocument();
  });

  it("fails visibly when the browser exposes no clipboard API at all", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });

    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_REPORT_READY",
          markdown: "# Research Report\n\n## Scientific Question\n\nIs H0 tension real?\n",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Copy as Markdown" }));
    expect(await screen.findByRole("button", { name: "Copy failed" })).toBeInTheDocument();
  });

  it("puts the report markdown in a container the stylesheet actually scopes to", async () => {
    // Every `md-*` rule in App.css is written as `.chat-message-content .md-…`,
    // and the report renders inside `.chat-actions-list`, a SIBLING of
    // `.chat-message-content`. Without its own scoped rule set the report's
    // headings, lists and robustness-matrix tables render completely unstyled.
    const { container } = render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_REPORT_READY",
          markdown: "# Research Report\n\n## Execution Trace\n\n| Cell | Model |\n|---|---|\n| a | lcdm |\n",
        }}
      />,
    );

    const body = container.querySelector(".research-report-markdown");
    expect(body).not.toBeNull();
    expect(body!.getAttribute("data-testid")).toBe("research-report-markdown");
    expect(body!.querySelector("table.md-table")).not.toBeNull();

    const fs = await import("node:fs");
    const css = fs.readFileSync("src/App.css", "utf8");
    for (const rule of [
      ".research-report-markdown .md-h2",
      ".research-report-markdown .md-h3",
      ".research-report-markdown .md-list",
      ".research-report-markdown .md-table",
      ".research-report-markdown .md-table th",
    ]) {
      expect(css).toContain(rule);
    }
  });

  it("renders the report without a package block when report_package is absent", () => {
    render(
      <ResearchProgramPanel
        result={{
          analysis_status: "RESEARCH_REPORT_READY",
          markdown: "# Research Report\n\n## 1. Scientific Question\n\nIs H0 tension real?\n",
        }}
      />,
    );

    expect(screen.getByText("Research Report Draft")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1. Scientific Question" })).toBeInTheDocument();
    expect(screen.queryByTestId("research-report-package")).not.toBeInTheDocument();
    expect(screen.queryByText("Paper Draft")).not.toBeInTheDocument();
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
