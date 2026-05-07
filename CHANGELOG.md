# Changelog

All notable project changes should be recorded here in English.

This file summarizes product-facing and scientific-infrastructure changes.
Low-level refactors, test-only edits, and temporary local diagnostics do not
need entries unless they change user-visible behavior or research validity.

## Unreleased

### Added

- Added Research Mode v1 for observational cosmology: `plan_research_program`
  creates a structured research DAG, `run_research_matrix` executes runnable
  compressed-likelihood cells while preserving config-only gaps,
  `build_evidence_graph` links claimable parameters to current-turn tool runs
  and dataset citations, and `export_research_report` drafts an auditable
  Markdown report.
- Added fact verification for research-mode outputs via `verify_research_facts`,
  including checked source identifiers, unsupported/contradicted claim reporting,
  and safe-rewrite guidance.
- Added paper-to-tool mining infrastructure: `mine_paper_tools`,
  `run_paper_tool_mining_batch`, `build_tool_ontology`,
  `build_tool_gap_matrix`, and `rank_tool_implementation_queue` map full-paper
  method/table/equation evidence into ToolSpecs, recurring capabilities, gaps,
  and implementation priorities.
- Added `build_paper_mining_candidate_pool` to assemble deduplicated paper
  candidates from supplied seeds or explicitly enabled arXiv searches before
  entering the mining loop.
- Added `load_cosmology_data_product`, a controlled registry-backed loader for
  machine-readable cosmology data vectors and covariance products. It parses
  ASCII tables/matrices, reports shape/preview rows, verifies sha256 when
  available, and performs covariance sanity checks without claiming posterior
  constraints.
- Added `run_nested_sampler`, a controlled dynesty nested sampler for typed
  Gaussian likelihood summaries. It reports posterior summaries, evidence,
  diagnostics, seed, package version, and provenance without accepting raw
  user likelihood code or arbitrary YAML.
- Added a bounded local paper-mining loop via `run_paper_tool_mining_loop`.
  Each round processes the next 20 unread related papers, updates local-only
  loop state, and carries the ToolSpec/gap/implementation queue into the next
  round without treating mining output as scientific evidence.
- Added frontend Research Plan, Research Matrix, Evidence Graph, Fact Check, and
  Research Report cards in the chat tool UI, plus Paper Tool Mining, Tool
  Candidate Pool, Tool Mining Loop, Tool Ontology, Tool Gap Matrix, and
  Implementation Queue cards.
- Registered machine-readable observational-cosmology data products for the
  priority executable-adapter path:
  - DESI DR1 BAO public mean vector, covariance matrix, and bin-level product
    entry points from `CobayaSampler/bao_data`.
  - Pantheon+ public distance table, statistical/systematic covariance
    matrices, and CosmoSIS likelihood wrappers.
  - Planck 2018 public likelihood-code landing page and compressed
    distance-prior table source.
- Added frontend display for cosmology registry `data_products`, including
  product count, product roles, and a source link.
- Added targeted registry filtering so the chat agent can list only datasets
  relevant to the current cosmology prompt instead of always showing the full
  registry.

### Changed

- Research-style observational-cosmology prompts are now routed through a
  plan-first workflow before executable compressed likelihood cells are run.
- Platform-expansion decisions now have a paper-mining path: abstract-only
  literature hits remain low-confidence, while high-confidence ToolSpecs require
  source spans from methods, tables, equations, appendices, or substantial full
  text.
- Long-running platform expansion can now be operated as repeated 20-paper
  mining rounds with explicit state handoff, rather than one-off subjective
  roadmap guesses.
- The first paper-mining-driven implementation target landed: cosmology
  data-product ingestion now validates registered public files before likelihood
  runners consume them.
- Research-mode final replies now attach a Fact Check card; contradicted claims
  are replaced with a conservative tool-grounded summary.
- Updated cosmology registry documentation to distinguish machine-readable
  data-product provenance from executable posterior results. DESI and
  Pantheon+ remain external-likelihood/config-only until a runner consumes
  their public files directly.
- Updated README and architecture documentation to reflect the expanded
  data-product coverage for DESI, Pantheon+, and Planck.

### Guardrails

- Cosmology posterior, tension, AIC/BIC, and robustness claims still require a
  `publication_ready=true` chain result. Registry metadata, paper abstracts,
  config-only outputs, and raw data-product links are not sufficient to support
  numerical posterior claims.

## Recent Milestones

### Observational Cosmology Execution Layer

- Added a phase-1 compressed Gaussian likelihood runner for registered datasets
  with explicit mean vectors and covariance matrices.
- Added a robustness-matrix workflow for comparing runnable and non-runnable
  dataset combinations without fabricating posterior numbers for config-only
  combinations.
- Added validator coverage for cosmology posterior claims such as `H0`,
  `Omega_m`, `sigma8`, `S8`, tension, `nσ`, and fit-statistic claims.

### Cosmology Routing and Blind-Test Hardening

- Routed cosmology prompts through the registry/config/runner path instead of
  ad-hoc Python or generic literature summaries.
- Added prompt-scope handling for CMB-only, SN-only, DESI-only, pre-DESI BAO,
  weak-lensing, curvature, and neutrino-mass requests.
- Added English and Chinese exclusion handling so prompts like "do not include
  BAO/SN/weak lensing" are respected.
- Kept registry citations verbatim in model context to avoid accidental
  citation drift.

### Provenance and Data-Product Transparency

- Exposed archive/source provenance in chat tool panels, including dataset
  citations, source authority, archive versions, and machine-readable product
  availability where applicable.
- Kept synthetic, failed, empty, unavailable, config-only, and compressed-result
  states separate in both backend payloads and frontend display.
