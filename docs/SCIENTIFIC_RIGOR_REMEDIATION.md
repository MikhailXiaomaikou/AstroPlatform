# Scientific-rigor remediation

This document records the scientific defects found during the July 2026 review,
the implemented safeguards, and the evidence required before a result may be
presented as publication-ready.  A green software test is not, by itself,
evidence that a cosmological result is scientifically established.

## Remediation ledger

| ID | Severity | Defect | Implemented remediation | Regression evidence |
|---|---|---|---|---|
| SR-001 | Critical | Numerical cosmology claims hidden in Markdown/LaTeX tables could bypass the claim gate. | Parse parameter/value table rows, including `H0`, `Omega_m`, `n_s`, `tau`, and `ombh2`; fail closed when no matching tool evidence exists. | Claim-validator and synthetic-fallback tests cover prose, Markdown tables, and LaTeX tables. |
| SR-002 | Critical | A citation could be accepted from a weak bibcode suffix/author-initial match, and unstructured caller metadata could appear authoritative. | Require a structured citation record or exact DOI/arXiv/bibcode identity.  Caller-provided URLs/citations no longer create trusted provenance. | Citation-validation and citation-pool audit tests. |
| SR-003 | Critical | Cached random numerical output and non-inferential Monte Carlo/bootstrap code could be labelled real merely because it executed. | Treat unexplained random numeric generation as suspicious; propagate `SYNTHETIC` and `__do_not_claim__` through normalization.  Explicit MCMC/manual-bootstrap workflows remain allowed but must pass their own diagnostics. | Synthetic-code detector, Python-runner provenance, and result-provenance tests. |
| SR-004 | Critical | Generic supernova `(z, mu, sigma)` rows appeared able to constrain `H0` without an absolute-magnitude calibration or full covariance. | Generic rows are permanently non-publication and cannot support an `H0` claim.  The Pantheon+SH0ES path now uses `m_b_corr`, `IS_CALIBRATOR`, `CEPH_DIST`, the official `(zHD > 0.01) OR IS_CALIBRATOR` selection, and a fitted `M_B`. | Supernova identifiability and Pantheon+ provenance-binding tests. |
| SR-005 | Critical | The vendored Pantheon+ artifact lacked the fields required to reproduce the official calibrated likelihood. | Rebuilt the bundle from DataRelease commit `c447f0fea703fcd0fff57de5000947b5ca81286b`; pinned artifact SHA-256 `bf0daa4ba2c06347db286d35f9f43c6de7c4fb85634e9f3821008911c7728bad`; verify hash, shapes, official selection, calibrators, covariance, and finite likelihood before execution. | 1701 released rows; 1657 selected rows; 77 calibrators; full-covariance likelihood smoke test. |
| SR-006 | High | Caller-supplied compressed Gaussians could inherit publication status, duplicate information could be counted twice, and a sampler stopped by `maxiter` could still look converged. | Publication use requires an exact match to registered parameter order, mean, and full covariance.  Reject duplicate keys/sources/independence groups before sampling.  `maxiter` before the convergence target is non-publication. | Compressed-likelihood guard and nested-sampling tests. |
| SR-007 | High | Hard priors were not guaranteed to bound posterior samples; retained-parameter errors could be derived from conditional precision; Planck `S8` risked being counted as an independent datum. | Sample from the truncated prior support, use the marginal covariance for retained parameters, and report `S8` only as a derived quantity in the analytic Planck path. | Hard-prior, covariance, and Planck compressed-likelihood regressions. |
| SR-008 | High | Convergence checks flattened chain boundaries, accepted too few chains, and could assign finite diagnostics to degenerate chains.  PPC could be synthesized without an observation model. | Preserve chain identity; require at least four independent chains, rank-normalized `R-hat < 1.01`, bulk ESS at least 400, finite within-chain variation, and explicit stochastic observation simulation for PPC.  Coupled emcee walkers are not called independent replicated chains. | Chain-diagnostic, Bayesian-rigor, determinism, and LinMix tests. |
| SR-009 | High | Vendored LinMix reused random streams and could report convergence after reaching only the iteration ceiling. | Give each chain its own deterministic stream; retain per-chain draws; expose iteration count, last `R-hat`, and an explicit convergence boolean; reaching `maxiter` is not convergence. | Kelly (2007) LinMix regressions, including forced non-convergence. |
| SR-010 | High | CMB birefringence compressed inference omitted the calibration-angle nuisance and could leak beyond a hard beta prior. | Model measured rotation as `beta + alpha`, marginalize the Gaussian calibration prior into the effective likelihood, and sample the exact bounded posterior.  Caller-compressed results remain exploratory. | Calibration-width and hard-bound tests. |
| SR-011 | High | Overlapping cosmological datasets/calibrations could be compared as independent, and an external DESI likelihood was misclassified as non-runnable. | Determine runnability from execution mode; detect exact-key, independence-group, source, and declared overlap; suppress n-sigma comparisons for overlapping data. | Published-constraint audit tests. |
| SR-012 | High | A historical preliminary Cobaya chain and its posterior displacement were described as a DESI detection significance. | Relabel the archived run as preliminary/non-converged; call the statistic a posterior Mahalanobis displacement, not a DESI MAP significance; remove the unsupported detection claim. | Documentation and analysis-script assertions. |
| SR-013 | Medium | Data-product readiness could survive content overrides, unverified hashes, wrong row counts, or positional column assumptions. | Publication readiness now requires verified bytes and declared row counts; parse named columns from the actual header; overrides stay non-publication. | Data-product registry and table-loader tests. |
| SR-014 | Medium | Benchmarks, blind tests, and evidence reports could count blocked/skipped work or textual keywords as scientific success. | Separate pass/fail/skip accounting, make execution errors hard failures, remove a fabricated blind-test line, require structured numeric-claim validation, and make the benchmark workflow pull-request-gating. | Benchmark-honesty, blind-runner, evaluator, and citation-audit tests. |
| SR-015 | Medium | Several interfaces silently changed scientific meaning: unknown cosmology presets fell back to Planck, CAMB output exceeded requested `lmax`, and luminosity conversion unnecessarily depended on redshift. | Unknown presets raise; theory spectra return exactly `lmax + 1` multipoles; rest-frequency luminosity conversion is independent of redshift while validating it when supplied. | Preset, spectrum-boundary, and luminosity-unit tests. |
| SR-016 | High | A linear relation could inherit publication readiness from data checks even when the requested Bayesian sampler failed convergence. | Top-level readiness is now the conjunction of data checks, the requested method, sampler convergence/readiness, and relation claimability; failures return explicit `do_not_claim` reasons. | Forced non-converged LinMix regression. |
| SR-017 | High | Tiny inline samples, including one-point summaries and three-point regressions, could be labelled publication-ready without provenance, uncertainty, or effect diagnostics. | Inline-array statistics are preliminary at best, require minimum sample/variation/uncertainty checks even for preliminary use, and always disclose the missing source/selection/model binding. | Small-sample, large-sample, bootstrap, ODR, and censored-summary regressions. |
| SR-018 | High | The hand-entered Gaussian lowE constraint on `tau` could inherit the same status as a selected, hash-verified low-l EE likelihood. | Mark the Gaussian constraint as a compressed stand-in; require the real low-l EE dataset to be both selected and individually hash-verified before it can clear the publication gate. | Stand-in, selected-but-unverified, and verified-lowE runner tests. |
| SR-019 | High | A likelihood chi-square difference evaluated at posterior modes was converted to a Wilks p-value and Gaussian-equivalent sigma even though the optimizer used `ignore_prior: false`. | Retain signed objective and likelihood chi-square differences as auditable descriptive quantities; withhold p/sigma unless attested optimizers prove likelihood-only MLE targets. | Canonical evidence-manifest MAP regression. |

## Publication gate after remediation

A numerical result may be labelled publication-ready only when all applicable
conditions below are true:

1. The exact data bytes and covariance are source-pinned and hash-verified.
2. Dataset independence/overlap has been checked before likelihood combination.
3. The implemented likelihood contains every calibration and nuisance parameter
   needed for identifiability.
4. Priors, parameter order, mean vector, and full covariance match a registered
   specification; caller metadata cannot elevate trust.
5. Sampling reaches its declared stopping target, uses genuinely independent
   chains where chain diagnostics are claimed, and passes the diagnostic floor.
6. Every reported numeric claim is bound to structured tool evidence and exact
   source identity; failed, skipped, exploratory, or blocked work is labelled as
   such and is not counted as a pass.
7. Method-specific claim gates also pass: the requested sampler converged, any
   compressed stand-in is disclosed, and significance calibration matches the
   quantity actually optimized.

## Deliberately unresolved scientific work

The earlier DESI+CMB+SN `w0wa` output is no longer represented as a converged
detection. Establishing a new significance would require a fresh, fully
converged free-`w0wa` run and a matched fixed-LambdaCDM reference, plus either
attested likelihood-only maximum-likelihood fits satisfying the likelihood-ratio
assumptions or a simulation calibration. The existing `ignore_prior: false` MAP
pair is useful for audit but does not supply that calibration. That hours-long
scientific computation was not replaced by a shortcut or by relabelling the old
chain; until it is run and independently checked, the platform must withhold
that detection claim.

## Verification performed

- Platform CI test scope: **2679 passed, 3 skipped, 59 deselected, 0 failed**.
- Focused scientific-remediation set: **497 passed** after correcting the one
  stale Pantheon+ header fixture uncovered by the first run.
- Default cosmology benchmark suite: **22 passed, 3 explicitly skipped/not
  validated, 0 failed**; outcome accounting is exhaustive (25 of 25).
- Slow Pantheon+SH0ES full-covariance benchmark: **passed**, with
  `cov_fidelity=full` and the fitted artifact digest equal to the registry pin.
- Cosmology registry audit: **34/34 clean**, with no executable-pin issue.
- Citation reachability audit: all **87 declared identifiers** (2 bibcodes, 67
  arXiv IDs, and 18 DOIs) are reachable through actual tool-result provenance.
- `ruff` application lint and `git diff --check`: passed.

The three default-suite skips are not passes: one is the intentionally
non-publication extended-DE diagnostic, while two are explicit slow full-SN
opt-ins.  The full Pantheon+ covariance opt-in was additionally executed during
this remediation and passed; the hours-long DESI `w0wa` significance rerun
described above remains deliberately outstanding.
