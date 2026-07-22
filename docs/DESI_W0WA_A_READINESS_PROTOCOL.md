# DESI 2024 VI `w0wa` A-readiness protocol

Status: preregistered; exact-likelihood execution is fail-closed.

This protocol governs one narrow claim: reproduction of the four marginalized
parameter intervals for the DESI DR1 BAO + CMB + PantheonPlus, flat
`w0waCDM` row in DESI 2024 VI. It does not test, quantify, or support a claim
that LambdaCDM is rejected or that dynamical dark energy has been discovered.

## Frozen target and answer-key separation

The repository's full answer-key record is stored only in the gitignored local
record `.local/w0wa-strict-a-readiness/hidden_answer.json`. The `generate`,
`run`, and `analyze` stages do not read that file or inject its contents into a
configuration, proposal center, sampler input, or generated chain. Offline
grading reads it only after the result manifest has been closed and signed.

Protocol-deviation disclosure: the implementation request itself stated the
four published target values, so the implementing model/analyst was not blind
to them. This run must therefore use the following exact labels and must never
be described as an analyst-blind or model-blind reproduction:

```text
TARGET_PREREGISTRATION=FROZEN
COMPUTATION_ANSWER_KEY_SEPARATION=ENFORCED
ANALYST_BLINDING=NOT_ACHIEVED
```

The enforceable claim is narrower: `generate`, `run`, and `analyze` do not read
the answer-key file, and the frozen commitment prevents target or tolerance
changes after seeing run output. Because the original no-prompt-exposure
condition was not achieved, it cannot be self-waived by the local grader. The
frozen protocol-adjudication authority registry for this known-target run is
empty, so its status remains `WITHHELD`; environment-selected keys cannot
authorize a waiver. Any future authority requires an immutable amendment and a
fresh formal run. An adjudication could not retroactively make this
implementation blind in any case.

The target record was frozen on 2026-07-13 before the exact-likelihood formal
run. Its commitment is the SHA-256 of UTF-8 JSON serialized with sorted keys
and separators `(',', ':')`:

```text
sha256:3ff17dead8bc529f19262d6db745f13cb6a284af161c6ce9b44f1fe0925a5029
```

The pre-run disclosure and stricter paper-fidelity ESS overlay were frozen in
[`DESI_W0WA_A_READINESS_AMENDMENT_001.md`](./DESI_W0WA_A_READINESS_AMENDMENT_001.md).
The cumulative revision-2 execution contract is frozen in
[`DESI_W0WA_A_READINESS_AMENDMENT_002.md`](./DESI_W0WA_A_READINESS_AMENDMENT_002.md),
whose exact file SHA-256 is
`fc8fb56ae5009bc80723ee347ade47cb1c0e1cc7fffc75f3ce80015833d9b7af`.
Every new workflow receipt must bind the target commitment and amendment 002.
Historical revision-1 receipts retain their original amendment-001 binding and
are verified with their originating release; they are never rewritten.

The committed hash binds the paper version, dataset combination, model,
allowed and forbidden claims, four central values and asymmetric 68% interval
statistics, direction checks, numerical tolerances, convergence thresholds,
and six model-adequacy requirements. Changing any of those fields creates a
new target and invalidates the preregistration.

## Exact data and likelihood profile

The only profile eligible for this protocol uses:

- DESI DR1 official Gaussian BAO likelihood, all released tracer bins;
- Pantheon+ statistical and systematic covariance with the likelihood's
  source-column `zHD > 0.01` selection (mapped internally by Cobaya to its
  `zcmb` array);
- Planck PR3 Commander low-multipole temperature, `simall` low-multipole
  polarization, and `plik` high-multipole TT/TE/EE likelihoods;
- ACT DR6 plus Planck PR4 CMB-lensing likelihood with
  `variant: actplanck_baseline` and `lens_only: false`;
- CAMB parametrized post-Friedmann dark-energy perturbations, one massive
  neutrino of 0.06 eV, and the DESI 2024 VI Table 2 priors including
  `w0 + wa < 0`;
- the ACT-recommended CAMB accuracy floor (`lmax=4000`, `lens_margin=1250`,
  `lens_potential_accuracy=4`, unit accuracy boosts, and `mead2016`).

The execution environment pins Cobaya 3.6.2, CAMB 1.6.6,
`clipy-like` 0.15, and `act_dr6_lenslike` 1.2.1. The preflight inventory must
record installed distributions, wheels or source artifacts, configuration,
likelihood code, and every consumed data file with SHA-256. Reference
likelihood values must pass before any smoke or formal chain can be certified.
It also rejects non-empty `PYTHONPATH`, unowned `.pth` files and Python startup
customization hooks. Formal Cobaya children run under `python -I`; the isolated
import-search policy is attested with the environment.

The historical CamSpec/native-lensing chains remain exploratory proxy
artifacts. They cannot be resumed, relabeled, or included in an A-readiness
manifest.

Primary specifications:

- DESI 2024 VI v3: <https://arxiv.org/abs/2404.03002v3>
- Cobaya Planck likelihood documentation:
  <https://github.com/CobayaSampler/cobaya/blob/master/docs/likelihood_planck.rst>
- ACT DR6 lensing likelihood:
  <https://github.com/ACTCollaboration/act_dr6_lenslike>

## Canonical offline state machine

The canonical workflow is strictly ordered:

```text
preflight -> generate -> run -> analyze -> grade
```

`preflight` verifies dependency versions, data hashes, likelihood reference
points, configuration fingerprints, target commitment, local resource policy,
the globally clean Git HEAD and tree, ancestry from frozen base commit
`f9efb4ac6f7850d4c7739ac038d08beb37ea785e`, and a never-reused output prefix.
Branch names are not evidence; clean branch descendants and detached HEADs are
both accepted. `generate` derives run configurations from the exact profile.
`run` creates fresh four-chain attestations. `analyze`
independently recomputes burn-in, weights, rank-normalized R-hat, bulk ESS,
Monte Carlo standard error, and intervals. `grade` verifies the closed result
against the hidden key and signs a run-bound Research Alpha manifest.

Any missing or changed datum, reference value, config, chain, seed, diagnostic,
adequacy artifact, result statistic, or claim-support path leaves the run in
`WITHHELD`. A smoke run is permanently marked non-citable and cannot be
promoted by later relabeling.

## Formal sampling and analysis gates

The main run uses four MPI processes and three threads per process on local
compute. Each chain has a fresh identity and seed. Every sampled cosmological
and nuisance parameter must satisfy all of the following in the independent
postprocessor:

- rank-normalized R-hat strictly below 1.01;
- bulk effective sample size at least 1000 (the preregistered 400 floor is
  retained and strengthened by amendment 001 to match the paper's reported
  convergence scale);
- Monte Carlo standard error below 0.05 of the corresponding paper standard
  deviation for the four reported target parameters; for sampled cosmological
  or nuisance parameters not tabulated in the target row, below 0.05 of that
  parameter's posterior standard deviation from the same closed run;
- no duplicate or degenerate chains, non-finite draws, premature termination,
  or iteration-limit false convergence.

The result is then repeated in an isolated environment with different seeds
and an independent postprocessing implementation.

## Required model-adequacy matrix

All six checks must be executed and hash-bound to the same run identity:

1. prior predictive checks;
2. posterior predictive checks;
3. widened-`w0`/`wa` prior sensitivity;
4. systematics variants covering PR3 `plik` versus PR4 CamSpec, lensing
   combinations, and a Pantheon+ covariance variant;
5. recovery of three preregistered fiducial injections;
6. isolated-environment independent reproduction.

For injection recovery, "inside the joint 95% region" is preregistered as the
two-parameter (`w0`, `wa`) Mahalanobis ellipse obtained from the recovered
posterior covariance with the chi-square two-degree-of-freedom 95% threshold.
It is an explicitly elliptical coverage diagnostic and must not be described as
an arbitrary non-Gaussian highest-posterior-density region.

The numerical thresholds are part of the hidden-answer commitment. They may
not be changed after looking at results. A failed threshold is a scientific
finding about reproducibility and keeps the state `WITHHELD`.

## Status semantics

- `WITHHELD`: at least one exact-input, convergence, numerical, adequacy,
  provenance, or support-path requirement failed or has not run.
- `A_READY_PENDING_EXTERNAL_REVIEW`: every automated and independent local
  gate passed and the complete signed evidence package exists.
- `A`: reserved for protocols that preregister a non-empty external-review
  authority registry; it is unreachable for this exact profile.

The exact-profile implementation is capped at
`A_READY_PENDING_EXTERNAL_REVIEW`. Its frozen external-review authority
registry is empty, so environment-selected IDs or public keys cannot promote a
manifest and `strict_A_count` is always zero. `A_ready_count` includes the
pending state, while strict `A` remains unavailable. Software tests and signed
manifests are necessary audit controls, not substitutes for peer review.

## Evidence package

The local package must retain the preregistration commitment, exact data and
environment inventories, generated configs, raw chains, runner logs,
independent diagnostics and intervals, all six adequacy artifacts, the
isolated reproduction report, claim-support paths, and final HMAC-signed
Research Alpha manifest. Exact signing requires an explicit durable
`EVIDENCE_SIGNING_KEY` with the frozen ID
`w0wa-strict-20260713-local-v1` and frozen key fingerprint; evidence records
only availability, ID and fingerprint, never the secret. The local execution
boundary trusts same-UID processes, root, and the machine operator. Receipts
detect bound-artifact drift or public-artifact tampering only without local
host or key control; they are neither hostile-host isolation nor external
review. Resolved-path execution is likewise a drift check, not isolation from
those trusted local principals. Ordinary CI verifies only small hash-fixed fixtures
and zero silent skips; hours-long chains are never fabricated or run in CI.
