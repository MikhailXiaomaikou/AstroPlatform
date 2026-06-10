# Standard-Astro cosmology — honest validation package

This is a small, self-contained package for a working cosmologist to run and judge.
It is **not a sales demo.** Every number it prints is computed live, this run,
through the platform's real code paths — none of it is hard-coded. Full-covariance
datasets are sha256-pinned; literature-typed compressed priors (e.g.
`planck2018_compressed`) carry a citation and a `literature_typed` fidelity grade
instead of a file checksum, and the demos label which is which. Demos that fail or
come back "exploratory" say so.

## What this platform is (and is not)

The bet is **not** that this is a better fitting engine. The samplers are modest
(importance sampling, emcee, an analytic BAO grid). If you want serious parameter
estimation, use cobaya / CosmoMC / MontePython directly — they will beat this.

The bet is **provenance + zero fabrication**: every quantitative claim is traceable
to a checksummed public data file and a real citation, fabricated numbers and
invented citations are hard-blocked, and the platform grades its own chains and
**refuses to certify a result it did not actually earn**. The four demos below are
each meant to prove one piece of that.

## How to run

From the `backend/` directory, with the project venv:

```bash
# quick open-box demos (1a + 3), ~5 s, zero setup
./venv/bin/python3 validation_package/run_demos.py

# the DES/DESI evolving-dark-energy reproduction (~70-90 s, full 1829-SN covariance)
./venv/bin/python3 validation_package/run_demos.py 1b

# the Planck 2018 high-l CMB chi2 (needs cobaya + camb; both ship in this venv)
./venv/bin/python3 validation_package/run_demos.py 2

# everything
./venv/bin/python3 validation_package/run_demos.py all
```

Results are deterministic at `seed=42` (last-digit drift is possible across
BLAS/numpy versions).

## The four demos

### 1a — ΛCDM fit, DESI DR1 BAO + Planck18 compressed *(open-box, no external deps, ~5 s)*

Real cosmological inference; the chain self-grades **publication-tier** — but as a
*compressed preliminary*, and it surfaces that scope itself. You should see ≈:

```
H0      = 67.38 km/s/Mpc
Omega_m = 0.312
chain_tier = 'publication'   (ESS ≈ 703)   publication_ready = True
claim_scope = 'compressed_likelihood_preliminary'
cov_fidelity = 'literature_typed'      (weakest grade used)
```

Two honest caveats the demo prints:
- The CMB leg here is `planck2018_compressed` — a **hand-typed Gaussian** on
  H0/Ωm/σ8/S8 (no full CMB likelihood, no sha256 file), graded `literature_typed`.
- The anchor match is **largely prior-driven**: DESI DR1 BAO *alone* gives
  H0 ≈ 68.4 / Ωm ≈ 0.29; the Planck Gaussian pulls the result toward its own mean
  (67.36 / 0.3153). So "matches the ΛCDM anchor" is mostly the prior asserting the
  anchor, not BAO independently finding it.

### 1b — w0waCDM fit, DESI DR2 BAO + full DES-SN5YR + BBN *(env flag, ~70-90 s)*

Recovers the same **evolving-dark-energy direction** (w0 > −1, wa < 0) that
DESI DR2 + DES-SN5YR report — **and** refuses to certify it. You should see ≈:

```
w0 = -0.79   wa = -0.69   Omega_m = 0.320   H0 = 65.4 km/s/Mpc
chain_tier = 'exploratory'   (ESS ≈ 443, ABOVE the 400 floor)   publication_ready = False
off_anchor_review_required = True
```

This is a **no-CMB** combination (BAO + SN + a BBN ωb prior), so it recovers the
*sign* of the effect — **not** the DESI ~4σ significance or its exact (w0, wa). The
DESI DR2 headline (w0 ≈ −0.75, wa ≈ −0.86) is driven by BAO + CMB + the same
DES-SN5YR sample. And it is
graded **exploratory _not_ because ESS is low** (ESS ≈ 443 is above the 400 floor)
but because w0/wa are **off-anchor frontier parameters** with no reproduced
published anchor — the platform routes them to human review and blocks the
publication claim. (The full 1829×1829 DES-SN5YR covariance is off by default for
speed; `run_demos.py 1b` sets `DES_SN5YR_FULL_CHI2_ENABLED=1` for you. No download —
the data is vendored and pinned.)

### 2 — Planck 2018 high-ℓ CMB χ², plik_lite TT/TE/EE *(needs cobaya + camb, ~1 s)*

The vendored, sha256-pinned plik_lite bandpowers + cobaya's clik-free *native*
likelihood + a CAMB spectrum reproduce Planck's base-ΛCDM χ² **evaluated at the
published parameters** — off our own checksummed copy of the data. This is a single
likelihood evaluation at a fixed point (τ pinned at 0.0544, A_planck = 1.0), **not**
a minimized best fit. You should see:

```
chi2       = 584.45   over 613 TT/TE/EE bandpowers
chi2 / dof = 0.963    (dof = 613−6, nominal ΛCDM convention; ~0.96)
```

### 3 — fabrication hard-block, the claim validator *(open-box, instant)*

A made-up number and a fake citation, both refused:

```
3a) tools returned parallax=7.50, reply claims 9.00  ->  validate_claims.ok = False
                                                          BLOCKED: parallax_mas = 9.0
3b) prose cites bibcode 2099XXXX...999Z (in no tool result) -> BLOCKED: invalid_bibcode
```

Each demo also prints **provenance receipts**: each dataset's `cov_fidelity` grade,
the sha256 pins where the data is a checksummed file (and an explicit "no sha256 —
literature-typed" line where it isn't), and the real arXiv/bibcode citations.

## Honest limitations — read this before judging

1. **Not a better sampler.** Inference is deliberately modest. The value is
   traceability and refusal-to-fabricate, not posterior sophistication.
2. **Many entries are compressed, not full likelihoods.** Full-covariance paths
   exist for DESI DR1/DR2 BAO, DES-SN5YR (flag-gated), eBOSS FSBAO, Moresco
   cosmic chronometers, and Planck high-ℓ plik_lite. But `planck2018_compressed`
   is **not** a CMB likelihood: for ΛCDM (demo 1a) it is applied as a **hand-typed
   diagonal Gaussian** on (H0, Ωm, σ8) — plus an S8 row applied on the derived S8 = σ8·√(Ωm/0.3) —
   centered at the Planck base-ΛCDM values
   (67.36 / 0.3153 / 0.8111 / 0.832) — a direct H0/Ωm prior with **no sha256 file**.
   (The R/ℓ_A/ωb acoustic-scale distance-prior form is only used for extended w0wa
   models.) Several other entries are single-point or literature-typed. The registry
   grades each one's `cov_fidelity`, and a chain reports the **weakest** fidelity it used.
3. **A "publication" chain can rest on an unpinned, hand-typed prior.** The
   publication gate only blocks `unverified`/`None` fidelity — `literature_typed`
   **passes**. So demo 1a is `publication_ready=True` with aggregate
   `cov_fidelity='literature_typed'`, because its Planck term is a hand-typed
   Gaussian with no file to checksum. "Publication-tier" here means *the chain
   converged tightly enough to quote, as a compressed preliminary* — not *every
   input is a checksummed full likelihood*. The demos print `claim_scope` and
   `cov_fidelity` so this is visible, not hidden.
4. **CMB is high-ℓ only.** plik_lite TT/TE/EE is connected; **low-ℓ** (Commander TT
   + SimAll lowE) is **not**, so in the full-CMB (plik_lite) sampling-chain path τ is supplied as a Gaussian
   prior (0.0544 ± 0.0073), not self-constrained. **ACT DR6** primary is not
   connected. (Demo 2 is a fixed-point χ² evaluation — it pins τ at 0.0544 directly;
   the Gaussian-prior τ applies to the sampling/chain path, not to demo 2.)
5. **The w0wa result is exploratory, by design — and for a specific reason.** It is
   demoted by the **off-anchor frontier guard** (w0/wa have no reproduced published
   anchor), *not* by low ESS. Its median/1σ may be discussed as exploratory, but the
   claim validator refuses to admit its posterior numbers as a published/citeable
   constraint (posterior claims require `publication_ready=true`). It recovers the
   *direction* of evolving DE, not DESI-grade precision.
6. **No runtime data fetching.** All data is vendored at build time (full-covariance
   files sha256-pinned). Good for provenance, a limit for freshness.
7. **The validator is a gate, not a proof system.** It matches numeric claims to
   the tool-result universe at ±1% (±0.1% when the universe is thin) and checks
   citations against the provenance pool. It catches fabricated numbers and
   invented citations; it is not a guarantee against every laundering attempt
   (the main ±1%-cross-label and bibcode-digit-leak surfaces are closed and
   regression-tested, but treat it as a strong filter, not a theorem).

## Please try to break it

The differentiator only matters if it holds under attack. Concretely:

- Take any demo's output and **edit a number** in a prose summary, then run that
  prose through `validate_claims` — does the gate catch it?
- **Invent a citation** (a plausible-looking bibcode or "Author et al. 2023") and
  see whether `provenance_citation_violations` flags it.
- **Combine overlapping datasets** (e.g. `desi_dr1_bao` + `desi_dr2_bao`, or two SN
  samples) and check the chain refuses to publish on the double-count.
- **Ask for a claim the chain didn't earn** — read a result at `exploratory` tier
  and try to get a publication-grade number out of it.
- Tell us where it's thin, wrong, or useless for your actual work. That feedback is
  the point of this package.
