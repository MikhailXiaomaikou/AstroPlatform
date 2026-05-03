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
- Added frontend Research Plan, Research Matrix, Evidence Graph, and Research
  Report cards in the chat tool UI.
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
