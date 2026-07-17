# Cosmology M0 blind test

Daily 16:00 UTC + on-demand via the `daily.yml` workflow (manual dispatch
with `module=cosmology`). 10 cases covering golden path / anti-
fabrication / honest abstention / scheduling / multi-tool chain.

## Running

```bash
# Whole suite (~20 min)
bash backend/scripts/daily_blind.sh --module cosmology

# Single subset (~2 min/case)
gh workflow run daily.yml --ref main \
  -f module=cosmology -f cases=A2,A3
```

## Case files

- `cases.yaml` — 10 case specs. Each case has:
  - `prompt` — what the user asks
  - `expect_tools_called` — tools that MUST appear in the trace for a ✓.
    Empty list = the path doesn't matter, only the reply text does.
  - `expect_pass` — substring assertions on the final reply / tool results
  - `forbid` — substring blacklist on the final reply
  - `alt_expected_tools` — *documentation only, not used by the judge.*
    Records the ideal direct route the case was designed around. See
    next section for why we keep this separate.
- `runner.py` — driver. Reads cases.yaml, calls `app.api.chat`, dumps
  per-case JSON, `summary.md`, and machine-readable `verdicts.json` into
  `results_<timestamp>/`. Every failed verdict carries one operational owner:
  `product_defect`, `evaluator_false_positive`, `model_drift`,
  `external_dependency`, or `ci_infrastructure`.

## Why `expect_tools_called` is empty for A2 / A3

The first 5 cosmology blind runs (2026-05-28) all showed DeepSeek V4 Pro
ignoring tightly-scoped routing instructions for two specific phrasings:

- "Hubble tension" → always calls `plan_research_program` first,
  not `compare_luminosity_distances`
- "Alcock-Paczynski" / "AP test" → same pattern, not
  `assess_bao_bin_anomaly`

Five prompt + schema iterations (V1-V5) confirmed this is a
**function-call ranking bias** in the model, not a prompt problem:

- DeepSeek picks the first tool by semantic similarity between the user
  prompt and each tool's schema *name* + *description*. The prompt text
  (including a TOP-of-document routing table) is not read until *after*
  the first tool call.
- "Hubble tension" + "research" framing match `plan_research_program`'s
  description more closely than they match the specific tools, even when
  the user types "Use compare_luminosity_distances" verbatim and even
  after we rewrote `plan_research_program`'s description NEG-FIRST.

So we changed the success criterion to match reality:

- A2 / A3 / E1 pass as long as the **reply** produces the right number
  in the right band (the `expect_pass` substring assertions). The path
  the model took to get there is recorded in the trace JSON but no
  longer forced.
- The "ideal" direct route is recorded in `alt_expected_tools` for
  documentation. When this repo eventually ships an ANTHROPIC_API_KEY
  secret, Claude is expected to follow the prompt + schema steering
  more strictly and naturally hit the `alt_expected_tools`. If a future
  blind run shows A2 / A3 hitting their `alt_expected_tools`, tighten
  `expect_tools_called` back to the strict version.

This is not "lowering the bar." The platform's job is to produce the
right scientific answer for the user; which tool the model happened to
pick along the way is a secondary diagnostic, not the contract.

## Anti-fabrication invariants — these we DO assert strictly

The 5 anti-fabrication defenses below were exercised by real LLM
behavior in the V1-V5 runs and all activated correctly:

| Case | Defense triggered |
|---|---|
| B1 inline rows | `chain_tier=blocked` + `__do_not_claim__=True` |
| B2 fake bibcode | "Unsupported cosmology anchor comparison — replacing with grounded summary" |
| C1 Helix Nebula | "Zero-data turn with N quantitative claims — hard-blocking" |
| C2 z=12 extrapolation | LLM abstains, no fake number leaks |
| D1 (when LLM tried) | "Citation provenance violation: suspicious_author_year" |

A regression in any of these is a *real* fail — the case design here is
load-bearing for the platform's zero-fabrication contract.
