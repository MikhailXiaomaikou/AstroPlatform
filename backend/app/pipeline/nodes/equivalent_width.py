"""Equivalent width node — measure EW of absorption/emission spectral lines."""

import numpy as np
from scipy.integrate import trapezoid


def equivalent_width(input_data: dict, params: dict) -> dict:
    """Measure the equivalent width of a spectral line.

    params:
        line_center: float — central wavelength of the line (required)
        continuum_window: list[float, float] — [lo, hi] wavelength range for
            continuum estimation (default: line_center +/- 50 A, excluding line)
        line_window: list[float, float] — [lo, hi] wavelength range for
            the line integration (default: line_center +/- 10 A)
        flux_key: str — key for flux array (default "flux")
        wavelength_key: str — key for wavelength array (default "wavelength")
        continuum_method: str — "linear" (default), "median", "polynomial", "spline"
        poly_order: int — polynomial order for "polynomial" method (default 3)
    """
    flux_key = params.get("flux_key", "flux")
    wave_key = params.get("wavelength_key", "wavelength")
    line_center = params.get("line_center")
    continuum_method = params.get("continuum_method", "linear")
    poly_order = int(params.get("poly_order", 3))

    if line_center is None:
        raise ValueError("EquivalentWidth: 'line_center' parameter is required")

    line_center = float(line_center)

    data = input_data.get("data", {})
    flux = np.array(data.get(flux_key, []), dtype=float)
    wavelength = np.array(data.get(wave_key, []), dtype=float)

    if len(flux) == 0 or len(wavelength) == 0:
        raise ValueError("EquivalentWidth: empty flux or wavelength array")

    # Default windows
    line_window = params.get("line_window", [line_center - 10.0, line_center + 10.0])
    continuum_window = params.get(
        "continuum_window", [line_center - 50.0, line_center + 50.0]
    )

    line_lo, line_hi = float(line_window[0]), float(line_window[1])
    cont_lo, cont_hi = float(continuum_window[0]), float(continuum_window[1])

    # Select continuum pixels (in continuum window but outside line window)
    cont_mask = (
        (wavelength >= cont_lo)
        & (wavelength <= cont_hi)
        & ~((wavelength >= line_lo) & (wavelength <= line_hi))
    )

    if np.sum(cont_mask) < 2:
        raise ValueError(
            "EquivalentWidth: not enough continuum pixels. "
            "Adjust continuum_window or line_window."
        )

    cont_wave = wavelength[cont_mask]
    cont_flux = flux[cont_mask]

    # Build continuum model based on method
    if continuum_method == "median":
        cont_level_val = float(np.median(cont_flux))
        def continuum_model(w, _v=cont_level_val):
            return np.full_like(w, _v)
    elif continuum_method == "polynomial":
        order = min(poly_order, len(cont_wave) - 1)
        coeffs = np.polyfit(cont_wave, cont_flux, deg=order)
        continuum_model = np.poly1d(coeffs)
    elif continuum_method == "spline":
        try:
            from scipy.interpolate import UnivariateSpline
            spl = UnivariateSpline(cont_wave, cont_flux, s=len(cont_wave))
            continuum_model = spl
        except Exception:
            # Fall back to linear
            coeffs = np.polyfit(cont_wave, cont_flux, deg=1)
            continuum_model = np.poly1d(coeffs)
    else:
        # linear (default)
        coeffs = np.polyfit(cont_wave, cont_flux, deg=1)
        continuum_model = np.poly1d(coeffs)

    # Select line pixels
    line_mask = (wavelength >= line_lo) & (wavelength <= line_hi)
    if np.sum(line_mask) < 2:
        raise ValueError("EquivalentWidth: not enough pixels in line window")

    line_wave = wavelength[line_mask]
    line_flux = flux[line_mask]
    line_continuum = continuum_model(line_wave)

    # Guard against zero or near-zero continuum
    safe_continuum = np.where(np.abs(line_continuum) < 1e-30, 1e-30, line_continuum)

    # Equivalent width: integral of (1 - F_line / F_continuum) dλ
    # Positive EW = absorption, negative EW = emission
    integrand = 1.0 - line_flux / safe_continuum
    ew_value = float(trapezoid(integrand, line_wave))

    # Line flux (integrated flux above/below continuum)
    line_flux_integrated = float(trapezoid(line_flux - line_continuum, line_wave))

    # Error estimate via continuum RMS
    cont_residuals = cont_flux - continuum_model(cont_wave)
    cont_rms = float(np.std(cont_residuals))
    cont_level = float(np.mean(line_continuum))

    n_line_pix = np.sum(line_mask)
    if len(line_wave) > 1:
        delta_lambda = float(np.median(np.diff(line_wave)))
    else:
        delta_lambda = 1.0

    ew_error = float(
        np.sqrt(n_line_pix) * delta_lambda * cont_rms / max(cont_level, 1e-30)
    )

    # Signal-to-noise of the line detection
    snr = abs(ew_value) / ew_error if ew_error > 0 else 0.0

    # Line type classification
    if ew_value > 0:
        line_type = "absorption"
    elif ew_value < 0:
        line_type = "emission"
    else:
        line_type = "none"

    return {
        **input_data,
        "equivalent_width_result": {
            "ew_value": ew_value,
            "ew_error": ew_error,
            "snr": float(snr),
            "line_type": line_type,
            "continuum_level": cont_level,
            "continuum_rms": cont_rms,
            "continuum_method": continuum_method,
            "line_flux": line_flux_integrated,
            "line_center": line_center,
            "line_window": [line_lo, line_hi],
            "continuum_window": [cont_lo, cont_hi],
            "n_line_pixels": int(n_line_pix),
        },
    }
