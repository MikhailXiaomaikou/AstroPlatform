# DESI `w0wa` A-readiness protocol amendment 001

Effective: 2026-07-13, before any smoke or formal chain was launched.

This public amendment does not change the frozen paper version, dataset stack,
model, parameter targets, target commitment, numerical agreement tolerances,
direction tests, or forbidden claims. It records one unavoidable disclosure and
one source-driven tightening discovered during pre-run adversarial review.

## A. Analyst blinding disclosure

The implementation request itself included all four published target values.
Consequently, this implementation is a known-target preregistered reproduction,
not an analyst-blind or model-blind reproduction. Evidence must record:

```text
TARGET_PREREGISTRATION=FROZEN
COMPUTATION_ANSWER_KEY_SEPARATION=ENFORCED
ANALYST_BLINDING=NOT_ACHIEVED
```

The answer-key file remains excluded from `generate`, `run`, and `analyze`; only
offline `grade` may read it. The local grader cannot waive the failed blinding
condition or retroactively assert blinding. If no-prompt exposure is required
for A-readiness, the result remains `WITHHELD` unless an independent, signed
protocol adjudication explicitly accepts a known-target reproduction without
altering any scientific threshold.

## B. Paper-fidelity ESS overlay

DESI 2024 VI v3 section 2.5 states that the chains used for reported parameter
moments have effective sample sizes of approximately `10^3` or more, associated
with about five-percent precision on the moments, and that results are produced
with GetDist. The preregistered `bulk ESS >= 400` threshold is retained as a
lower bound, but the exact-profile A-readiness gate is tightened before seeing
run output to:

```text
bulk ESS >= max(400, 1000) = 1000
```

This overlay applies to every sampled cosmological and nuisance parameter and
every reported derived parameter. It does not relax the separate rank-normalized
`R-hat < 1.01` or Monte Carlo standard-error requirement. The reported interval
convention must be checked against GetDist on a fixed weighted-chain fixture.

Primary source: DESI 2024 VI v3, section 2.5,
<https://arxiv.org/html/2404.03002v3>.

## Binding rule

Preflight, generation, runner, analysis, grade, and any ResearchAlpha manifest
must carry the SHA-256 of this exact file. Missing or changed amendment bytes
leave the workflow `WITHHELD`.
