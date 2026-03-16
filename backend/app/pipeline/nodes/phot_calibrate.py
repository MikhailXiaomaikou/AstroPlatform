"""Photometric calibration node — apply zeropoint and extinction correction."""

import numpy as np


def phot_calibrate(input_data: dict, params: dict) -> dict:
    """Apply photometric calibration: convert instrumental flux to calibrated magnitudes.

    mag = -2.5 * log10(flux) + zeropoint - extinction_coeff * airmass

    params:
        zeropoint: float — photometric zeropoint (required)
        extinction_coeff: float — atmospheric extinction coefficient (default 0.0)
        airmass: float — observation airmass (default 1.0)
        flux_key: str — key for instrumental flux array (default "flux")
        flux_err_key: str — key for flux error array (optional)
    """
    zeropoint = params.get("zeropoint")
    if zeropoint is None:
        raise ValueError("PhotCalibrate: 'zeropoint' parameter is required")

    zeropoint = float(zeropoint)
    extinction_coeff = float(params.get("extinction_coeff", 0.0))
    airmass = float(params.get("airmass", 1.0))
    flux_key = params.get("flux_key", "flux")
    flux_err_key = params.get("flux_err_key", None)

    data = input_data.get("data", {})
    flux = np.array(data.get(flux_key, []), dtype=float)

    if len(flux) == 0:
        raise ValueError(f"PhotCalibrate: no '{flux_key}' column in input data")

    # Handle non-positive flux values
    valid = flux > 0
    magnitudes = np.full_like(flux, np.nan)
    magnitudes[valid] = (
        -2.5 * np.log10(flux[valid]) + zeropoint - extinction_coeff * airmass
    )

    # Propagate flux errors to magnitude errors if available
    mag_errors = np.full_like(flux, np.nan)
    if flux_err_key and flux_err_key in data:
        flux_err = np.array(data[flux_err_key], dtype=float)
        # mag_err = 2.5 / ln(10) * flux_err / flux  (for positive flux)
        mag_errors[valid] = (2.5 / np.log(10.0)) * flux_err[valid] / flux[valid]

    output_data = dict(data)
    output_data["calibrated_mag"] = magnitudes.tolist()
    output_data["mag_error"] = mag_errors.tolist()

    n_valid = int(np.sum(valid))
    n_invalid = int(np.sum(~valid))

    return {
        **input_data,
        "data": output_data,
        "phot_calibrate_result": {
            "zeropoint": zeropoint,
            "extinction_coeff": extinction_coeff,
            "airmass": airmass,
            "n_calibrated": n_valid,
            "n_invalid_flux": n_invalid,
            "mag_range": [float(np.nanmin(magnitudes)), float(np.nanmax(magnitudes))]
            if n_valid > 0
            else None,
        },
    }
