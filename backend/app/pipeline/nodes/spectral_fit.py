"""SpectralFit node — fits a Gaussian or Lorentzian model to spectral data."""

import numpy as np
from scipy.optimize import curve_fit


def _gaussian(x, amplitude, mean, stddev):
    return amplitude * np.exp(-0.5 * ((x - mean) / stddev) ** 2)


def _lorentzian(x, amplitude, center, gamma):
    return amplitude * gamma**2 / ((x - center) ** 2 + gamma**2)


MODELS = {
    "gaussian": (_gaussian, ["amplitude", "mean", "stddev"]),
    "lorentzian": (_lorentzian, ["amplitude", "center", "gamma"]),
}


def spectral_fit(input_data: dict, params: dict) -> dict:
    """Fit a spectral line model to the data.

    params:
        model: str — "gaussian" (default) or "lorentzian"
        x_key: str — key for wavelength/index array (default "index")
        y_key: str — key for flux array (default "flux")
    """
    model_name = params.get("model", "gaussian")
    x_key = params.get("x_key", "index")
    y_key = params.get("y_key", "flux")

    if model_name not in MODELS:
        raise ValueError(f"Unknown model '{model_name}'. Choose from: {list(MODELS.keys())}")

    func, param_names = MODELS[model_name]
    data = input_data.get("data", {})

    x = np.array(data.get(x_key, []), dtype=float)
    y = np.array(data.get(y_key, []), dtype=float)

    if len(x) == 0 or len(y) == 0:
        raise ValueError("SpectralFit: empty input arrays")

    # Initial guesses
    peak_idx = np.argmax(y)
    p0 = [y[peak_idx], x[peak_idx], (x[-1] - x[0]) / 10]

    try:
        popt, pcov = curve_fit(func, x, y, p0=p0, maxfev=10000)
        perr = np.sqrt(np.diag(pcov))
    except RuntimeError as e:
        return {
            **input_data,
            "fit_result": {
                "model": model_name,
                "success": False,
                "error": str(e),
            },
        }

    fitted_y = func(x, *popt).tolist()
    residuals = (y - func(x, *popt)).tolist()

    fit_params = {}
    for name, val, err in zip(param_names, popt, perr):
        fit_params[name] = {"value": float(val), "error": float(err)}

    return {
        **input_data,
        "fit_result": {
            "model": model_name,
            "success": True,
            "params": fit_params,
            "fitted_y": fitted_y,
            "residuals": residuals,
            "chi_squared": float(np.sum(np.array(residuals) ** 2)),
        },
    }
