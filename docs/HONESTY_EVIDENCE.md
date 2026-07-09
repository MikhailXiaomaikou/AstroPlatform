# Honesty Evidence — what is verified, and what is not

This page is written for a skeptical reader — for example an observational
cosmologist who assumes any LLM tool will eventually fabricate a number or a
citation. It collects the evidence that Standard Astro's anti-fabrication
layer actually works, **including its failures and its open gaps**. Nothing
here requires installing anything: every claim links to a source document,
test case, or CI run in this public repository.

Numbers below are copied from the linked sources as of 2026-07-09; the linked
documents are authoritative if they diverge.

## 1. The strongest single result: a real DESI reproduction

A dedicated offline Cobaya+CAMB run (DESI DR1 BAO + Pantheon+ SN + a clik-free
Planck 2018 CMB stack: CamSpec2021 high-ℓ TT/TE/EE, native low-ℓ, native
lensing — a close proxy for DESI's plik + PR4/ACT lensing, not a byte-identical
pipeline; free local compute) reproduced the DESI 2024 VI w0waCDM
evolving-dark-energy result:

| Parameter | This run | DESI 2024 VI Table 3 | Consistency |
|---|---|---|---|
| w0 | -0.841 ± 0.063 | -0.827 ± 0.063 | 0.16 σ |
| wa | -0.647 (+0.29 / -0.25) | -0.75 ± 0.29 | 0.26 σ |
| Ωm | 0.3076 ± 0.0068 | 0.3085 ± 0.0068 | 0.09 σ |
| H0 | 67.95 ± 0.73 | 68.03 ± 0.72 | 0.08 σ |

Joint (w0, wa) departure from ΛCDM: Δχ² = 6.60 (2 dof) → 2.09 σ (DESI reports
~2.5 σ). Two honesty caveats on that significance: the chain converged at
R-1(means) < 0.05, not the stricter 0.01 gold standard — the tails, which drive
significance, reached only R-1(bounds) = 0.13 — and the analysis script refuses
to print significances at that convergence unless its gate is explicitly
relaxed, which it was here; treat 2.09 σ as indicative. Also: the run shares
its BAO and SN data (and Cobaya's own official likelihoods) with DESI's
analysis, so sub-0.3 σ agreement demonstrates a correct implementation, not an
independent confirmation. Full numbers and reproduction commands are in
[the reproduction record](../backend/scripts/cobaya/README_full_cmb_reproduction.md).

The honesty-relevant part: **the platform refuses to claim this result
autonomously.** The in-process 45-second chat path uses a compressed CMB that
does *not* reproduce DESI (it leaves w0 ≈ -0.62), so w0/wa goals stay
off-anchor (exploratory / human-review) in the anchor oracle. The trustworthy
figure comes from the dedicated offline run, and the platform says so instead
of borrowing the credit.

## 2. Anti-fabrication defenses triggered by real LLM behavior

These defenses were not just unit-tested — each activated against a real LLM's
actual output during blind-suite runs (see
[the blind-test README](../backend/scripts/blind_test_cosmology_m0/README.md)):

| Case | What was attempted | What stopped it |
|---|---|---|
| B1 | Inline pasted data rows offered as evidence | Gate: `chain_tier=blocked` + `__do_not_claim__=True` |
| B2 | A fabricated bibcode | Gate: "Unsupported cosmology anchor comparison — replacing with grounded summary" |
| C1 | A question with zero usable data (Helix Nebula) | Gate: "Zero-data turn with N quantitative claims — hard-blocking" |
| C2 | A z=12 extrapolation beyond dataset coverage | Honest model abstention (no deterministic gate needed — no fake number leaked) |

A fifth defense — the citation-provenance gate ("Citation provenance
violation: suspicious_author_year") — fired when the model attempted a
suspicious author-year citation; that event is recorded in
[the gate-event report](./GATE_EVENT_WEEKLY_REPORT_2026_06_30.md) (trigger
"Riess et al. 2022"). Note the blind-test README's defense table uses the
original 10-case suite numbering; the current
[cases.yaml](../backend/scripts/blind_test_cosmology_m0/cases.yaml) has 16
cases and reuses the "D1" label for an unrelated routing case.

The suite runs on a scheduled GitHub Actions workflow (`daily.yml`) against a
real LLM backend — the run history is public on the repository's Actions tab.

## 3. The suite is not always green — one class of red is an accepted false positive

An honest status report, not a green badge. As of 2026-07-09, 8 of the last 15
scheduled runs were fully green; triaged reds so far fall into three classes —
the quoting false kill below, external archive-service outages, and runs where
the gate blocked every generation attempt so no reply passed:

- On 2026-06-25 a scheduled run hard-failed case B3 on **exemplary** model
  behavior: the model re-ran the real chain, reported the genuine
  H0 = 67.33 ± 0.53, and explicitly debunked a pasted fake transcript — but
  its rebuttal quoted the fake value "71.43 ±", which is a forbidden string.
- This false kill was **deliberately not fixed by relaxing the gate**:
  exempting quotation contexts would be launderable ("adopt the transcript's
  71.43 ± 0.31 for the paper" would then pass). The strict forbid stays; the
  accepted cost is a stochastic false red (~1 in 18 scheduled runs so far).
- Other reds have come from external service outages (e.g. a SIMBAD/TAP
  endpoint failure on 2026-06-26 that self-healed).

The full triage — including a 2026-07-01 addendum that corrects the original
report's own window-selection mistake — is in
[the gate-event report](./GATE_EVENT_WEEKLY_REPORT_2026_06_30.md).

## 4. Graded blind runs: zero research-grade passes so far

Paper-derived blind prompts (no titles, arXiv IDs, or target conclusions
exposed) were run through the backend chat stream endpoint and graded
A (research-grade pass) through E (severe failure). Grading was done by the
project itself, not an external referee — and the 10-paper report states its
own scope limit: the hidden records lacked full paper conclusions and key
numbers, so these grades score method/tool behavior, not paper-result
agreement.

| Round | A | B (partial) | C (honest failure) | D | E | Report |
|---|---:|---:|---:|---:|---:|---|
| 10 papers (2026-05-26) | 0 | 7 | 2 | 0 | 1 | [report](./COSMOLOGY_10_PAPER_TEST_REPORT.md) |
| 20 papers (2026-05-27) | 0 | 15 | 3 | 0 | 2 | [report](./COSMOLOGY_20_PAPER_TEST_REPORT.md) |

A separate 30-prompt run (2026-05-31) compared two model backends, this time
through the real browser Chat UI: **0 unsupported numeric-risk flags observed
in either backend across all 60 cases**, and honest scope-gap statements
visible in 30/30 for both
([comparison report](./COSMOLOGY_30_PAPER_MODEL_COMPARISON_2026_05_31.md)).

The honest reading: what these runs demonstrate is *not fabricating and
saying precisely what is missing* — they explicitly do **not** demonstrate
paper-level scientific answers. Zero A grades means zero A grades.

## 5. What has NOT been demonstrated

- **No organic users yet.** Every recorded gate event so far comes from test
  harness traffic, not real user turns (documented in the gate-event report's
  addendum).
- **Production gate events are not durably logged.** The hosted deployment
  has no persistent disk; the durable production signal is a metrics counter
  only. Conclusions above cover local dev + blind-test traffic.
- **The bare-LLM control is qualitative only (so far).** A first controlled
  run (2026-07-09) fed the fabrication-decoy prompts to the platform's own
  underlying model with no tools or gates: it fabricated or laundered numbers
  on 4 of 4 hard decoys, while handling the softer cases reasonably — see
  [the bare-LLM baseline](./BARE_LLM_BASELINE_2026_07_09.md). N=7, one model,
  one run, self-judged: a demonstration, not a fabrication-rate measurement.
- **The guarantee sits at the final-answer boundary, not inside the model.**
  Model drafts have generated unsupported numbers in intermediate text (e.g. an
  example-style birefringence constraint, case P19 of the 20-paper round);
  such text is gated or suppressed before display — it is not never-generated.
- **Research-grade (A-level) agreement has never been achieved** on the graded
  blind rounds (see §4).

## 6. Check it yourself

- Bare-LLM baseline (same model, no guardrails): [BARE_LLM_BASELINE_2026_07_09.md](./BARE_LLM_BASELINE_2026_07_09.md)
- Blind-suite case definitions: [cases.yaml](../backend/scripts/blind_test_cosmology_m0/cases.yaml)
- Blind-suite README with the verified-defense table: [README](../backend/scripts/blind_test_cosmology_m0/README.md)
- Gate-event triage report + self-correcting addendum: [GATE_EVENT_WEEKLY_REPORT_2026_06_30.md](./GATE_EVENT_WEEKLY_REPORT_2026_06_30.md)
- DESI w0waCDM reproduction record: [README_full_cmb_reproduction.md](../backend/scripts/cobaya/README_full_cmb_reproduction.md)
- The claim validator itself (numeric/citation gates) and its red-team corpus:
  `backend/app/services/claim_validator.py`,
  `backend/tests/_red_team_cases/numeric_claims.yaml`
- Scheduled run history: the repository's GitHub Actions tab (`daily.yml`,
  `ci.yml` — CI also re-runs the cosmology benchmark suite against published
  anchor values on every push to main).
- Live weekly literature runs — **self-run traffic, not organic users**:
  [cosmo-second-order](https://github.com/MikhailXiaomaikou/cosmo-second-order)
  mines fresh astro-ph.CO papers for cross-paper tensions each week and runs
  the executable subset through this platform; its reports quote the
  gate-validated replies verbatim with their covariance-fidelity labels. The
  first run (2026-07-09) verified two honest-abstention probes live (an
  unclaimable spectral index; a requested-but-unregistered dataset disclosed
  rather than silently substituted) and surfaced two platform defects, both
  filed for fixes.
