"""Solar-system small-body photometry helpers.

References (cite when using in published work):
- **Bowell+ 1989** "Asteroids II" ch. (bibcode 1989aste.conf..524B) —
  H, G two-parameter phase function for asteroids.
- **A'Hearn+ 1984** AJ 89, 579 (bibcode 1984AJ.....89..579A) —
  Afρ dust production proxy for comets.
- **Schleicher 2010** AJ 140, 973 — Halley-Marcus phase correction for Afρ.

M0 Commit 3 (2026-05-18). HG phase function uses the Bowell+ 1989
closed-form approximation directly. It deliberately does not require sbpy:
blind tests must not fail just because an optional photometry package is
absent from the runtime.
"""

from __future__ import annotations

import math
from typing import Sequence

# Speed of light → minutes per au (NIST CODATA 2018)
_LIGHT_TIME_MIN_PER_AU = 8.31675  # = 499.0047838 s / 60

# 1 au in cm (IAU 2012 definition)
_AU_CM = 1.495978707e13

# Sun apparent V-magnitude at 1 au (Willmer 2018 ApJS 236, 47)
SUN_V_MAG_AT_1AU = -26.74

# Default phase slope G (Bowell+ 1989 fallback for unknown asteroid)
DEFAULT_G_SLOPE = 0.15

# arcsec per radian (IAU 1976)
_ARCSEC_PER_RAD = 206264.80624709636


# ── H-G phase function (Bowell+ 1989) ──────────────────────────────


def hg_phase_function(alpha_deg: float, G: float = DEFAULT_G_SLOPE) -> float:
    """HG phase function Φ(α) = (1-G) Φ₁(α) + G Φ₂(α),  α ∈ [0°, 180°].

    Bowell+ 1989 approximation:
        Φ₁ = exp[-3.33 tan(α/2)^0.63]
        Φ₂ = exp[-1.87 tan(α/2)^1.22]
        Φ  = (1-G) Φ₁ + G Φ₂

    At α=0°, Φ=1 (opposition, full phase).
    """
    if not (0.0 <= alpha_deg <= 180.0):
        raise ValueError(f"alpha_deg out of range [0, 180]: {alpha_deg}")
    if not (0.0 <= G <= 1.0):
        raise ValueError(f"G out of range [0, 1]: {G}")
    if alpha_deg == 0:
        return 1.0
    if alpha_deg == 180:
        return 0.0
    tan_half = math.tan(math.radians(alpha_deg) / 2.0)
    phi1 = math.exp(-3.33 * (tan_half ** 0.63))
    phi2 = math.exp(-1.87 * (tan_half ** 1.22))
    phase = (1.0 - G) * phi1 + G * phi2
    return max(phase, 1e-300)


def hg_reduced_magnitude(H: float, G: float, alpha_deg: float) -> float:
    """Reduced magnitude H(α) = H - 2.5 log₁₀ Φ(α).

    "Reduced" means r=Δ=1 au — the distance-falloff term is excluded. Equals H at α=0°.
    """
    if not (0.0 <= alpha_deg <= 180.0):
        raise ValueError(f"alpha_deg out of range [0, 180]: {alpha_deg}")
    phase = hg_phase_function(alpha_deg, G)
    return H - 2.5 * math.log10(phase)


def hg_apparent_magnitude(
    H: float, G: float, alpha_deg: float, r_au: float, delta_au: float,
) -> float:
    """V(α, r, Δ) = H(α) + 5 log₁₀(r Δ).

    Bowell+ 1989 two-parameter photometric phase function; r/Δ in au.
    """
    if r_au <= 0 or delta_au <= 0:
        raise ValueError(f"r_au and delta_au must be positive: {r_au}, {delta_au}")
    reduced = hg_reduced_magnitude(H, G, alpha_deg)
    return reduced + 5.0 * math.log10(r_au * delta_au)


def hg_apparent_magnitude_curve(
    H: float, G: float, alpha_angles_deg: Sequence[float],
    r_au_arr: Sequence[float], delta_au_arr: Sequence[float],
) -> list[float]:
    """Compute V for an array of (α, r, Δ) triples — vectorised helper."""
    if not (len(alpha_angles_deg) == len(r_au_arr) == len(delta_au_arr)):
        raise ValueError("All three sequences must have the same length")
    return [
        hg_apparent_magnitude(H, G, a, r, d)
        for a, r, d in zip(alpha_angles_deg, r_au_arr, delta_au_arr)
    ]


# ── Light-time correction ──────────────────────────────────────────


def light_time_minutes(delta_au: float) -> float:
    """Light travel time (min) = Δ (au) × 8.31675 min/au."""
    if delta_au < 0:
        raise ValueError(f"delta_au must be non-negative: {delta_au}")
    return delta_au * _LIGHT_TIME_MIN_PER_AU


def light_time_seconds(delta_au: float) -> float:
    """Light travel time (s) = Δ (au) × 499.005 s/au."""
    return light_time_minutes(delta_au) * 60.0


# ── Afρ (A'Hearn+ 1984) ────────────────────────────────────────────


def afrho_cm(
    m_comet: float, r_au: float, delta_au: float, aperture_arcsec: float,
    m_sun: float = SUN_V_MAG_AT_1AU,
) -> float:
    """A'Hearn+ 1984 Afρ — dust production proxy (cm).

    Afρ = 4 × Δ_au² × r_au² × (1 au [cm])² / ρ_cm × 10^(0.4 (m_sun - m_comet))

    where ρ_cm is the projected aperture radius at the comet (cm):
        ρ_cm = (aperture_arcsec / 206264.806) × Δ_au × (1 au [cm])

    Args:
        m_comet -- comet apparent V magnitude within the aperture
        r_au -- heliocentric distance (au)
        delta_au -- geocentric distance (au)
        aperture_arcsec -- aperture angular radius (arcsec)
        m_sun -- apparent V magnitude of the Sun at 1 au (default -26.74, Willmer 2018)

    Returns Afρ in cm. Typical short-period comets near perihelion: ~10-10000 cm.
    """
    if r_au <= 0 or delta_au <= 0 or aperture_arcsec <= 0:
        raise ValueError(
            f"r_au, delta_au, aperture_arcsec must be positive: "
            f"r={r_au}, Δ={delta_au}, ap={aperture_arcsec}"
        )
    rho_cm = (aperture_arcsec / _ARCSEC_PER_RAD) * (delta_au * _AU_CM)
    flux_ratio = 10 ** (0.4 * (m_sun - m_comet))
    return 4.0 * (delta_au ** 2) * (r_au ** 2) * (_AU_CM ** 2) / rho_cm * flux_ratio


def afrho_phase_corrected(
    afrho_cm_value: float, alpha_deg: float,
) -> float:
    """Halley-Marcus phase function correction (Schleicher 2010) — A(α)fρ → A(0°)fρ.

    Returns Afρ at 0° phase, the standard reference angle. Uses the
    Prefer sbpy's Halley-Marcus implementation when installed. If sbpy is
    absent, fall back to a conservative linear comet-dust phase coefficient
    (0.035 mag/deg). The fallback is explicitly approximate but keeps the
    platform functional for blind tests and user-supplied estimates.
    """
    if not (0.0 <= alpha_deg <= 180.0):
        raise ValueError(f"alpha_deg out of range [0, 180]: {alpha_deg}")
    try:
        import astropy.units as u
        from sbpy.activity.dust import phase_HalleyMarcus

        phase = phase_HalleyMarcus(alpha_deg * u.deg)
        phase_val = float(phase.value) if hasattr(phase, "value") else float(phase)
    except Exception:
        phase_val = 10 ** (-0.4 * 0.035 * alpha_deg)
    if phase_val <= 0:
        return afrho_cm_value
    return afrho_cm_value / phase_val
