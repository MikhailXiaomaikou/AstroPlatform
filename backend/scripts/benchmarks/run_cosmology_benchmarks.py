#!/usr/bin/env python3
"""Cosmology science-regression benchmark suite.

Pinned baselines for the cosmology stack. Runnable from CI; exits non-zero
on any benchmark failure so a regression breaks the build.

Each benchmark is a small, deterministic call into the cosmology services
that should recover a known physical value within a fixed tolerance. The
suite intentionally does NOT exercise the LLM — it isolates the science
kernels and likelihood runners from prompt / agent-loop variability.

Usage:
    python scripts/benchmarks/run_cosmology_benchmarks.py
    python scripts/benchmarks/run_cosmology_benchmarks.py --json results.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
import traceback
from typing import Any, Callable

# Make `app.*` importable when this script is invoked from anywhere
# (CI runners, local cwd, the cosmology-smoke skill, etc.).
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark functions
# ─────────────────────────────────────────────────────────────────────────────

def bench_lcdm_h0_anchor() -> dict[str, Any]:
    """ΛCDM BAO+CMB → H0 anchor.

    DESI DR1 BAO + Planck 2018 compressed under flat ΛCDM should recover
    H0 = 67.4 ± 0.5 km/s/Mpc (the Planck-anchored result), with publication
    tier and ESS ≥ 400.
    """
    from app.services.cosmology_likelihoods import run_likelihood_chain
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=4000,
        random_seed=42,
    )
    h0 = float(r["parameters"]["H0"]["median"])
    tier = str(r["chain_tier"])
    ess = float(r["chain_diagnostics"].get("proposal_ess") or 0.0)
    return {
        "pass": 66.5 < h0 < 68.5 and tier == "publication" and ess >= 400.0,
        "h0_median": round(h0, 4),
        "chain_tier": tier,
        "proposal_ess": round(ess, 1),
        "target": "H0 in [66.5, 68.5] + tier=publication + ESS >= 400",
    }


def bench_wcdm_w_near_minus_one() -> dict[str, Any]:
    """wCDM BAO+CMB → w near -1 (DESI 2024 reports w ≈ -0.95 ± 0.20)."""
    from app.services.cosmology_likelihoods import run_likelihood_chain
    r = run_likelihood_chain(
        model="wcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=4000,
        random_seed=42,
    )
    w = float(r["parameters"].get("w", {}).get("median") or float("nan"))
    return {
        "pass": -1.5 < w < -0.5,
        "w_median": round(w, 4),
        "chain_tier": str(r["chain_tier"]),
        "target": "w in [-1.5, -0.5]",
    }


def bench_hubble_tension_planck18_vs_riess22() -> dict[str, Any]:
    """planck18 baseline vs riess22 target → 5-12% luminosity-distance offset.

    Reproduces the published Hubble tension (~7-8% at z>0). If this drifts
    out of range either a preset H0 silently changed or cross-cosmology
    math regressed.
    """
    from app.services.cosmology import build_cosmology_from_preset
    p18 = build_cosmology_from_preset("planck18")
    riess = build_cosmology_from_preset("riess22_shoes")
    z_grid = np.array([0.1, 0.5, 1.0, 2.0])
    dl_p18 = np.asarray([p18.luminosity_distance(z).value for z in z_grid])
    dl_riess = np.asarray([riess.luminosity_distance(z).value for z in z_grid])
    delta_pct = float(np.max(np.abs(dl_riess - dl_p18) / dl_p18) * 100.0)
    return {
        "pass": 5.0 < delta_pct < 12.0,
        "max_abs_delta_pct": round(delta_pct, 3),
        "p18_H0": float(p18.H0.value),
        "riess_H0": float(riess.H0.value),
        "target": "5 < max_abs_delta_pct < 12",
    }


def bench_alcock_paczynski_omega_m() -> dict[str, Any]:
    """Alcock-Paczynski geometric test on DESI DR1 BAO → Ωm in [0.27, 0.36]."""
    from app.services.cosmology_likelihoods import run_alcock_paczynski_test
    r = run_alcock_paczynski_test()
    om = float(r["omega_m_best"])
    chi2_dof = float(r["chi2_per_dof"])
    return {
        "pass": 0.27 < om < 0.36 and chi2_dof < 5.0,
        "omega_m_best": round(om, 4),
        "chi2_per_dof": round(chi2_dof, 3),
        "n_pairs_used": int(r["n_redshift_pairs"]),
        "target": "Ωm in [0.27, 0.36] + chi2/dof < 5",
    }


def bench_chain_tier_blocked_on_inline() -> dict[str, Any]:
    """Inline rows must trigger chain_tier=blocked + __do_not_claim__=True.

    The anti-fabrication gate: even a perfectly-converged emcee chain on
    inline/unverified rows must NOT become citeable.
    """
    from app.services.cosmology_mcmc import fit_cosmology_emcee
    rng = np.random.default_rng(0)
    z = np.linspace(0.05, 1.0, 20)
    mu = 5 * np.log10(z * 3000) + 25 + rng.normal(0, 0.1, 20)
    rows = [{"z": float(zi), "mu": float(mi), "sigma_mu": 0.1}
            for zi, mi in zip(z, mu, strict=True)]
    r = fit_cosmology_emcee(rows, n_walkers=16, n_steps=200, n_burn=50)
    return {
        "pass": r["chain_tier"] == "blocked" and r.get("__do_not_claim__") is True,
        "chain_tier": r["chain_tier"],
        "do_not_claim": r.get("__do_not_claim__"),
        "target": "tier=blocked + __do_not_claim__=True on inline rows",
    }


def bench_distance_modulus_vs_astropy() -> dict[str, Any]:
    """distance_modulus_model must stay within 1e-3 mag of astropy.

    Covers flat_lcdm, flat_wcdm, flat_w0wa_cdm at z in {0.1, 0.5, 1.0, 2.3}.
    """
    from app.services.cosmology_mcmc import distance_modulus_model
    from astropy.cosmology import FlatLambdaCDM, FlatwCDM, Flatw0waCDM
    worst = 0.0
    for z_val in (0.1, 0.5, 1.0, 2.3):
        z_arr = np.array([z_val])
        worst = max(worst, abs(
            distance_modulus_model(z_arr, "flat_lcdm", {"H0": 67.4, "Om0": 0.315})[0]
            - FlatLambdaCDM(H0=67.4, Om0=0.315, Tcmb0=2.7255).distmod(z_arr).value[0]
        ))
        worst = max(worst, abs(
            distance_modulus_model(z_arr, "flat_wcdm", {"H0": 70.0, "Om0": 0.30, "w0": -0.9})[0]
            - FlatwCDM(H0=70.0, Om0=0.30, w0=-0.9, Tcmb0=2.7255).distmod(z_arr).value[0]
        ))
        worst = max(worst, abs(
            distance_modulus_model(z_arr, "flat_w0wa_cdm", {"H0": 70.0, "Om0": 0.30, "w0": -0.9, "wa": 0.1})[0]
            - Flatw0waCDM(H0=70.0, Om0=0.30, w0=-0.9, wa=0.1, Tcmb0=2.7255).distmod(z_arr).value[0]
        ))
    return {
        "pass": worst < 1e-3,
        "worst_diff_mag": round(float(worst), 6),
        "target": "max |μ_ours − μ_astropy| < 1e-3 mag",
    }


def bench_curved_neutrino_distance_vs_astropy() -> dict[str, Any]:
    """Curved + massive-neutrino distance_modulus_model vs astropy (<1e-3 mag).

    Curvature uses the exact FLRW sinn transverse distance (Hogg 1999 Eq.16,
    machine precision); massive neutrinos use the non-relativistic fold-into-
    matter approximation, valid at z≤2.3 (measured ~3e-4 mag). Guards the
    2026-05-28 M0-C kernel extension (ok_*/​*_mnu models).
    """
    from app.services.cosmology_mcmc import distance_modulus_model
    from astropy.cosmology import FlatLambdaCDM, LambdaCDM, w0waCDM
    import astropy.units as u
    worst = 0.0
    for z_val in (0.1, 0.5, 1.0, 2.3):
        z_arr = np.array([z_val])
        # Curvature (open + closed) vs astropy LambdaCDM, Tcmb0=0 pure-curvature.
        for ok0 in (-0.1, 0.1):
            worst = max(worst, abs(
                distance_modulus_model(z_arr, "ok_lcdm", {"H0": 70.0, "Om0": 0.30, "Ok0": ok0})[0]
                - LambdaCDM(H0=70.0, Om0=0.30, Ode0=1.0 - 0.30 - ok0, Tcmb0=0).distmod(z_arr).value[0]
            ))
        # Curvature + CPL dark energy.
        worst = max(worst, abs(
            distance_modulus_model(z_arr, "ok_w0wa_cdm", {"H0": 70.0, "Om0": 0.30, "Ok0": 0.05, "w0": -0.9, "wa": 0.1})[0]
            - w0waCDM(H0=70.0, Om0=0.30, Ode0=1.0 - 0.30 - 0.05, w0=-0.9, wa=0.1, Tcmb0=0).distmod(z_arr).value[0]
        ))
        # Massive neutrinos: fold-into-matter approx vs astropy m_nu (Tcmb0=2.7255).
        for mnu in (0.06, 0.3):
            worst = max(worst, abs(
                distance_modulus_model(z_arr, "lcdm_mnu", {"H0": 67.4, "Om0": 0.30, "Mnu": mnu})[0]
                - FlatLambdaCDM(H0=67.4, Om0=0.30, m_nu=[mnu / 3] * 3 * u.eV, Tcmb0=2.7255).distmod(z_arr).value[0]
            ))
    return {
        "pass": worst < 1e-3,
        "worst_diff_mag": round(float(worst), 6),
        "target": "curved (exact sinn) + neutrino (non-rel fold-in) within 1e-3 mag of astropy",
    }


def bench_planck18_preset_matches_cited() -> dict[str, Any]:
    """planck18 preset must compute distances at the cited CMB-only values.

    Regression guard for the 2026-05-28 fix: build_cosmology_from_preset
    must not silently fall back to astropy's +BAO Planck18 builtin
    (H0=67.66/Ωm=0.30966) while the manifest cites the CMB-only column
    (H0=67.36/Ωm=0.3153).
    """
    from app.services.cosmology import build_cosmology_from_preset
    p18 = build_cosmology_from_preset("planck18")
    p18_bao = build_cosmology_from_preset("planck18_bao")
    h0_p18 = float(p18.H0.value)
    om_p18 = float(p18.Om0)
    h0_bao = float(p18_bao.H0.value)
    # planck18 vs planck18_bao must give distinguishable distances; the old
    # bug collapsed both to 67.66 (no distinguishability).
    dl_diff_z1 = float(abs(p18.luminosity_distance(1.0).value
                           - p18_bao.luminosity_distance(1.0).value))
    return {
        "pass": (abs(h0_p18 - 67.36) < 0.01
                 and abs(om_p18 - 0.3153) < 0.001
                 and abs(h0_bao - 67.66) < 0.01
                 and dl_diff_z1 > 1.0),
        "planck18_H0": round(h0_p18, 4),
        "planck18_Om0": round(om_p18, 4),
        "planck18_bao_H0": round(h0_bao, 4),
        "dl_diff_z1_mpc": round(dl_diff_z1, 3),
        "target": "planck18 H0=67.36/Om0=0.3153 (CMB-only), distinguishable from planck18_bao",
    }


def bench_compressed_chain_exploratory_tier() -> dict[str, Any]:
    """ESS-floor / exploratory tier path is reachable when the importance
    sampler ESS lands in [100, 400). Confirms the three-tier gate
    (publication / exploratory / blocked) is wired and ESS thresholds fire.

    We accept either: the chain reaches publication (good — main path), OR
    exploratory tier with the warning attached. We only fail on blocked
    when inputs were claimable (a regression of the over-conservative
    blanket-PARTIAL behavior).
    """
    from app.services.cosmology_likelihoods import run_likelihood_chain
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        n_samples=2000,
        random_seed=42,
    )
    tier = r["chain_tier"]
    return {
        "pass": tier in {"publication", "exploratory"},
        "chain_tier": tier,
        "proposal_ess": float(r["chain_diagnostics"].get("proposal_ess") or 0.0),
        "target": "BAO-only single-cell deep run reaches publication or exploratory (not blocked)",
    }


def bench_dataset_z_coverage() -> dict[str, Any]:
    """Pin each registered dataset's declared redshift coverage (M0-F).

    z_coverage is the deterministic backend fact that load_cosmology_data_product
    / list_cosmology_datasets surface so that "report X at z=N" beyond a dataset's
    range is recognisable as ΛCDM extrapolation, not a data constraint (blind-test
    C2). This is the LLM-independent regression guard: if a registry edit moves
    Pantheon+'s ceiling off z=2.26, or drops a coverage that the C2 anchor depends
    on, CI goes red here — not silently in a daily blind run.
    """
    from app.services.cosmology_likelihoods import get_cosmology_dataset

    expected = {
        "pantheon_plus": (0.001, 2.26),
        "des_sn5yr": (0.025, 1.13),
        "union3": (0.01, 2.26),
        "desi_dr1_bao": (0.295, 2.33),
        "sdss_6df_bao": (0.106, 2.33),
        "eboss_dr16_rsd": (0.15, 2.33),
        "cosmic_chronometers": (0.07, 1.965),
    }
    # Probes with no discrete-z coverage interval MUST stay None (H0 priors,
    # CMB primary/compressed). Guards against accidentally faking a range.
    none_keys = ("planck2018_compressed", "shoes_h0_riess22", "spt3g_cmb")
    mismatches: list[dict[str, Any]] = []
    for key, want in expected.items():
        got = get_cosmology_dataset(key).z_coverage
        if got is None or abs(got[0] - want[0]) > 1e-9 or abs(got[1] - want[1]) > 1e-9:
            mismatches.append({"key": key, "want": list(want), "got": list(got) if got else None})
    for key in none_keys:
        got = get_cosmology_dataset(key).z_coverage
        if got is not None:
            mismatches.append({"key": key, "want": None, "got": list(got)})
    return {
        "pass": not mismatches,
        "n_pinned": len(expected) + len(none_keys),
        "pantheon_plus_z_max": get_cosmology_dataset("pantheon_plus").z_coverage[1],
        "mismatches": mismatches,
        "target": "registered z_coverage matches pinned survey ranges; non-z probes stay None",
    }


BENCHMARKS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("lcdm_h0_anchor", bench_lcdm_h0_anchor),
    ("wcdm_w_near_minus_one", bench_wcdm_w_near_minus_one),
    ("hubble_tension_planck18_vs_riess22", bench_hubble_tension_planck18_vs_riess22),
    ("alcock_paczynski_omega_m", bench_alcock_paczynski_omega_m),
    ("chain_tier_blocked_on_inline", bench_chain_tier_blocked_on_inline),
    ("distance_modulus_vs_astropy", bench_distance_modulus_vs_astropy),
    ("curved_neutrino_distance_vs_astropy", bench_curved_neutrino_distance_vs_astropy),
    ("planck18_preset_matches_cited", bench_planck18_preset_matches_cited),
    ("compressed_chain_exploratory_tier", bench_compressed_chain_exploratory_tier),
    ("dataset_z_coverage", bench_dataset_z_coverage),
]


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=str, default=None,
                    help="Also write the full result JSON to this path.")
    ap.add_argument("--name", type=str, default=None,
                    help="Run only the named benchmark (for debugging).")
    args = ap.parse_args()

    results: dict[str, Any] = {}
    for name, fn in BENCHMARKS:
        if args.name and name != args.name:
            continue
        try:
            results[name] = fn()
        except Exception as exc:
            results[name] = {
                "pass": False,
                "error": str(exc),
                "error_class": exc.__class__.__name__,
                "traceback": traceback.format_exc(limit=4),
            }

    payload = {
        "suite": "cosmology_benchmarks",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_benchmarks": len(results),
        "n_pass": sum(1 for r in results.values() if r.get("pass")),
        "n_fail": sum(1 for r in results.values() if not r.get("pass")),
        "results": results,
    }
    print(json.dumps(payload, indent=2, default=float))
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(payload, fp, indent=2, default=float)

    all_pass = all(r.get("pass") for r in results.values())
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
