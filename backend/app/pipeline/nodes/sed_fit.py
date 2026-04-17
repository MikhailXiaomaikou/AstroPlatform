"""SED fitting node — fit spectral energy distribution to multiple models."""

import numpy as np
from scipy.optimize import curve_fit

# Physical constants (CGS)
_H_PLANCK = 6.62607015e-27   # erg s
_K_BOLTZ = 1.380649e-16      # erg K^-1
_C_LIGHT = 2.99792458e10     # cm s^-1


def _blackbody(wavelength_angstrom, temperature, norm):
    """Planck function: B_lambda in arbitrary normalized units."""
    lam_cm = wavelength_angstrom * 1e-8
    exponent = (_H_PLANCK * _C_LIGHT) / (lam_cm * _K_BOLTZ * temperature)
    exponent = np.clip(exponent, 1e-10, 500)
    return norm * (2.0 * _H_PLANCK * _C_LIGHT**2 / lam_cm**5) / (np.exp(exponent) - 1.0)


def _power_law(wavelength_angstrom, index, norm):
    """Power-law model: F = norm * (lambda / lambda_ref)^index."""
    lam_ref = 5000.0
    return norm * (wavelength_angstrom / lam_ref) ** index


def _modified_blackbody(wavelength_angstrom, temperature, beta, norm):
    """Modified blackbody (greybody): B_lambda * (lambda_0/lambda)^beta.

    Common for dust emission where beta ~ 1.5-2.0 (dust emissivity index).
    """
    lam_cm = wavelength_angstrom * 1e-8
    lam_ref_cm = 2500.0 * 1e-8  # reference wavelength 2500 A
    exponent = (_H_PLANCK * _C_LIGHT) / (lam_cm * _K_BOLTZ * temperature)
    exponent = np.clip(exponent, 1e-10, 500)
    bb = (2.0 * _H_PLANCK * _C_LIGHT**2 / lam_cm**5) / (np.exp(exponent) - 1.0)
    dust_term = (lam_ref_cm / lam_cm) ** beta
    return norm * bb * dust_term


def _composite(wavelength_angstrom, temp_hot, norm_hot, temp_cool, norm_cool):
    """Two-temperature composite: hot BB + cool BB (e.g. stellar + dust)."""
    return (_blackbody(wavelength_angstrom, temp_hot, norm_hot)
            + _blackbody(wavelength_angstrom, temp_cool, norm_cool))


MODELS = {
    "blackbody": {
        "func": _blackbody,
        "param_names": ["temperature", "normalization"],
        "p0_func": lambda w, f: [5000.0, np.max(f) * 1e-10],
        "bounds": ([100.0, 0.0], [1e6, np.inf]),
    },
    "power_law": {
        "func": _power_law,
        "param_names": ["index", "normalization"],
        "p0_func": lambda w, f: [-2.0, np.median(f)],
        "bounds": ([-10.0, 0.0], [10.0, np.inf]),
    },
    "modified_blackbody": {
        "func": _modified_blackbody,
        "param_names": ["temperature", "beta", "normalization"],
        "p0_func": lambda w, f: [
            # Estimate T from Wien's law using peak wavelength
            2.8977719e7 / max(w[np.argmax(f)], 100.0),
            1.5,
            np.max(f) * 1e-10,
        ],
        "bounds": ([5.0, 0.0, 0.0], [1e5, 5.0, np.inf]),
    },
    "composite": {
        "func": _composite,
        "param_names": ["temp_hot", "norm_hot", "temp_cool", "norm_cool"],
        "p0_func": lambda w, f: [10000.0, np.max(f) * 1e-10, 300.0, np.max(f) * 1e-12],
        "bounds": ([1000.0, 0.0, 10.0, 0.0], [1e6, np.inf, 5000.0, np.inf]),
    },
}


def sed_fit(input_data: dict, params: dict) -> dict:
    """Fit multi-band photometry to a model.

    params:
        model: str — "blackbody" (default), "power_law", "modified_blackbody", or "composite"
        flux_key: str — key for flux/magnitude array (default "flux")
        wavelength_key: str — key for effective wavelength array (default "wavelength")
        flux_err_key: str — key for flux error array (optional)
        auto_model: bool — if True, try all models and pick best reduced chi2 (default False)
    """
    model_name = params.get("model", "blackbody")
    flux_key = params.get("flux_key", "flux")
    wave_key = params.get("wavelength_key", "wavelength")
    flux_err_key = params.get("flux_err_key", None)
    auto_model = params.get("auto_model", False)

    data = input_data.get("data", {})
    wavelength = np.array(data.get(wave_key, []), dtype=float)
    flux = np.array(data.get(flux_key, []), dtype=float)

    if len(wavelength) == 0 or len(flux) == 0:
        raise ValueError("SEDFit: empty wavelength or flux array")

    if len(wavelength) != len(flux):
        raise ValueError("SEDFit: wavelength and flux arrays must have same length")

    # Remove NaN/Inf values
    valid = np.isfinite(wavelength) & np.isfinite(flux) & (flux > 0)
    if np.sum(valid) < 2:
        raise ValueError("SEDFit: not enough valid data points after filtering NaN/negative values")
    wavelength = wavelength[valid]
    flux = flux[valid]

    # Optional flux errors for weighted fit
    sigma = None
    if flux_err_key and flux_err_key in data:
        sigma_raw = np.array(data[flux_err_key], dtype=float)[valid]
        bad = (sigma_raw <= 0) | ~np.isfinite(sigma_raw)
        if np.any(bad):
            sigma_raw[bad] = np.median(sigma_raw[~bad]) if np.any(~bad) else 1.0
        sigma = sigma_raw

    if auto_model:
        return _fit_all_models(input_data, wavelength, flux, sigma)

    if model_name not in MODELS:
        raise ValueError(
            f"Unknown SED model '{model_name}'. Choose from: {list(MODELS.keys())}"
        )

    return _fit_single_model(input_data, model_name, wavelength, flux, sigma)


def _fit_single_model(input_data, model_name, wavelength, flux, sigma):
    """Fit a single model and return results."""
    model_info = MODELS[model_name]
    func = model_info["func"]
    p0 = model_info["p0_func"](wavelength, flux)
    bounds = model_info["bounds"]

    try:
        popt, pcov = curve_fit(
            func, wavelength, flux, p0=p0, sigma=sigma,
            bounds=bounds, maxfev=10000
        )
        perr = np.sqrt(np.diag(pcov))
    except (RuntimeError, ValueError) as e:
        return {
            **input_data,
            "sed_fit_result": {
                "model": model_name,
                "success": False,
                "error": str(e),
            },
        }

    fitted_flux = func(wavelength, *popt)
    residuals = flux - fitted_flux

    if sigma is not None:
        chi2 = float(np.sum((residuals / sigma) ** 2))
    else:
        chi2 = float(np.sum(residuals**2))

    dof = max(len(flux) - len(popt), 1)
    reduced_chi2 = chi2 / dof

    # BIC for model comparison
    n = len(flux)
    k = len(popt)
    bic = chi2 + k * np.log(n) if n > 0 else chi2

    model_params = {}
    for name, val, err in zip(model_info["param_names"], popt, perr):
        model_params[name] = {"value": float(val), "error": float(err)}

    # Add derived quantities
    derived = {}
    if model_name in ("blackbody", "modified_blackbody", "composite"):
        temp = popt[0]
        # M23: curve_fit occasionally walks outside the stated bounds on
        # degenerate fits, producing temp ≤ 0.  Refuse rather than divide
        # by zero / produce a negative peak wavelength.
        if not (temp > 0) or not np.isfinite(temp):
            raise ValueError(
                f"SEDFit: fitted temperature {temp!r} is non-positive; "
                f"fit did not converge."
            )
        # Wien's displacement law: lambda_peak = b / T
        wien_b = 2.8977719e7  # Angstroms * Kelvin
        derived["peak_wavelength_angstrom"] = float(wien_b / temp)
        # Luminosity proxy (Stefan-Boltzmann)
        derived["luminosity_proxy"] = float(5.67e-5 * temp**4)

    if model_name == "composite":
        derived["temp_ratio"] = float(popt[0] / popt[2])

    return {
        **input_data,
        "sed_fit_result": {
            "model": model_name,
            "success": True,
            "model_params": model_params,
            "chi_squared": chi2,
            "reduced_chi_squared": reduced_chi2,
            "bic": float(bic),
            "fitted_fluxes": fitted_flux.tolist(),
            "residuals": residuals.tolist(),
            "derived": derived,
        },
    }


def _fit_all_models(input_data, wavelength, flux, sigma):
    """Try all models and return the best one by reduced chi2."""
    best_result = None
    best_chi2 = float("inf")
    all_results = {}

    for model_name in MODELS:
        try:
            result = _fit_single_model({}, model_name, wavelength, flux, sigma)
            fit = result.get("sed_fit_result", {})
            if fit.get("success"):
                rchi2 = fit["reduced_chi_squared"]
                all_results[model_name] = {
                    "reduced_chi_squared": rchi2,
                    "bic": fit.get("bic", 0),
                    "model_params": fit["model_params"],
                }
                if rchi2 < best_chi2:
                    best_chi2 = rchi2
                    best_result = result
        except Exception:
            continue

    if best_result is None:
        return {
            **input_data,
            "sed_fit_result": {"success": False, "error": "All models failed to converge"},
        }

    best_fit = best_result["sed_fit_result"]
    best_fit["model_comparison"] = all_results
    return {
        **input_data,
        "sed_fit_result": best_fit,
    }
