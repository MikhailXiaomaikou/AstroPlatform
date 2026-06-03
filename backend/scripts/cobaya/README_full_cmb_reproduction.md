# Full-CMB w0waCDM reproduction of DESI 2024 VI (dark-energy frontier)

Reproduces the DESI 2024 VI evolving-dark-energy result end-to-end with Cobaya +
CAMB, entirely on free local compute, using Cobaya's OWN native official
likelihoods. Run from `backend/`.

## Result — converged 2026-06-03 (R-1(means) = 0.047, ~19,400 samples)

Data: DESI DR1 BAO + Pantheon+ SN + a clik-free Planck 2018 CMB stack
(CamSpec2021 high-l TT/TE/EE + native low-l TT/EE + native lensing).

| Parameter | This run | DESI 2024 VI Table 3 | Consistency |
|---|---|---|---|
| w0 | -0.841 ± 0.063 | -0.827 ± 0.063 | 0.16 sigma |
| wa | -0.647 (+0.29 / -0.25) | -0.75 ± 0.29 | 0.26 sigma |
| Omega_m | 0.3076 ± 0.0068 | 0.3085 ± 0.0068 | 0.09 sigma |
| H0 | 67.95 ± 0.73 | 68.03 ± 0.72 | 0.08 sigma |

Joint (w0, wa) departure from LambdaCDM (w0=-1, wa=0):
**Delta chi^2 = 6.60 (2 dof) -> 2.09 sigma** (DESI reports ~2.5 sigma).
Recovered w0-wa correlation -0.89 (DESI ~ -0.9).

All four parameters land within 0.3 sigma of DESI, with DESI-level error bars:
the full Planck CMB delivers DESI-grade constraining power, and the chain recovers
the evolving-dark-energy preference.

### Honest caveats (do not overclaim)
- Converged by the standard Gelman-Rubin R-1(means) < 0.05 criterion. The stricter
  0.01 gold standard, and the R-1(bounds) = 0.13 tails, would need a much longer
  run; the slow w0-wa degeneracy (correlation -0.89) dominates the mixing time.
- The 2.09 sigma here vs DESI's ~2.5 sigma is attributable to the clik-free
  CamSpec2021 + native-lensing CMB stack used here vs DESI's plik (PR3) high-l +
  PR4/ACT lensing — a close proxy, not a byte-identical pipeline.
- DESI BAO and Pantheon+ are Cobaya's own official likelihoods
  (`bao.desi_2024_bao_all`, `sn.pantheonplus`), not a reimplementation.

## Reproduce it

1. One-time install (clik-free Planck set + CAMB into `packages/`):

   ```bash
   venv/bin/cobaya-install scripts/cobaya/planck_clikfree_install.yaml -p packages
   ```

2. Run — 4 parallel MPI chains (`--bind-to none` lets each chain's OMP threads
   spread; ~5 h to R-1<0.05 on an Apple M4 Pro):

   ```bash
   OMP_NUM_THREADS=3 mpirun -n 4 --bind-to none venv/bin/cobaya-run \
     scripts/cobaya/w0wa_desi_sn_planck.yaml -p packages -o cobaya_runs/w0wa --force
   ```

   Tighten `Rminus1_stop` in the yaml from 0.05 to 0.01 for a publication-grade
   final (much longer).

3. Analyze (medians, 68% credible intervals, joint significance; refuses to print
   significances until R-1 <= 0.01 unless you relax the gate):

   ```bash
   venv/bin/python scripts/cobaya/analyze_w0wa_run.py
   ```

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
the dark-energy result. This full-CMB reproduction is the trustworthy figure, and it
is produced by a dedicated offline run, not the autonomous path.
