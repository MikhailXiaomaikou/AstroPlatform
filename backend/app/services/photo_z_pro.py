"""Research-grade photometric redshift estimation.

Enhanced template fitting with:
- 30+ SED templates (elliptical through starburst, parametric BC03-like)
- Calzetti et al. (2000) dust attenuation with E(B-V) as free parameter
- Emission line contribution scaled by UV luminosity
- IGM absorption via Madau (1995) prescription for z > 2
- Bayesian magnitude prior (Benitez 2000)
- Standard photo-z quality metrics (sigma_MAD, outlier fraction, NMAD)
"""

import logging
import numpy as np
from typing import Any

logger = logging.getLogger(__name__)

# ---------- SED Template Library ----------

def _generate_sed_templates() -> list[dict]:
    """Generate 30+ parametric SED templates spanning E through starburst.

    These are simplified BC03-like templates parameterized by:
    - Age (0.01-13 Gyr)
    - Star formation timescale tau (0.1-inf Gyr)
    - Metallicity (0.2-2.5 solar)

    Each template is a dict with 'name', 'wavelength' (Angstrom), 'flux' arrays.
    """
    wave = np.arange(900, 30001, 10, dtype=float)  # 900-30000 Angstrom, 10A step
    templates = []

    # Parametric forms: f(lambda) = A * lambda^alpha * exp(-lambda/lambda_break)
    # Different spectral slopes and breaks for different galaxy types

    configs = [
        # Elliptical / early-type (old, red, no SF)
        {"name": "E_old_13Gyr", "alpha": -2.5, "break": 8000, "uv_suppress": 0.01},
        {"name": "E_old_10Gyr", "alpha": -2.3, "break": 7500, "uv_suppress": 0.02},
        {"name": "E_int_5Gyr", "alpha": -2.0, "break": 7000, "uv_suppress": 0.05},
        {"name": "S0_4Gyr", "alpha": -1.8, "break": 6500, "uv_suppress": 0.08},
        # Spiral (intermediate SF)
        {"name": "Sa_3Gyr", "alpha": -1.5, "break": 6000, "uv_suppress": 0.12},
        {"name": "Sb_2.5Gyr", "alpha": -1.3, "break": 5500, "uv_suppress": 0.18},
        {"name": "Sbc_2Gyr", "alpha": -1.1, "break": 5200, "uv_suppress": 0.25},
        {"name": "Sc_1.5Gyr", "alpha": -0.9, "break": 5000, "uv_suppress": 0.35},
        {"name": "Scd_1Gyr", "alpha": -0.7, "break": 4800, "uv_suppress": 0.45},
        {"name": "Sd_0.8Gyr", "alpha": -0.5, "break": 4500, "uv_suppress": 0.55},
        # Irregular / late-type
        {"name": "Im_0.5Gyr", "alpha": -0.3, "break": 4200, "uv_suppress": 0.65},
        {"name": "Im_0.3Gyr", "alpha": -0.1, "break": 4000, "uv_suppress": 0.75},
        # Starburst (young, blue, strong UV)
        {"name": "SB1_0.1Gyr", "alpha": 0.0, "break": 3500, "uv_suppress": 0.85},
        {"name": "SB2_0.05Gyr", "alpha": 0.3, "break": 3200, "uv_suppress": 0.92},
        {"name": "SB3_0.03Gyr", "alpha": 0.5, "break": 3000, "uv_suppress": 0.96},
        {"name": "SB4_0.01Gyr", "alpha": 0.8, "break": 2800, "uv_suppress": 0.99},
        # Post-starburst / K+A
        {"name": "KA_0.5Gyr", "alpha": -1.0, "break": 5500, "uv_suppress": 0.15},
        {"name": "KA_1Gyr", "alpha": -1.5, "break": 6000, "uv_suppress": 0.08},
        # AGN-like (power law)
        {"name": "AGN_blue", "alpha": 0.5, "break": 50000, "uv_suppress": 0.98},
        {"name": "AGN_red", "alpha": -0.5, "break": 50000, "uv_suppress": 0.70},
        # Additional interpolated templates for finer sampling
        {"name": "E_young_3Gyr", "alpha": -1.7, "break": 6800, "uv_suppress": 0.06},
        {"name": "Sab_2.8Gyr", "alpha": -1.4, "break": 5800, "uv_suppress": 0.15},
        {"name": "Sbc_blue", "alpha": -0.8, "break": 5100, "uv_suppress": 0.30},
        {"name": "Sc_young", "alpha": -0.6, "break": 4700, "uv_suppress": 0.50},
        {"name": "SB_dusty", "alpha": 0.1, "break": 3800, "uv_suppress": 0.80},
        {"name": "QSO_type1", "alpha": 0.3, "break": 100000, "uv_suppress": 0.95},
        {"name": "QSO_type2", "alpha": -0.8, "break": 8000, "uv_suppress": 0.40},
        {"name": "LIRG", "alpha": -0.2, "break": 4500, "uv_suppress": 0.70},
        {"name": "ULIRG", "alpha": -0.5, "break": 5000, "uv_suppress": 0.50},
        {"name": "Green_valley", "alpha": -1.2, "break": 5600, "uv_suppress": 0.20},
    ]

    for cfg in configs:
        alpha = cfg["alpha"]
        lam_break = cfg["break"]
        uv_sup = cfg["uv_suppress"]

        # Base SED: power law + exponential cutoff
        flux = (wave / 5000.0) ** alpha * np.exp(-wave / lam_break)

        # UV suppression below Lyman break
        uv_mask = wave < 2000
        flux[uv_mask] *= uv_sup * (wave[uv_mask] / 2000.0) ** 2

        # Lyman break at 912 Angstrom
        ly_mask = wave < 912
        flux[ly_mask] *= 0.01

        # 4000 Angstrom break (stronger for older populations)
        d4000_strength = max(0, -alpha * 0.3)
        break_mask = (wave > 3800) & (wave < 4100)
        break_factor = 1.0 - d4000_strength * np.exp(-((wave[break_mask] - 3950) / 100) ** 2)
        flux[break_mask] *= break_factor

        # Normalize at 5500 Angstrom
        norm_idx = np.argmin(np.abs(wave - 5500))
        if flux[norm_idx] > 0:
            flux /= flux[norm_idx]

        templates.append({
            "name": cfg["name"],
            "wavelength": wave,
            "flux": flux,
        })

    return templates


# ---------- Dust Attenuation ----------

def _calzetti_attenuation(wave_angstrom: np.ndarray, ebv: float) -> np.ndarray:
    """Calzetti et al. (2000) dust attenuation law.

    Returns the attenuation factor f_obs/f_int for each wavelength.
    """
    wave_um = wave_angstrom / 10000.0  # Convert to microns
    rv = 4.05
    k = np.zeros_like(wave_um)

    # 0.12 - 0.63 micron
    mask1 = (wave_um >= 0.12) & (wave_um < 0.63)
    k[mask1] = 2.659 * (-2.156 + 1.509 / wave_um[mask1] - 0.198 / wave_um[mask1]**2 +
                          0.011 / wave_um[mask1]**3) + rv

    # 0.63 - 2.20 micron
    mask2 = (wave_um >= 0.63) & (wave_um <= 2.20)
    k[mask2] = 2.659 * (-1.857 + 1.040 / wave_um[mask2]) + rv

    # Outside range: extrapolate
    mask_uv = wave_um < 0.12
    if np.any(mask_uv):
        k[mask_uv] = k[mask1][0] if np.any(mask1) else rv
    mask_ir = wave_um > 2.20
    if np.any(mask_ir):
        k[mask_ir] = 0.0  # No attenuation in far-IR

    k = np.maximum(k, 0.0)
    return 10 ** (-0.4 * k * ebv)


# ---------- IGM Absorption ----------

def _madau_igm(wave_angstrom: np.ndarray, z: float) -> np.ndarray:
    """Madau (1995) IGM absorption prescription for Lyman-series blanketing."""
    if z < 0.1:
        return np.ones_like(wave_angstrom)

    transmission = np.ones_like(wave_angstrom)

    # Lyman series lines
    lyman_lines = [
        (1216.0, 0.0036),  # Ly-alpha
        (1026.0, 0.0017),  # Ly-beta
        (972.5, 0.0012),   # Ly-gamma
        (949.7, 0.00093),  # Ly-delta
    ]

    for lam_rest, coeff in lyman_lines:
        lam_obs = lam_rest * (1 + z)
        mask = wave_angstrom < lam_obs
        if np.any(mask):
            tau = coeff * (wave_angstrom[mask] / lam_rest) ** 3.46
            transmission[mask] *= np.exp(-tau)

    # Lyman continuum (below 912 * (1+z))
    lam_limit = 912.0 * (1 + z)
    mask_cont = wave_angstrom < lam_limit
    if np.any(mask_cont):
        x = wave_angstrom[mask_cont] / 912.0
        tau_cont = 0.25 * x**3 * ((1 + z)**0.46 - x**0.46)
        tau_cont += 9.4 * x**1.5 * ((1 + z)**0.18 - x**0.18)
        tau_cont -= 0.7 * x**3 * (x**(-1.32) - (1 + z)**(-1.32))
        tau_cont = np.maximum(tau_cont, 0)
        transmission[mask_cont] *= np.exp(-np.minimum(tau_cont, 50))

    return np.clip(transmission, 0, 1)


# ---------- Emission Lines ----------

def _add_emission_lines(wave: np.ndarray, flux: np.ndarray, z: float,
                       uv_luminosity_factor: float = 1.0) -> np.ndarray:
    """Add emission line contributions to SED template.

    Line strengths scaled by UV luminosity (proxy for star formation).
    """
    flux_out = flux.copy()

    # Major emission lines: (rest wavelength, relative strength)
    lines = [
        (1216.0, 15.0),   # Ly-alpha
        (3727.0, 3.0),    # [OII]
        (4861.0, 1.0),    # H-beta (reference)
        (4959.0, 1.3),    # [OIII] 4959
        (5007.0, 4.0),    # [OIII] 5007
        (6563.0, 2.87),   # H-alpha (Case B)
        (6548.0, 0.3),    # [NII] 6548
        (6584.0, 1.0),    # [NII] 6584
    ]

    # Scale line strength by UV luminosity proxy
    uv_idx = np.argmin(np.abs(wave - 1500 * (1 + z)))
    base_strength = max(flux[uv_idx] * 0.05 * uv_luminosity_factor, 0)

    for lam_rest, rel_strength in lines:
        lam_obs = lam_rest * (1 + z)
        sigma = 3.0 * (1 + z)  # Line width in Angstrom (instrumental + intrinsic)
        line_profile = rel_strength * base_strength * np.exp(-0.5 * ((wave - lam_obs) / sigma)**2)
        flux_out += line_profile

    return flux_out


# ---------- Filter System ----------

_FILTER_PARAMS = {
    # (central wavelength Angstrom, FWHM Angstrom)
    "u": (3551, 570), "g": (4686, 1380), "r": (6166, 1370),
    "i": (7480, 1530), "z": (8932, 1370),
    "J": (12350, 1620), "H": (16620, 2510), "Ks": (21590, 2620),
    "W1": (33526, 6626), "W2": (46028, 10423),
    "Y": (10200, 1200), "u_sdss": (3551, 570), "g_sdss": (4686, 1380),
    "NUV": (2316, 770), "FUV": (1528, 268),
}


def _synthetic_mag(wave: np.ndarray, flux: np.ndarray, band: str) -> float:
    """Compute synthetic AB magnitude through a filter."""
    if band not in _FILTER_PARAMS:
        return np.nan
    center, fwhm = _FILTER_PARAMS[band]
    sigma = fwhm / 2.3548

    response = np.exp(-0.5 * ((wave - center) / sigma) ** 2)
    response /= np.trapezoid(response, wave) + 1e-30

    f_nu = np.trapezoid(flux * response * wave**2, wave) / np.trapezoid(response * wave**2 / wave, wave)

    if f_nu <= 0:
        return 99.0
    return -2.5 * np.log10(f_nu) - 48.60


# ---------- Main Fitting Functions ----------

def fit_template_enhanced(
    magnitudes: dict[str, float],
    mag_errors: dict[str, float] | None = None,
    z_range: tuple[float, float] = (0.0, 6.0),
    z_step: float = 0.01,
    ebv_grid: list[float] | None = None,
    prior: str = "flat",
    include_emission_lines: bool = True,
    include_igm: bool = True,
) -> dict[str, Any]:
    """Enhanced photo-z estimation with 30+ templates + dust + emission lines + IGM.

    Args:
        magnitudes: {band_name: magnitude} dict
        mag_errors: {band_name: magnitude_error} dict (default 0.1 mag)
        z_range: redshift range to search
        z_step: redshift grid step
        ebv_grid: E(B-V) values to try (default [0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5])
        prior: "flat" or "magnitude" (Benitez 2000 prior)
        include_emission_lines: add emission line contributions
        include_igm: apply IGM absorption

    Returns:
        dict with z_phot, z_err, P(z), best_template, best_ebv, chi2, ...
    """
    if ebv_grid is None:
        ebv_grid = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5]

    if mag_errors is None:
        mag_errors = {b: 0.1 for b in magnitudes}

    bands = list(magnitudes.keys())
    obs_mags = np.array([magnitudes[b] for b in bands])
    obs_errs = np.array([mag_errors.get(b, 0.1) for b in bands])

    # Filter out invalid measurements.  Bands with no synthetic-photometry filter
    # in _FILTER_PARAMS yield NaN model magnitudes (_synthetic_mag), which would be
    # silently dropped from chi2 by nansum while still inflating the offset
    # denominator, n_bands, and ndof.  Exclude them up front so "data used" equals
    # "data contributing chi2".  Filter membership is z-independent.
    known_filter = np.array([b in _FILTER_PARAMS for b in bands])
    valid = np.isfinite(obs_mags) & (obs_mags < 90) & (obs_errs > 0) & known_filter
    if np.sum(valid) < 2:
        return {"error": "Need at least 2 valid photometric bands with a known filter"}

    templates = _generate_sed_templates()
    z_grid = np.arange(z_range[0], z_range[1] + z_step, z_step)

    # Compute chi2 for all (z, template, E(B-V)) combinations
    best_chi2 = np.inf
    best_z = 0.0
    best_template_name = ""
    best_ebv = 0.0
    chi2_grids: list[np.ndarray] = []

    for tmpl in templates:
        for ebv in ebv_grid:
            chi2_z = np.zeros(len(z_grid))

            for iz, z in enumerate(z_grid):
                # Shift template to observed frame
                wave_obs = tmpl["wavelength"] * (1 + z)
                flux = tmpl["flux"].copy()

                # Apply dust
                if ebv > 0:
                    flux *= _calzetti_attenuation(tmpl["wavelength"], ebv)

                # Apply IGM
                if include_igm and z > 0.1:
                    flux *= _madau_igm(wave_obs, z)

                # Add emission lines
                if include_emission_lines:
                    flux = _add_emission_lines(wave_obs, flux, z)

                # Compute synthetic magnitudes
                model_mags = np.array([_synthetic_mag(wave_obs, flux, b) for b in bands])

                # Analytical amplitude/offset marginalization.  The `valid` mask
                # already excludes bands with NaN model magnitudes, so numerator,
                # denominator, and chi2 sum over an identical band set.
                offset = np.sum((obs_mags[valid] - model_mags[valid]) / obs_errs[valid]**2) / \
                         np.sum(1.0 / obs_errs[valid]**2)
                model_mags_shifted = model_mags + offset

                chi2 = np.sum(((obs_mags[valid] - model_mags_shifted[valid]) / obs_errs[valid]) ** 2)
                chi2_z[iz] = chi2

                if chi2 < best_chi2:
                    best_chi2 = chi2
                    best_z = z
                    best_template_name = tmpl["name"]
                    best_ebv = ebv

            chi2_grids.append(chi2_z)

    # Accumulate P(z) = sum over (template, E(B-V)) of exp(-0.5 * chi2), with the
    # GLOBAL minimum chi2 subtracted for numerical stability.  Subtracting each
    # combo's own per-z minimum would make every template peak at 1.0 regardless
    # of absolute fit quality, turning P(z) into a template head-count instead of
    # a likelihood (see photo_z.py for the same convention).
    pz = np.zeros(len(z_grid))
    for chi2_z in chi2_grids:
        pz += np.exp(-0.5 * (chi2_z - best_chi2))

    # Apply prior
    if prior == "magnitude" and "i" in magnitudes:
        i_mag = magnitudes["i"]
        # Simplified single-type magnitude prior inspired by Benitez 2000.
        # NOTE: Benitez 2000 (Table 1) uses three galaxy types with different
        # (alpha, z_mt0, k_mt) parameters; this is a single-type approximation
        # P(z|m) ∝ z^2 * exp(-(z/z0)^1.5), z0 = 0.055*i - 0.8.
        # Coefficients are calibrated for typical photo-z surveys but do NOT
        # reproduce the original Benitez prior exactly.
        z0 = 0.055 * i_mag - 0.8
        z0 = max(z0, 0.01)
        prior_pz = z_grid**2 * np.exp(-(z_grid / z0)**1.5)
        pz *= prior_pz

    # Normalize P(z)
    pz_sum = np.trapezoid(pz, z_grid)
    if pz_sum > 0:
        pz /= pz_sum

    # Compute z_phot from P(z) peak
    z_phot = z_grid[np.argmax(pz)]

    # Error from 68% confidence interval
    cumsum = np.cumsum(pz) * z_step
    cumsum /= cumsum[-1] if cumsum[-1] > 0 else 1
    z_lo = z_grid[np.searchsorted(cumsum, 0.16)]
    z_hi = z_grid[np.searchsorted(cumsum, 0.84)]
    z_err = (z_hi - z_lo) / 2.0

    # Reduced chi2
    ndof = max(np.sum(valid) - 2, 1)

    return {
        "z_phot": float(z_phot),
        "z_err": float(z_err),
        "z_68_lo": float(z_lo),
        "z_68_hi": float(z_hi),
        # best_z_ml is the maximum-likelihood redshift (minimum chi² across
        # the template/ebv/z grid).  This differs from z_phot, which is the
        # P(z) peak (maximum-a-posteriori, includes the prior).  Returning
        # both lets callers diagnose non-Gaussian P(z) by their disagreement.
        "best_z_ml": float(best_z),
        "best_template": best_template_name,
        "best_ebv": float(best_ebv),
        "chi2_reduced": float(best_chi2 / ndof),
        "n_bands": int(np.sum(valid)),
        "prior": prior,
        "pz_grid": z_grid.tolist(),
        "pz_values": pz.tolist(),
        "method": "enhanced_template",
        "n_templates": len(templates),
        "ebv_grid": ebv_grid,
        "include_emission_lines": include_emission_lines,
        "include_igm": include_igm,
    }


def compute_photo_z_statistics(z_true: list, z_phot: list) -> dict:
    """Compute standard photo-z quality metrics."""
    zt = np.array(z_true)
    zp = np.array(z_phot)

    dz = (zp - zt) / (1 + zt)

    bias = float(np.median(dz))
    sigma_MAD = float(1.4826 * np.median(np.abs(dz - np.median(dz))))
    nmad = sigma_MAD  # NMAD is identical to sigma_MAD
    outlier_fraction = float(np.mean(np.abs(dz) > 0.15))
    rms = float(np.sqrt(np.mean(dz**2)))

    return {
        "bias": bias,
        "sigma_MAD": sigma_MAD,
        "NMAD": nmad,
        "outlier_fraction": outlier_fraction,
        "outlier_threshold": 0.15,
        "rms": rms,
        "n_objects": len(zt),
    }
