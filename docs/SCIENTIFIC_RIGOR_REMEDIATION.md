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
| SR-006 | High | Caller-supplied Gaussian records could inherit publication status, duplicate information could be counted twice, and a sampler stopped by `maxiter` could still look converged. | Exact registry matching establishes provenance only; controlled Gaussian nested sampling is always diagnostic without a full likelihood/model-adequacy attestation. Posterior-summary/proposal roles are rejected before sampling, duplicate sources are rejected, and `maxiter` before the target is non-publication. | Statistical-role, duplicate-source, and nested-sampling tests. |
| SR-007 | High | Hard priors were not guaranteed to bound posterior samples; retained-parameter errors could be derived from conditional precision; Planck posterior `sigma8/S8` rows risked being counted as independent likelihood data. | Screen prior dominance, use marginal covariance for retained parameters, and execute only the independent CHW2019 distance-prior approximation. Planck parameter-posterior rows are proposal/context only and no `sigma8/S8` constraint is emitted by the distance-prior path. | Prior-dominance, covariance, and Planck distance-prior regressions. |
| SR-008 | High | Convergence checks flattened chain boundaries, accepted too few chains, and could assign finite diagnostics to degenerate chains.  PPC could be synthesized without an observation model. | Preserve chain identity; require at least four independent chains, rank-normalized `R-hat < 1.01`, bulk ESS at least 400, finite within-chain variation, and explicit stochastic observation simulation for PPC.  Coupled emcee walkers are not called independent replicated chains. | Chain-diagnostic, Bayesian-rigor, determinism, and LinMix tests. |
| SR-009 | High | Vendored LinMix reused random streams and could report convergence after reaching only the iteration ceiling. | Give each chain its own deterministic stream; retain per-chain draws; expose iteration count, last `R-hat`, and an explicit convergence boolean; reaching `maxiter` is not convergence. | Kelly (2007) LinMix regressions, including forced non-convergence. |
| SR-010 | High | CMB birefringence compressed inference omitted the calibration-angle nuisance and could leak beyond a hard beta prior. | Model measured rotation as `beta + alpha`, marginalize the Gaussian calibration prior into the effective likelihood, and sample the exact bounded posterior.  Caller-compressed results remain exploratory. | Calibration-width and hard-bound tests. |
| SR-011 | High | Overlapping or independence-unknown cosmological datasets/calibrations could be compared with independent-error quadrature, and requested datasets could be reported as run without appearing in `datasets_used`. | Derive execution truth from runner output, detect exact-key/group/declared overlap, and withhold n-sigma unless registry metadata explicitly verifies independence. | Published-constraint audit and context-only dataset tests. |
| SR-012 | High | A historical preliminary Cobaya chain and its posterior displacement were described as a DESI detection significance. | Relabel the archived run as preliminary/non-converged; call the statistic a posterior Mahalanobis displacement, not a DESI MAP significance; remove the unsupported detection claim. | Documentation and analysis-script assertions. |
| SR-013 | Medium | Data-product readiness could survive content overrides, unverified hashes, wrong row counts, or positional column assumptions. | Publication readiness now requires verified bytes and declared row counts; parse named columns from the actual header; overrides stay non-publication. | Data-product registry and table-loader tests. |
| SR-014 | Medium | Benchmarks, blind tests, and evidence reports could count blocked/skipped work or textual keywords as scientific success. | Separate pass/fail/skip accounting, make execution errors hard failures, remove a fabricated blind-test line, require structured numeric-claim validation, and make the benchmark workflow pull-request-gating. | Benchmark-honesty, blind-runner, evaluator, and citation-audit tests. |
| SR-015 | Medium | Several interfaces silently changed scientific meaning: unknown cosmology presets fell back to Planck, CAMB output exceeded requested `lmax`, and luminosity conversion unnecessarily depended on redshift. | Unknown presets raise; theory spectra return exactly `lmax + 1` multipoles; rest-frequency luminosity conversion is independent of redshift while validating it when supplied. | Preset, spectrum-boundary, and luminosity-unit tests. |
| SR-016 | High | A linear relation could inherit publication readiness from data checks even when the requested Bayesian sampler failed convergence. | Top-level readiness is now the conjunction of data checks, the requested method, sampler convergence/readiness, and relation claimability; failures return explicit `do_not_claim` reasons. | Forced non-converged LinMix regression. |
| SR-017 | High | Tiny inline samples, including one-point summaries and three-point regressions, could be labelled publication-ready without provenance, uncertainty, or effect diagnostics. | Inline-array statistics are preliminary at best, require minimum sample/variation/uncertainty checks even for preliminary use, and always disclose the missing source/selection/model binding. | Small-sample, large-sample, bootstrap, ODR, and censored-summary regressions. |
| SR-018 | High | The hand-entered Gaussian lowE constraint on `tau` could inherit the same status as a selected, hash-verified low-l EE likelihood. | Mark the Gaussian constraint as a compressed stand-in; require the real low-l EE dataset to be both selected and individually hash-verified before it can clear the publication gate. | Stand-in, selected-but-unverified, and verified-lowE runner tests. |
| SR-019 | High | A likelihood chi-square difference evaluated at posterior modes was converted to a Wilks p-value and Gaussian-equivalent sigma even though the optimizer used `ignore_prior: false`. | Retain signed objective and likelihood chi-square differences as auditable descriptive quantities; withhold p/sigma unless attested optimizers prove likelihood-only MLE targets. | Canonical evidence-manifest MAP regression. |
| SR-020 | Critical | A paper could pair an unrelated signed catalog lookup with an unsupported qualitative cosmology conclusion, including claims that dark energy evolves or that the cosmological constant is ruled out. | Any conclusion recognized by the current strong-cosmology claim catalogue now needs a same-sentence evidence reference to one versioned attestation whose claim kind, baseline/alternative model pair, data and likelihood fingerprints, comparison calibration, and manifest hash match in the same signed result branch. A session-level `significance_ready` boolean cannot unlock unrelated prose. The catalogue is not claimed to cover every possible paraphrase. | Synonym/LaTeX attacks, cross-model/data/branch attacks, and exact calibrated-attestation positive regressions. |
| SR-021 | Critical | Non-English claim text bypassed the English regex catalogue, and a client-authored session transcript could inject queries/code into the public reproducibility appendix. | Claim-bearing segments of four or more natural-language words are deterministically language-detected; automatic attestation requires English probability at least 0.95, while short formula-only content is neutral and editable content needs a content-hash-bound server/human attestation. Evidence uses an independent versioned signing key with a retained verification keyring. The attestation records this gate outcome for exact content; it does not validate the scientific meaning or correctness of the content. | Spanish, French, German, Portuguese, Italian, CJK, mixed-language, English-formula, edit-invalidates-signature, and signing-key rotation regressions. |
| SR-022 | Critical | A number that was legitimate for exploratory chat could enter the paper validator's numeric universe even when its tool explicitly said `publication_ready=false`; an unrelated value with the same number could also support the wrong physical quantity. | The paper boundary excludes partial/exploratory/non-publication results and enables typed quantity matching. Cosmology parameters, significance, p-values, correlations, parallax, distance, mass, age, period, redshift, transit ratios/depths, and line widths cannot fall back to an unrelated flat numeric pool. | Preliminary/free-form manuscript attacks plus H0-from-parallax, Omega-m-from-redshift, sigma-from-S/N, p-from-period/redshift, and correlation cross-quantity regressions. |
| SR-023 | High | HST/JWST proposal planning substituted Cerro Paranal for orbital visibility and fed space observatories into a ground-sky/seeing CCD exposure model. | Ground visibility is now `not_applicable` for space telescopes, the generic ETC rejects them, and proposal output directs users to STScI APT and the official instrument ETC without computing a ground proxy. | Space-observatory visibility and ETC guard regressions. |
| SR-024 | Critical | The so-called reproducibility appendix silently cut ADQL after 200 characters and Python after 600, and listed only a pipeline action name rather than its DAG. | The appendix now derives a deterministic owner-bound manifest only from HMAC-verified records, preserves the complete input payload stored in each included server-attested evidence record (including queries, code, and DAGs), records result/data/config hashes, seeds, execution versions and record signatures, and publishes an explicit manifest SHA-256. | Long-query/code/DAG preservation, manifest tamper/hash, and forged-client-action exclusion regressions. |
| SR-025 | Critical | Several archive connectors could return a metadata-only synthetic FITS header, and `LoadData` could accept the zero-row placeholder as though it were a scientific data product. | Metadata-only headers are no longer treated as scientific products; real archive products must be downloaded or uploaded, and empty FITS payloads fail the load boundary. | Connector metadata-only and empty-LoadData guard regressions. |
| SR-026 | Critical | `classify_transient` trained a random forest entirely on generated feature distributions, returned a specific class/confidence for empty or undersampled inputs, and could then be normalized as `real_archive/completed`. | Empty, malformed, incomplete-feature, and fewer-than-10-point inputs now fail closed. Every successful output is forcibly marked `SYNTHETIC`, `__do_not_claim__`, non-preliminary, and non-publication at both the classifier and result-normalization boundaries; the candidate label and score are explicitly uncalibrated software-demo values. | Transient input attacks, normal sampled-light-curve regression, and contradictory-provenance laundering regression. |
| SR-027 | Critical | `analyze_cross_wavelength` reported “No discrepancies” when every check was skipped, discarded catalog photometric errors already returned by 2MASS/AllWISE, and exposed chi-square/significance after silently assuming 10% flux errors. | Zero evaluated checks now return `EMPTY`, non-publication, and `__do_not_claim__`; dossiers preserve a band-aligned catalog-error tree; magnitude errors are propagated to flux and used in a weighted fit. Incomplete-error SED/IR screens remain visibly preliminary and expose no claimable significance or chi-square. | All-skipped abstention, missing-error quarantine, catalog-error propagation, and real-error weighted-fit regressions. |
| SR-028 | Critical | Model-adequacy and Strict-A manifests trusted caller-written `signature_verified` booleans, arbitrary hashes, empty support mappings, and partial numerical agreement. | Recompute canonical SHA-256, verify an independent evidence-key HMAC (including retired-key support), bind model adequacy to the exact model/datasets/seed/result fingerprint, require structured diagnostic/support records, and reject partial numerical agreement for A. | Manifest tamper, wrong-run subject, empty-support, and partially-wrong hidden-number regressions. |
| SR-029 | Critical | Blocked chain/audit numbers and child cells in diagnostic matrix aggregates could re-enter the claim universe or timeout summary. | Use literal boolean taint sentinels, redact blocked posterior fields server-side, keep research/robustness aggregates permanently diagnostic, and suppress child-cell numbers whenever the parent is tainted.  AP geometry and published-constraint audits no longer self-certify publication. | Chain/audit laundering, blocked analytic posterior, AP wrapper, and tainted-timeout-summary regressions. |
| SR-030 | High | Reproducibility receipts could be overwritten by stale tool-returned fields; `seed=0` could change inside a fitter; emcee/dynesty/time-series pipeline nodes did not consistently control their sampler or bootstrap RNG. | Re-stamp authoritative run/version/query fields and retain old receipts only as labelled upstream context; inject seeds before execution, derive stable per-node pipeline seeds, seed emcee and dynesty/resampling explicitly, preserve zero, and reject UltraNest where an isolated RNG is unavailable. | Forged-envelope, line-fit zero-seed, fit-RV, fit-isochrone, Bayesian pipeline, and Lomb-Scargle replay regressions. |
| SR-031 | Critical | A cosmology central value could be certified by a coincidentally equal interval edge, uncertainty, proposal anchor, or unrelated parameter; dimensionless and Unicode-unit `value ± error` forms could also leave the error unchecked. | Maintain separate per-parameter universes for central estimates, lower/upper interval endpoints, and uncertainties; exclude context/proposal/tainted helpers; bind interval cues to the correct parameter and clause; and extract an adjacent uncertainty independently of physical-unit spelling. | Cross-statistic, cross-parameter, nested-taint, decimal-boundary, dimensionless-S8, Unicode-H0, and one-sigma-central regressions. |
| SR-032 | Critical | Backtracking sentence/conclusion regexes could be driven superlinearly by untrusted prose, while naive period splitting let decimals such as `z=0.5` separate a dark-energy subject from its conclusion. | Replace both paths with linear sentence/token/span scans, incremental line accounting, decimal-aware boundaries, and the original 120-character semantic window. | Repeated-sentence and long-input scaling, decimal-bearing conclusion, long-intervening-clause, and TeX/Unicode `w_a` regressions; PR-head CodeQL open alerts: zero. |

## Platform publication-export gate after remediation

Within this platform, a numerical result is eligible for publication export
only when all applicable evidence-binding conditions below are true. Passing
these controls is necessary but not sufficient for scientific validity, which
still requires domain review, model-adequacy and systematics assessment, and
independent reproduction.

1. The exact data bytes and covariance are source-pinned and hash-verified.
2. Dataset independence/overlap has been checked before likelihood combination.
3. The implemented likelihood contains every calibration and nuisance parameter
   needed for identifiability.
4. Priors, parameter order, mean vector, and full covariance match a registered
   specification with an executable statistical role; exact numeric matching
   cannot turn a published posterior summary into a likelihood.
5. Sampling reaches its declared stopping target, uses genuinely independent
   chains where chain diagnostics are claimed, and passes the diagnostic floor.
6. Every reported numeric claim is bound to structured tool evidence and exact
   source identity; failed, skipped, exploratory, or blocked work is labelled as
   such and is not counted as a pass.
7. Method-specific claim gates also pass: the requested sampler converged, any
   compressed stand-in is disclosed, and significance calibration matches the
   quantity actually optimized.
8. Qualitative headline conclusions are relevant to the signed evidence scope;
   model-selection language requires its own calibrated significance evidence,
   and the public reproducibility appendix contains only server-attested runs.
9. A chat-discussable preliminary value is not manuscript evidence: the paper
   numeric universe excludes every explicitly exploratory or non-publication
   result, requires the same typed physical quantity, and never treats
   successful process execution as scientific validity.
10. Claim-bearing prose passes the English-language gate for the exact draft
    content, and the exported reproducibility manifest contains the full stored
    input payload of every included signed evidence record plus hashes rather
    than silently truncated display text.
11. A model trained only on generated distributions remains synthetic even
    when it receives real observations; its label and confidence cannot become
    a scientific conclusion until an independently validated trained artefact
    and calibration evidence replace the demonstration model.
12. A missing-data cross-wavelength screen is not evidence of consistency, and
    an assumed uncertainty cannot support a quoted significance or goodness of
    fit; catalog-reported per-band errors must survive ingestion and propagation.
13. Model-adequacy and blind-evaluation manifests are server-signed, content-
    hashed, and bound to the exact run subject. A caller-written trust boolean,
    arbitrary evidence ID, or a valid manifest from another run cannot unlock
    publication or Strict-A status.

The language attestation records a detector or reviewer gate outcome; it does
not establish that the scientific content is correct. The manifest does not by
itself guarantee external-service availability, long-term data availability,
or bit-for-bit environment reconstruction.

## Deliberately unresolved scientific work and validation boundaries

The earlier DESI+CMB+SN `w0wa` output is no longer represented as a converged
detection. Establishing a new significance would require a fresh, fully
converged free-`w0wa` run and a matched fixed-LambdaCDM reference, plus either
attested likelihood-only maximum-likelihood fits satisfying the likelihood-ratio
assumptions or a simulation calibration. The existing `ignore_prior: false` MAP
pair is useful for audit but does not supply that calibration. That hours-long
scientific computation was not replaced by a shortcut or by relabelling the old
chain; until it is run and independently checked, the platform must withhold
that detection claim.

Passing the software suite demonstrates that known unsupported claims are
blocked; it does not establish a positive astrophysical result. The synthetic
transient classifier remains an uncalibrated demonstration, incomplete-error
cross-wavelength analyses remain non-publication, HST/JWST exposure feasibility
still requires the official instrument tools, and archive metadata alone is
not scientific data. The platform now provides a signed model-adequacy
attestation constructor, but ordinary Cobaya completion does not generate that
attestation: predictive checks, prior/systematics sensitivity, simulation
recovery, and independent reproduction must actually run first. Until those
records exist, the production path intentionally remains fail-closed.
Independent scientific review and reproduction remain outside the automated
test claim.

## Verification performed

- Scheduled core scientific suite (2026-07-12): **798 passed, 0 skipped, 0
  failed** across 40 explicit test files; the JUnit anti-silent-skip guard
  passed.
- Full backend regression suite (2026-07-10): **2871 passed, 3 skipped, 59
  deselected, 0 failed**; total measured coverage was **63.49%**.
- Joint scientific-integrity, security-authority, storage, migration, archive,
  transient, and cross-wavelength attack suite: **238 passed, 0 failed**.
- Default cosmology benchmark suite: **23 passed, 2 explicitly skipped/not
  validated, 0 failed**; outcome accounting is exhaustive (25 of 25).
- Slow Pantheon+SH0ES full-covariance benchmark: **passed**, with
  `cov_fidelity=full` and the fitted artifact digest equal to the registry pin.
- Cosmology registry audit: **34/34 clean**, with no executable-pin issue.
- Citation reachability audit: all **87 declared identifiers** (2 bibcodes, 67
  arXiv IDs, and 18 DOIs) are reachable through actual tool-result provenance.
- Ruff fatal-rule checks for the backend application and `git diff --check`:
  passed.

The two default-suite skips are not passes: they are explicit slow full-data
opt-ins. The full Pantheon+ covariance opt-in and the extended-DE numerical
diagnostic were additionally executed during this remediation and passed their
stated numerical checks, but both remained correctly withheld from scientific
publication. The hours-long DESI `w0wa` significance rerun described above
remains deliberately outstanding.
