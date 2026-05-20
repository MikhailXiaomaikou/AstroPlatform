# Changelog

All notable project changes should be recorded here in English.

This file summarizes product-facing and scientific-infrastructure changes.
Low-level refactors, test-only edits, and temporary local diagnostics do not
need entries unless they change user-visible behavior or research validity.

## Unreleased

### Added

- Added the **Solar System** research module (M0, 2026-05-18 to 2026-05-20):
  - Activated `backend/app/prompts/modules/solar_system/` (manifest + prompt + appendix) with `status: active`; selected via `ASTRO_RESEARCH_FOCUS=solar_system`.
  - Added 12 LLM-callable solar-system tools in `ai_tools_solar_system.py`:
    `query_mpc_orbit`, `fetch_horizons_ephemeris`, `query_sbdb_orbit`,
    `query_sbdb_close_approaches`, `query_sentry_risk`,
    `query_damit_shape_model`, `compute_hg_magnitude`, `compute_afrho`,
    `fit_neatm_diameter_albedo`, `compute_neo_collision_probability`,
    `classify_asteroid_busdemeo`, `classify_asteroid_sdss_colors`. Each tool
    carries inline literature references (Bowell+1989, A'Hearn+1984,
    Harris 1998 + Mainzer+2011, Öpik 1951 / Wetherill 1967 / Morbidelli+2002,
    DeMeo+2009, Carvano+2010, Ďurech+2010, Giorgini+1996).
  - Added pure-function science kernels under
    `services/solar_system_dynamics.py`, `solar_system_phot.py`,
    `solar_system_taxonomy.py`, `solar_system_thermo.py`.
  - Promoted JPL Horizons (`jpl`) and IAU Minor Planet Center (`mpc`)
    connectors to provenance-v2 active and added their provenance entries.
- Introduced **modular prompt + focus-gate architecture** (M1):
  - New three-layer prompt tree under `backend/app/prompts/`:
    `base.md` + `core/*.md` (cross-cutting rules) +
    `modules/<name>/{manifest.yaml, prompt.md, appendix.md}`.
    2 active modules (`cosmology`, `solar_system`) + 13 dormant modules.
  - New `services/prompt_loader.py` assembles the SYSTEM_PROMPT and the
    per-focus tool allowlist; `api/chat.py` `_filter_tools_by_research_focus`
    enforces L1 hard tool gating before tools reach the LLM.
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

### Fixed

- **Solar System M0 round-2 blind-test fixes (2026-05-20)** — fixes uncovered by
  a 20-case end-to-end blind test of the new module:
  - **P0 cross-module: agent-loop circuit breaker now covers hard-reject
    error_class.** `chat.py:_run_agent_loop` G3.4 mechanism extended:
    `_DATA_FETCH_TOOLS` now includes the 6 solar-system data-fetch tools
    (`query_mpc_orbit`, `fetch_horizons_ephemeris`, `query_sbdb_orbit`,
    `query_sbdb_close_approaches`, `query_sentry_risk`,
    `query_damit_shape_model`); previously they sat outside the disable gate
    and could be retried indefinitely. Added `_HARD_REJECT_ERROR_CLASSES =
    {"range_too_large", "missing_argument", "invalid_argument"}` short-circuit
    so soft/hard classification no longer misclassifies local tool rejections
    as soft via a `"too large"` substring match. This is a platform-wide
    safety improvement, not just solar-system.
  - **P1: A4 NEATM blind-test case now physically self-consistent.** Swapped
    `(1) Ceres` (12 μm 100 Jy + r=2.8 / Δ=2.0 forward-modeled to D≈423 km, not
    Ceres' real 940 km) for `(433) Eros` (12 μm 15 Jy + r=1.13 / Δ=0.46
    forward-modeled to D=16.7 km / p_V=0.22, matching Eros' real
    D≈16.84 km / p_V≈0.25).
  - **P2: MPC connector designation handling.** Added
    `_normalize_mpc_designations()` to expand input like `"(3200) Phaethon"`
    into multiple candidates (`"3200"`, `"Phaethon"`, `"(3200) Phaethon"`);
    `_query_mpc` now iterates variants × `target_type ∈ {asteroid, comet}`
    instead of issuing a single literal query. Provisional designations like
    `"1983 TB"` / `"2024 YR4"` are preserved intact.
  - **P3: Carvano+ 2010 SDSS classifier calibration.** Replaced previously
    inaccurate class centers (notably V class `r-i = -0.05`, which caused
    Vesta to be misclassified as O) with paper-accurate values
    (V class `r-i = -0.40`, reflecting the strong 1 μm absorption signature)
    plus per-color std fields for each class. Switched the scorer from
    Euclidean nearest-center to χ² (Mahalanobis-like, diagonal covariance),
    keeping `distance` / `all_distances` as backward-compat fields.
    Verified against Vesta / Bennu / Itokawa / Trojan prototypes.

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
- Paper-mining candidate pools can now switch arXiv live search from latest
  submissions to relevance-ranked queries, and the capability map now recognizes
  `run_nested_sampler` while keeping full external likelihood packages as a
  separate missing gap.
- Cosmology data-product loading now recognizes SN-style dimension-prefixed
  flattened covariance files, enabling Pantheon+/similar public covariance
  products to parse as real matrices instead of partial failures.
- Added `evaluate_chain_diagnostics`, a controlled posterior-chain diagnostic
  kernel for explicit samples. It computes R-hat, ESS, MCSE, HDI, and
  publication-readiness status without running a likelihood or inventing
  parameter constraints.
- `load_cosmology_data_product` can now expose registry-backed compressed
  Gaussian mean/covariance summaries for ACT, Planck, weak-lensing, SH0ES, and
  similar datasets even when there is no separate downloadable data-product
  file registered.
- `export_research_report` now returns a small report package with Markdown,
  BibTeX, dataset/citation summaries, and a reproducibility manifest instead of
  only a prose draft.
- Research-mode final replies now attach a Fact Check card; contradicted claims
  are replaced with a conservative tool-grounded summary.
- Updated cosmology registry documentation to distinguish machine-readable
  data-product provenance from executable posterior results. DESI and
  Pantheon+ remain external-likelihood/config-only until a runner consumes
  their public files directly.
- Updated README and architecture documentation to reflect the expanded
  data-product coverage for DESI, Pantheon+, and Planck.
- Removed 18 shell tool specs that had no dispatch path (M2) and 4 dead
  frontend pages with their pipeline node components (M3), so the tool
  catalog and page surface match what the agent loop and router actually
  execute.

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
