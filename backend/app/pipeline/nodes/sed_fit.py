"""SED fitting node — fit spectral energy distribution to blackbody or power-law models."""

import numpy as np
from scipy.optimize import curve_fit

# Physical constants (CGS)
_H_PLANCK = 6.62607015e-27   # erg s
_K_BOLTZ = 1.380649e-16      # erg K^-1
_C_LIGHT = 2.99792458e10     # cm s^-1


def _blackbody(wavelength_angstrom, temperature, norm):
    """Planck function: B_lambda in arbitrary normalized units.

    wavelength_angstrom: wavelength in Angstroms
    temperature: in Kelvin
    norm: overall scaling
    """
    lam_cm = wavelength_angstrom * 1e-8  # Angstrom -> cm
    exponent = (_H_PLANCK * _C_LIGHT) / (lam_cm * _K_BOLTZ * temperature)
    # Clip to avoid overflow
    exponent = np.clip(exponent, 0, 500)
    return norm * (2.0 * _H_PLANCK * _C_LIGHT**2 / lam_cm**5) / (np.exp(exponent) - 1.0)


def _power_law(wavelength_angstrom, index, norm):
    """Power-law model: F = norm * (lambda / lambda_ref)^index."""
    lam_ref = 5000.0  # reference wavelength in Angstroms
    return norm * (wavelength_angstrom / lam_ref) ** index


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
}


def sed_fit(input_data: dict, params: dict) -> dict:
    """Fit multi-band photometry to a blackbody or power-law model.

    params:
        model: str — "blackbody" (default) or "power_law"
        flux_key: str — key for flux/magnitude array (default "flux")
        wavelength_key: str — key for effective wavelength array (default "wavelength")
        flux_err_key: str — key for flux error array (optional)
    """
    model_name = params.get("model", "blackbody")
    flux_key = params.get("flux_key", "flux")
    wave_key = params.get("wavelength_key", "wavelength")
    flux_err_key = params.get("flux_err_key", None)

    if model_name not in MODELS:
        raise ValueError(
            f"Unknown SED model '{model_name}'. Choose from: {list(MODELS.keys())}"
        )

    data = input_data.get("data", {})
    wavelength = np.array(data.get(wave_key, []), dtype=float)
    flux = np.array(data.get(flux_key, []), dtype=float)

    if len(wavelength) == 0 or len(flux) == 0:
        raise ValueError("SEDFit: empty wavelength or flux array")

    if len(wavelength) != len(flux):
        raise ValueError("SEDFit: wavelength and flux arrays must have same length")

    # Optional flux errors for weighted fit
    sigma = None
    if flux_err_key and flux_err_key in data:
        sigma = np.array(data[flux_err_key], dtype=float)
        # Replace zeros/negatives with median to avoid division issues
        bad = sigma <= 0
        if np.any(bad):
            sigma[bad] = np.median(sigma[~bad]) if np.any(~bad) else 1.0

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
    except RuntimeError as e:
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

    # Chi-squared
    if sigma is not None:
        chi2 = float(np.sum((residuals / sigma) ** 2))
    else:
        chi2 = float(np.sum(residuals**2))

    dof = max(len(flux) - len(popt), 1)
    reduced_chi2 = chi2 / dof

    model_params = {}
    for name, val, err in zip(model_info["param_names"], popt, perr):
        model_params[name] = {"value": float(val), "error": float(err)}

    return {
        **input_data,
        "sed_fit_result": {
            "model": model_name,
            "success": True,
            "model_params": model_params,
            "chi_squared": chi2,
            "reduced_chi_squared": reduced_chi2,
            "fitted_fluxes": fitted_flux.tolist(),
            "residuals": residuals.tolist(),
        },
    }
