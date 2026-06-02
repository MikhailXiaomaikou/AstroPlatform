#!/usr/bin/env python3
"""Read the w0waCDM Cobaya chain and check it reproduces DESI 2024 VI.

Usage:  venv/bin/python scripts/cobaya/analyze_w0wa_run.py [chain_prefix]
Default prefix: cobaya_runs/w0wa .

Honest by construction:
  * If the chain has not reached publication-grade convergence (Gelman-Rubin
    R-1 <= 0.01) it prints ONLY the running medians as a PRELIMINARY trend and
    REFUSES to print any sigma / tension / detection figure -- an unconverged
    chain has an artificially tight spread that fabricates significance.
  * Error bars are getdist's marginalized 68% credible intervals (asymmetric
    for wa), NOT the raw sample std.
  * The dark-energy detection significance is the JOINT 2-D (w0, wa) departure
    from LambdaCDM (w0=-1, wa=0), from the 2x2 covariance (Delta chi^2, 2 dof
    -> equivalent sigma) -- the SAME statistic DESI's ~2.5 sigma is. The
    per-parameter 1-D sigmas are shown but explicitly labelled as NOT that.

DESI 2024 VI Table 3, DESI+CMB+Pantheon+: w0=-0.827+-0.063,
wa=-0.75 (+0.29/-0.25), Omega_m=0.3085+-0.0068, H0=68.03+-0.72; ~2.5 sigma.
"""
from __future__ import annotations

import sys

import numpy as np
from getdist import loadMCSamples
from scipy.stats import chi2 as chi2dist, norm

PREFIX = sys.argv[1] if len(sys.argv) > 1 else "cobaya_runs/w0wa"
# Publication-grade convergence: only above this do we print significances.
RMINUS1_CLEAN = 0.01
# Published DESI 2024 VI Table 3 (DESI+CMB+Pantheon+). wa's error is asymmetric
# (+0.29/-0.25); we use the larger (+0.29) side as a conservative symmetric unit.
DESI = {"w": (-0.827, 0.063), "wa": (-0.75, 0.29),
        "omegam": (0.3085, 0.0068), "H0": (68.03, 0.72)}


def _read_rminus1(prefix):
    """Last Gelman-Rubin R-1 cobaya wrote to <prefix>.progress (None if absent)."""
    try:
        with open(prefix + ".progress") as fh:
            rows = [ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        if not rows:
            return None
        # columns: N, timestamp(date time), acceptance_rate, Rminus1, Rminus1_cl
        return float(rows[-1].split()[-2])
    except (OSError, ValueError, IndexError):
        return None


def _marge(samples, name):
    """(mean, lower_err, upper_err) from getdist's marginalized 68% limits."""
    par = samples.getMargeStats().parWithName(name)
    lim = par.limits[0]  # index 0 = 68%
    mean = float(par.mean)
    return mean, float(mean - lim.lower), float(lim.upper - mean)


def _joint_sigma(samples):
    """Joint 2-D (w0, wa) departure from LCDM as an equivalent 1-D sigma."""
    cov = np.asarray(samples.cov(pars=["w", "wa"]))
    delta = np.array([float(samples.mean("w")) + 1.0, float(samples.mean("wa"))])
    dchi2 = float(delta @ np.linalg.inv(cov) @ delta)
    p_enclosed = float(chi2dist.cdf(dchi2, df=2))      # mass inside this contour
    nsigma = float(norm.ppf(0.5 + p_enclosed / 2.0))    # equivalent 1-D sigma
    return dchi2, nsigma


try:
    s = loadMCSamples(PREFIX, settings={"ignore_rows": 0.3})
    n = s.numrows
except Exception as exc:  # header-only / empty / unreadable chain
    print(f"chain: {PREFIX} -- no usable samples yet ({exc})")
    raise SystemExit(0)

r1 = _read_rminus1(PREFIX)
converged = r1 is not None and r1 <= RMINUS1_CLEAN
r1_str = f"{r1:.4f}" if r1 is not None else "n/a"
print(f"chain: {PREFIX}  samples(after 30% burn-in)={n}  Gelman-Rubin R-1={r1_str}")

if not converged:
    print(f"\n** NOT publication-grade converged (need R-1 <= {RMINUS1_CLEAN}). "
          f"PRELIMINARY medians only -- significances suppressed. **")
    for p in ("w", "wa", "omegam", "H0"):
        try:
            print(f"  {p:8s} ~ {float(s.mean(p)):+.4f}   (trend only, no error bar)")
        except Exception as exc:
            print(f"  {p:8s} n/a ({exc})")
    raise SystemExit(0)

# Converged -> full honest report against DESI.
print(f"{'param':8s} {'ours (mean, 68% CI)':>28s} {'DESI published':>20s} {'consistency':>12s}")
for p, (pub, pubsig) in DESI.items():
    try:
        mean, lo, hi = _marge(s, p)
    except Exception as exc:
        print(f"{p:8s}  n/a ({exc})")
        continue
    sig = (lo + hi) / 2.0
    comb = (sig ** 2 + pubsig ** 2) ** 0.5
    nsig = abs(mean - pub) / comb if comb else float("nan")
    print(f"{p:8s} {mean:+8.4f} (-{lo:.4f}/+{hi:.4f}) {pub:+8.4f} +/- {pubsig:6.4f} {nsig:6.2f} sigma")

# JOINT 2-D dark-energy significance -- the statistic DESI's ~2.5 sigma reports.
try:
    dchi2, njoint = _joint_sigma(s)
    w1 = (float(s.mean("w")) + 1.0) / float(s.std("w"))
    wa1 = float(s.mean("wa")) / float(s.std("wa"))
    print(f"\nJOINT (w0,wa) departure from LCDM (w0=-1, wa=0): "
          f"Delta chi^2={dchi2:.2f} (2 dof) -> {njoint:.2f} sigma   [DESI reports ~2.5 sigma]")
    print(f"(Per-parameter 1-D, FOR REFERENCE ONLY -- NOT the detection significance: "
          f"w0 {w1:+.1f}s, wa {wa1:+.1f}s. The detection is the JOINT number above, "
          f"which uses the w0-wa anti-correlation.)")
except Exception as exc:
    print(f"\nJOINT (w0,wa) significance: n/a ({exc})")
