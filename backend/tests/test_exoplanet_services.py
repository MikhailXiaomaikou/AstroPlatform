"""Exoplanet M0 (2026-05-20) — service module tests.

Covers exoplanet_physical (T_eq, transit depth, density) + exoplanet_transit
(transit fit). Uses known prototype values (HD 209458 b, TRAPPIST-1 e).
"""

from __future__ import annotations

import numpy as np
import pytest


# ── equilibrium temperature ─────────────────────────────────────────


def test_t_eq_earth_at_1au_solar():
    """Earth at 1 au around Sun with A=0.3 → T_eq ≈ 254 K (textbook value)."""
    from app.services.exoplanet_physical import compute_equilibrium_temperature

    r = compute_equilibrium_temperature(
        T_star_K=5778, R_star_solar=1.0, a_au=1.0, albedo=0.3,
    )
    assert 250 <= r["T_eq_K"] <= 260, f"got T_eq={r['T_eq_K']}"
    assert r["bibcode"] == "2003ApJ...585.1038S"


def test_t_eq_hot_jupiter_hd209458b():
    """HD 209458 b: T_star=6065, R_star=1.18, a=0.047 au, A=0.3 → T_eq ~1300-1500 K."""
    from app.services.exoplanet_physical import compute_equilibrium_temperature

    r = compute_equilibrium_temperature(
        T_star_K=6065, R_star_solar=1.18, a_au=0.047, albedo=0.3,
    )
    assert 1200 <= r["T_eq_K"] <= 1500, f"got T_eq={r['T_eq_K']}"


def test_t_eq_with_zero_albedo_is_higher():
    """A=0 (perfect absorber) gives higher T_eq than A=0.3."""
    from app.services.exoplanet_physical import compute_equilibrium_temperature

    r_a0 = compute_equilibrium_temperature(5778, 1.0, 1.0, albedo=0.0)
    r_a3 = compute_equilibrium_temperature(5778, 1.0, 1.0, albedo=0.3)
    assert r_a0["T_eq_K"] > r_a3["T_eq_K"]


def test_t_eq_rejects_invalid_inputs():
    from app.services.exoplanet_physical import compute_equilibrium_temperature

    with pytest.raises(ValueError):
        compute_equilibrium_temperature(-100, 1.0, 1.0)
    with pytest.raises(ValueError):
        compute_equilibrium_temperature(5778, 1.0, 1.0, albedo=1.5)
    with pytest.raises(ValueError):
        compute_equilibrium_temperature(5778, 0.0, 1.0)


# ── transit depth ───────────────────────────────────────────────────


def test_transit_depth_hot_jupiter_around_sun():
    """R_p = 1 R_J = 11.2 R_E around 1 R_sun → depth ≈ (11.2 * R_E / R_sun)² ≈ 0.0106."""
    from app.services.exoplanet_physical import compute_transit_depth

    r = compute_transit_depth(R_p_earth=11.2, R_star_solar=1.0)
    assert 0.009 <= r["depth"] <= 0.012, f"depth={r['depth']}"
    assert r["depth_ppm"] == pytest.approx(r["depth"] * 1e6, rel=1e-9)
    assert r["depth_pct"] == pytest.approx(r["depth"] * 100, rel=1e-9)


def test_transit_depth_earth_around_sun():
    """Earth around Sun: (R_E / R_sun)² ≈ 8.4e-5 → 84 ppm."""
    from app.services.exoplanet_physical import compute_transit_depth

    r = compute_transit_depth(R_p_earth=1.0, R_star_solar=1.0)
    assert 80 <= r["depth_ppm"] <= 90, f"depth_ppm={r['depth_ppm']}"


def test_transit_depth_rejects_invalid():
    from app.services.exoplanet_physical import compute_transit_depth

    with pytest.raises(ValueError):
        compute_transit_depth(-1.0, 1.0)
    with pytest.raises(ValueError):
        compute_transit_depth(1.0, 0.0)


# ── planet density ──────────────────────────────────────────────────


def test_density_earth_recovers_5_51():
    """Earth: 1 M_E + 1 R_E → 5.51 g/cm³ (canonical)."""
    from app.services.exoplanet_physical import compute_planet_density

    r = compute_planet_density(M_earth=1.0, R_earth=1.0)
    assert 5.4 <= r["density_g_cm3"] <= 5.6, f"got {r['density_g_cm3']}"


def test_density_jupiter_recovers_1_33():
    """Jupiter: 317.8 M_E + 11.21 R_E → ~1.33 g/cm³ (gas giant)."""
    from app.services.exoplanet_physical import compute_planet_density

    r = compute_planet_density(M_earth=317.8, R_earth=11.21)
    assert 1.2 <= r["density_g_cm3"] <= 1.4, f"got {r['density_g_cm3']}"


# ── Kepler 3rd law ─────────────────────────────────────────────────


def test_kepler_a_at_1yr_solar_is_1au():
    """P = 365.25 d, M_star = 1 M_sun → a ≈ 1 au."""
    from app.services.exoplanet_physical import kepler_a_from_period

    a = kepler_a_from_period(365.25, M_star_solar=1.0)
    assert 0.99 <= a <= 1.01


# ── transit fit (synthetic data) ───────────────────────────────────


def test_fit_transit_recovers_known_box():
    """Inject a known transit (P=3.5 d, depth=0.01, dur=0.1 d) → fit should
    recover within 10% (trapezoidal fit is fast but coarse).
    """
    from app.services.exoplanet_transit import fit_transit_lightcurve

    np.random.seed(42)
    t = np.linspace(0, 14, 1500)
    period_true = 3.5
    t0_true = 1.0
    depth_true = 0.01
    duration_true = 0.1

    phase = ((t - t0_true + 0.5 * period_true) % period_true) - 0.5 * period_true
    flux = np.ones_like(t)
    flux[np.abs(phase) <= 0.5 * duration_true] -= depth_true
    flux += np.random.normal(0, 0.001, len(t))

    r = fit_transit_lightcurve(
        time=t.tolist(), flux=flux.tolist(),
        period_guess=3.5, t0_guess=1.0,
        duration_guess=0.1, depth_guess=0.01,
    )
    assert r["fit_success"]
    assert abs(r["period_days"] - period_true) / period_true < 0.1
    assert abs(r["depth"] - depth_true) / depth_true < 0.3  # trapezoidal coarse


def test_fit_transit_rejects_short_data():
    from app.services.exoplanet_transit import fit_transit_lightcurve

    with pytest.raises(ValueError):
        fit_transit_lightcurve(time=[1, 2, 3], flux=[1, 1, 1])


def test_fit_transit_rejects_mismatched_lengths():
    from app.services.exoplanet_transit import fit_transit_lightcurve

    with pytest.raises(ValueError):
        fit_transit_lightcurve(time=list(range(50)), flux=[1] * 49)
