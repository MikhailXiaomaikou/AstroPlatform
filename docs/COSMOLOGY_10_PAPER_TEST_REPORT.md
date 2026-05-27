# Cosmology 10-Paper Blind Test Report

Date: 2026-05-26  
Scope: observational cosmology, paper-derived blind prompts Q01-Q10  
Execution path: local backend Chat stream endpoint (`/api/chat/message/stream`) with fresh sessions per paper  
Model route: DeepSeek V4 Pro via the platform inference router  
Full local artifacts: `.local/blind-research-tests/round-2026-05-26-10/chat-runs-20260526_212208/`

This report records a 10-paper cosmology blind-test pass. The hidden paper
records were kept outside the prompts. The platform received only research
questions derived from each paper's direction and method, not the title,
arXiv ID, conclusion, or key numbers.

## Summary

| Metric | Count |
|---|---:|
| Cases run | 10 |
| Backend/API crashes | 0 |
| Research-grade pass (A) | 0 |
| Partial pass (B) | 7 |
| Honest failure (C) | 2 |
| Route error (D) | 0 |
| Severe failure (E) | 1 |
| Silent / no-final-prose failures | 1 |
| Internal marker leaks | 0 |
| Unsupported numeric claims in final prose | 0 observed |
| Runs with publication-ready compressed numeric cells | 8 |
| Runs with fact-check `passed` | 7 |
| Runs with fact-check `blocked` draft but safe final summary | 2 |
| Runs without final fact-check because final synthesis failed | 1 |

Overall result: the cosmology research-mode path is usable for controlled
compressed-likelihood exploratory work, but it is not yet a paper-level
anomaly reproduction system. It consistently distinguishes executable
baseline ΛCDM compressed cells from missing model-specific likelihoods. The
main failure is one final-answer synthesis backend failure after successful
tool execution.

## Test Inputs

The 10 hidden records were stored locally at:

```text
.local/blind-research-tests/round-2026-05-26-10/hidden_records.json
.local/blind-research-tests/round-2026-05-26-10/blind_prompts.json
```

The prompts covered:

- DESI-era dark-energy and scalar-field/quintessence tests.
- Non-DESI w0wa cross-checks.
- Hubble-tension and pre-recombination model scenarios.
- Early-dark-energy / ACT lensing / DESI BAO scenarios.
- Planck and ACT cosmic-birefringence / polarization-rotation scenarios.
- Model-independent DESI-era expansion-history checks.
- Physical dark-energy source-history models.
- Field-level BAO inference.
- DESI BAO redshift-bin consistency / outlier diagnostics.

## Per-Case Results

| ID | Hidden paper class | Score | Tool chain | Result |
|---|---|---:|---|---|
| Q01 | DESI dark energy / quintessence | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `run_cosmology_likelihood_chain` | Baseline BAO and BAO+CMB compressed cells ran. Scalar-field-specific pieces correctly marked as missing. |
| Q02 | w0wa dark energy / non-DESI cross-check | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline cells ran. H(z), growth-rate, SN and extended-model branches marked as external or missing. Draft fact-check blocked unsafe wording; final summary used safe rewrite. |
| Q03 | DESI BAO / Hubble tension / pre-recombination models | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline BAO and BAO+CMB results returned as compressed preliminary. Pre-recombination model-specific claims withheld as scope gap. |
| Q04 | Early dark energy / ACT lensing / DESI BAO | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | ACT/BAO/CMB baseline matrix was built; EDE emulator/Boltzmann likelihoods correctly marked missing. |
| Q05 | Cosmic birefringence / Planck polarization | C | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Honest failure. No polarization-rotation likelihood exists, so no rotation-angle or posterior claim was made. |
| Q06 | Cosmic birefringence / ACT DR6 | C | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Honest failure. ACT-like EB/TB rotation-angle likelihood, calibration priors, leakage marginalization and covariance are missing. |
| Q07 | Model-independent DESI BAO / reconstruction | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` → `extract_literature_tables` | Baseline BAO and BAO+CMB cells ran. Non-parametric reconstruction tooling is missing. Table extraction was attempted but not enough to make reconstruction claims. |
| Q08 | DESI BAO / compact-object source-history model | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline distance tools ran. The physical source-history model is not implemented. Draft fact-check caught unsupported/full-likelihood wording and an unsupported source; final summary omitted unsafe claims. |
| Q09 | BAO field-level inference / forward model | E | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Tools completed, but final synthesis failed with `All configured AI backends failed: deepseek:`. No final prose was returned. |
| Q10 | DESI BAO bin consistency / outlier diagnostics | B | `plan_research_program` → `run_research_matrix` → `build_evidence_graph` | Baseline BAO and BAO+CMB cells ran. Per-bin covariance/outlier diagnostics are not registered, so trend/tension claims were correctly withheld. |

## Repeated Numeric Pattern

The successful baseline compressed cells repeatedly returned the same stable
sanity-check values:

- BAO only: `H0 median 68.66`, `ESS 470.8`, `Rhat 1`.
- BAO + CMB: usually `H0 median 67.31`, `ESS 471.3`, `Rhat 1`.
- ACT-lensing-including variants in some prompts appeared only when supported
by the registered matrix and were still labeled compressed preliminary.

These values were presented as `compressed-likelihood preliminary`, not as full
Planck/Cobaya/CosmoSIS results. That distinction is critical and was preserved
in final prose for the successful cases.

## Main Findings

### 1. Research Mode is stable for baseline compressed cosmology

Most cases executed the intended high-level pattern:

```text
research question
→ plan_research_program
→ run_research_matrix
→ build_evidence_graph
→ final research-mode summary
```

For DESI/BAO/CMB/H0-style prompts, the platform usually produced a usable
compressed preliminary baseline. It did not claim full external likelihood
reproduction.

### 2. The platform is honest about missing anomaly-specific tools

The following model classes were not overclaimed:

- scalar-field/quintessence dynamics beyond baseline w0wa planning;
- pre-recombination and early-dark-energy Boltzmann/emulator inference;
- cosmic-birefringence EB/TB likelihoods;
- ACT/Planck polarization-rotation calibration and leakage modeling;
- field-level BAO forward modeling;
- per-bin BAO covariance/outlier diagnostics;
- physical compact-object source-history dark-energy models.

This is the correct behavior. It is better to return B/C than to invent
paper-level anomaly numbers.

### 3. Fact verification is useful but still awkward

Q02 and Q08 had `fact_check_status=blocked` on the draft, but the final answer
did not expose the unsafe claims. Instead it stated:

```text
Draft fact-check status: blocked; ...
The final summary omits unsafe draft claims; use safe-rewrite guidance ...
```

This is scientifically safer than letting the draft through, but it is still
awkward UX. The user sees "blocked" even when the visible final answer has
already been rewritten safely. The product should distinguish:

- `draft_blocked_final_safe`
- `final_blocked_no_answer`
- `final_passed`

### 4. Q09 exposes a final-synthesis reliability bug

Q09 completed the tool chain but returned no final prose. The last events were:

```text
tool_result: build_evidence_graph
status: still thinking...
error: All configured AI backends failed: deepseek:
done
```

That is a platform/inference reliability failure, not a science failure. The
tool results existed, so the backend should have emitted a deterministic
fallback summary rather than an empty answer.

### 5. The hidden records are insufficient for full paper-conclusion scoring

The local Q01-Q10 hidden records include title, arXiv ID, class, hidden method
and prompt, but they do not include full paper conclusions or key numerical
results. Therefore this run can score method/tool behavior, but cannot honestly
claim full paper-result agreement.

For future 10-paper batches, hidden records should include:

- paper conclusion;
- key numbers / significance / posterior;
- public data and code status;
- expected minimum tool family;
- expected answer category if the platform is behaving correctly.

## Problems To Fix

### P0: Deterministic fallback when final LLM synthesis fails

If tools ran successfully but the final model call fails, the backend should
emit a safe structured fallback:

```text
The research tools completed, but final language synthesis failed.
Here is the tool-grounded summary:
- executed tools ...
- ready cells ...
- missing cells ...
- no unsupported conclusion is made.
```

This would have converted Q09 from E to C/B.

### P0: Connector cache async `InvalidStateError`

During the run, backend shutdown logs showed:

```text
Task exception was never retrieved
connector_cache.py ... fut.set_result(result)
asyncio.exceptions.InvalidStateError: invalid state
```

This likely happens when a cached connector computation finishes after the
waiting future has already been cancelled, timed out, or otherwise completed.
The cache runner should check `fut.done()` before calling `set_result()` or
`set_exception()`. This is not the same as a scientific error, but it is exactly
the kind of async hygiene issue that can produce flaky behavior in long blind
test loops.

### P1: Clarify fact-check blocked draft vs safe final answer

Current wording can make a safe final answer look like a failure. Add explicit
status:

- `fact_check_draft_status`
- `final_answer_status`
- `safe_rewrite_applied: true/false`

Frontend should show "unsafe draft rewritten" rather than "blocked" when the
final visible prose is safe.

### P1: Add capability-specific missing-tool cards

Cosmic birefringence and field-level BAO failures are honest, but the missing
capability should be turned into a clear implementation row:

- CMB polarization rotation likelihood runner.
- EB/TB estimator and calibration-prior registry.
- Field-level BAO forward-model runner.
- Per-bin BAO covariance and outlier-diagnostic runner.

### P2: Improve hidden-record schema before larger scoring

Without hidden conclusions and key numbers, the system cannot be scored against
paper outcomes. This is a test-protocol gap, not a platform feature bug.

### P2: Reduce repeated generic matrix summaries

Many B-class answers are nearly identical because the executable layer is
mostly BAO and BAO+CMB ΛCDM. This is accurate, but it makes anomaly-specific
responses feel shallow. The answer should put the model-specific gap first:

```text
This paper-style question cannot be tested at its intended model level because
the EDE / birefringence / field-level runner is missing. The only executable
baseline is ...
```

### P2: Citation/narrative gate noise should be summarized cleanly

The backend logs showed multiple internal guardrail interventions, including
suspicious author-year citation blocks and unsupported narrative replacements.
The final visible answers were mostly safe, but the logs are noisy and hard to
interpret during test triage. Diagnostic bundles should summarize these as
structured counters:

- citation gate blocks by agent;
- narrative gate blocks by agent;
- safe rewrite applied or not;
- final answer emitted or withheld.

This would make it easier to distinguish "guardrail worked and final answer was
safe" from "guardrail blocked the only useful answer".

## Current Assessment

The cosmology module is not yet ready to reproduce arbitrary recent anomaly
papers. It is, however, useful as an honest exploratory research workbench for
registered compressed BAO/CMB-style baselines.

The strongest positive result is that 9/10 runs avoided unsupported anomaly
numbers. The strongest negative result is that one successful tool-chain run
ended with no final prose because the selected model backend failed during
final synthesis.

The next engineering step is not another likelihood. The next step is a
reliable deterministic fallback summary layer for completed tool chains.
