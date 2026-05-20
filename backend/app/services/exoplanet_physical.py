"""Exoplanet physical-parameter helpers.

References (cite when using in published work):
- **Seager & Mallén-Ornelas 2003** ApJ 585, 1038 (2003ApJ...585.1038S) —
  analytic transit light-curve geometry, equilibrium temperature, derived params.
- **Mandel & Agol 2002** ApJ 580, L171 (2002ApJ...580L.171M) — analytic
  transit light curves with limb darkening (used by exoplanet_transit.py).
- **Akeson+ 2013** PASP 125, 989 (2013PASP..125..989A) — NASA Exoplanet
  Archive composite parameters reference.

M0 (2026-05-20). Simplified implementation: all functions analytic,
no fitting (fitting lives in exoplanet_transit.py).
"""

from __future__ import annotations

import math

# ── physical constants (SI / astro units) ──────────────────────────

R_EARTH_KM = 6378.137  # Earth equatorial radius (km)
R_JUPITER_KM = 71492.0  # Jupiter equatorial radius (km)
R_SUN_KM = 695700.0  # Sun radius (km)
M_EARTH_KG = 5.9722e24
M_JUPITER_KG = 1.89813e27
M_SUN_KG = 1.98892e30
AU_M = 1.495978707e11
G_NEWTON = 6.67430e-11

# Sun effective temperature
T_SUN_K = 5778.0


# ── equilibrium temperature (Seager & Mallén-Ornelas 2003 Eq 1) ────


def compute_equilibrium_temperature(
    T_star_K: float,
    R_star_solar: float,
    a_au: float,
    albedo: float = 0.3,
    redistribution_factor: float = 1.0,
) -> dict[str, float]:
    """Equilibrium temperature of a planet from stellar radiation balance.

    T_eq = T_star * sqrt(R_star / 2a) * [f * (1 - A_B)]^(1/4)

    where f is the energy-redistribution factor (1 = perfect equilibration,
    2 = dayside-only; we use 1 by default = global average), A_B is the
    Bond albedo (~0.3 for Earth, ~0.4 for Jupiter).

    Args:
        T_star_K: stellar effective temperature (K).
        R_star_solar: stellar radius (R_sun).
        a_au: semi-major axis (au).
        albedo: Bond albedo, default 0.3.
        redistribution_factor: 1 (global average) or 2 (dayside-only).

    Returns:
        dict with T_eq_K, formula reference.

    Reference: Seager & Mallén-Ornelas 2003 ApJ 585, 1038, Eq 1.
    """
    if T_star_K <= 0 or R_star_solar <= 0 or a_au <= 0:
        raise ValueError("T_star_K, R_star_solar, a_au must be positive")
    if not (0.0 <= albedo < 1.0):
        raise ValueError(f"albedo must be in [0, 1), got {albedo}")
    if redistribution_factor <= 0:
        raise ValueError("redistribution_factor must be positive")

    R_star_au = R_star_solar * R_SUN_KM * 1000 / AU_M  # to au
    T_eq = (
        T_star_K
        * math.sqrt(R_star_au / (2.0 * a_au))
        * (redistribution_factor * (1.0 - albedo)) ** 0.25
    )
    return {
        "T_eq_K": T_eq,
        "albedo_used": albedo,
        "redistribution_factor": redistribution_factor,
        "reference": "Seager & Mallén-Ornelas 2003 ApJ 585, 1038 (2003ApJ...585.1038S)",
        "bibcode": "2003ApJ...585.1038S",
    }


# ── transit depth (R_p / R_star)² ──────────────────────────────────


def compute_transit_depth(
    R_p_earth: float,
    R_star_solar: float,
) -> dict[str, float]:
    """Transit depth (geometric, no limb darkening): depth = (R_p / R_star)².

    Args:
        R_p_earth: planet radius (R_Earth).
        R_star_solar: stellar radius (R_sun).

    Returns:
        dict with depth (fractional), depth_ppm, depth_pct, R_p_over_R_star.
    """
    if R_p_earth <= 0 or R_star_solar <= 0:
        raise ValueError("R_p_earth and R_star_solar must be positive")

    R_p_km = R_p_earth * R_EARTH_KM
    R_star_km = R_star_solar * R_SUN_KM
    rp_over_rstar = R_p_km / R_star_km
    depth = rp_over_rstar ** 2
    return {
        "depth": depth,
        "depth_ppm": depth * 1e6,
        "depth_pct": depth * 100.0,
        "R_p_over_R_star": rp_over_rstar,
        "reference": "Seager & Mallén-Ornelas 2003 ApJ 585, 1038 (2003ApJ...585.1038S)",
        "bibcode": "2003ApJ...585.1038S",
    }


# ── planet density (mean) ──────────────────────────────────────────


def compute_planet_density(
    M_earth: float,
    R_earth: float,
) -> dict[str, float]:
    """Mean planet density (g/cm³) from mass + radius.

    rho = (3 M) / (4 π R³)

    Reference rho values:
      - Earth: 5.51 g/cm³
      - Jupiter: 1.33 g/cm³ (gas giant)
      - Mercury: 5.43 g/cm³
      - Saturn: 0.69 g/cm³

    Args:
        M_earth: planet mass (M_Earth).
        R_earth: planet radius (R_Earth).

    Returns:
        dict with density_g_cm3, density_kg_m3.
    """
    if M_earth <= 0 or R_earth <= 0:
        raise ValueError("M_earth and R_earth must be positive")

    M_kg = M_earth * M_EARTH_KG
    R_m = R_earth * R_EARTH_KM * 1000
    volume_m3 = (4.0 / 3.0) * math.pi * R_m ** 3
    density_kg_m3 = M_kg / volume_m3
    density_g_cm3 = density_kg_m3 / 1000.0
    return {
        "density_g_cm3": density_g_cm3,
        "density_kg_m3": density_kg_m3,
        "earth_density_ratio": density_g_cm3 / 5.514,
        "reference": "Seager & Mallén-Ornelas 2003 ApJ 585, 1038 (2003ApJ...585.1038S)",
        "bibcode": "2003ApJ...585.1038S",
    }


# ── Kepler's third law (period ↔ semi-major axis) ──────────────────


def kepler_a_from_period(
    P_days: float,
    M_star_solar: float = 1.0,
) -> float:
    """Semi-major axis (au) from period + stellar mass via Kepler's third law.

    a³ = G M_star P² / (4 π²)

    Args:
        P_days: orbital period in days.
        M_star_solar: stellar mass (M_sun).
    Returns:
        Semi-major axis in au.
    """
    if P_days <= 0 or M_star_solar <= 0:
        raise ValueError("P_days and M_star_solar must be positive")
    P_s = P_days * 86400.0
    M_kg = M_star_solar * M_SUN_KG
    a_m = (G_NEWTON * M_kg * P_s ** 2 / (4 * math.pi ** 2)) ** (1.0 / 3.0)
    return a_m / AU_M
