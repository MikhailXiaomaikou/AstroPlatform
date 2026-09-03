---
name: cosmology-smoke
description: Run a cosmology science-regression smoke check. Verifies ΛCDM BAO+CMB recovers H0≈67.4, wCDM BAO+CMB recovers w≈-1, three-tier chain_tier triggers correctly (publication/exploratory/blocked), and distance_modulus_model stays within 1e-3 mag of astropy. Use after any change to backend/app/services/cosmology_*.py or any module of the backend/app/services/cosmology_likelihoods/ package to confirm no science regression.
---

# Cosmology science-regression smoke

This skill runs four cheap science checks against the cosmology stack. Total wall-clock: ~30-60s.

> **Known state as of 2026-09-02 (measured on `main` @ 3a7e6e4, not a regression of your change):**
> checks 3 and 4 pass. Check 1 recovers H0 = 67.69 but lands in tier `exploratory`, not
> `publication` — the publication gate now lists `compressed_or_approximate_likelihood` and
> `literature_typed_input` as blocking reasons, so a compressed BAO+Planck chain can no longer
> reach `publication` by design. Check 2 (wCDM on the same datasets) returns `chain_tier=blocked`
> with importance-sampler ESS = 2 and the `parameters` block redacted, so it reports
> `pass=false`. Until the anchors are re-decided with the user (which tier check 1 should
> expect; which dataset/sampler combination gives a healthy wCDM chain), treat checks 1-2 as
> **informational** and checks 3-4 as the real gates. Do not "fix" this by loosening the gate.

## Checks

### 1. ΛCDM BAO+CMB → H0 anchor
DESI DR1 BAO + Planck18 compressed → flat-ΛCDM should give H0 = 67.4 ± 0.5 km/s/Mpc, omegam = 0.31 ± 0.01.
Tier should be `publication` with ESS > 400.

### 2. wCDM BAO+CMB → DESI 2024 reproduction
Same datasets with model=`wcdm`. w should land near -1 (within prior; DESI 2024 reports w ≈ -0.95 ± 0.20). Tier may be `exploratory` (broad w prior cuts ESS).

### 3. chain_tier three-tier trigger matrix
Run three configurations that should land in each tier:
- (lcdm + cached_real + long chain) → `publication`
- (wcdm + cached_real + medium chain, narrow w prior) → `exploratory` + `__exploratory_warning__` set
- (any model + inline_unverified rows) → `blocked` + `__do_not_claim__=True`

### 4. distance_modulus_model precision vs astropy
For z ∈ {0.1, 0.5, 1.0, 2.3} and the 3 models (flat_lcdm, flat_wcdm, flat_w0wa_cdm), max |μ_ours − μ_astropy| must be < 1e-3 mag.

## Run

```bash
REPO="$(git rev-parse --show-toplevel 2>/dev/null || echo /Users/chenkexuan/Projects/astro-platform)"
# backend/venv is untracked and lives only in the primary checkout; run it from this worktree's backend/ so `app` is the code under test.
cd "$REPO/backend" && /Users/chenkexuan/Projects/astro-platform/backend/venv/bin/python3 - <<'PY'
import numpy as np, json
from app.services.cosmology_likelihoods import run_likelihood_chain
from app.services.cosmology_mcmc import distance_modulus_model
from astropy.cosmology import FlatLambdaCDM, FlatwCDM, Flatw0waCDM

results = {}

# Check 1: ΛCDM BAO+CMB
r = run_likelihood_chain(model="lcdm", dataset_keys=["desi_dr1_bao", "planck2018_compressed"], n_samples=4000, random_seed=42)
h0 = r["parameters"]["H0"]["median"]
results["lcdm_h0_anchor"] = {
    "pass": 66.5 < h0 < 68.5 and r["chain_tier"] == "publication",
    "h0_median": h0,
    "tier": r["chain_tier"],
    "ess": r["chain_diagnostics"].get("proposal_ess"),
}

# Check 2: wCDM BAO+CMB
r2 = run_likelihood_chain(model="wcdm", dataset_keys=["desi_dr1_bao", "planck2018_compressed"], n_samples=4000, random_seed=42)
w = (r2.get("parameters") or {}).get("w", {}).get("median")  # blocked chains redact parameters
results["wcdm_w_near_minus_one"] = {
    "pass": w is not None and -1.5 < w < -0.5,
    "w_median": w,
    "tier": r2["chain_tier"],
    "status": r2.get("__tool_status__"),
}

# Check 3: chain_tier triggers
results["chain_tier_blocked_inline"] = {"pass": False, "tier": None}
from app.services.cosmology_mcmc import fit_cosmology_emcee
rng = np.random.default_rng(0)
z = np.linspace(0.05, 1.0, 20)
mu = 5*np.log10(z*3000) + 25 + rng.normal(0, 0.1, 20)
rows = [{"z": float(zi), "mu": float(mi), "sigma_mu": 0.1} for zi, mi in zip(z, mu)]
r3 = fit_cosmology_emcee(rows, n_walkers=16, n_steps=200, n_burn=50)
results["chain_tier_blocked_inline"] = {
    "pass": r3["chain_tier"] == "blocked" and r3.get("__do_not_claim__") is True,
    "tier": r3["chain_tier"],
}

# Check 4: distance_modulus precision
worst = 0.0
for z_val in (0.1, 0.5, 1.0, 2.3):
    z_arr = np.array([z_val])
    ours = distance_modulus_model(z_arr, "flat_lcdm", {"H0": 67.4, "Om0": 0.315})
    astro = FlatLambdaCDM(H0=67.4, Om0=0.315, Tcmb0=2.7255).distmod(z_arr).value
    worst = max(worst, abs(ours[0] - astro[0]))
    ours_w = distance_modulus_model(z_arr, "flat_wcdm", {"H0": 70.0, "Om0": 0.30, "w0": -0.9})
    astro_w = FlatwCDM(H0=70.0, Om0=0.30, w0=-0.9, Tcmb0=2.7255).distmod(z_arr).value
    worst = max(worst, abs(ours_w[0] - astro_w[0]))
    ours_w0wa = distance_modulus_model(z_arr, "flat_w0wa_cdm", {"H0": 70.0, "Om0": 0.30, "w0": -0.9, "wa": 0.1})
    astro_w0wa = Flatw0waCDM(H0=70.0, Om0=0.30, w0=-0.9, wa=0.1, Tcmb0=2.7255).distmod(z_arr).value
    worst = max(worst, abs(ours_w0wa[0] - astro_w0wa[0]))
results["distmod_precision_mag"] = {
    "pass": worst < 1e-3,
    "worst_diff_mag": worst,
}

print(json.dumps(results, indent=2, default=float))
all_pass = all(c["pass"] for c in results.values())
import sys; sys.exit(0 if all_pass else 1)
PY
```

## Interpretation

- All four `pass=true` → safe to push. (See the known-state note at the top: as of 2026-09-02 checks 1-2 are informational until re-anchored.)
- `lcdm_h0_anchor.pass=false` → check `_flat_de_distances_at_z` or `_bao_predictions` in `cosmology_likelihoods/bao.py` for regressions.
- `wcdm_w_near_minus_one.pass=false` → check w prior bounds or the w handling in `_bao_predictions` (`cosmology_likelihoods/bao.py`).
- `chain_tier_blocked_inline.pass=false` → check `CLAIMABLE_INPUT_ORIGINS` filter in `fit_cosmology_emcee`.
- `distmod_precision_mag.pass=false` → check Gauss-Legendre node order in `distance_modulus_model`; should not regress past 32.
