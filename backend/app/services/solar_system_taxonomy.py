"""Asteroid taxonomic classification helpers.

References (cite when using in published work):
- **DeMeo+ 2009** Icarus 202, 160 (bibcode 2009Icar..202..160D) —
  Bus-DeMeo 25-class system, visible + NIR (0.45-2.45 μm), PCA-based.
- **Bus & Binzel 2002** Icarus 158, 106 — Bus visible-only taxonomy.
- **Carvano+ 2010** A&A 510, A43 (bibcode 2010A&A...510A..43C) —
  SDSS MOC 4-color (g-r, r-i, i-z, g-z) → Bus-class mapping.
- **Sergeyev & Carry 2021** A&A 652, A59 — SDSS-based 3D color taxonomy refinement.

M0 Commit 3 (2026-05-18). Simplified implementation:
- Carvano+ 2010 SDSS 4-color → 9 major classes (C, X, S, V, D, Q, L, A, O)
- Bus-DeMeo 11 main types via spectrum slope + 1-μm feature depth (no PCA)
"""

from __future__ import annotations

import math
from typing import Sequence

# ── Bus-DeMeo 11 main types (DeMeo+ 2009 Tables 1-3 statistics) ────────
#
# Slope is the visible 0.45-0.74 μm spectral slope (per 0.1 μm);
# Band-I depth is the fractional absorption depth of the ~1 μm band relative to continuum.
# Nearest-class assignment uses (slope, band1_depth) center + radius in 3D feature space;
# values taken from DeMeo+ 2009 Table 2/3 + Bus & Binzel 2002 Table 4 typical values.
BUS_DEMEO_TYPE_CENTERS: dict[str, dict[str, float]] = {
    # type: (slope per 0.1μm, band1_depth fraction, NIR slope per μm)
    "C":  {"vis_slope": 0.04, "band1_depth": 0.00, "nir_slope": -0.02, "p_V_typical": 0.06},
    "B":  {"vis_slope": -0.05, "band1_depth": 0.00, "nir_slope": -0.05, "p_V_typical": 0.07},
    "X":  {"vis_slope": 0.15, "band1_depth": 0.00, "nir_slope": 0.05, "p_V_typical": 0.15},
    "D":  {"vis_slope": 0.40, "band1_depth": 0.00, "nir_slope": 0.20, "p_V_typical": 0.05},
    "T":  {"vis_slope": 0.30, "band1_depth": 0.00, "nir_slope": 0.10, "p_V_typical": 0.08},
    "S":  {"vis_slope": 0.20, "band1_depth": 0.15, "nir_slope": 0.05, "p_V_typical": 0.20},
    "Q":  {"vis_slope": 0.15, "band1_depth": 0.25, "nir_slope": -0.05, "p_V_typical": 0.23},
    "V":  {"vis_slope": 0.25, "band1_depth": 0.40, "nir_slope": -0.10, "p_V_typical": 0.35},
    "A":  {"vis_slope": 0.55, "band1_depth": 0.35, "nir_slope": -0.10, "p_V_typical": 0.20},
    "R":  {"vis_slope": 0.35, "band1_depth": 0.45, "nir_slope": -0.10, "p_V_typical": 0.34},
    "K":  {"vis_slope": 0.15, "band1_depth": 0.10, "nir_slope": -0.05, "p_V_typical": 0.18},
    "L":  {"vis_slope": 0.30, "band1_depth": 0.10, "nir_slope": -0.10, "p_V_typical": 0.18},
}


def classify_bus_demeo_from_features(
    visible_slope: float, band1_depth: float, nir_slope: float = 0.0,
) -> dict[str, object]:
    """Nearest-class chi-squared assignment (Euclidean distance in 3D feature space).

    Args:
        visible_slope -- 0.45-0.74 μm spectral slope (per 0.1 μm); typical range [-0.1, 0.6]
        band1_depth -- fractional absorption depth of the 1-μm band relative to continuum;
                       typical range [0, 0.5], 0 = featureless
        nir_slope -- 0.85-2.45 μm spectral slope (per μm), default 0

    Returns a dict with best_class, chi_sq, all_chi_sq, typical_albedo.
    Simplified — does not distinguish Bus-DeMeo's 25 sub-classes (Sa/Sk/Sl/Sq/Sr/Sv etc.),
    only recognises the 11 main types.
    """
    chi_sqs: dict[str, float] = {}
    for type_name, center in BUS_DEMEO_TYPE_CENTERS.items():
        d_vis = (visible_slope - center["vis_slope"]) ** 2 / 0.05  # sigma ~0.22
        d_b1 = (band1_depth - center["band1_depth"]) ** 2 / 0.01    # sigma ~0.10
        d_nir = (nir_slope - center["nir_slope"]) ** 2 / 0.02       # sigma ~0.14
        chi_sqs[type_name] = d_vis + d_b1 + d_nir
    best_class = min(chi_sqs, key=chi_sqs.get)
    return {
        "best_class": best_class,
        "chi_sq": chi_sqs[best_class],
        "all_chi_sq": chi_sqs,
        "typical_albedo_pV": BUS_DEMEO_TYPE_CENTERS[best_class]["p_V_typical"],
        "classification_system": "Bus-DeMeo main-type (simplified, 11 classes)",
        "reference": "DeMeo+ 2009 Icarus 202, 160 (2009Icar..202..160D)",
    }


# ── Carvano+ 2010 SDSS 4-color → Bus class ─────────────────────────────
#
# Carvano+ 2010 (A&A 510, A43) gives mean and typical scatter of SDSS 4-colors
# (u-g, g-r, r-i, i-z) for each Bus class. P3 (2026-05-20) calibration:
# previous V-class r-i=-0.05 was wrong (Vesta got misclassified as O); now
# uses paper-accurate centers + per-color std for χ² nearest-center scoring
# (Mahalanobis-like, diagonal covariance). Key 1-μm absorption signature
# lives in r-i: V (~-0.4) / Q (~-0.05) strong, S (~+0.18) weak,
# C/X/D (~+0.10 to +0.35) absent.

CARVANO_2010_COLOR_CENTERS: dict[str, dict[str, float]] = {
    # 4-color mean and per-color std (mag); p_V_typical from NEOWISE Mainzer+ 2011.
    "C":  {"u-g": 1.45, "g-r": 0.42, "r-i": 0.10, "i-z": 0.01,
           "u-g_std": 0.10, "g-r_std": 0.08, "r-i_std": 0.06, "i-z_std": 0.08, "p_V_typical": 0.06},
    "X":  {"u-g": 1.65, "g-r": 0.50, "r-i": 0.15, "i-z": 0.05,
           "u-g_std": 0.10, "g-r_std": 0.08, "r-i_std": 0.08, "i-z_std": 0.10, "p_V_typical": 0.15},
    "D":  {"u-g": 2.00, "g-r": 0.75, "r-i": 0.35, "i-z": 0.20,
           "u-g_std": 0.15, "g-r_std": 0.10, "r-i_std": 0.10, "i-z_std": 0.12, "p_V_typical": 0.05},
    "S":  {"u-g": 1.85, "g-r": 0.55, "r-i": 0.18, "i-z": -0.15,
           "u-g_std": 0.10, "g-r_std": 0.08, "r-i_std": 0.08, "i-z_std": 0.10, "p_V_typical": 0.20},
    "V":  {"u-g": 1.85, "g-r": 0.60, "r-i": -0.40, "i-z": -0.20,
           "u-g_std": 0.20, "g-r_std": 0.10, "r-i_std": 0.10, "i-z_std": 0.10, "p_V_typical": 0.35},
    "A":  {"u-g": 2.15, "g-r": 0.85, "r-i": 0.20, "i-z": -0.05,
           "u-g_std": 0.15, "g-r_std": 0.10, "r-i_std": 0.10, "i-z_std": 0.10, "p_V_typical": 0.20},
    "Q":  {"u-g": 1.90, "g-r": 0.55, "r-i": -0.05, "i-z": -0.20,
           "u-g_std": 0.10, "g-r_std": 0.08, "r-i_std": 0.10, "i-z_std": 0.10, "p_V_typical": 0.23},
    "L":  {"u-g": 1.95, "g-r": 0.65, "r-i": 0.20, "i-z": 0.05,
           "u-g_std": 0.10, "g-r_std": 0.08, "r-i_std": 0.08, "i-z_std": 0.10, "p_V_typical": 0.18},
    "O":  {"u-g": 1.75, "g-r": 0.50, "r-i": -0.15, "i-z": -0.30,
           "u-g_std": 0.15, "g-r_std": 0.10, "r-i_std": 0.12, "i-z_std": 0.10, "p_V_typical": 0.30},  # rare
}


def classify_carvano_sdss_colors(
    u_g: float, g_r: float, r_i: float, i_z: float,
) -> dict[str, object]:
    """Carvano+ 2010 SDSS 4-color χ² nearest-center classification.

    Mahalanobis-like χ² (assumes diagonal covariance):
        χ² = Σ ((obs - mean) / std)²
    so high-scatter colors (D class i-z spread, etc.) don't over-penalize.

    Returns dict with best_class, chi2, all_chi2, typical_albedo, reference.
    Also keeps `distance` / `all_distances` (= sqrt(chi2)) for backward compat.
    """
    obs = {"u-g": u_g, "g-r": g_r, "r-i": r_i, "i-z": i_z}
    chi2_per_class: dict[str, float] = {}
    for type_name, center in CARVANO_2010_COLOR_CENTERS.items():
        chi2 = 0.0
        for color in ("u-g", "g-r", "r-i", "i-z"):
            diff = obs[color] - center[color]
            std = center[f"{color}_std"]
            chi2 += (diff / std) ** 2
        chi2_per_class[type_name] = chi2
    best = min(chi2_per_class, key=chi2_per_class.get)
    return {
        "best_class": best,
        "chi2": chi2_per_class[best],
        "all_chi2": chi2_per_class,
        # Backward-compat: old code reads `distance` / `all_distances` (Euclidean sqrt)
        "distance": math.sqrt(chi2_per_class[best]),
        "all_distances": {k: math.sqrt(v) for k, v in chi2_per_class.items()},
        "typical_albedo_pV": CARVANO_2010_COLOR_CENTERS[best]["p_V_typical"],
        "classification_system": "Carvano+ 2010 SDSS 4-color χ² nearest-center",
        "reference": "Carvano+ 2010 A&A 510, A43 (2010A&A...510A..43C)",
    }


def spectrum_to_features(
    wavelengths_um: Sequence[float], reflectance: Sequence[float],
) -> dict[str, float]:
    """Extract the 3 keystone features used by Bus-DeMeo nearest-class classification
    from a visible/near-infrared spectrum.

    Returns:
        visible_slope -- linear regression slope per 0.1 μm in [0.45, 0.74] μm
        band1_depth -- 1-μm absorption depth fraction relative to continuum
                      (estimated from endpoints at [0.74, 0.85] and [1.0, 1.3] μm)
        nir_slope -- linear regression slope per μm in [1.3, 2.45] μm
    Simplified — no PCA (deferred to M2).
    """
    if len(wavelengths_um) != len(reflectance):
        raise ValueError("wavelengths_um and reflectance must have the same length")
    if len(wavelengths_um) < 3:
        raise ValueError("Fewer than 3 spectral points; cannot fit a slope")
    wl = list(wavelengths_um)
    rf = list(reflectance)
    visible = [(w, r) for w, r in zip(wl, rf) if 0.45 <= w <= 0.74]
    nir = [(w, r) for w, r in zip(wl, rf) if 1.3 <= w <= 2.45]
    if not visible:
        raise ValueError("No data points in the visible range [0.45, 0.74] μm")
    visible_slope = _linear_slope(visible) * 0.1
    nir_slope = _linear_slope(nir) if nir else 0.0
    # Band-I depth: continuum interpolated at 0.7 + 1.5 μm, measured at 1.0 μm
    cont_left = _value_at(wl, rf, 0.7)
    cont_right = _value_at(wl, rf, 1.5)
    bot = _value_at(wl, rf, 1.0)
    if cont_left is not None and cont_right is not None and bot is not None:
        # interpolate continuum at 1.0 μm
        cont_at_1um = cont_left + (cont_right - cont_left) * (1.0 - 0.7) / (1.5 - 0.7)
        if cont_at_1um > 0:
            band1_depth = max(0.0, (cont_at_1um - bot) / cont_at_1um)
        else:
            band1_depth = 0.0
    else:
        band1_depth = 0.0
    return {
        "visible_slope": visible_slope,
        "band1_depth": band1_depth,
        "nir_slope": nir_slope,
    }


def _linear_slope(points: list[tuple[float, float]]) -> float:
    n = len(points)
    if n < 2:
        return 0.0
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0
    return (n * sxy - sx * sy) / denom


def _value_at(
    wl: list[float], rf: list[float], target_um: float, tol: float = 0.1,
) -> float | None:
    """Return the reflectance value closest to target_um (within tol), or None if none qualify."""
    if not wl:
        return None
    diffs = [(abs(w - target_um), r) for w, r in zip(wl, rf)]
    diffs.sort(key=lambda x: x[0])
    if diffs[0][0] > tol:
        return None
    return diffs[0][1]
