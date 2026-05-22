"""Solar-system small-body dynamics — Öpik-style NEO collision probability +
keplerian helpers. **Simplified estimates only**; precise analysis must query
the JPL CNEOS Sentry-II API.

References (cite when using in published work):
- **Öpik 1951** Proc. R. Irish Acad. 54A, 165 — Earth-crossing collision rate.
- **Wetherill 1967** J. Geophys. Res. 72, 2429 — formal Öpik update.
- **Morbidelli+ 2002** Icarus 158, 329 (bibcode 2002Icar..158..329M) —
  NEO population + collision rate modernised treatment.
- **Bottke+ 1994** Icarus 107, 255 — Öpik formalism for orbit-crossing collision
  probability per encounter geometry.
- **Vereš+ 2017** Icarus 296, 139 — Yarkovsky drift detection from astrometry.

M0 Commit 3 (2026-05-18). Closed-form Öpik estimate + MOID calculation helpers.
REBOUND integration deferred to M2. Precise 100-yr impact monitoring uses
cneos.jpl.nasa.gov/sentry, not this module.
"""

from __future__ import annotations

import math

# Physical constants
GAUSS_GRAV = 0.01720209895          # k, AU^(3/2) / day
AU_KM = 1.495978707e8               # km
EARTH_RADIUS_AU = 4.2635e-5         # 6371 km
EARTH_GRAV_PARAMETER_GMS_AU = 8.887692e-10  # G M_Earth in au³/day² (solar gravitational units)
EARTH_ORBITAL_RADIUS_AU = 1.0       # simplified


def opik_collision_probability_per_encounter(
    moid_au: float, U_rel_km_s: float,
    target_radius_km: float = 6371.0, planet_gravity_focus: bool = True,
) -> float:
    """Öpik per-encounter collision probability.

    Simplified Öpik 1951 + Wetherill 1967 closed-form:
        P_coll ≈ R_eff² / (2π × MOID × U_rel × T_orb)  [per orbital period]
    Uses the gravitationally focused cross-section R_eff² = R² × (1 + (v_esc/U)²).

    Args:
        moid_au -- Minimum Orbital Intersection Distance (au)
        U_rel_km_s -- relative encounter velocity (km/s) at infinity
        target_radius_km -- target physical radius (default Earth 6371 km)
        planet_gravity_focus -- whether to apply gravitational focusing

    Returns per-encounter collision probability (dimensionless), typically 1e-10..1e-6.

    **Note**: this is an order-of-magnitude estimate. Real NEA risk assessment
    must query JPL CNEOS Sentry-II, which runs a full Monte Carlo 100-yr
    propagation including Yarkovsky.
    """
    if moid_au <= 0:
        raise ValueError(f"moid_au must be positive: {moid_au}")
    if U_rel_km_s <= 0:
        raise ValueError(f"U_rel_km_s must be positive: {U_rel_km_s}")
    if target_radius_km <= 0:
        raise ValueError(f"target_radius_km must be positive: {target_radius_km}")
    # Convert target radius to au
    R_au = target_radius_km / AU_KM
    # Gravitational focusing factor
    if planet_gravity_focus and target_radius_km == 6371.0:
        # Earth: v_esc = 11.186 km/s
        v_esc_km_s = 11.186
        focus = 1.0 + (v_esc_km_s / U_rel_km_s) ** 2
    else:
        focus = 1.0
    R_eff_au = R_au * math.sqrt(focus)
    # Convert U_rel to au/day for consistency
    U_au_per_day = U_rel_km_s / AU_KM * 86400.0
    # Closed-form Öpik estimate (per encounter)
    return (R_eff_au ** 2) / (2.0 * math.pi * moid_au * U_au_per_day)


def encounter_velocity_au_per_day(
    a_target_au: float, e_target: float, i_target_deg: float,
    a_earth_au: float = EARTH_ORBITAL_RADIUS_AU,
) -> float:
    """Approximate relative encounter velocity (au/day) at Earth crossing.

    Wetherill 1967 + Öpik 1951 — derives U from the Tisserand parameter T_E:
        U² = 3 - T_E      (units of v_E²)
        T_E = a_E/a + 2 cos(i) √(a(1-e²)/a_E)
    Returns |U_∞| in au/day.

    Simplified: assumes a circular Earth orbit + Keplerian target. The true
    close-approach speed also includes gravitational focusing, handled in
    opik_collision_probability_per_encounter.
    """
    if a_target_au <= 0:
        raise ValueError(f"a_target_au must be positive: {a_target_au}")
    if not (0 <= e_target < 1):
        raise ValueError(f"e_target must be ∈ [0, 1): {e_target}")
    if not (0 <= i_target_deg <= 180):
        raise ValueError(f"i_target_deg must be ∈ [0, 180]: {i_target_deg}")
    cos_i = math.cos(math.radians(i_target_deg))
    sqrt_term = math.sqrt(max(1 - e_target ** 2, 0.0))
    # Tisserand parameter w.r.t. Earth (a_earth=1 au)
    T_E = a_earth_au / a_target_au + 2.0 * cos_i * sqrt_term * math.sqrt(
        a_target_au / a_earth_au
    )
    inner = 3.0 - T_E
    U_norm = math.sqrt(max(inner, 0.0))  # in units of v_E
    v_earth_au_per_day = 2 * math.pi / 365.25  # ≈ 0.01721 au/day
    return U_norm * v_earth_au_per_day


def encounter_velocity_km_s(
    a_target_au: float, e_target: float, i_target_deg: float,
) -> float:
    """Same as encounter_velocity_au_per_day, but returns km/s."""
    U_au_per_day = encounter_velocity_au_per_day(a_target_au, e_target, i_target_deg)
    return U_au_per_day * AU_KM / 86400.0


def estimate_100yr_collision_probability(
    moid_au: float, a_target_au: float, e_target: float,
    i_target_deg: float, target_radius_km: float = 6371.0,
) -> dict[str, float]:
    """**Öpik geometric upper bound**: 100-yr cumulative (encounter rate x cross-section)
    — NOT the true impact probability.

    Logic:
        opik_upper_bound_100yr ≈ P_per_encounter × n_encounters/yr × 100

    where P_per_encounter = R_eff² / (2π × MOID × U) is the Öpik flux-times-cross-section ratio.

    **Critical caveat**: this function returns a geometric upper bound assuming the MOID
    stays fixed for 100 years and the asteroid crosses that MOID point every orbit.
    True NEA impact probabilities (e.g. from JPL CNEOS Sentry-II) are typically
    **10⁴–10⁶ times smaller** because:
      - Each encounter perturbs the orbit, changing future MOID values
      - Observational uncertainties mean the asteroid's probability of being
        exactly at the MOID point is not 1
      - Yarkovsky drift changes the long-term trajectory
      - Resonant keyhole geometries

    Any publication-grade NEO risk assessment must use Sentry-II or ESA NEODyS
    CLOMON2 Monte Carlo results — do NOT cite this function's output as an impact
    probability.
    """
    P = a_target_au ** 1.5
    n_encounters_per_year = 1.0 / P
    U_km_s = encounter_velocity_km_s(a_target_au, e_target, i_target_deg)
    if U_km_s <= 0:
        # Non-Earth-crossing orbit (Tisserand T_E >= 3), no close approach
        return {
            "p_per_encounter": 0.0,
            "encounters_per_year": 0.0,
            "encounter_velocity_km_s": 0.0,
            "opik_upper_bound_100yr": 0.0,
            "warning": "Non-Earth-crossing orbit (Tisserand T_E ≥ 3); 不发生 close approach.",
            "reference": (
                "Öpik 1951 PRIA 54A, 165; Wetherill 1967 JGR 72, 2429."
            ),
        }
    p_per_encounter = opik_collision_probability_per_encounter(
        moid_au, U_km_s, target_radius_km=target_radius_km,
    )
    opik_100yr = p_per_encounter * n_encounters_per_year * 100.0
    return {
        "p_per_encounter": p_per_encounter,
        "encounters_per_year": n_encounters_per_year,
        "encounter_velocity_km_s": U_km_s,
        "opik_upper_bound_100yr": opik_100yr,
        "warning": (
            "Öpik 几何上限,不是实际 impact 概率。 真实 100-yr 概率从 Sentry-II "
            "通常 ×10⁻⁴..×10⁻⁶ 小(orbit uncertainty + Yarkovsky + keyhole). "
            "Publication-grade 必查 https://cneos.jpl.nasa.gov/sentry/."
        ),
        "reference": (
            "Öpik 1951 PRIA 54A, 165; Wetherill 1967 JGR 72, 2429; "
            "Morbidelli+ 2002 Icarus 158, 329 (2002Icar..158..329M)"
        ),
    }


# ── Kepler helpers ────────────────────────────────────────────────


def orbital_period_years(a_au: float) -> float:
    """Kepler's third law: T (yr) = a_au^(3/2)."""
    if a_au <= 0:
        raise ValueError(f"a_au must be positive: {a_au}")
    return a_au ** 1.5


def perihelion_aphelion_au(a_au: float, e: float) -> tuple[float, float]:
    """q = a(1-e), Q = a(1+e)."""
    if a_au <= 0:
        raise ValueError(f"a_au must be positive: {a_au}")
    if not (0 <= e < 1):
        raise ValueError(f"e must be ∈ [0, 1): {e}")
    return a_au * (1 - e), a_au * (1 + e)
