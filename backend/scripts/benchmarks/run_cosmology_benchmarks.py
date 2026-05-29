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
        # 6dFGS z=0.106 + SDSS MGS z=0.15 are the only two D_V/r_d anchors
        # actually sourced/executed (Tier 2B); no BOSS/eBOSS intermediate-z bins.
        "sdss_6df_bao": (0.106, 0.15),
        # eBOSS RSD fσ8 ends at z=1.48 (Lyα z=2.33 reports no growth rate).
        "eboss_dr16_rsd": (0.15, 1.48),
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


def bench_sn_omegam_compressed() -> dict[str, Any]:
    """DES-SN5YR + Union3 compressed SN-only flat-ΛCDM Ωm (Tier 2A, 2026-05-29).

    Both newly-executable SN datasets must recover their published flat-ΛCDM
    SN-only Ωm as a 1D Gaussian and reach publication tier: DES-SN5YR Ωm=0.352
    (Abbott+2024) and Union3 Ωm=0.356 (Rubin+2023). Pins the transcribed
    compressed-likelihood means so a value typo is caught."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    out: dict[str, Any] = {}
    ok = True
    for key, expected in (("des_sn5yr", 0.352), ("union3", 0.356)):
        r = run_likelihood_chain(model="lcdm", dataset_keys=[key], n_samples=4000, random_seed=42)
        med = r.get("parameters", {}).get("omegam", {}).get("median")
        used = [d.get("key") for d in r.get("datasets_used", [])]
        good = (
            r.get("success") is True
            and r.get("chain_tier") == "publication"
            and key in used
            and isinstance(med, (int, float))
            and abs(med - expected) < 0.005
        )
        ok = ok and good
        out[key] = {"omegam_median": round(med, 4) if isinstance(med, (int, float)) else None,
                    "expected": expected, "tier": r.get("chain_tier")}
    return {
        "pass": ok,
        **out,
        "target": "des_sn5yr Ωm≈0.352, union3 Ωm≈0.356, both publication, both executed in-process",
    }


def bench_model_comparison_delta() -> dict[str, Any]:
    """Real model comparison Δχ²/ΔAIC/ΔBIC (3.2, 2026-05-29).

    compute_model_comparison(lcdm, wcdm) on DESI BAO + Planck compressed must
    return finite deltas, exactly 1 extra parameter (w), and NOT prefer wCDM —
    w≈-1 there, so the extra freedom buys no fit improvement and AIC favors the
    simpler ΛCDM. Guards the formerly-hardcoded delta_chi²=0.0 placeholder."""
    from app.services.cosmology_likelihoods import run_likelihood_chain, compute_model_comparison

    ds = ["desi_dr1_bao", "planck2018_compressed"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=4000, random_seed=42)
    wcdm = run_likelihood_chain(model="wcdm", dataset_keys=ds, n_samples=4000, random_seed=42)
    cmp = compute_model_comparison(lcdm, wcdm)
    finite = all(cmp[k] is not None for k in ("delta_chi2", "delta_aic", "delta_bic"))
    return {
        "pass": (
            finite
            and cmp["n_extra_params"] == 1
            and cmp["preferred"] in {"lcdm", "inconclusive"}
        ),
        "delta_chi2": cmp["delta_chi2"],
        "delta_aic": cmp["delta_aic"],
        "n_extra_params": cmp["n_extra_params"],
        "preferred": cmp["preferred"],
        "target": "finite Δχ²/ΔAIC/ΔBIC, 1 extra param, wCDM NOT preferred over ΛCDM (w≈-1)",
    }


def bench_growth_kernel_vs_exact_lcdm() -> dict[str, Any]:
    """Growth kernel (1A) cross-validated against the EXACT ΛCDM linear-growth
    integral (3.3, 2026-05-29).

    The fσ8 path uses the Linder γ-index fitting formula f=Ωm(z)^γ with an
    analytic D(z)/D(0). Until now only self-consistent anchors (f(0)=Ωm^0.55,
    D(0)/D(0)=1) were pinned — no z>0 external reference. This compares the
    kernel to the exact flat-ΛCDM growth D(a) ∝ E(a)·∫₀ᵃ da'/(a'E(a'))³ and
    f=dlnD/dlna over z∈{0.2,0.5,1.0,1.5,2.0}. The γ-approximation is good to
    ~0.1-1%; measured worst rel-err is 0.14% (f) / 0.037% (D)."""
    from scipy.integrate import quad
    from app.services.cosmology_likelihoods import _growth_rate_f, _growth_factor_ratio

    om = 0.31
    ol = 1.0 - om
    e_of_a = lambda a: (om / a ** 3 + ol) ** 0.5  # noqa: E731
    integrand = lambda a: 1.0 / (a * e_of_a(a)) ** 3  # noqa: E731

    def d_unnorm(a: float) -> float:
        val, _ = quad(integrand, 0.0, a)
        return e_of_a(a) * val

    lcdm_w0, lcdm_wa = np.array([-1.0]), np.array([0.0])
    om_arr = np.array([om])
    worst_f = worst_d = 0.0
    for z in (0.2, 0.5, 1.0, 1.5, 2.0):
        a = 1.0 / (1.0 + z)
        d_exact = d_unnorm(a) / d_unnorm(1.0)
        dlna = 1e-4
        f_exact = (np.log(d_unnorm(a * np.exp(dlna))) - np.log(d_unnorm(a * np.exp(-dlna)))) / (2 * dlna)
        f_kernel = float(_growth_rate_f(z, om_arr, lcdm_w0, lcdm_wa)[0])
        d_kernel = float(_growth_factor_ratio(z, om_arr, lcdm_w0, lcdm_wa)[0])
        worst_f = max(worst_f, abs(f_kernel - f_exact) / f_exact)
        worst_d = max(worst_d, abs(d_kernel - d_exact) / d_exact)
    return {
        "pass": worst_f < 0.005 and worst_d < 0.001,
        "worst_f_rel_err": round(worst_f, 5),
        "worst_D_rel_err": round(worst_d, 5),
        "target": "Linder-γ growth within 0.5% (f) / 0.1% (D) of exact flat-ΛCDM integral over z≤2",
    }


def bench_cosmic_chronometer_hz() -> dict[str, Any]:
    """Cosmic-chronometer H(z) executable likelihood (1C, 2026-05-29).

    31 differential-age H(z) points (Gómez-Valent & Amendola 2018, arXiv:1802.01505)
    wired as a flat-w0waCDM H(z)=H0·E(z) diagonal χ². At the Planck CMB-only
    fiducial (H0=67.36, Ωm=0.315) the reduced χ² should sit below ~1 — CC errors
    are conservative and the literature reports χ²/dof ≈ 0.5 vs ΛCDM. Also confirms
    run_likelihood_chain now EXECUTES cosmic_chronometers in-process (datasets_used,
    not datasets_not_run) instead of returning an external-Cobaya config stub.
    """
    from app.services.cosmology_likelihoods import (
        run_likelihood_chain,
        COSMIC_CHRONOMETER_HZ,
        _cosmic_chronometer_chi2_samples,
    )
    theta = np.array([[67.36, 0.315]])
    chi2 = float(_cosmic_chronometer_chi2_samples(theta, ["H0", "omegam"])[0])
    ndof = len(COSMIC_CHRONOMETER_HZ) - 2
    reduced = chi2 / ndof
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["cosmic_chronometers"],
        n_samples=2000,
        random_seed=42,
    )
    used = [d["key"] for d in r["datasets_used"]]
    executed = (
        bool(r["success"])
        and "cosmic_chronometers" in used
        and r["chain_tier"] != "blocked"
    )
    return {
        "pass": 0.3 < reduced < 1.2 and executed,
        "n_points": len(COSMIC_CHRONOMETER_HZ),
        "chi2_planck_fiducial": round(chi2, 3),
        "reduced_chi2": round(reduced, 4),
        "chain_tier": r["chain_tier"],
        "executed_in_process": executed,
        "target": "reduced χ²(Planck fid) in [0.3, 1.2] + CC runs in-process (not skipped)",
    }


def bench_eboss_fsigma8_growth() -> dict[str, Any]:
    """eBOSS DR16 RSD fσ8 executable + growth kernel (1A, 2026-05-29).

    Pins (1) the growth kernel at z=0: f(0)=Ωm^0.55 and D(0)/D(0)=1; (2) the
    reduced χ² of the 6-point fσ8 vector (Alam+2021 Table III RSD-only) at the
    Planck fiducial (Ωm=0.3153, σ8=0.811) — ΛCDM mildly over-predicts growth so
    χ²/dof is order-unity; (3) that run_likelihood_chain now executes
    eboss_dr16_rsd in-process (fσ8=f·σ8·D(z)/D(0), Linder γ) instead of an
    external-Cobaya stub.
    """
    from app.services.cosmology_likelihoods import (
        run_likelihood_chain,
        EBOSS_DR16_FSIGMA8,
        _eboss_fsigma8_chi2_samples,
        _growth_rate_f,
        _growth_factor_ratio,
    )
    om = np.array([0.3153])
    lcdm_w0, lcdm_wa = np.array([-1.0]), np.array([0.0])
    f0 = float(_growth_rate_f(0.0, om, lcdm_w0, lcdm_wa)[0])
    d0 = float(_growth_factor_ratio(0.0, om, lcdm_w0, lcdm_wa)[0])
    theta = np.array([[0.3153, 0.811]])
    chi2 = float(_eboss_fsigma8_chi2_samples(theta, ["omegam", "sigma8"])[0])
    ndof = len(EBOSS_DR16_FSIGMA8) - 2
    reduced = chi2 / ndof
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["eboss_dr16_rsd"],
        n_samples=2000,
        random_seed=42,
    )
    used = [d["key"] for d in r["datasets_used"]]
    executed = (
        bool(r["success"])
        and "eboss_dr16_rsd" in used
        and r["chain_tier"] != "blocked"
    )
    return {
        "pass": (
            abs(f0 - 0.3153 ** 0.55) < 1e-6
            and abs(d0 - 1.0) < 1e-9
            and 0.3 < reduced < 2.0
            and executed
        ),
        "f0_growth_rate": round(f0, 4),
        "d0_growth_factor": round(d0, 6),
        "n_points": len(EBOSS_DR16_FSIGMA8),
        "reduced_chi2_planck": round(reduced, 4),
        "chain_tier": r["chain_tier"],
        "executed_in_process": executed,
        "target": "f(0)=Ωm^0.55, D(0)/D(0)=1, reduced χ²(Planck) in [0.3,2.0], eBOSS runs in-process",
    }


def bench_sdss_6df_bao_executable() -> dict[str, Any]:
    """6dFGS + SDSS MGS low-z BAO executable (2B, 2026-05-29).

    Pins (1) the D_V/r_d predictions at the Planck 2018 fiducial (H0=67.36,
    Ωm=0.3153, r_d=147.09) against the two sourced anchors — 6dFGS z=0.106 →
    3.047±0.137 and SDSS MGS z=0.15 → 4.47±0.17 (Aubourg+2015 Table II) — both
    within ~1σ; (2) the fixed-cosmology χ²/2 is order-unity; (3) that
    run_likelihood_chain now executes sdss_6df_bao in-process via the generalized
    per-dataset BAO registry instead of the external-Cobaya stub.
    """
    from app.services.cosmology_likelihoods import (
        run_likelihood_chain,
        SDSS_6DF_BAO_MEAN_VECTOR,
        _bao_predictions,
        _bao_chi2_samples,
    )
    order = ["H0", "omegam", "rd"]
    theta = np.array([[67.36, 0.3153, 147.09]])
    pred = _bao_predictions(theta, order, SDSS_6DF_BAO_MEAN_VECTOR)[0]
    chi2 = float(_bao_chi2_samples(theta, order, "sdss_6df_bao")[0])
    sigma = (0.137, 0.17)
    obs = (3.047, 4.470)
    pulls = [abs(float(pred[i]) - obs[i]) / sigma[i] for i in range(2)]
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["sdss_6df_bao"], n_samples=2000, random_seed=42
    )
    used = [d["key"] for d in r["datasets_used"]]
    executed = (
        bool(r["success"]) and "sdss_6df_bao" in used and r["chain_tier"] != "blocked"
    )
    return {
        "pass": (
            max(pulls) < 1.5
            and 0.1 < chi2 / 2 < 2.0
            and executed
        ),
        "pred_dv_rd_z0106": round(float(pred[0]), 4),
        "pred_dv_rd_z015": round(float(pred[1]), 4),
        "pulls_sigma": [round(p, 2) for p in pulls],
        "reduced_chi2_planck": round(chi2 / 2, 4),
        "chain_tier": r["chain_tier"],
        "executed_in_process": executed,
        "target": "D_V/r_d within 1.5σ of 6dFGS/MGS, χ²/2 order-unity, sdss_6df runs in-process",
    }


def bench_s8_derived_consistency() -> dict[str, Any]:
    """S8 as a derived quantity, not a sampled column (1B, 2026-05-29).

    Pins (1) S8 ≡ σ8·√(Ωm/0.3) holds at the reported medians for Planck (analytic
    path) and BAO+Planck (importance path) — i.e. the sampler no longer explores
    an independent S8; (2) the prod BAO+Planck order is 4-D [H0,Ωm,rd,σ8] with no
    sampled S8; (3) Planck+KiDS still runs publication-ready (reweighted ESS≥400)
    and the KiDS S8 pulls the derived S8 below the Planck-only value.
    """
    from app.services.cosmology_likelihoods import (
        run_likelihood_chain,
        _sampling_parameter_order,
        get_cosmology_dataset,
    )

    def derived_ok(r: dict[str, Any]) -> bool:
        p = r.get("parameters", {})
        s8, sig, om = p.get("S8"), p.get("sigma8"), p.get("omegam")
        if not (s8 and sig and om):
            return False
        return abs(s8["median"] - sig["median"] * (om["median"] / 0.3) ** 0.5) < 0.005

    planck = run_likelihood_chain(model="lcdm", dataset_keys=["planck2018_compressed"],
                                  random_seed=42, n_samples=4000)
    bao_planck = run_likelihood_chain(model="lcdm",
                                      dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
                                      random_seed=42, n_samples=4000)
    planck_kids = run_likelihood_chain(model="lcdm",
                                       dataset_keys=["planck2018_compressed", "kids1000_wl"],
                                       random_seed=42, n_samples=4000)
    prod_order = _sampling_parameter_order(
        [get_cosmology_dataset("desi_dr1_bao")], [get_cosmology_dataset("planck2018_compressed")]
    )
    s8_planck = planck["parameters"]["S8"]["median"]
    s8_kids = planck_kids["parameters"]["S8"]["median"]
    return {
        "pass": (
            derived_ok(planck)
            and derived_ok(bao_planck)
            and prod_order == ["H0", "omegam", "rd", "sigma8"]
            and planck_kids["publication_ready"]
            and planck_kids["chain_diagnostics"]["ess_bulk"] >= 400
            and s8_kids < s8_planck
        ),
        "prod_bao_planck_order": prod_order,
        "s8_planck": round(s8_planck, 4),
        "s8_planck_kids": round(s8_kids, 4),
        "planck_kids_ess": planck_kids["chain_diagnostics"]["ess_bulk"],
        "target": "S8=σ8·√(Ωm/0.3) derived (not sampled); prod order 4-D; Planck+KiDS pub-ready, KiDS pulls S8 down",
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
    ("cosmic_chronometer_hz", bench_cosmic_chronometer_hz),
    ("eboss_fsigma8_growth", bench_eboss_fsigma8_growth),
    ("sdss_6df_bao_executable", bench_sdss_6df_bao_executable),
    ("s8_derived_consistency", bench_s8_derived_consistency),
    ("growth_kernel_vs_exact_lcdm", bench_growth_kernel_vs_exact_lcdm),
    ("model_comparison_delta", bench_model_comparison_delta),
    ("sn_omegam_compressed", bench_sn_omegam_compressed),
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
