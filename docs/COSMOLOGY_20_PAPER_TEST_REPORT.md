# Cosmology 20-Paper Blind Test Report

Date: 2026-05-27  
Scope: observational cosmology anomaly papers, paper-derived blind prompts P01-P20  
Execution path: local backend Chat stream endpoint (`/api/chat/message/stream`) with a fresh session per paper  
Model route: DeepSeek V4 Pro through the platform inference router  
Full local artifacts: `.local/blind-research-tests/round-2026-05-27-20-cosmology/chat-runs-20260527_125315/`

This is a paper-derived blind run. The platform received only ordinary
research-style prompts derived from each paper's direction and method. It did
not receive paper titles, arXiv IDs, conclusions, or hidden key numbers.

## Summary

| Metric | Count |
|---|---:|
| Cases run | 20 |
| Backend/API process crashes | 0 |
| Research-grade pass (A) | 0 |
| Partial pass (B) | 15 |
| Honest failure (C) | 3 |
| Route error (D) | 0 |
| Severe failure (E) | 2 |
| Runs with ready numeric compressed cells | 17 |
| Runs with no ready numeric result | 3 |
| Fact check `passed` | 9 |
| Fact check `blocked` on draft | 9 |
| Fact check absent because final workflow timed out | 2 |
| Final prose empty | 2 |
| Deterministic fallback summary emitted after synthesis failure | 3 |
| Internal marker leak in final prose | 0 |
| Unsupported numeric claim in final prose | 0 observed |

Short verdict: the platform is behaving like an honest compressed-likelihood
research workbench, not like a full anomaly-paper reproduction system. It
usually plans the study, runs baseline BAO/CMB compressed cells, marks
model-specific branches as missing, and avoids unsupported final numbers. The
largest product flaw is still final-answer reliability under timeout.

## Per-Case Results

| ID | Broad paper class | Score | Tool chain | Result |
|---|---|---:|---|---|
| P01 | BAOtr vs 3D BAO / CPL / Hubble tension | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline BAO and BAO+CMB cells ran. SN/CPL-specific branches marked missing. Draft fact-check blocked unsafe wording; final answer was conservative. |
| P02 | ACT-era radiation / spectral running / Hubble tension | E | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `list_cosmology_datasets` → `search_literature` → `build_cosmology_likelihood` → `run_cosmology_likelihood_chain` | Tool chain progressed deeply, then workflow timed out after 420s with no final prose. |
| P03 | Late-time dynamical dark energy / w0wa | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed cells ran. w0wa/SN/full-likelihood branches correctly limited. |
| P04 | Cosmic birefringence + EDE | C | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Honest config-only result. Polarization-rotation datasets are registered but not runnable. |
| P05 | Coupled dark energy / dark matter | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline distance matrix ran. Interacting dark-sector model runner missing. |
| P06 | Early-vs-late dark energy distance tension | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline matrix ran. Model-specific early/late separation still missing. |
| P07 | Information geometry / wCDM Hubble tension | E | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `run_python` | Tool chain ran, then workflow timed out after 420s with no final prose. |
| P08 | SPIDER/Planck/ACT cosmic birefringence | C | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Honest no-runnable-likelihood result. No rotation angle claimed. |
| P09 | Dark acoustic oscillations / Hubble tension | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran. Dark-acoustic/EDE-specific runner missing. |
| P10 | Reformulated Hubble-tension expansion history | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran. Missing E(z)-specific reconstruction runner. |
| P11 | Barotropic alternative to EDE | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Final synthesis backend failed, but deterministic tool-grounded fallback summary was emitted. |
| P12 | Late-time dark energy + pre-recombination physics | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Same as P11: fallback summary protected the user from an empty answer. |
| P13 | Inhomogeneous-universe density profile / SN+BAO | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran. Density-profile reconstruction runner missing. |
| P14 | Local void model / Hubble and BAO tensions | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran. Local-void model runner missing. |
| P15 | Spectral siren H0 robustness | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran, but spectral-siren machinery is not present. |
| P16 | CROCS / H0 constraints | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `fact_check_report` → `verify_research_facts` | Baseline compressed matrix ran; explicit fact-check tools appeared. |
| P17 | Redshift evolution of H0 / interacting DE | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran. H(z)-evolution/interacting model runner missing. |
| P18 | GW standard sirens + EM observations | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Final synthesis backend failed, but deterministic fallback summary was emitted. Standard-siren runner missing. |
| P19 | Field-level cosmic birefringence | C | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `search_literature` ×3 | Honest no-runnable-likelihood result. Literature backend returned empty/timeouts/429, but final answer did not invent a rotation angle. |
| P20 | Hubble-parameter tomography / cosmography | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline compressed matrix ran. Linear-cosmography tomography runner missing. |

## What Worked

### 1. Baseline compressed cosmology path is stable

Every case invoked:

```text
plan_research_program
→ run_research_matrix
→ build_evidence_graph
```

Most H0 / EDE / dark-energy prompts produced the same controlled baseline:

- BAO only.
- BAO + CMB.
- `compressed-likelihood preliminary` wording.
- no claim of full Planck, full Cobaya, or full CosmoSIS reproduction.

The repeated sanity numbers were stable:

- BAO only: `H0 median 68.66`, `ESS 470.8`, `Rhat 1`.
- BAO + CMB: usually `H0 median 67.31`, `ESS 471.3`, `Rhat 1`.

Those numbers are useful only as registered compressed-likelihood preliminary
checks. The final answers mostly preserved that limitation.

### 2. Missing anomaly-specific methods were usually stated clearly

The platform did not pretend to have:

- EDE Boltzmann/emulator inference.
- Interacting dark-sector solvers.
- Local-void or inhomogeneous-universe reconstruction.
- Information-geometric Fisher/eigenmode machinery.
- Spectral-siren H0 inference.
- GW standard-siren likelihoods.
- Hubble-parameter tomography / cosmography runner.
- Field-level BAO forward model.
- EB/TB cosmic-birefringence likelihoods.

This is the right failure mode for professional alpha testing.

### 3. Synthesis fallback has improved

P11, P12 and P18 hit final language synthesis failures, but the platform emitted
a deterministic tool-grounded fallback:

```text
The research tools completed, but the model's final language synthesis failed...
Below is the tool-grounded summary of what ran; no unsupported conclusion is made.
```

That converts what used to be an empty answer into a usable B-class partial
result. This is a major reliability improvement compared with earlier rounds.

### 4. No unsupported final cosmology numbers observed

Backend guardrails did catch multiple attempted unsupported draft claims in
logs, including bare significance values, Omega_m, and suspicious citations.
The final visible answers did not carry those unsupported numbers through.

## What Failed

### P0: Workflow timeout still produces empty final answers

P02 and P07 both ended with:

```text
AI workflow timed out after 420s. Try a narrower query or split the task...
```

No final prose was emitted, even though useful tool results existed. The
deterministic fallback layer catches synthesis-backend failure, but it does not
yet catch workflow timeout. This is now the top reliability bug.

Fix direction:

- On workflow timeout, inspect accumulated tool results.
- If any research plan/matrix/evidence graph exists, emit a timeout fallback
  summary.
- Mark it `partial_timeout`, not silent failure.

### P0: Some intermediate agent text still contains unsafe literature-like claims

P19 had an intermediate `agent_text` block after three failed literature
queries. It included an example-style canonical birefringence constraint even
though no current-turn literature result supported that number. The final
`text` event was safe and conservative, but the test trace proves the agent can
still generate unsupported literature priors in intermediate text.

Fix direction:

- Ensure `agent_text` is never rendered as final user prose unless it passes
  the same citation/fact gates.
- Add diagnostic counters for unsafe intermediate text.

### P1: Draft fact-check blocked is too common and too ambiguous

9 of 20 runs had `fact_check_status=blocked` on the draft. In most of those,
the final answer appears safe and rewritten. The current status does not
distinguish:

- unsafe draft blocked and safely rewritten;
- unsafe final answer blocked;
- no final answer;
- clean pass.

Fix direction:

- Introduce `draft_fact_check_status`.
- Introduce `final_answer_status`.
- Add `safe_rewrite_applied`.

### P1: Registry lacks literature-source service entries

During P02/P19 style literature calls, backend logs showed missing provenance
registry entries for:

- `arxiv`
- `arxiv_free_text`
- `ads_or_arxiv_object`

This does not break the core cosmology matrix, but it weakens DataSources /
provenance display for literature-derived context.

Fix direction:

- Add registry entries for arXiv and ADS/arXiv literature objects.
- Make search_literature provenance distinguish paper metadata from
  measurement/posterior evidence.

### P1: Literature backend can timeout or rate-limit

P19 produced:

```text
arXiv fallback failed: read operation timed out
arXiv literature search failed: 429
```

The final answer handled this honestly, but repeated 429s will make broad blind
testing noisy.

Fix direction:

- Add local literature cache for repeated anomaly-test queries.
- Add rate-limit backoff and explicit `literature_backend_degraded` status.

### P2: Prompt-specific method coverage is still shallow

The answers are scientifically honest, but many B-class cases reduce to the
same BAO/CMB baseline. For alpha testers, the platform should put the
paper-specific missing method at the top every time.

The current answers improved by adding:

```text
Model-level limitation (read first)
```

but the missing-runner taxonomy should become more precise and less repetitive.

## Comparison With The Hidden Papers

This run mostly agrees with the hidden-paper methods at the **capability
boundary** level:

- Papers requiring only baseline BAO/CMB compressed checks receive partial
  executable results.
- Papers requiring model-specific anomaly machinery are correctly marked
  incomplete.
- Birefringence papers are treated as config-only / no-likelihood rather than
  given invented rotation angles.

It does not reproduce the paper conclusions in the strong sense. Most hidden
papers rely on special likelihoods, datasets, forward models, or model
implementations that are not currently in the platform. Therefore the correct
score is mostly B/C, not A.

## Engineering Priorities

1. Timeout fallback summary for any run with accumulated tool results.
2. Treat unsafe `agent_text` like final prose unless explicitly hidden.
3. Clarify draft-vs-final fact-check states.
4. Add arXiv / ADS literature provenance registry entries.
5. Add literature cache / rate-limit backoff for blind-test loops.
6. Add actual runners for the most frequent missing methods:
   - Pantheon+ SN likelihood.
   - CMB EB/TB rotation-angle likelihood.
   - H(z) / cosmic chronometer compressed runner.
   - GW standard-siren likelihood.
   - BAO per-bin consistency/outlier diagnostic.
   - EDE / interacting dark sector remains second-stage because it needs a
     controlled Boltzmann/emulator stack.

## Current Assessment

Compared with earlier rounds, this is a better platform state:

- Fewer empty answers because synthesis failure fallback exists.
- Stronger model-level limitation wording.
- Better separation between compressed preliminary and full likelihood.
- Still no evidence of final-answer numerical hallucination in this run.

But it is not ready for open-ended anomaly-paper reproduction. It is ready for
postdoc-level closed alpha feedback if framed correctly:

> This platform can plan and execute registered compressed cosmology workflows,
> expose missing likelihoods, and produce evidence-linked preliminary summaries.
> It cannot yet reproduce arbitrary recent anomaly papers end-to-end.

## Follow-up Fixes Applied

After this 20-paper run, the top reliability issues were patched in the backend:

- Workflow timeout now attempts to recover already-streamed tool results from
  the current turn and emits a deterministic tool-grounded partial summary
  instead of returning only an error.
- Research/cosmology intermediate `agent_text` is hidden while tools are still
  running, so unsupported draft prose cannot appear as user-facing research
  narrative before evidence/fact checks.
- Research-mode final answers are now forced through the deterministic
  `Research-mode summary` path whenever the turn contains Research Plan /
  Matrix / Evidence Graph tool results. This prevents the LLM from appending
  unsupported literature background, citations, or model-comparison claims after
  the tools have completed.
- The Research-mode summary now includes both `H0` and `Omega_m` medians for
  publication-ready matrix cells when those values are present in the tool
  output.
- The fallback provenance registry now includes paper-level entries for `arxiv`
  and `ads_or_arxiv_object`, explicitly marked as literature/context sources
  rather than measurement or posterior evidence.

Post-fix validation:

- `ruff check app tests`: passed.
- `pytest tests/test_cosmology_likelihood_routing.py -q --no-cov`: 34 passed.
- `pytest tests/test_cosmology_likelihood_routing.py tests/test_h_regression.py -q --no-cov`: 137 passed.
- Chat UI smoke test for a BAO + SN + CMB research matrix now produces a
  deterministic `Research-mode summary`. The BAO+CMB cell reports
  `H0 median 67.31`, `Omega_m median 0.3116`, `ESS 471`, and `Rhat 1.0`; cells
  involving Pantheon+ remain clearly marked config-only / not numerically run.

## Loop Continuation: S8 / Weak-Lensing Smoke

Prompt class: weak-lensing S8 consistency with CMB and BAO in flat LCDM.

Observed chain:

```text
plan_research_program
→ run_research_matrix
→ build_evidence_graph
→ verify_research_facts
→ export_research_report
```

Initial issue exposed:

- The matrix did run BAO+WL and All-selected-probes cells, but their
  importance-sampling ESS values were below the publication threshold. The
  deterministic summary previously grouped every non-ready cell under a broad
  “external likelihood / not numerical evidence” message. That was too coarse:
  an executed-but-low-ESS cell is different from a config-only cell.

Fix applied:

- Research summaries now separate:
  - publication-ready compressed cells;
  - executed but not claimable cells with ESS/Rhat diagnostics;
  - config-only / missing-runner cells.
- Ready cells now include `S8` when present in the publication-ready tool
  output.

Post-fix S8 smoke result:

- Ready cells:
  - BAO only: `H0 median 68.66`, `Omega_m median 0.2951`,
    `ESS 470.8`, `Rhat 1`.
  - BAO + CMB: `H0 median 67.28`, `Omega_m median 0.3117`,
    `S8 median 0.8317`, `ESS 507.5`, `Rhat 1`.
- Executed but not claimable:
  - All selected probes: `ESS 2.503 below threshold 400`.
  - BAO + WL: `ESS 105 below threshold 400`.
- Final answer status:
  - deterministic `Research-mode summary`;
  - no unsupported final numerical claim;
  - unsafe draft claims rewritten out before display.

Validation after this fix:

- `ruff check app tests`: passed.
- `pytest tests/test_cosmology_likelihood_routing.py -q --no-cov`: 35 passed.

## Loop Continuation: CMB Polarization Rotation / Cosmic Birefringence

Prompt class: CMB EB/TB polarization-rotation / parity-violation test.

Observed chain:

```text
plan_research_program
→ run_research_matrix
→ build_evidence_graph
→ verify_research_facts
→ export_research_report
```

Initial issue exposed:

- The platform correctly refused to quote a rotation angle because no
  publication-ready EB/TB rotation likelihood was available.
- However, the Research-mode summary leaked raw Python dictionary-like dataset
  objects in the user-facing missing-dataset sentence, e.g. `{'key': ...}`.
- The same summary also used “compressed-likelihood preliminary baseline”
  wording even when zero publication-ready compressed cells completed. That was
  technically misleading for this class of config-only CMB-rotation request.

Fix applied:

- Config-only dataset gaps are now formatted as user-facing dataset labels,
  such as `Planck PR4/NPIPE EB/TB polarization-rotation products
  (planck_pr4_ebtb_rotation)`.
- If no publication-ready compressed-likelihood cell completed, the summary now
  says that explicitly instead of implying a baseline result exists.

Post-fix CMB-rotation smoke result:

- Ready cells: none.
- Matrix status: `0 ready out of 1`.
- Config-only / not-runnable branch:
  - CMB rotation — isotropic beta requires Planck PR4/NPIPE EB/TB products,
    ACT DR6 EB/TB products, and BICEP/Keck BK18 rotation products.
- Final answer status:
  - deterministic `Research-mode summary`;
  - no rotation angle quoted;
  - no raw dict / internal object leakage;
  - clear statement that this turn supports dataset/method availability only,
    not posterior claims.

Validation after this fix:

- `ruff check app tests`: passed.
- `pytest tests/test_cosmology_likelihood_routing.py -q --no-cov`: 36 passed.
