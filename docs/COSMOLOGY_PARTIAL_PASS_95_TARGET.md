# Cosmology Blind-Test Partial-Pass Target

Status: implemented as a Research Mode readiness signal.

## Target

The near-term blind-test target is:

- **B-or-better partial pass rate: 95%**
- Scope: observational-cosmology hidden-paper prompts
- Meaning: Standard Astro should either run a relevant controlled baseline or produce an exact, auditable missing-capability map without unsupported numerical claims.

This is **not** a claim that the platform reproduces 95% of paper conclusions. A strict paper-conclusion match remains a separate external assessment.

## B-Level Partial Pass Definition

A run can count as B-level partial pass only if all of these hold:

1. The science workflow/probe family is recognized.
2. The plan selects plausible registered datasets, or explicitly explains why no dataset is executable.
3. The system either:
   - runs at least one controlled compressed/preliminary baseline, or
   - builds a domain-specific capability gap matrix with named missing pieces.
4. The final answer does not contain unsupported posterior, fit, significance, or anomaly claims.
5. The output tells the user what is runnable now and what would be required for A-level reproduction.

## A-Level Difference

A-level requires actual paper-level scientific agreement:

- correct data products,
- correct likelihood/estimator,
- correct model family,
- publication-ready diagnostics,
- result direction and numerical scale compatible with the hidden paper.

B-level is allowed to be an honest and precise scope-gap result.

## Strict A-Class Evaluator

The backend now includes an offline hidden-answer evaluator:

- `backend/app/services/research_alpha_evaluator.py`
- public entry point: `evaluate_alpha_class(platform_record, hidden_record)`
- batch summary helper: `summarize_alpha_evaluations(evaluations)`

This evaluator is **not** part of the Chat UI prompt path.  Hidden paper answers
stay outside the assistant.  The evaluator is used only after a blind test has
finished, when the local diagnostic harness compares platform output against
the hidden paper record.

Strict A is deliberately hard to obtain.  A record cannot be A unless all of
the following are true:

1. The hidden answer has been fully read and converted into structured
   expectations.
2. The platform output uses the expected data products.
3. The platform output uses the expected method / likelihood / estimator.
4. The platform output uses the expected model family.
5. The relevant runner or fit is publication-ready for the claim being made.
6. Diagnostics such as ESS / R-hat / acceptance / posterior checks are present
   where applicable.
7. The result direction matches the hidden paper.
8. The numerical scale is compatible with the hidden paper under explicit
   tolerances.
9. Strong claims are supported by evidence graph / fact-check / current-turn
   tool evidence.

If a hidden record still says `pending_full_paper_read`, it can never score A.
At best it can score B for correct routing or a precise missing-capability map.

Suggested hidden-answer fields for future blind-test records:

```json
{
  "full_paper_read_status": "complete",
  "expected_datasets": ["DESI DR1 BAO", "Pantheon+", "Planck compressed"],
  "expected_methods": ["compressed Gaussian likelihood", "robustness matrix"],
  "expected_models": ["flat LCDM", "w0waCDM"],
  "expected_direction_terms": ["H0 tension remains", "SN drives late-time branch"],
  "expected_numbers": [
    {
      "name": "H0",
      "value": 67.4,
      "tolerance_abs": 0.5
    }
  ]
}
```

This schema records **expectations**, not hardcoded platform behavior.  It is
the external answer key for the evaluator, not a source used by the assistant.

## Implementation

Research Mode now emits two plan fields:

- `partial_pass_readiness`
- `capability_gap_matrix`

`partial_pass_readiness` records:

- target: `B_OR_BETTER_PARTIAL_PASS_95`
- `meets_partial_pass`
- `score_floor`
- `coverage_status`
- criteria met / missing
- runnable candidate keys
- number of missing/partial gap components

`capability_gap_matrix` records concrete components such as:

- EB/TB covariance,
- instrument-angle calibration prior,
- dedicated rotation-angle likelihood,
- TT/TE/EE spectra covariance,
- feature-template frequency scan,
- EDE Boltzmann model,
- modified-gravity growth solver.

The deterministic Chat summary surfaces this target check so users see whether a run is a legitimate partial pass or only a C-level failure.

The local dual-model scorer now consumes the strict A evaluator.  Its aggregate
report separates:

- strict A-ready records,
- B-or-better partial-pass records,
- top `why_not_A` reasons,
- severe failure flags,
- an implementation queue inferred from the missing criteria.

## Why This Matters

Earlier blind-test reports mixed several different failures:

- real hallucination,
- missing likelihood,
- missing covariance,
- correct scope gap but no final conclusion,
- low-diagnostic executable run,
- UI/reporting failure.

The 95% partial-pass target is meant to eliminate the messy middle: even when the platform cannot reproduce a paper, it should classify the task correctly and explain the exact missing capability.

## Non-Goals

- Do not hardcode paper conclusions.
- Do not change C-level failures into B by relabeling.
- Do not let abstract metadata support numerical claims.
- Do not treat compressed preliminary results as full external likelihood reproduction.
