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
import math
import pathlib
import sys
import traceback
from typing import Any, Callable

# Make `app.*` importable when this script is invoked from anywhere
# (CI runners, local cwd, the cosmology-smoke skill, etc.).
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import numpy as np  # noqa: E402 -- path bootstrap above is intentional for CLI use


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark functions
# ─────────────────────────────────────────────────────────────────────────────


def _preliminary_chain_gate_ok(result: dict[str, Any]) -> bool:
    """True only when a useful chain is correctly withheld from publication."""
    return bool(
        result.get("publication_ready") is False
        and result.get("preliminary_ready") is True
        and result.get("chain_tier") == "exploratory"
    )


def _preliminary_benchmark_result(
    *,
    numerical_pass: bool,
    chain_result: dict[str, Any],
    target: str,
    **details: Any,
) -> dict[str, Any]:
    """Separate numerical-regression success from scientific publication.

    ``pass`` means the benchmark contract passed: the number stayed in its
    physical window *and* the platform correctly labelled the importance/single-
    ensemble result preliminary.  It never means a publication validation.
    """
    gate_ok = _preliminary_chain_gate_ok(chain_result)
    return {
        "pass": bool(numerical_pass and gate_ok),
        "numerical_regression_pass": bool(numerical_pass),
        "publication_gate_correct": gate_ok,
        "scientific_publication_pass": False,
        "validation_scope": "preliminary_numerical_regression",
        "publication_ready": bool(chain_result.get("publication_ready")),
        "preliminary_ready": bool(chain_result.get("preliminary_ready")),
        "chain_tier": chain_result.get("chain_tier"),
        "publication_gate_reasons": list(chain_result.get("preliminary_reasons") or []),
        "target": target,
        **details,
    }


def _preliminary_multi_benchmark_result(
    *,
    numerical_pass: bool,
    chain_results: dict[str, dict[str, Any]],
    target: str,
    **details: Any,
) -> dict[str, Any]:
    """Apply the preliminary-only contract to every chain in a benchmark."""
    gate_by_chain = {
        name: _preliminary_chain_gate_ok(result)
        for name, result in chain_results.items()
    }
    gate_ok = bool(gate_by_chain) and all(gate_by_chain.values())
    return {
        "pass": bool(numerical_pass and gate_ok),
        "numerical_regression_pass": bool(numerical_pass),
        "publication_gate_correct": gate_ok,
        "publication_gate_by_chain": gate_by_chain,
        "scientific_publication_pass": False,
        "validation_scope": "preliminary_numerical_regression",
        "chain_tiers": {
            name: result.get("chain_tier")
            for name, result in chain_results.items()
        },
        "publication_gate_reasons": {
            name: list(result.get("preliminary_reasons") or [])
            for name, result in chain_results.items()
        },
        "target": target,
        **details,
    }


def bench_lcdm_h0_anchor() -> dict[str, Any]:
    """ΛCDM BAO+CMB → H0 anchor.

    DESI DR1 BAO + Planck 2018 compressed under flat ΛCDM should recover
    H0 = 67.4 ± 0.5 km/s/Mpc (the Planck-anchored result), with ESS ≥ 400.
    The importance sample is a preliminary numerical regression only.
    """
    from app.services.cosmology_likelihoods import run_likelihood_chain
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=4000,
        random_seed=42,
    )
    h0 = float(r["parameters"]["H0"]["median"])
    ess = float(r["chain_diagnostics"].get("proposal_ess") or 0.0)
    return _preliminary_benchmark_result(
        numerical_pass=66.5 < h0 < 68.5 and ess >= 400.0,
        chain_result=r,
        h0_median=round(h0, 4),
        proposal_ess=round(ess, 1),
        target="H0 in [66.5, 68.5] + ESS >= 400; chain must remain preliminary",
    )


def bench_wcdm_w_near_minus_one() -> dict[str, Any]:
    """Diagnostic-only wCDM BAO+CMB check.

    The in-process extended-DE result is deliberately off-anchor and therefore
    cannot become a passed scientific benchmark.  A numerically implausible
    value is still a hard regression failure; an in-range chain passes only
    when it is explicitly labelled preliminary and non-publication.
    """
    from app.services.cosmology_likelihoods import run_likelihood_chain
    r = run_likelihood_chain(
        model="wcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=4000,
        random_seed=42,
        allow_emcee_fallback=True,
    )
    w = float(r["parameters"].get("w", {}).get("median") or float("nan"))
    numeric_ok = bool(math.isfinite(w) and -1.5 < w < -0.5)
    return _preliminary_benchmark_result(
        numerical_pass=numeric_ok,
        chain_result=r,
        target="w in [-1.5, -0.5]; numerical sanity only, never publication",
        w_median=round(w, 4),
        reason="off-anchor extended-DE result is a preliminary numerical regression",
    )


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
    """AP numerical regression must remain a diagnostic-only constraint."""
    from app.services.cosmology_likelihoods import run_alcock_paczynski_test
    r = run_alcock_paczynski_test()
    om = float(r["omega_m_best"])
    chi2_dof = float(r["chi2_per_dof"])
    numerical_pass = 0.27 < om < 0.36 and chi2_dof < 5.0
    gate_ok = bool(
        r.get("publication_ready") is False
        and r.get("preliminary_ready") is True
        and r.get("__do_not_claim__") is True
    )
    return {
        "pass": bool(numerical_pass and gate_ok),
        "numerical_regression_pass": numerical_pass,
        "publication_gate_correct": gate_ok,
        "scientific_publication_pass": False,
        "validation_scope": "diagnostic_numerical_regression",
        "publication_ready": bool(r.get("publication_ready")),
        "preliminary_ready": bool(r.get("preliminary_ready")),
        "omega_m_best": round(om, 4),
        "chi2_per_dof": round(chi2_dof, 3),
        "n_pairs_used": int(r["n_redshift_pairs"]),
        "target": "Ωm in [0.27, 0.36] + chi2/dof < 5; AP remains diagnostic-only",
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
    gate_ok = (
        r["chain_tier"] == "blocked"
        and r.get("__do_not_claim__") is True
        and r.get("publication_ready") is False
    )
    return {
        "pass": gate_ok,
        "numerical_regression_pass": None,
        "publication_gate_correct": gate_ok,
        "scientific_publication_pass": False,
        "validation_scope": "safety_gate_regression",
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
    """The importance sampler can validate numbers, never publication status."""
    from app.services.cosmology_likelihoods import run_likelihood_chain
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao"],
        n_samples=2000,
        random_seed=42,
    )
    ess = float(r["chain_diagnostics"].get("proposal_ess") or 0.0)
    return _preliminary_benchmark_result(
        numerical_pass=bool(r.get("success")) and ess >= 100.0,
        chain_result=r,
        proposal_ess=ess,
        target="BAO-only importance run has usable ESS and remains preliminary",
    )


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
    """Union3 full-vector SN inference plus DES posterior-summary refusal.

    Union3 must recover its published flat-ΛCDM SN-only Ωm through the released
    22-bin likelihood. DES-SN5YR's registered Ωm row is a posterior summary,
    not a likelihood; the runner must keep it context-only instead of recreating
    a pseudo-posterior from the paper's final number.
    """
    from app.services.cosmology_likelihoods import (
        get_cosmology_dataset,
        run_likelihood_chain,
    )

    out: dict[str, Any] = {}
    des = run_likelihood_chain(
        model="lcdm", dataset_keys=["des_sn5yr"], n_samples=4000, random_seed=42
    )
    des_role = get_cosmology_dataset("des_sn5yr").compressed_likelihood
    des_refused = bool(
        des_role is not None
        and des_role.statistical_role == "published_posterior_summary"
        and des.get("analysis_status") == "NO_COMPRESSED_LIKELIHOOD"
        and des.get("datasets_used") == []
        and des.get("__do_not_claim__") is True
    )
    union = run_likelihood_chain(
        model="lcdm", dataset_keys=["union3"], n_samples=4000, random_seed=42
    )
    med = union.get("parameters", {}).get("omegam", {}).get("median")
    union_ok = bool(
        "union3" in [d.get("key") for d in union.get("datasets_used", [])]
        and isinstance(med, (int, float))
        and abs(med - 0.356) < 0.005
    )
    out["des_sn5yr"] = {
        "statistical_role": des_role.statistical_role if des_role else None,
        "analysis_status": des.get("analysis_status"),
        "context_only_refusal": des_refused,
    }
    out["union3"] = {
        "omegam_median": round(med, 4) if isinstance(med, (int, float)) else None,
        "expected": 0.356,
        "tier": union.get("chain_tier"),
    }
    return _preliminary_multi_benchmark_result(
        numerical_pass=des_refused and union_ok,
        chain_results={"union3": union},
        target="DES-SN5YR posterior summary stays context-only; Union3 full vector recovers Ωm≈0.356",
        **out,
    )


def bench_sn_compressed_provenance() -> dict[str, Any]:
    """T1-U8a: SN-only chains certify honest provenance (2026-06-01; union3
    full-vector 2026-06-12).

    Pantheon+/DES-SN paper posterior rows must yield no numerical chain or fake
    likelihood provenance. Union3 runs the released 22-bin vector and must
    certify full fidelity with the verified covariance digest."""
    from app.services.cosmology_likelihoods import (
        load_verified_union3_data,
        run_likelihood_chain,
    )

    out: dict[str, Any] = {}
    chains: dict[str, dict[str, Any]] = {}
    context_ok = True
    for key in ("pantheon_plus", "des_sn5yr"):
        r = run_likelihood_chain(model="lcdm", dataset_keys=[key], n_samples=4000, random_seed=42)
        chains[key] = r
        prov = r.get("provenance", {}).get("cosmology_likelihood", {})
        fid = prov.get("cov_fidelity")
        refused = bool(
            r.get("analysis_status") == "NO_COMPRESSED_LIKELIHOOD"
            and r.get("datasets_used") == []
            and r.get("__do_not_claim__") is True
            and not prov.get("datasets_used")
        )
        context_ok = context_ok and refused
        out[key] = {
            "cov_fidelity": fid,
            "publication_ready": r.get("publication_ready"),
            "preliminary_ready": r.get("preliminary_ready"),
            "context_only_refusal": refused,
        }
    r = run_likelihood_chain(model="lcdm", dataset_keys=["union3"], n_samples=4000, random_seed=42)
    chains["union3"] = r
    prov = r.get("provenance", {}).get("cosmology_likelihood", {})
    sha_ok = (prov.get("artifact_sha256") or {}).get("union3") == load_verified_union3_data("union3")["sha256"]
    provenance_ok = prov.get("cov_fidelity") == "full" and sha_ok
    out["union3"] = {
        "cov_fidelity": prov.get("cov_fidelity"),
        "artifact_sha256_match": sha_ok,
        "publication_ready": r.get("publication_ready"),
        "preliminary_ready": r.get("preliminary_ready"),
    }
    gate_by_chain = {"union3": _preliminary_chain_gate_ok(r)}
    gate_ok = gate_by_chain["union3"]
    return {
        "pass": bool(context_ok and provenance_ok and gate_ok),
        "numerical_regression_pass": None,
        "provenance_regression_pass": bool(provenance_ok),
        "publication_gate_correct": gate_ok,
        "publication_gate_by_chain": gate_by_chain,
        "scientific_publication_pass": False,
        "validation_scope": "preliminary_provenance_regression",
        **out,
        "target": "Pantheon+/DES posterior summaries stay context-only; Union3 certifies full + sha256 and remains preliminary",
    }


def bench_pantheon_full_cov_fidelity() -> dict[str, Any]:
    """T1-U8b: the official 1657-row selection certifies a verified full covariance.

    The release contains 1701 rows; the likelihood applies the official
    ``(zHD > 0.01) OR IS_CALIBRATOR`` selection before fitting 1657 rows.
    SLOW OPT-IN — the full Pantheon+SH0ES χ² fit is minutes long, far past the chat
    deadline, so it is skipped unless PANTHEON_PLUS_FULL_CHI2_ENABLED is set
    (run it locally or on a background worker). When enabled, the pantheon_plus
    chain must stamp cov_fidelity='full' with the verified npz digest in
    artifact_sha256 and remain preliminary because it is one coupled ensemble."""
    from app.services.cosmology_likelihoods import (
        PANTHEON_PLUS_FULL_CHI2_ENABLED,
        load_verified_pantheon_plus_data,
        run_likelihood_chain,
    )

    if not PANTHEON_PLUS_FULL_CHI2_ENABLED:
        return {
            "pass": None,
            "status": "skipped",
            "skipped": "needs PANTHEON_PLUS_FULL_CHI2_ENABLED (slow 1657-selected-row full-covariance fit)",
            "target": "full SN path certifies cov_fidelity='full' with verified npz sha256",
        }
    expected_sha = load_verified_pantheon_plus_data("pantheon_plus")["sha256"]
    r = run_likelihood_chain(model="lcdm", dataset_keys=["pantheon_plus"], n_samples=2000, random_seed=42)
    prov = r.get("provenance", {}).get("cosmology_likelihood", {})
    fid = prov.get("cov_fidelity")
    sha = (prov.get("artifact_sha256") or {}).get("pantheon_plus")
    return _preliminary_benchmark_result(
        numerical_pass=fid == "full" and sha == expected_sha,
        chain_result=r,
        cov_fidelity=fid,
        artifact_sha256_match=sha == expected_sha,
        target="full SN path certifies full covariance + sha256 and remains preliminary",
    )


def bench_w0wa_full_sn_w0_tight() -> dict[str, Any]:
    """Dark-energy frontier: with the full 1657-selected-row covariance (not the compressed
    3-number SN summary), the w0waCDM DESI+SN+CMB fit tightens w0 to ~DESI's
    precision (σ_w0 ≈ 0.07 measured, vs ≈ 0.15 from the compressed summary; DESI
    2024 VI reports 0.063). This confirms the constraint gap is DATA COMPRESSION,
    not the sampler — the emcee chain already converges either way.

    SLOW OPT-IN (~5 min on an M4 Pro; FREE, runs locally / in GitHub Actions, not
    the 45s chat path) — skipped unless PANTHEON_PLUS_FULL_CHI2_ENABLED is set."""
    from app.services.cosmology_likelihoods import (
        PANTHEON_PLUS_FULL_CHI2_ENABLED,
        run_likelihood_chain,
    )

    if not PANTHEON_PLUS_FULL_CHI2_ENABLED:
        return {
            "pass": None,
            "status": "skipped",
            "skipped": "needs PANTHEON_PLUS_FULL_CHI2_ENABLED (~5 min full 1657-selected-row w0wa fit; local/Actions)",
            "target": "full-SN w0waCDM tightens σ_w0 to ≤ 0.09 (~DESI 0.063); stays off-anchor exploratory",
        }
    r = run_likelihood_chain(
        model="w0wa_cdm",
        dataset_keys=["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"],
        n_samples=4000, random_seed=42, allow_emcee_fallback=True,
    )
    w0 = r.get("parameters", {}).get("w0", {})
    sig = w0.get("std")
    numeric_ok = (
        isinstance(sig, (int, float))
        and sig <= 0.09
        and r.get("off_anchor_review_required") is True
    )
    return _preliminary_benchmark_result(
        # The benchmark's point is the DATA-COMPRESSION claim: the full 1701-SN
        # covariance tightens σ_w0 to ~DESI precision.  w0waCDM is off-anchor, so by
        # the safety contract it MUST stay preliminary and never publication.
        numerical_pass=numeric_ok,
        chain_result=r,
        w0_median=w0.get("median"),
        w0_sigma=sig,
        off_anchor_review_required=r.get("off_anchor_review_required"),
        target="full-SN w0waCDM tightens σ_w0 to ≤ 0.09 and stays preliminary",
    )


def bench_oracle_genuine_reproductions() -> dict[str, Any]:
    """T1-U16: every GENUINE oracle anchor reproduces its published value.

    Runs the harness over the independent (parameter recovery: DESI-only Ωm,
    DESI+CMB Ωm) and fit_quality (reduced χ²: CC, eBOSS) anchors and asserts each
    lands within tolerance.  The compressed self-consistency anchors are excluded
    — they trivially recover their own input and are not a correctness check."""
    from app.services.cosmology_oracle import PUBLISHED_ANCHORS, reproduce_anchor

    out: dict[str, Any] = {}
    reproduced: dict[str, dict[str, Any]] = {}
    numerical_ok = True
    for a in PUBLISHED_ANCHORS:
        if a.independence == "consistency":
            continue
        r = reproduce_anchor(a)
        reproduced[a.goal_key] = r
        numerical_ok = numerical_ok and bool(r["within_tol"])
        val = r["reproduced_value"]
        out[a.goal_key] = {
            "kind": a.independence,
            "reproduced": round(val, 4) if isinstance(val, (int, float)) else None,
            "published": a.value,
            "within_tol": r["within_tol"],
            "publication_ready": r["publication_ready"],
            "preliminary_ready": r["preliminary_ready"],
        }
    return _preliminary_multi_benchmark_result(
        numerical_pass=numerical_ok,
        chain_results=reproduced,
        target="every oracle anchor reproduces within tolerance and remains preliminary",
        **out,
    )


def bench_model_comparison_delta() -> dict[str, Any]:
    """Real model comparison Δχ²/ΔAIC/ΔBIC (3.2, 2026-05-29; dataset switched
    2026-06-11; pair switched to non-blocked chains 2026-06-12).

    compute_model_comparison(lcdm, wcdm) on a model-invariant likelihood must
    return finite diagnostic deltas and exactly 1 extra parameter (w), while
    withholding preference because these runners only report the minimum chi2
    encountered among posterior draws.  That point is not an independently
    optimised, converged likelihood-only MLE.  Guards both the formerly-
    hardcoded delta_chi²=0.0 placeholder and the later AIC-overclaim regression.

    planck2018_compressed note (contract changed 2026-07-07): every FLAT
    model now executes the same CHW2019 correlated (R, l_A, ombh2, ns)
    distance priors without posterior-summary growth rows, so an lcdm-vs-wcdm pair on it IS
    comparison_valid now. The representation-mismatch invalidity case is
    covered by synthetic-mismatch fixtures in
    tests/test_model_comparison_validity.py; this benchmark keeps the
    BAO+CC pair as an independent same-likelihood check.

    DESI + cosmic chronometers with the emcee upgrade (2026-06-12): the
    chain-tier validity guard now also refuses blocked inputs, and a wcdm
    importance fit on DESI-only collapses to ESS ~50 — the old version of this
    benchmark was reading its verdict off exactly that one-effective-sample
    chain. Both chains take allow_emcee_fallback=True so the pair is genuinely
    non-blocked. All invalidity cases are locked by
    tests/test_model_comparison_validity.py; the benchmark pins the honest
    diagnostic-only same-likelihood comparison."""
    from app.services.cosmology_likelihoods import run_likelihood_chain, compute_model_comparison

    ds = ["desi_dr1_bao", "cosmic_chronometers"]
    lcdm = run_likelihood_chain(
        model="lcdm", dataset_keys=ds, n_samples=1500, random_seed=42,
        allow_emcee_fallback=True,
    )
    wcdm = run_likelihood_chain(
        model="wcdm", dataset_keys=ds, n_samples=1500, random_seed=42,
        allow_emcee_fallback=True,
    )
    cmp = compute_model_comparison(lcdm, wcdm)
    finite = all(cmp[k] is not None for k in ("delta_chi2", "delta_aic", "delta_bic"))
    numeric_ok = (
        finite
        and cmp["comparison_valid"] is False
        and cmp["n_extra_params"] == 1
        and cmp["preferred"] == "undetermined"
        and cmp.get("__do_not_claim__") is True
        and "likelihood-only MLE" in str(cmp.get("comparison_warning") or "")
    )
    return _preliminary_multi_benchmark_result(
        numerical_pass=numeric_ok,
        chain_results={"lcdm": lcdm, "wcdm": wcdm},
        delta_chi2=cmp["delta_chi2"],
        delta_aic=cmp["delta_aic"],
        n_extra_params=cmp["n_extra_params"],
        preferred=cmp["preferred"],
        target=(
            "finite diagnostic deltas from preliminary chains, with model "
            "preference withheld until matched likelihood-only MLE attestations"
        ),
    )


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
    )
    return _preliminary_benchmark_result(
        numerical_pass=0.3 < reduced < 1.2 and executed,
        chain_result=r,
        n_points=len(COSMIC_CHRONOMETER_HZ),
        chi2_planck_fiducial=round(chi2, 3),
        reduced_chi2=round(reduced, 4),
        executed_in_process=executed,
        target="CC reduced χ² in [0.3,1.2], executed in-process, preliminary only",
    )


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
    )
    return _preliminary_benchmark_result(
        numerical_pass=(
            abs(f0 - 0.3153 ** 0.55) < 1e-6
            and abs(d0 - 1.0) < 1e-9
            and 0.3 < reduced < 2.0
            and executed
        ),
        chain_result=r,
        f0_growth_rate=round(f0, 4),
        d0_growth_factor=round(d0, 6),
        n_points=len(EBOSS_DR16_FSIGMA8),
        reduced_chi2_planck=round(reduced, 4),
        executed_in_process=executed,
        target="growth identities + eBOSS χ² regression; in-process chain remains preliminary",
    )


def bench_sdss_6df_bao_executable() -> dict[str, Any]:
    """6dFGS + SDSS MGS low-z BAO executable (2B, 2026-05-29; MGS table 2026-06-12).

    Pins (1) the D_V/r_d predictions at the Planck 2018 fiducial (H0=67.36,
    Ωm=0.3153, r_d=147.09) against the two sourced anchors — 6dFGS z=0.106 →
    3.047±0.137 and SDSS MGS z=0.15 → 4.47±0.17 (Aubourg+2015 Table II; the
    MGS Gaussian is documentation-only since the table upgrade) — both within
    ~1.5σ; (2) the total χ² decomposes EXACTLY into the 6dFGS Gaussian term +
    the MGS non-Gaussian chi2(alpha) table lookup at the fiducial alpha (the
    2026-06-12 fidelity upgrade: the hand-typed 4.47±0.17 Gaussian is retired
    from the fit); (3) that run_likelihood_chain executes sdss_6df_bao
    in-process via the generalized per-dataset BAO registry.
    """
    from app.services.cosmology_likelihoods import (
        run_likelihood_chain,
        MGS_ALPHA_RESCALE,
        SDSS_6DF_BAO_MEAN_VECTOR,
        _bao_predictions,
        _bao_chi2_samples,
        _mgs_chi2_spline,
    )
    order = ["H0", "omegam", "rd"]
    theta = np.array([[67.36, 0.3153, 147.09]])
    pred = _bao_predictions(theta, order, SDSS_6DF_BAO_MEAN_VECTOR)[0]
    chi2 = float(_bao_chi2_samples(theta, order, "sdss_6df_bao")[0])
    sigma = (0.137, 0.17)
    obs = (3.047, 4.470)
    pulls = [abs(float(pred[i]) - obs[i]) / sigma[i] for i in range(2)]
    gauss_6df = ((float(pred[0]) - obs[0]) ** 2) / sigma[0] ** 2
    mgs_table = -2.0 * float(_mgs_chi2_spline()(float(pred[1]) / MGS_ALPHA_RESCALE))
    decomposition_err = abs(chi2 - (gauss_6df + mgs_table))
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["sdss_6df_bao"], n_samples=2000, random_seed=42
    )
    used = [d["key"] for d in r["datasets_used"]]
    executed = (
        bool(r["success"]) and "sdss_6df_bao" in used
    )
    return _preliminary_benchmark_result(
        numerical_pass=(
            max(pulls) < 1.5
            and decomposition_err < 1e-9
            and 0.1 < chi2 / 2 < 2.0
            and executed
        ),
        chain_result=r,
        pred_dv_rd_z0106=round(float(pred[0]), 4),
        pred_dv_rd_z015=round(float(pred[1]), 4),
        pulls_sigma=[round(p, 2) for p in pulls],
        chi2_6df_gaussian=round(gauss_6df, 4),
        chi2_mgs_table=round(mgs_table, 4),
        chi2_decomposition_err=decomposition_err,
        reduced_chi2_planck=round(chi2 / 2, 4),
        executed_in_process=executed,
        target="6dF/MGS pulls + exact χ² decomposition; in-process chain remains preliminary",
    )


def bench_sdss_dr12_consensus_bao() -> dict[str, Any]:
    """BOSS DR12 consensus BAO executable (P2b, 2026-06-12).

    The BAO-only consensus of Alam et al. 2017 — the '+BAO' column behind the
    Planck 2018 parameter tables. Pins (1) the DIMENSIONAL rs_fid=147.78
    storage convention: at the Planck 2018 fiducial the predicted
    D_M·(rs_fid/r_d) must land within 5% of the released values (~1512 Mpc at
    z=0.38, NOT the dimensionless ~10 of the same-named eBOSS quantity);
    (2) per-point pulls < 2σ and order-unity χ²/n at the fiducial (measured
    5.615 for 6 points; cobaya parity within 0.1 is locked in
    tests/test_sdss_dr12_consensus.py); (3) in-process execution against the
    full 6×6 covtot, with the single ensemble held at preliminary status.
    """
    from app.services.cosmology_likelihoods import (
        load_verified_dr12_consensus_data,
        run_likelihood_chain,
        _dr12_chi2_samples,
        _dr12_consensus_predictions,
    )
    order = ["H0", "omegam", "rd"]
    theta = np.array([[67.36, 0.3153, 147.09]])
    v = load_verified_dr12_consensus_data()
    pred = _dr12_consensus_predictions(theta, order, v["mean_vector"])[0]
    observed = np.array([row[1] for row in v["mean_vector"]])
    sigmas = np.sqrt(np.diag(v["covariance"]))
    pulls = np.abs(pred - observed) / sigmas
    rel = np.abs(pred - observed) / observed
    chi2 = float(_dr12_chi2_samples(theta, order, "sdss_dr12_consensus_bao")[0])
    r = run_likelihood_chain(
        model="lcdm", dataset_keys=["sdss_dr12_consensus_bao"], n_samples=2000, random_seed=42
    )
    used = [d["key"] for d in r["datasets_used"]]
    executed = (
        bool(r["success"])
        and "sdss_dr12_consensus_bao" in used
        and v["hash_verified"] is True
    )
    return _preliminary_benchmark_result(
        numerical_pass=(
            bool(np.all(pulls < 2.0))
            and bool(np.all(rel < 0.05))
            and 0.3 < chi2 / 6 < 2.0
            and executed
        ),
        chain_result=r,
        chi2_planck_fiducial=round(chi2, 4),
        reduced_chi2_planck=round(chi2 / 6, 4),
        max_pull_sigma=round(float(pulls.max()), 2),
        pred_dm_z038_mpc=round(float(pred[0]), 2),
        executed_in_process=executed,
        target="DR12 dimensional convention, pulls and χ² pass; ensemble remains preliminary",
    )


def bench_eboss_dr16_grid_bao() -> dict[str, Any]:
    """eBOSS DR16 non-Gaussian BAO surfaces (P2b, 2026-06-12).

    Pins (1) the released surfaces still peak at the published best fits
    (ELG D_V/r_d=18.33 de Mattia+2021; Lyα auto 37.76/8.92 and cross
    37.44/9.06 du Mas des Bourboux 2020) — a transposed reshape or axis swap
    would move the apparent peak; (2) chi2 at the Planck 2018 fiducial: ELG
    agrees (~0.4), the Lyα grids sit at the known mild DR16 Lyα offset
    (chi2 2-4 for 2 dof — NOT a failure; cobaya parity is locked in
    tests/test_eboss_dr16_grid_bao.py); (3) the 3-grid joint chain executes
    in-process but remains preliminary as one coupled ensemble.
    """
    from app.services.cosmology_likelihoods import (
        EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS,
        load_verified_grid_bao_data,
        run_likelihood_chain,
        _grid_bao_chi2_samples,
    )
    order = ["H0", "omegam", "rd"]
    theta = np.array([[67.36, 0.3153, 147.09]])
    elg = load_verified_grid_bao_data("eboss_dr16_elg_bao")["grid"]
    elg_peak = float(elg[np.argmax(elg[:, 1]), 0])
    lya_peaks = {}
    for key, expected in (("eboss_dr16_lyauto_bao", (37.76, 8.92)),
                          ("eboss_dr16_lyxqso_bao", (37.44, 9.06))):
        g = load_verified_grid_bao_data(key)["grid"]
        peak = g[np.argmax(g[:, 2])]
        lya_peaks[key] = (round(float(peak[0]), 3), round(float(peak[1]), 3), expected)
    chi2 = {
        key: round(float(_grid_bao_chi2_samples(theta, order, key)[0]), 4)
        for key in sorted(EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS)
    }
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=sorted(EBOSS_DR16_GRID_BAO_EXECUTABLE_KEYS),
        n_samples=3000,
        random_seed=42,
    )
    peaks_ok = (
        abs(elg_peak - 18.33) < 0.05
        and all(abs(v[0] - v[2][0]) < 0.2 and abs(v[1] - v[2][1]) < 0.2 for v in lya_peaks.values())
    )
    chi2_ok = (
        0.05 < chi2["eboss_dr16_elg_bao"] < 4.0
        and 0.3 < chi2["eboss_dr16_lyauto_bao"] < 6.0
        and 0.5 < chi2["eboss_dr16_lyxqso_bao"] < 8.0
    )
    return _preliminary_benchmark_result(
        numerical_pass=peaks_ok and chi2_ok and bool(r.get("success")),
        chain_result=r,
        elg_peak_dv_rd=elg_peak,
        lya_peaks={k: v[:2] for k, v in lya_peaks.items()},
        chi2_planck_fiducial=chi2,
        target="surface peaks + fiducial χ² bands; 3-grid ensemble remains preliminary",
    )


def bench_pantheon18_full_vector() -> dict[str, Any]:
    """Pantheon (2018) 1048-SN full vector (P2b, 2026-06-13).

    Data-level checks run WITHOUT the env flag (the vendored, sha256-pinned
    files are committed): (1) the offset-marginalized chi2 over an Omega_m grid
    bottoms out at the published Scolnic+2018 SN-only value 0.298±0.022 with
    chi2/n ≈ 1; (2) H0-invariance of the marginalized chi2. The chain-level
    emcee path needs PANTHEON18_FULL_CHI2_ENABLED (live-verified 2026-06-13:
    Ωm = 0.300 ± 0.0217 vs published 0.298 ± 0.022, ~22 s).
    """
    from app.services.cosmology_likelihoods import (
        load_verified_pantheon18_data,
        _pantheon18_chi2_samples,
    )
    v = load_verified_pantheon18_data()
    if not v["hash_verified"]:
        return {
            "pass": False,
            "error": "pantheon18 vendored files missing or unverified",
            "target": "chi2 minimum at published Omega_m=0.298±0.022, chi2/n≈1, H0-invariant",
        }
    oms = np.linspace(0.20, 0.42, 45)
    samples = np.column_stack([np.full(45, 70.0), oms])
    chi2 = _pantheon18_chi2_samples(samples, ["H0", "omegam"])
    om_best = float(oms[np.argmin(chi2)])
    reduced = float(chi2.min()) / 1048.0
    h0_lo = _pantheon18_chi2_samples(np.array([[60.0, 0.30]]), ["H0", "omegam"])[0]
    h0_hi = _pantheon18_chi2_samples(np.array([[80.0, 0.30]]), ["H0", "omegam"])[0]
    h0_invariant = abs(h0_lo - h0_hi) < 1e-6
    return {
        "pass": abs(om_best - 0.298) < 0.022 and 0.85 < reduced < 1.1 and h0_invariant,
        "omegam_best": round(om_best, 4),
        "published": "0.298 ± 0.022 (Scolnic+2018 SN-only flat-LCDM)",
        "reduced_chi2": round(reduced, 4),
        "h0_invariant": h0_invariant,
        "target": "chi2 minimum at published Omega_m=0.298±0.022, chi2/n≈1, H0-invariant",
    }


def bench_s8_derived_consistency() -> dict[str, Any]:
    """Planck/KiDS posterior summaries never become growth likelihoods.

    The CHW2019 distance prior constrains geometry, ombh2, and ns only. Planck
    sigma8/S8 and KiDS S8 are published posterior summaries: neither may enter
    chi2 or appear as a derived constraint. Co-selecting KiDS must report a
    partial dataset run and redact the blocked posterior.
    """
    from app.services.cosmology_likelihoods import (
        run_likelihood_chain,
        _sampling_parameter_order,
        get_cosmology_dataset,
    )

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
    used_kids = {d.get("key") for d in planck_kids.get("datasets_used", [])}
    not_run_kids = {d.get("key") for d in planck_kids.get("datasets_not_run", [])}
    numeric_ok = (
        set(planck.get("parameters", {})) == {"H0", "omegam", "ombh2", "ns"}
        and set(bao_planck.get("parameters", {})) == {"H0", "omegam", "rd", "ombh2", "ns"}
        and prod_order == ["H0", "omegam", "rd", "ombh2", "ns"]
        and used_kids == {"planck2018_compressed"}
        and "kids1000_wl" in not_run_kids
        and planck_kids.get("chain_tier") == "blocked"
        and planck_kids.get("__do_not_claim__") is True
        and "parameters" not in planck_kids
    )
    gate_by_chain = {
        "planck": _preliminary_chain_gate_ok(planck),
        "bao_planck": _preliminary_chain_gate_ok(bao_planck),
        "planck_kids_blocked": bool(
            planck_kids.get("publication_ready") is False
            and planck_kids.get("chain_tier") == "blocked"
        ),
    }
    return {
        "pass": bool(numeric_ok and all(gate_by_chain.values())),
        "numerical_regression_pass": bool(numeric_ok),
        "publication_gate_correct": all(gate_by_chain.values()),
        "publication_gate_by_chain": gate_by_chain,
        "scientific_publication_pass": False,
        "validation_scope": "statistical_role_separation_regression",
        "prod_bao_planck_order": prod_order,
        "planck_parameters": sorted(planck.get("parameters", {})),
        "bao_planck_parameters": sorted(bao_planck.get("parameters", {})),
        "planck_kids_datasets_not_run": sorted(not_run_kids),
        "target": "Planck distance-prior outputs no sigma8/S8; KiDS posterior summary stays context-only",
    }


def bench_cmb_distance_prior_reproduces_planck() -> dict[str, Any]:
    """The CMB distance-prior kernel must reproduce the published Planck 2018
    distance priors at LCDM-Planck params (Chen-Huang-Wang 2019, arXiv:1808.05724,
    Table I): R=1.7502±0.0046, l_A=301.471±0.090. Locks the acoustic-scale prior
    used for extended FLAT dark-energy CMB constraints so the physics can't drift."""
    from app.services.cosmology_likelihoods import _cmb_distance_priors

    R, lA, _ = _cmb_distance_priors(0.3153, 67.36, 0.02237, w0=-1.0, wa=0.0)
    return {
        # Tight bounds around the validated kernel output (R≈1.7496, l_A≈301.55),
        # both inside ~1σ of the published prior.
        "pass": (1.749 < R < 1.751) and (301.40 < lA < 301.70),
        "R": round(R, 4),
        "l_A": round(lA, 3),
        "R_pub_dev_sigma": round((R - 1.7502) / 0.0046, 2),
        "lA_pub_dev_sigma": round((lA - 301.471) / 0.090, 2),
        "target": "R=1.7502±0.0046, l_A=301.471±0.090 (CHW2019 Table I), within ~1σ",
    }


BENCHMARKS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("lcdm_h0_anchor", bench_lcdm_h0_anchor),
    ("wcdm_w_near_minus_one", bench_wcdm_w_near_minus_one),
    ("cmb_distance_prior_reproduces_planck", bench_cmb_distance_prior_reproduces_planck),
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
    ("sdss_dr12_consensus_bao", bench_sdss_dr12_consensus_bao),
    ("eboss_dr16_grid_bao", bench_eboss_dr16_grid_bao),
    ("pantheon18_full_vector", bench_pantheon18_full_vector),
    ("s8_derived_consistency", bench_s8_derived_consistency),
    ("growth_kernel_vs_exact_lcdm", bench_growth_kernel_vs_exact_lcdm),
    ("model_comparison_delta", bench_model_comparison_delta),
    ("sn_omegam_compressed", bench_sn_omegam_compressed),
    ("sn_compressed_provenance", bench_sn_compressed_provenance),
    ("pantheon_full_cov_fidelity", bench_pantheon_full_cov_fidelity),
    ("oracle_genuine_reproductions", bench_oracle_genuine_reproductions),
    ("w0wa_full_sn_w0_tight", bench_w0wa_full_sn_w0_tight),
]


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_benchmark_accounting(
    name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Give every benchmark explicit, non-overlapping validation semantics."""
    outcome = result.get("pass")
    if isinstance(outcome, np.bool_):
        outcome = bool(outcome)
        result["pass"] = outcome
    elif outcome is not None and type(outcome) is not bool:
        raise TypeError(
            f"benchmark {name!r} returned non-boolean pass={outcome!r} "
            f"({type(outcome).__name__})"
        )

    default_scope = "skipped" if outcome is None else "numerical_regression"
    scope = result.setdefault("validation_scope", default_scope)
    if not isinstance(scope, str) or not scope:
        raise TypeError(f"benchmark {name!r} returned invalid validation_scope={scope!r}")

    if "numerical_regression_pass" not in result:
        result["numerical_regression_pass"] = (
            outcome if scope == "numerical_regression" else None
        )
    result.setdefault("publication_gate_correct", None)
    result.setdefault("scientific_publication_pass", None)
    result["benchmark_contract_pass"] = outcome

    for field in (
        "numerical_regression_pass",
        "publication_gate_correct",
        "scientific_publication_pass",
    ):
        value = result[field]
        if isinstance(value, np.bool_):
            value = bool(value)
            result[field] = value
        if value is not None and type(value) is not bool:
            raise TypeError(
                f"benchmark {name!r} returned non-boolean {field}={value!r} "
                f"({type(value).__name__})"
            )

    if scope.startswith("preliminary_"):
        if result["scientific_publication_pass"] is not False:
            raise ValueError(
                f"preliminary benchmark {name!r} must set scientific_publication_pass=False"
            )
        if outcome is True and result["publication_gate_correct"] is not True:
            raise ValueError(
                f"preliminary benchmark {name!r} passed without a correct publication gate"
            )
    if result["scientific_publication_pass"] is True:
        if result.get("publication_ready") is not True:
            raise ValueError(
                f"benchmark {name!r} claimed scientific publication success without publication_ready=True"
            )
    return result


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
            result = fn()
            if not isinstance(result, dict):
                raise TypeError(
                    f"benchmark {name!r} returned {type(result).__name__}, expected dict"
                )
            # NumPy comparisons return np.bool_, which json(default=float)
            # otherwise serializes as 1.0/0.0.  That used to make a benchmark
            # disappear from pass/fail/skip accounting while the suite still
            # exited zero.  Canonicalize only genuine boolean scalars; reject
            # numeric/string lookalikes rather than guessing their meaning.
            results[name] = _normalize_benchmark_accounting(name, result)
        except Exception as exc:
            results[name] = {
                "pass": False,
                "benchmark_contract_pass": False,
                "numerical_regression_pass": False,
                "publication_gate_correct": None,
                "scientific_publication_pass": None,
                "validation_scope": "benchmark_execution_error",
                "error": str(exc),
                "error_class": exc.__class__.__name__,
                "traceback": traceback.format_exc(limit=4),
            }

    n_pass = sum(1 for r in results.values() if r.get("pass") is True)
    n_fail = sum(1 for r in results.values() if r.get("pass") is False)
    n_skipped = sum(
        1 for r in results.values()
        if r.get("pass") is None or r.get("status") == "skipped"
    )
    if n_pass + n_fail + n_skipped != len(results):
        # Defense in depth: no benchmark may vanish from the scientific
        # accounting, even if result-shape validation changes later.
        raise RuntimeError(
            "benchmark outcome accounting is not exhaustive: "
            f"pass={n_pass}, fail={n_fail}, skipped={n_skipped}, total={len(results)}"
        )
    n_numerical_pass = sum(
        1 for r in results.values() if r.get("numerical_regression_pass") is True
    )
    n_numerical_fail = sum(
        1 for r in results.values() if r.get("numerical_regression_pass") is False
    )
    n_numerical_not_applicable = len(results) - n_numerical_pass - n_numerical_fail
    n_publication_gate_pass = sum(
        1 for r in results.values() if r.get("publication_gate_correct") is True
    )
    n_publication_gate_fail = sum(
        1 for r in results.values() if r.get("publication_gate_correct") is False
    )
    n_publication_gate_not_applicable = (
        len(results) - n_publication_gate_pass - n_publication_gate_fail
    )
    n_scientific_publication_pass = sum(
        1 for r in results.values() if r.get("scientific_publication_pass") is True
    )
    n_scientific_publication_withheld = sum(
        1 for r in results.values() if r.get("scientific_publication_pass") is False
    )
    n_scientific_publication_not_applicable = (
        len(results)
        - n_scientific_publication_pass
        - n_scientific_publication_withheld
    )
    n_preliminary_numerical_pass = sum(
        1
        for r in results.values()
        if r.get("validation_scope") == "preliminary_numerical_regression"
        and r.get("numerical_regression_pass") is True
        and r.get("publication_gate_correct") is True
        and r.get("scientific_publication_pass") is False
        and r.get("pass") is True
    )
    payload = {
        "suite": "cosmology_benchmarks",
        "pass_semantics": "benchmark_contract_only_not_scientific_publication",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "n_benchmarks": len(results),
        "n_executed": n_pass + n_fail,
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_skipped": n_skipped,
        "n_numerical_regression_pass": n_numerical_pass,
        "n_numerical_regression_fail": n_numerical_fail,
        "n_numerical_regression_not_applicable": n_numerical_not_applicable,
        "n_preliminary_numerical_pass": n_preliminary_numerical_pass,
        "n_publication_gate_pass": n_publication_gate_pass,
        "n_publication_gate_fail": n_publication_gate_fail,
        "n_publication_gate_not_applicable": n_publication_gate_not_applicable,
        "n_scientific_publication_pass": n_scientific_publication_pass,
        "n_scientific_publication_withheld": n_scientific_publication_withheld,
        "n_scientific_publication_not_applicable": n_scientific_publication_not_applicable,
        "suite_status": "failed" if n_fail else ("passed_with_skips" if n_skipped else "passed"),
        "results": results,
    }
    print(json.dumps(payload, indent=2, default=float))
    if args.json:
        with open(args.json, "w") as fp:
            json.dump(payload, fp, indent=2, default=float)

    # Skips are reported separately and never counted as passes.  They do not
    # fail the default smoke suite because the full-SN paths are explicit slow
    # opt-ins; any executed failure still returns non-zero.
    all_pass = n_fail == 0
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
