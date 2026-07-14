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

_EMISSION_LINES = (
    (1216.0, 15.0),   # Ly-alpha
    (3727.0, 3.0),    # [OII]
    (4861.0, 1.0),    # H-beta (reference)
    (4959.0, 1.3),    # [OIII] 4959
    (5007.0, 4.0),    # [OIII] 5007
    (6563.0, 2.87),   # H-alpha (Case B)
    (6548.0, 0.3),    # [NII] 6548
    (6584.0, 1.0),    # [NII] 6584
)


def _emission_line_profile(wave: np.ndarray, z: float) -> np.ndarray:
    """Return the unit-strength line profile used by the fitter."""
    profile = np.zeros_like(wave)
    for lam_rest, rel_strength in _EMISSION_LINES:
        lam_obs = lam_rest * (1 + z)
        sigma = 3.0 * (1 + z)
        profile += rel_strength * np.exp(-0.5 * ((wave - lam_obs) / sigma) ** 2)
    return profile

def _add_emission_lines(wave: np.ndarray, flux: np.ndarray, z: float,
                       uv_luminosity_factor: float = 1.0) -> np.ndarray:
    """Add emission line contributions to SED template.

    Line strengths scaled by UV luminosity (proxy for star formation).
    """
    flux_out = flux.copy()

    # Scale line strength by UV luminosity proxy
    uv_idx = np.argmin(np.abs(wave - 1500 * (1 + z)))
    base_strength = max(flux[uv_idx] * 0.05 * uv_luminosity_factor, 0)

    # Keep the scalar operation order stable for callers of this helper and
    # for the equivalence regression used to validate the projected fitter.
    for lam_rest, rel_strength in _EMISSION_LINES:
        lam_obs = lam_rest * (1 + z)
        sigma = 3.0 * (1 + z)
        line_profile = rel_strength * base_strength * np.exp(
            -0.5 * ((wave - lam_obs) / sigma) ** 2
        )
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
    response_integral = np.trapezoid(response, wave)
    if not np.isfinite(response_integral) or response_integral <= 0:
        return np.nan
    response /= response_integral

    denominator = np.trapezoid(response * wave**2 / wave, wave)
    if not np.isfinite(denominator) or denominator <= 0:
        return np.nan
    f_nu = np.trapezoid(flux * response * wave**2, wave) / denominator

    if not np.isfinite(f_nu):
        return np.nan
    if f_nu <= 0:
        return 99.0
    return -2.5 * np.log10(f_nu) - 48.60


# ---------- Main Fitting Functions ----------

def _trapezoid_weights(x: np.ndarray) -> np.ndarray:
    """Return weights whose dot product reproduces trapezoidal integration."""
    dx = np.diff(x)
    weights = np.empty_like(x, dtype=float)
    weights[0] = dx[0] / 2.0
    weights[-1] = dx[-1] / 2.0
    weights[1:-1] = (dx[:-1] + dx[1:]) / 2.0
    return weights


_MODEL_Z_CHUNK_SIZE = 32


def _make_redshift_grid(z_range: tuple[float, float], z_step: float) -> np.ndarray:
    """Build an inclusive-on-grid redshift grid without exceeding its bound."""
    z_min, z_max = (float(value) for value in z_range)
    if not np.isfinite([z_min, z_max, z_step]).all():
        raise ValueError("z_range and z_step must be finite")
    if z_min < 0 or z_max < z_min:
        raise ValueError("z_range must satisfy 0 <= z_min <= z_max")
    if z_step <= 0:
        raise ValueError("z_step must be positive")

    ratio = (z_max - z_min) / z_step
    # nextafter recovers exact mathematical integers such as 0.3 / 0.1 that
    # binary floating point can represent one ULP below the integer.
    n_steps = int(np.floor(np.nextafter(ratio, np.inf)))
    grid = z_min + z_step * np.arange(n_steps + 1, dtype=float)
    tolerance = 8 * np.finfo(float).eps * max(abs(z_min), abs(z_max), z_step, 1.0)
    grid[np.abs(grid - z_max) <= tolerance] = z_max
    return grid[grid <= z_max]


def _prepare_model_fluxes(
    templates: list[dict],
    ebv_grid: list[float],
) -> tuple[np.ndarray, np.ndarray, list[tuple[str, float]]]:
    """Validate the shared wavelength grid and prepare template/dust fluxes."""
    if not templates:
        raise ValueError("At least one SED template is required")

    wave_rest = np.asarray(templates[0]["wavelength"], dtype=float)
    if (
        wave_rest.ndim != 1
        or len(wave_rest) < 2
        or not np.isfinite(wave_rest).all()
        or not np.all(np.diff(wave_rest) > 0)
    ):
        raise ValueError("Template wavelength grid must be finite and strictly increasing")

    for template in templates:
        template_wave = np.asarray(template["wavelength"], dtype=float)
        if not np.array_equal(template_wave, wave_rest):
            raise ValueError(
                f"Template {template.get('name', '<unnamed>')} does not share the exact "
                "wavelength grid"
            )
        template_flux = np.asarray(template["flux"], dtype=float)
        if template_flux.shape != wave_rest.shape:
            raise ValueError(
                f"Template {template.get('name', '<unnamed>')} flux shape does not match "
                "its wavelength grid"
            )

    # Preserve the legacy behavior that non-positive E(B-V) means no dust.
    dust_factors = {
        ebv: (
            _calzetti_attenuation(wave_rest, ebv)
            if ebv > 0
            else np.ones_like(wave_rest)
        )
        for ebv in ebv_grid
    }
    combo_fluxes: list[np.ndarray] = []
    combo_metadata: list[tuple[str, float]] = []
    for template in templates:
        template_flux = np.asarray(template["flux"], dtype=float)
        for ebv in ebv_grid:
            combo_fluxes.append(template_flux * dust_factors[ebv])
            combo_metadata.append((template["name"], ebv))
    return wave_rest, np.asarray(combo_fluxes), combo_metadata


def _model_magnitude_chunk(
    wave_rest: np.ndarray,
    intrinsic_flux: np.ndarray,
    z_values: np.ndarray,
    bands: list[str],
    *,
    include_emission_lines: bool,
    include_igm: bool,
) -> np.ndarray:
    """Project one bounded redshift chunk into model AB magnitudes."""
    n_bands = len(bands)
    n_z = len(z_values)
    n_wave = len(wave_rest)

    # projection[band, z, wavelength] maps an observed-frame SED directly to
    # f_nu. It is _synthetic_mag's normalized Gaussian response and trapezoidal
    # integration expressed as linear weights. Zero initialization is
    # deliberate: uncovered band/redshift pairs are tracked by coverage and
    # never consumed as numerical model values.
    projection = np.zeros((n_bands, n_z, n_wave), dtype=float)
    coverage = np.zeros((n_bands, n_z), dtype=bool)
    igm = np.ones((n_z, n_wave), dtype=float)
    line_projection = np.zeros((n_bands, n_z), dtype=float)
    uv_indices = np.empty(n_z, dtype=np.intp)

    for iz, z in enumerate(z_values):
        wave_obs = wave_rest * (1 + z)
        integration_weights = _trapezoid_weights(wave_obs)

        if include_igm and z > 0.1:
            igm[iz] = _madau_igm(wave_obs, z)

        if include_emission_lines:
            line_profile = _emission_line_profile(wave_obs, z)
            uv_indices[iz] = np.argmin(np.abs(wave_obs - 1500 * (1 + z)))

        for ib, band in enumerate(bands):
            center, fwhm = _FILTER_PARAMS[band]
            sigma = fwhm / 2.3548
            response = np.exp(-0.5 * ((wave_obs - center) / sigma) ** 2)
            response_integral = np.trapezoid(response, wave_obs)
            if not np.isfinite(response_integral) or response_integral <= 0:
                continue
            response /= response_integral
            denominator = np.trapezoid(response * wave_obs**2 / wave_obs, wave_obs)
            if not np.isfinite(denominator) or denominator <= 0:
                continue

            band_projection = (
                integration_weights * response * wave_obs**2 / denominator
            )
            if not np.isfinite(band_projection).all():
                continue
            projection[ib, iz] = band_projection
            coverage[ib, iz] = True
            if include_emission_lines:
                line_projection[ib, iz] = band_projection @ line_profile

    # Emission lines are added after IGM attenuation in the scalar algorithm.
    # Their base strength is the attenuated flux nearest rest-frame 1500 A.
    if include_emission_lines:
        emission_strength = np.empty((len(intrinsic_flux), n_z), dtype=float)
        for iz, uv_idx in enumerate(uv_indices):
            emission_strength[:, iz] = np.maximum(
                intrinsic_flux[:, uv_idx] * igm[iz, uv_idx] * 0.05,
                0.0,
            )

    projection *= igm[np.newaxis, :, :]
    f_nu = (
        projection.reshape(n_bands * n_z, n_wave) @ intrinsic_flux.T
    ).reshape(n_bands, n_z, len(intrinsic_flux)).transpose(2, 1, 0)

    if include_emission_lines:
        f_nu += (
            emission_strength[:, :, np.newaxis]
            * line_projection.T[np.newaxis, :, :]
        )

    # NaN is an explicit "model has no finite coverage" marker. It becomes an
    # infinite chi-square in the fitter; it must not masquerade as magnitude 99.
    model_mags = np.full_like(f_nu, np.nan)
    covered = coverage.T[np.newaxis, :, :]
    finite_flux = covered & np.isfinite(f_nu)
    positive = finite_flux & (f_nu > 0)
    model_mags[positive] = -2.5 * np.log10(f_nu[positive]) - 48.60
    model_mags[finite_flux & (f_nu <= 0)] = 99.0
    return model_mags


def _iter_model_magnitude_chunks(
    wave_rest: np.ndarray,
    intrinsic_flux: np.ndarray,
    z_grid: np.ndarray,
    bands: list[str],
    *,
    include_emission_lines: bool,
    include_igm: bool,
    z_chunk_size: int | None = None,
):
    """Yield bounded projection chunks so memory does not scale as nz*nwave."""
    chunk_size = _MODEL_Z_CHUNK_SIZE if z_chunk_size is None else z_chunk_size
    if chunk_size <= 0:
        raise ValueError("z_chunk_size must be positive")
    for start in range(0, len(z_grid), chunk_size):
        stop = min(start + chunk_size, len(z_grid))
        yield start, stop, _model_magnitude_chunk(
            wave_rest,
            intrinsic_flux,
            z_grid[start:stop],
            bands,
            include_emission_lines=include_emission_lines,
            include_igm=include_igm,
        )


def _build_model_magnitude_grid(
    templates: list[dict],
    ebv_grid: list[float],
    z_grid: np.ndarray,
    bands: list[str],
    *,
    include_emission_lines: bool,
    include_igm: bool,
    z_chunk_size: int | None = None,
) -> tuple[np.ndarray, list[tuple[str, float]]]:
    """Compute every template/dust/redshift model without changing the grid.

    Filter curves, IGM transmission, dust attenuation, and line profiles are
    independent of at least one of the three fit axes.  Precomputing those
    linear projections gives the same synthetic photometry while evaluating
    all 30 x 8 template/dust combinations in one matrix multiplication.
    """
    wave_rest, intrinsic_flux, combo_metadata = _prepare_model_fluxes(
        templates,
        ebv_grid,
    )
    n_bands = len(bands)
    n_z = len(z_grid)
    model_mags = np.empty((len(intrinsic_flux), n_z, n_bands), dtype=float)
    for start, stop, chunk in _iter_model_magnitude_chunks(
        wave_rest,
        intrinsic_flux,
        z_grid,
        bands,
        include_emission_lines=include_emission_lines,
        include_igm=include_igm,
        z_chunk_size=z_chunk_size,
    ):
        model_mags[:, start:stop, :] = chunk
    return model_mags, combo_metadata

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
    valid = (
        np.isfinite(obs_mags)
        & (obs_mags < 90)
        & np.isfinite(obs_errs)
        & (obs_errs > 0)
        & known_filter
    )
    if np.sum(valid) < 2:
        return {"error": "Need at least 2 valid photometric bands with a known filter"}

    templates = _generate_sed_templates()
    z_grid = _make_redshift_grid(z_range, z_step)

    valid_bands = [band for band, is_valid in zip(bands, valid, strict=True) if is_valid]
    wave_rest, intrinsic_flux, combo_metadata = _prepare_model_fluxes(
        templates,
        ebv_grid,
    )

    # Stream model magnitudes in bounded redshift chunks. The only nz-sized
    # state is the one-dimensional marginalized log-likelihood, so a finer
    # z_step cannot allocate projection[band, nz, wavelength] or a full
    # model[template*dust, nz, band] cube.
    obs_valid = obs_mags[valid]
    errs_valid = obs_errs[valid]
    inv_variance = 1.0 / errs_valid**2
    inv_variance_sum = np.sum(inv_variance)
    log_pz = np.full(len(z_grid), -np.inf, dtype=float)
    best_chi2 = np.inf
    best_rank = len(combo_metadata) * len(z_grid)
    best_combo = 0
    best_iz = 0

    for start, stop, model_mags in _iter_model_magnitude_chunks(
        wave_rest,
        intrinsic_flux,
        z_grid,
        valid_bands,
        include_emission_lines=include_emission_lines,
        include_igm=include_igm,
    ):
        # A model cell must cover every input band used by the likelihood.
        # Reject incomplete cells with chi2=inf rather than silently dropping a
        # band or converting a non-finite synthetic magnitude to 99.
        complete_model = np.isfinite(model_mags).all(axis=2)
        safe_model_mags = np.where(np.isfinite(model_mags), model_mags, 0.0)
        offsets = np.sum(
            (obs_valid[np.newaxis, np.newaxis, :] - safe_model_mags)
            * inv_variance[np.newaxis, np.newaxis, :],
            axis=2,
        ) / inv_variance_sum
        residuals = (
            obs_valid[np.newaxis, np.newaxis, :]
            - safe_model_mags
            - offsets[:, :, np.newaxis]
        ) / errs_valid[np.newaxis, np.newaxis, :]
        chi2 = np.sum(residuals**2, axis=2)
        chi2[~complete_model | ~np.isfinite(chi2)] = np.inf

        finite_cells = np.isfinite(chi2)
        if finite_cells.any():
            chunk_best = float(np.min(chi2[finite_cells]))
            candidate_combo, candidate_local_iz = np.nonzero(chi2 == chunk_best)
            candidate_iz = candidate_local_iz + start
            candidate_ranks = candidate_combo * len(z_grid) + candidate_iz
            candidate_index = int(np.argmin(candidate_ranks))
            candidate_rank = int(candidate_ranks[candidate_index])
            if chunk_best < best_chi2 or (
                chunk_best == best_chi2 and candidate_rank < best_rank
            ):
                best_chi2 = chunk_best
                best_rank = candidate_rank
                best_combo = int(candidate_combo[candidate_index])
                best_iz = int(candidate_iz[candidate_index])

        # Marginalize over template/dust combinations in log space for each z.
        # The per-z minimum is only a numerical factor and is restored in
        # log_pz, so absolute fit quality remains in the posterior.
        min_chi2_at_z = np.min(chi2, axis=0)
        finite_z = np.isfinite(min_chi2_at_z)
        if finite_z.any():
            scaled_sum = np.zeros(np.sum(finite_z), dtype=float)
            for combo_chi2 in chi2:
                scaled_sum += np.exp(
                    -0.5 * (combo_chi2[finite_z] - min_chi2_at_z[finite_z])
                )
            log_pz[start:stop][finite_z] = (
                np.log(scaled_sum) - 0.5 * min_chi2_at_z[finite_z]
            )

    if not np.isfinite(best_chi2):
        return {
            "error": (
                "No finite model likelihood: at least one requested band has no "
                "template/filter coverage throughout z_range"
            )
        }

    best_z = z_grid[best_iz]
    best_template_name, best_ebv = combo_metadata[best_combo]
    finite_log_pz = np.isfinite(log_pz)
    pz = np.zeros(len(z_grid), dtype=float)
    pz[finite_log_pz] = np.exp(
        log_pz[finite_log_pz] - np.max(log_pz[finite_log_pz])
    )

    # Apply prior
    prior_applied = False
    i_is_valid = "i" in bands and bool(valid[bands.index("i")])
    if prior == "magnitude" and i_is_valid:
        i_mag = float(magnitudes["i"])
        # Simplified single-type magnitude prior inspired by Benitez 2000.
        # NOTE: Benitez 2000 (Table 1) uses three galaxy types with different
        # (alpha, z_mt0, k_mt) parameters; this is a single-type approximation
        # P(z|m) ∝ z^2 * exp(-(z/z0)^1.5), z0 = 0.055*i - 0.8.
        # Coefficients are calibrated for typical photo-z surveys but do NOT
        # reproduce the original Benitez prior exactly.
        z0 = max(0.055 * i_mag - 0.8, 0.01)
        positive_z = z_grid > 0
        log_prior = np.full(len(z_grid), -np.inf, dtype=float)
        log_prior[positive_z] = (
            2 * np.log(z_grid[positive_z]) - (z_grid[positive_z] / z0) ** 1.5
        )
        finite_log_prior = np.isfinite(log_prior)
        if finite_log_prior.any():
            prior_pz = np.zeros(len(z_grid), dtype=float)
            prior_pz[finite_log_prior] = np.exp(
                log_prior[finite_log_prior] - np.max(log_prior[finite_log_prior])
            )
            posterior_with_prior = pz * prior_pz
            if np.isfinite(posterior_with_prior).all() and np.any(
                posterior_with_prior > 0
            ):
                pz = posterior_with_prior
                prior_applied = True

    # Normalize P(z)
    pz_sum = np.trapezoid(pz, z_grid) if len(z_grid) > 1 else 0.0
    if np.isfinite(pz_sum) and pz_sum > 0:
        pz /= pz_sum
    elif len(z_grid) == 1 and pz[0] > 0:
        pz[0] = 1.0
    else:
        return {"error": "No finite posterior probability over z_range"}

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
        "prior_applied": prior_applied,
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
