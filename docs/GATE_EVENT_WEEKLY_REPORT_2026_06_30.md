# Gate Event Weekly Report — 2026-06-30

## Summary

This is the first lightweight gate-event triage report for the observational
cosmology guardrail layer.

Inputs inspected:

- Local gate-event sink: `data/gate_events.jsonl`
- Recent GitHub daily blind workflow status: latest 3 scheduled runs
- Triage command: `backend/scripts/triage_gate_events.py`

Result:

- Local sink contains 4 historical gate events from 2026-06-11 to 2026-06-12.
- The latest 3 GitHub `daily.yml` scheduled runs all completed successfully.
- No new high-volume false-positive pattern is visible in the local sink.
- The observed events match known guardrail themes: inline-data blocking,
  cosmology-anchor downgrade, old-context numeric rejection, and self-supplied
  evidence rejection.

This report does not prove the gate layer is complete. It establishes the first
triage baseline and the event categories to keep watching.

## Recent Daily Runs

| Workflow | Status | Date | Duration |
|---|---|---:|---:|
| Daily | success | 2026-06-29 | 15m21s |
| Daily | success | 2026-06-28 | 15m19s |
| Daily | success | 2026-06-27 | 16m58s |

## Local Event Counts

From `data/gate_events.jsonl`:

| Gate / action / reason | Count | Interpretation |
|---|---:|---|
| `citation_methodology / annotated_blocked / -` | 1 | Inline or audit-only data was correctly blocked from becoming a citeable result. |
| `cosmology_anchor / downgraded_summary / -` | 1 | A draft was downgraded to a tool-grounded summary to avoid unsupported anchor language. |
| `numeric_claims / regenerated_clean / -` | 1 | A stale or unsupported numeric claim was replaced by a current-turn value. |
| `zero_data / regenerated / qualitative_rewrite` | 1 | A report/export turn had no quantitative tool result and was rewritten qualitatively. |

Top trigger phrases:

| Trigger | Count | Notes |
|---|---:|---|
| `2022ApJ...934L...7R` | 1 | Citation-methodology event. |
| `Riess et al. 2022` | 1 | Suspicious author-year / citation-methodology event. |
| `H0 = 71.43` | 1 | Old-context or caller-supplied number rejected by current-turn evidence gate. |

## Event Review

### 1. Citation methodology block

The event involved inline distance-modulus rows and a draft that described a
completed fit but correctly kept the chain blocked because the input rows were
not verifiable as a published table or registered dataset.

Assessment: expected behavior. No false-positive action needed.

### 2. Cosmology anchor downgrade

The draft contained a valid compressed-likelihood result, but the gate rewrote
the final answer into a more explicit tool-grounded summary. The final summary
kept the current-turn posterior values and marked the scope as compressed
preliminary.

Assessment: acceptable, but this category should remain monitored for clean-run
false positives. Specificity tests are the right backstop.

### 3. Numeric claim regeneration

The draft attempted to reuse `H0 = 71.43` from an earlier session while the
current turn reran the registered DESI DR1 BAO + Planck compressed chain and
returned `H0 = 67.33 ± 0.53`. The final answer used the current-turn value and
explicitly rejected the earlier-session number as not tool-determined.

Assessment: expected behavior. This is exactly the old-context laundering
defense working.

### 4. Zero-data qualitative rewrite

The export/report path received caller-supplied parameter values without a
server-side chain run in the current turn. The final answer refused to report a
quantitative result and asked for a real likelihood-chain run.

Assessment: expected behavior. This matches the self-supplied evidence defense.

## Current Risk Assessment

No immediate P0 gate regression is visible from the local sink.

Main residual risks to keep testing:

- false positives on clean successful likelihood-chain turns;
- stale old-context numbers reappearing through report/export flows;
- future source strings, hashes, warnings, or citation metadata leaking numeric
  tokens into the claim universe;
- final-answer rewrites that are scientifically safe but visually confusing in
  the Chat UI.

## Recommended Next Checks

1. Keep `daily.yml` green and continue watching the scheduled blind suite.
2. Run `backend/scripts/triage_gate_events.py` after any guardrail or prompt
   change.
3. Add multi-turn blind-test coverage for repeated old-context laundering:
   first turn blocks a fake number; second turn asks the assistant to reuse it.
4. Keep clean-turn specificity cases in the daily suite so guardrails cannot
   become one-way blockers.

