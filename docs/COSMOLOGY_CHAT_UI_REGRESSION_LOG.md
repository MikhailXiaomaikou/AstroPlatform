# Cosmology Chat UI Regression Log

This log records local front-end Chat UI checks for the observational
cosmology research workflow. It is separate from hidden-paper blind tests:
these entries verify that the user-visible workflow, panels, fact check, and
report export behave correctly.

## 2026-05-27 — BAO + SN + CMB Research Matrix

### Test Setup

- Interface: local Codex in-app browser, not Chrome.
- URL: `http://127.0.0.1:5173/chat`.
- Backend: local FastAPI server on `127.0.0.1:8000`.
- Frontend: local Vite dev server on `127.0.0.1:5173`.
- Model route shown by UI: DeepSeek V4 Pro through the platform backend.
- Prompt class: ordinary research request for public BAO, supernova, and CMB
  compressed data under flat LCDM.

### Expected Behavior

- The system should plan the research matrix before reporting numbers.
- `BAO + CMB` should be a claimable compressed-preliminary cell.
- `BAO + CMB` should have `ESS > 400`.
- `BAO + CMB` H0 should be approximately in the `67-68 km/s/Mpc` range.
- Low-diagnostic cells should be visible but not claimable.
- Fact Check should not globally block valid ready cells because of a scope-gap
  sentence.
- Final report and paper draft should keep ready cells in Results and move
  low-diagnostic cells to Robustness/Scope.

### Observed Tool Flow

The Chat UI executed the expected research-mode tool chain:

```text
plan_research_program
-> run_research_matrix
-> build_evidence_graph
-> verify_research_facts
-> export_research_report
```

The UI displayed Research Plan, Research Matrix, Claim Provenance, Fact
Verification, Report, and Paper Draft sections.

### Key Matrix Results

| Cell | Status | H0 median | Omega_m median | ESS | Rhat | Claimable |
|---|---:|---:|---:|---:|---:|---|
| BAO only | ready | 68.455 | 0.2951 | 471 | 1.000 | yes |
| SN only | ready | 73.052 | 0.3341 | 4000 | 1.000 | yes |
| CMB only | ready | 67.353 | 0.3152 | 4000 | 1.000 | yes |
| BAO + CMB | ready | 67.305 | 0.3116 | 471 | 1.000 | yes |
| BAO + SN | ready | 72.981 | 0.3107 | 525 | 1.000 | yes |
| SN + CMB | ready | 68.559 | 0.3183 | 4000 | 1.000 | yes |
| BAO + SN + CMB | executed_not_ready | 68.691 | not claimable | 38.8 | 1.000 | no |
| BAO only + H0 prior | executed_not_ready | 73.038 | not claimable | 323 | 1.000 | no |
| BAO + CMB + H0 prior | executed_not_ready | not claimable | not claimable | 2.663 | 1.000 | no |
| BAO + SN + H0 prior | executed_not_ready | not claimable | not claimable | 14.805 | 1.000 | no |
| BAO + SN + CMB + H0 prior | executed_not_ready | not claimable | not claimable | 1 | 1.000 | no |

Primary acceptance check passed:

```text
BAO + CMB: H0=67.305, Omega_m=0.3116, ESS=471, Rhat=1.000
```

### Fact Check Result

Initial regression exposed a false block:

- A draft sentence saying that a full external Cobaya/CosmoSIS chain would be
  needed was interpreted as a contradictory claim.
- This caused the report to treat the whole turn as blocked even though several
  cells were ready and claimable.

Fix applied:

- Future-work language such as `would be needed`, `would be required`, and
  `would require` is now classified as a scope-gap statement.
- Such text no longer blocks ready current-turn numerical results.

Post-fix Chat UI result:

- Fact Verification status: `passed`.
- Verified claims: 2.
- Unsupported/contradicted claims: 0.
- Ready cells remained in Results.
- Low-ESS cells remained visible under Robustness/Scope.

### Verification Commands

Backend checks:

```bash
cd backend
./.venv/bin/ruff check app tests
./.venv/bin/pytest tests/test_research_program.py -q --no-cov
```

Results:

```text
ruff: all checks passed
pytest: 50 passed
```

### Product Interpretation

This workflow is now behaving like a controlled compressed-likelihood
research workbench:

- It can produce claimable preliminary numbers for ready two-probe cells.
- It does not promote low-ESS multi-probe cells to Results.
- It preserves the distinction between compressed preliminary runs and full
  external likelihood reproductions.
- It exposes the process and diagnostics in the Chat UI instead of silently
  failing.

Remaining limitation:

- The first-phase compressed importance-sampling path still struggles with
  several three-probe combinations. Those cells are correctly labelled
  `executed_not_ready`, but a full external Cobaya/CosmoSIS runner or improved
  sampler is still needed for publication-grade BAO+SN+CMB claims.
