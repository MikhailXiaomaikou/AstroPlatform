# Full-CMB w0waCDM reproduction of DESI 2024 VI (dark-energy frontier)

Documents an attempted DESI 2024 VI evolving-dark-energy cross-check with
Cobaya + CAMB on local compute, using Cobaya's native official likelihoods.
Run from `backend/`.

## Archived preliminary snapshot — not publication-converged

The 2026-06-03 run stopped at R-1(means) = 0.047 with R-1(bounds) = 0.13
(~19,400 samples). The publication gate is R-1 <= 0.01, so the numbers below
are retained only as a reproducibility/debugging snapshot. They must not be
cited as final constraints or as a dark-energy detection.

Data: DESI DR1 BAO + Pantheon+ SN + a clik-free Planck 2018 CMB stack
(CamSpec2021 high-l TT/TE/EE + native low-l TT/EE + native lensing).

| Parameter | This run | DESI 2024 VI Table 3 | Consistency |
|---|---|---|---|
| w0 | -0.841 ± 0.063 | -0.827 ± 0.063 | 0.16 sigma |
| wa | -0.647 (+0.29 / -0.25) | -0.75 ± 0.29 | 0.26 sigma |
| Omega_m | 0.3076 ± 0.0068 | 0.3085 ± 0.0068 | 0.09 sigma |
| H0 | 67.95 ± 0.73 | 68.03 ± 0.72 | 0.08 sigma |

The archived sample has w0-wa correlation -0.89. Its posterior-mean
Mahalanobis displacement was formerly labelled “Delta chi^2 = 6.60 -> 2.09
sigma”. That label has been retired: it is not DESI's model-comparison
statistic. A Wilks likelihood-ratio interpretation requires likelihood-only
maximum-likelihood fits (plus its regularity assumptions), not two posterior
modes. The repository now contains paired generated MAP configurations and a
fail-closed comparison program, but no calibrated significance is reported
from those MAP points.

The table is a qualitative parameter cross-check only. Its “consistency”
column used an independent-error quadrature even though the analyses share
BAO/SN data and related CMB information, so those values are not calibrated
agreement probabilities.

### Scientific limitations
- The chain does not meet the R-1 <= 0.01 publication threshold; the slow w0-wa
  degeneracy dominates the mixing time.
- No DESI-comparable preference can be quoted until free-w0waCDM and fixed-
  LambdaCDM are compared with a validated likelihood-only or simulation-
  calibrated procedure on the same likelihood stack.
- The clik-free CamSpec2021 + native-lensing stack is a close proxy, not DESI's
  plik (PR3) high-l + PR4/ACT lensing pipeline.
- DESI BAO and Pantheon+ are Cobaya's own official likelihoods
  (`bao.desi_2024_bao_all`, `sn.pantheonplus`), not a reimplementation.

## Canonical evidence workflow

The formal path is implemented by
`scripts/cobaya/canonical_full_likelihood_evidence.py`. It hashes the exact
data files, records the environment and run outputs, diagnoses four chains,
and compares paired MAP fits. A missing file, stale hash, failed optimizer,
config mismatch, or diagnostic failure produces a `FAIL` manifest and no
affected numerical result.

1. One-time install (clik-free Planck set + CAMB into `packages/`):

   ```bash
   venv/bin/cobaya-install scripts/cobaya/planck_clikfree_install.yaml -p packages
   ```

2. Reproduce the committed paired MAP configs from the canonical chain config:

   ```bash
   venv/bin/python scripts/cobaya/canonical_full_likelihood_evidence.py generate
   ```

   This derives `w0wa_desi_sn_planck_map.yaml` and
   `lcdm_desi_sn_planck_map.yaml` from one source. Their theory, likelihood,
   shared cosmological parameters, and automatically supplied nuisance
   definitions must remain identical; only `w=-1, wa=0` is fixed in the
   LambdaCDM file.

3. Run the formal four-chain sample through the attesting wrapper. The YAML now
   stops at Cobaya `Rminus1_stop=0.01` and `Rminus1_cl_stop=0.10`; these native
   checks do not replace the independent ArviZ diagnostics in step 5.

   ```bash
   OMP_NUM_THREADS=3 venv/bin/python \
     scripts/cobaya/canonical_full_likelihood_evidence.py run \
     --kind chain \
     --config scripts/cobaya/w0wa_desi_sn_planck.yaml \
     --prefix cobaya_runs/w0wa \
     --packages-path packages --mpi 4 --force
   ```

   The wrapper writes `cobaya_runs/w0wa.run.json` before/after execution. A
   chain launched without this certificate is deliberately not accepted by
   the publication analyzer.

4. Run both paired MAP minimizations. Each config uses four BOBYQA starts via
   MPI, identical likelihood/data/nuisance definitions, and a tighter
   `rhoend=0.01` than Cobaya's noisy-likelihood default. The committed
   `ignore_prior: false` setting targets the posterior, so these runs audit
   paired MAP points; they do not establish likelihood-only MLEs.

   ```bash
   OMP_NUM_THREADS=3 venv/bin/python \
     scripts/cobaya/canonical_full_likelihood_evidence.py run \
     --kind map --config scripts/cobaya/w0wa_desi_sn_planck_map.yaml \
     --prefix cobaya_runs/w0wa_free_map \
     --packages-path packages --mpi 4 --force

   OMP_NUM_THREADS=3 venv/bin/python \
     scripts/cobaya/canonical_full_likelihood_evidence.py run \
     --kind map --config scripts/cobaya/lcdm_desi_sn_planck_map.yaml \
     --prefix cobaya_runs/lcdm_fixed_map \
     --packages-path packages --mpi 4 --force
   ```

5. Build the evidence manifest:

   ```bash
   venv/bin/python scripts/cobaya/canonical_full_likelihood_evidence.py analyze
   ```

   Posterior intervals are emitted only when all four distinct chain files
   pass rank-normalized split R-hat `<1.01` and bulk ESS `>=400` for every
   sampled cosmological and Planck nuisance parameter. Integer Cobaya weights
   are expanded before diagnosis; 30% burn-in is removed per chain and the
   most recent draws are aligned to the shortest chain.

   Two raw paired-point differences are emitted only when both finite Cobaya
   minima are attested and their expanded likelihood, shared-parameter,
   config, artifact, and byte-level data fingerprints match:
   `2 * (minuslogpost_fixed - minuslogpost_free)` and
   `chi2_fixed - chi2_free` evaluated at those optimized points. Because the
   committed optimizers target posterior modes, the latter is descriptive and
   is not a Wilks likelihood-ratio statistic. The manifest therefore withholds
   p-values and Gaussian-equivalent significance. Those require attested
   likelihood-only MLEs and a justified asymptotic or simulation calibration.

   The output `cobaya_runs/w0wa_evidence_manifest.json` contains configuration
   hashes, all data versions/file hashes, Python/package environment, chain
   diagnostics, MAP component chi-squares, run attestations, and a final
   manifest hash. The command exits nonzero whenever `publication_ready=false`.
   If a future likelihood-only or simulation-calibrated comparison also sets
   `significance_ready=true`, the manifest emits versioned conclusion
   attestations. Each attestation binds one claim kind to the exact LCDM versus
   w0waCDM model pair, data/likelihood fingerprints, calibration method, and
   evidence-manifest hash; a bare readiness boolean is never manuscript
   authority.

The legacy `analyze_w0wa_run.py` command is retained only as a compatibility
wrapper around this strict analyzer; it no longer prints progress-file-based
intervals or posterior-mean pseudo-significances.

`packages/` (installed CMB data) and `cobaya_runs/` (chains) are both gitignored —
large and machine-local. Re-run step 1 on a fresh checkout.

## Why this is a dedicated offline run, not the autonomous oracle path

The platform's in-process likelihood runner (the 45 s autonomous chat path) uses a
COMPRESSED CMB: the Chen-Huang-Wang 2019 acoustic-scale distance prior
(R, l_A, Omega_b h^2) for extended dark-energy models. That compressed path is fast
but does NOT reproduce DESI as cleanly as this full-CMB run — measured in-process it
leaves w0 ~ -0.62 ± 0.17 with H0 pulled high (~73) by the compressed Pantheon SH0ES
calibration, well outside DESI. So w0/wa goals correctly stay OFF-ANCHOR
(exploratory / human-review) in the oracle: the platform does not autonomously claim
the dark-energy result. This full-CMB run is an offline validation path, not an
autonomous result. It becomes publication-usable only after strict convergence and
all data/likelihood provenance gates pass. Even then, those conditions support
posterior intervals only. A claim that the extended model is preferred or that
LambdaCDM is ruled out additionally requires matched likelihood-only MLE fits
with justified Wilks assumptions, or an explicit simulation calibration; the
committed posterior-mode MAP pair does not provide that significance evidence.
