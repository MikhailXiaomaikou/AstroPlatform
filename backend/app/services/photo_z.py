"""Template-based photometric redshift estimation.

Fits broadband photometry against a library of 7 simplified SED templates
(E, Sbc, Scd, Im, SB1, SB2, SB3) over a redshift grid to produce z_phot,
uncertainties, and the full P(z).  Uses only numpy/scipy — no external SED
libraries required.
"""

import numpy as np

# NumPy 2.0 renamed trapz -> trapezoid
_trapz = getattr(np, "trapezoid", None) or np.trapz


# ---------------------------------------------------------------------------
# 1.  SED Templates  (simplified parametric forms, 3000–25000 Angstrom)
# ---------------------------------------------------------------------------

def _make_wavelength_grid(n=50):
    """Return a rest-frame wavelength grid from 3000 to 25000 A."""
    return np.linspace(3000.0, 25000.0, n)


def _template_elliptical(wave):
    """E — strong 4000A break, red continuum declining blueward."""
    flux = np.where(
        wave < 4000.0,
        0.2 * (wave / 4000.0) ** 3,
        1.0 * (wave / 4000.0) ** (-1.5),
    )
    return flux / flux.max()


def _template_sbc(wave):
    """Sbc — moderate 4000A break, intermediate colour."""
    flux = np.where(
        wave < 4000.0,
        0.4 * (wave / 4000.0) ** 2,
        0.9 * (wave / 4000.0) ** (-0.8),
    )
    return flux / flux.max()


def _template_scd(wave):
    """Scd — weak break, bluer continuum."""
    flux = np.where(
        wave < 4000.0,
        0.6 * (wave / 4000.0) ** 1.5,
        0.85 * (wave / 4000.0) ** (-0.4),
    )
    return flux / flux.max()


def _template_irregular(wave):
    """Im — blue, relatively flat."""
    flux = np.where(
        wave < 4000.0,
        0.75 * (wave / 4000.0) ** 0.8,
        0.80 * (wave / 4000.0) ** (-0.2),
    )
    return flux / flux.max()


def _template_sb1(wave):
    """SB1 — starburst, UV-bright."""
    flux = np.where(
        wave < 4000.0,
        0.85 * (wave / 4000.0) ** 0.3,
        0.75 * (wave / 4000.0) ** (-0.1),
    )
    return flux / flux.max()


def _template_sb2(wave):
    """SB2 — stronger starburst, more UV flux."""
    flux = np.where(
        wave < 4000.0,
        0.90 * (wave / 4000.0) ** 0.1,
        0.70 * (wave / 4000.0) ** (0.0),
    )
    return flux / flux.max()


def _template_sb3(wave):
    """SB3 — extreme starburst, rising UV."""
    flux = np.where(
        wave < 4000.0,
        1.0 * (wave / 4000.0) ** (-0.2),
        0.65 * (wave / 4000.0) ** (0.1),
    )
    return flux / flux.max()


TEMPLATE_NAMES = ["E", "Sbc", "Scd", "Im", "SB1", "SB2", "SB3"]
_TEMPLATE_FUNCS = [
    _template_elliptical,
    _template_sbc,
    _template_scd,
    _template_irregular,
    _template_sb1,
    _template_sb2,
    _template_sb3,
]


def _build_templates(n_wave=50):
    """Return (wave, list_of_flux_arrays) for all 7 templates."""
    wave = _make_wavelength_grid(n_wave)
    fluxes = [fn(wave) for fn in _TEMPLATE_FUNCS]
    return wave, fluxes


# ---------------------------------------------------------------------------
# 2.  Filter response curves  (Gaussian approximations)
# ---------------------------------------------------------------------------

# (name, central_wavelength_A, FWHM_A)
_FILTER_DEFS = {
    # SDSS
    "u": (3551.0, 560.0),
    "g": (4686.0, 1390.0),
    "r": (6166.0, 1370.0),
    "i": (7480.0, 1530.0),
    "z": (8932.0, 1370.0),
    # 2MASS
    "J": (12350.0, 1620.0),
    "H": (16620.0, 2510.0),
    "Ks": (21590.0, 2620.0),
    # WISE
    "W1": (33526.0, 6626.0),
    "W2": (46028.0, 10423.0),
}

ALL_BANDS = list(_FILTER_DEFS.keys())


def _gaussian_filter(wave, centre, fwhm):
    """Return a normalised Gaussian response evaluated on *wave*."""
    sigma = fwhm / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    resp = np.exp(-0.5 * ((wave - centre) / sigma) ** 2)
    return resp


# ---------------------------------------------------------------------------
# 3.  Synthetic photometry helpers
# ---------------------------------------------------------------------------

def _synth_mag(wave, flux, band):
    """Compute the AB magnitude of *flux(wave)* in a given band.

    Uses the standard flux-weighted integral:
        <f> = int(f * R * dlambda) / int(R * dlambda)
    Then converts to AB magnitude: m = -2.5 log10(<f>) + 23.9
    where the reference is the AB zero-point flux density
    (f_nu = 3631 Jy  =>  the 23.9 offset absorbs the conversion when the
     templates are on an arbitrary relative scale — the amplitude is
     analytically marginalised later, so the zero-point constant cancels).
    """
    centre, fwhm = _FILTER_DEFS[band]
    resp = _gaussian_filter(wave, centre, fwhm)
    # Trapezoidal integration
    num = _trapz(flux * resp, wave)
    den = _trapz(resp, wave)
    if den == 0 or num <= 0:
        return np.nan
    mean_flux = num / den
    return -2.5 * np.log10(mean_flux)


def _mag_to_flux(mag):
    """AB magnitude -> linear flux (arbitrary normalisation)."""
    return 10.0 ** (-0.4 * (mag - 23.9))


# ---------------------------------------------------------------------------
# 4.  Chi-squared template fitting
# ---------------------------------------------------------------------------

def estimate_photo_z_template(magnitudes: dict, mag_errors: dict) -> dict:
    """Estimate photometric redshift via chi-squared template fitting.

    Parameters
    ----------
    magnitudes : dict
        Observed magnitudes keyed by band name (e.g. ``{"g": 21.3, "r": 20.8}``).
        Use ``None`` or ``float('nan')`` for missing / unobserved bands.
    mag_errors : dict
        1-sigma magnitude uncertainties, same keys as *magnitudes*.

    Returns
    -------
    dict
        z_phot       : best-fit photometric redshift
        z_err        : half-width of the delta-chi2 < 1 region
        pdf_z        : normalised P(z) array  (list)
        z_grid       : redshift grid           (list)
        best_template: name of the best-fit SED template
        chi2_min     : minimum chi2 value
    """
    # --- parse observed data, skipping missing bands ---
    bands_used = []
    f_obs = []
    f_err = []
    for band in ALL_BANDS:
        mag = magnitudes.get(band)
        err = mag_errors.get(band)
        if mag is None or err is None:
            continue
        if np.isnan(mag) or np.isnan(err):
            continue
        if err <= 0:
            continue
        bands_used.append(band)
        f = _mag_to_flux(mag)
        # Error propagation: sigma_f = f * ln(10)/2.5 * sigma_m
        sigma_f = f * (np.log(10.0) / 2.5) * err
        f_obs.append(f)
        f_err.append(sigma_f)

    if len(bands_used) < 2:
        raise ValueError(
            "At least 2 valid photometric bands are required; "
            f"got {len(bands_used)}."
        )

    f_obs = np.array(f_obs)
    f_err = np.array(f_err)

    # --- build templates ---
    wave_rest, template_fluxes = _build_templates(n_wave=80)

    # --- redshift grid ---
    z_grid = np.linspace(0.0, 2.0, 401)

    # --- chi2 cube:  (n_z, n_templates) ---
    chi2_cube = np.full((len(z_grid), len(TEMPLATE_NAMES)), np.inf)

    for iz, z in enumerate(z_grid):
        wave_obs = wave_rest * (1.0 + z)

        for it, t_flux_rest in enumerate(template_fluxes):
            # synthetic magnitudes through each used filter
            f_model = np.empty(len(bands_used))
            valid = True
            for ib, band in enumerate(bands_used):
                smag = _synth_mag(wave_obs, t_flux_rest, band)
                if np.isnan(smag):
                    valid = False
                    break
                f_model[ib] = _mag_to_flux(smag)

            if not valid:
                continue

            # Analytical amplitude marginalisation
            w = 1.0 / f_err ** 2
            A_num = np.sum(f_obs * f_model * w)
            A_den = np.sum(f_model ** 2 * w)
            if A_den <= 0:
                continue
            A_best = A_num / A_den

            chi2 = np.sum((f_obs - A_best * f_model) ** 2 * w)
            chi2_cube[iz, it] = chi2

    # --- best fit ---
    idx = np.unravel_index(np.argmin(chi2_cube), chi2_cube.shape)
    iz_best, it_best = idx
    chi2_min = chi2_cube[iz_best, it_best]
    z_phot = z_grid[iz_best]
    best_template = TEMPLATE_NAMES[it_best]

    # --- P(z) from marginalisation over templates ---
    # P(z) proportional to sum_T exp(-0.5 * chi2(z,T))
    chi2_min_per_z = np.min(chi2_cube, axis=1)
    # Subtract the global min to avoid overflow
    delta_chi2 = chi2_min_per_z - chi2_min
    pdf_z = np.exp(-0.5 * delta_chi2)
    # Normalise (trapezoidal)
    norm = _trapz(pdf_z, z_grid)
    if norm > 0:
        pdf_z = pdf_z / norm

    # --- z_err: half-width of delta_chi2 < 1 region ---
    mask = delta_chi2 < 1.0
    if np.any(mask):
        z_lo = z_grid[mask].min()
        z_hi = z_grid[mask].max()
        z_err = (z_hi - z_lo) / 2.0
    else:
        z_err = z_grid[1] - z_grid[0]  # single grid step

    return {
        "z_phot": float(z_phot),
        "z_err": float(z_err),
        "pdf_z": pdf_z.tolist(),
        "z_grid": z_grid.tolist(),
        "best_template": best_template,
        "chi2_min": float(chi2_min),
    }


# ---------------------------------------------------------------------------
# 5.  Empirical color–redshift estimator  (ML-style, no heavy dependencies)
# ---------------------------------------------------------------------------

# Ordered color pairs — each element is (bluer_band, redder_band).
_COLOR_PAIRS = [
    ("u", "g"),
    ("g", "r"),
    ("r", "i"),
    ("i", "z"),
    ("z", "J"),
    ("J", "H"),
    ("H", "Ks"),
    ("W1", "W2"),
]


def _compute_colors(magnitudes: dict, mag_errors: dict | None = None):
    """Compute colours from adjacent bands.

    Returns
    -------
    colors : list[float]
        One value per color pair.  ``float('nan')`` when either band is
        missing.
    color_errs : list[float]
        Propagated 1-sigma uncertainties (quadrature sum).
    """
    colors = []
    color_errs = []
    for b1, b2 in _COLOR_PAIRS:
        m1 = magnitudes.get(b1)
        m2 = magnitudes.get(b2)
        if m1 is None or m2 is None or np.isnan(m1) or np.isnan(m2):
            colors.append(float("nan"))
            color_errs.append(float("nan"))
        else:
            colors.append(m1 - m2)
            e1 = (mag_errors or {}).get(b1, 0.0) or 0.0
            e2 = (mag_errors or {}).get(b2, 0.0) or 0.0
            color_errs.append(np.sqrt(e1 ** 2 + e2 ** 2))
    return colors, color_errs


def estimate_photo_z_ml(magnitudes: dict, mag_errors: dict | None = None) -> dict:
    """Estimate photometric redshift using empirical colour–redshift relations.

    Uses a polynomial mapping of optical/NIR colours to redshift, calibrated
    on the typical locus of field galaxies.  No external ML library is
    required — the polynomial coefficients are hard-coded from simplified
    empirical relations.

    Parameters
    ----------
    magnitudes : dict
        Observed magnitudes keyed by band name (e.g. ``{"g": 21.3, "r": 20.8}``).
    mag_errors : dict, optional
        1-sigma magnitude uncertainties, same keys as *magnitudes*.

    Returns
    -------
    dict
        z_phot   : estimated photometric redshift
        z_err    : propagated uncertainty estimate
        pdf_z    : Gaussian P(z) (list of floats)
        z_grid   : redshift grid (list of floats)
        method   : ``'empirical_colors'``
        colors_used : number of valid colours used
    """
    if mag_errors is None:
        mag_errors = {}

    colors, color_errs = _compute_colors(magnitudes, mag_errors)

    # Indices into _COLOR_PAIRS
    IDX_GR = 1   # g-r
    IDX_RI = 2   # r-i

    gr = colors[IDX_GR]
    ri = colors[IDX_RI]

    # ---- Primary polynomial: z ~ 0.1 + 0.5*(g-r) + 0.3*(r-i) - 0.1*(g-r)^2
    # Requires at least g-r.
    if np.isnan(gr):
        raise ValueError(
            "At least g and r band magnitudes are required for the "
            "empirical color estimator."
        )

    ri_val = ri if not np.isnan(ri) else 0.0  # default contribution = 0

    z_phot = 0.1 + 0.5 * gr + 0.3 * ri_val - 0.1 * gr ** 2

    # ---- Error propagation through the polynomial ----
    # dz/d(g-r) = 0.5 - 0.2*(g-r)
    # dz/d(r-i) = 0.3
    dz_dgr = 0.5 - 0.2 * gr
    dz_dri = 0.3

    e_gr = color_errs[IDX_GR] if not np.isnan(color_errs[IDX_GR]) else 0.1
    e_ri = color_errs[IDX_RI] if not np.isnan(color_errs[IDX_RI]) else 0.1

    z_err = np.sqrt((dz_dgr * e_gr) ** 2 + (dz_dri * e_ri) ** 2)
    # Floor the error at 0.02 (fundamental scatter of the empirical relation)
    z_err = max(float(z_err), 0.02)

    # Clamp z_phot to a physically plausible range
    z_phot = float(max(z_phot, 0.0))

    # ---- Gaussian P(z) ----
    z_grid = np.linspace(0.0, 2.0, 401)
    pdf_z = np.exp(-0.5 * ((z_grid - z_phot) / z_err) ** 2)
    norm = _trapz(pdf_z, z_grid)
    if norm > 0:
        pdf_z = pdf_z / norm

    n_valid = sum(1 for c in colors if not np.isnan(c))

    return {
        "z_phot": float(z_phot),
        "z_err": float(z_err),
        "pdf_z": pdf_z.tolist(),
        "z_grid": z_grid.tolist(),
        "method": "empirical_colors",
        "colors_used": n_valid,
    }


# ---------------------------------------------------------------------------
# 6.  Unified wrapper
# ---------------------------------------------------------------------------

def estimate_photo_z(
    magnitudes: dict,
    mag_errors: dict | None = None,
    method: str = "hybrid",
) -> dict:
    """Estimate photometric redshift from multi-band photometry.

    Parameters
    ----------
    magnitudes : dict
        Observed magnitudes keyed by band name.
    mag_errors : dict, optional
        1-sigma magnitude uncertainties.
    method : str
        ``'template'`` — chi-squared template fitting only.
        ``'ml'``       — empirical colour–redshift polynomial only.
        ``'hybrid'``   — run both and return an inverse-variance weighted
                         average, with individual estimates in *details*.

    Returns
    -------
    dict
        z_phot, z_err, pdf_z, z_grid, method, and (for hybrid) details.
    """
    if mag_errors is None:
        mag_errors = {}

    if method == "template":
        return estimate_photo_z_template(magnitudes, mag_errors)

    if method == "ml":
        return estimate_photo_z_ml(magnitudes, mag_errors)

    if method != "hybrid":
        raise ValueError(f"Unknown method '{method}'; use 'template', 'ml', or 'hybrid'.")

    # ---- Hybrid: run both, combine via inverse-variance weighting ----
    errors: list[str] = []
    template_result = None
    ml_result = None

    try:
        template_result = estimate_photo_z_template(magnitudes, mag_errors)
    except Exception as exc:
        errors.append(f"template: {exc}")

    try:
        ml_result = estimate_photo_z_ml(magnitudes, mag_errors)
    except Exception as exc:
        errors.append(f"ml: {exc}")

    # If only one succeeded, return that one
    if template_result is None and ml_result is None:
        raise ValueError(
            "Both estimators failed. " + "; ".join(errors)
        )
    if template_result is None:
        ml_result["details"] = {"ml": ml_result.copy(), "template_error": errors[0] if errors else None}
        ml_result["method"] = "hybrid (ml only)"
        return ml_result
    if ml_result is None:
        template_result["details"] = {"template": template_result.copy(), "ml_error": errors[0] if errors else None}
        template_result["method"] = "hybrid (template only)"
        return template_result

    # Both succeeded — inverse-variance weighted average
    z_t = template_result["z_phot"]
    e_t = template_result["z_err"]
    z_m = ml_result["z_phot"]
    e_m = ml_result["z_err"]

    w_t = 1.0 / e_t ** 2 if e_t > 0 else 0.0
    w_m = 1.0 / e_m ** 2 if e_m > 0 else 0.0
    w_sum = w_t + w_m

    if w_sum > 0:
        z_combined = (w_t * z_t + w_m * z_m) / w_sum
        z_err_combined = np.sqrt(1.0 / w_sum)
    else:
        z_combined = (z_t + z_m) / 2.0
        z_err_combined = max(e_t, e_m)

    # Combined Gaussian P(z)
    z_grid = np.linspace(0.0, 2.0, 401)
    pdf_z = np.exp(-0.5 * ((z_grid - z_combined) / z_err_combined) ** 2)
    norm = _trapz(pdf_z, z_grid)
    if norm > 0:
        pdf_z = pdf_z / norm

    return {
        "z_phot": float(z_combined),
        "z_err": float(z_err_combined),
        "pdf_z": pdf_z.tolist(),
        "z_grid": z_grid.tolist(),
        "method": "hybrid",
        "details": {
            "template": template_result,
            "ml": ml_result,
        },
    }
