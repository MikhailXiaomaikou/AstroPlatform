"""Redshift estimation node — cross-correlate spectrum against emission line templates."""

import numpy as np
from scipy.signal import correlate


# Common rest-frame emission/absorption lines in Angstroms
DEFAULT_LINES = {
    "Lyman-alpha": 1216.0,
    "MgII": 2798.0,
    "OII": 3727.0,
    "CaII_K": 3934.0,
    "CaII_H": 3969.0,
    "H-beta": 4861.0,
    "OIII": 5007.0,
    "H-alpha": 6563.0,
}


def redshift_estimate(input_data: dict, params: dict) -> dict:
    """Estimate redshift by matching observed spectral lines to rest-frame templates.

    params:
        line_list: dict — override rest-frame lines {name: wavelength_angstrom}
        flux_key: str — key for flux array (default "flux")
        wavelength_key: str — key for wavelength array (default "wavelength")
        method: str — "peak" (find peaks and match) or "xcorr" (cross-correlation)
    """
    flux_key = params.get("flux_key", "flux")
    wave_key = params.get("wavelength_key", "wavelength")
    method = params.get("method", "peak")
    line_list = params.get("line_list", DEFAULT_LINES)

    data = input_data.get("data", {})
    flux = np.array(data.get(flux_key, []), dtype=float)
    wavelength = np.array(data.get(wave_key, []), dtype=float)

    if len(flux) == 0 or len(wavelength) == 0:
        raise ValueError("RedshiftEstimate: empty flux or wavelength array")

    rest_wavelengths = np.array(list(line_list.values()))
    line_names = list(line_list.keys())

    if method == "peak":
        best_z, z_error, matched, confidence = _peak_method(
            wavelength, flux, rest_wavelengths, line_names
        )
    elif method == "xcorr":
        best_z, z_error, matched, confidence = _xcorr_method(
            wavelength, flux, rest_wavelengths, line_names
        )
    else:
        raise ValueError(f"Unknown method '{method}'. Choose 'peak' or 'xcorr'.")

    return {
        **input_data,
        "redshift_result": {
            "best_z": float(best_z),
            "z_error": float(z_error),
            "matched_lines": matched,
            "confidence": float(confidence),
            "method": method,
        },
    }


def _find_peaks_simple(wavelength: np.ndarray, flux: np.ndarray, n_peaks: int = 20):
    """Find local maxima in the flux array."""
    if len(flux) < 3:
        return np.array([]), np.array([])

    # Simple peak detection: points higher than both neighbors
    peaks = []
    for i in range(1, len(flux) - 1):
        if flux[i] > flux[i - 1] and flux[i] > flux[i + 1]:
            peaks.append(i)

    if not peaks:
        return np.array([]), np.array([])

    peaks = np.array(peaks)
    # Sort by flux strength, take top n_peaks
    order = np.argsort(flux[peaks])[::-1]
    top = peaks[order[:n_peaks]]

    return wavelength[top], flux[top]


def _peak_method(wavelength, flux, rest_wavelengths, line_names):
    """Match observed peaks to rest-frame lines across a grid of trial redshifts."""
    obs_peaks, _ = _find_peaks_simple(wavelength, flux)

    if len(obs_peaks) == 0:
        return 0.0, -1.0, [], 0.0

    # Try a grid of redshifts
    z_trials = np.linspace(0.0, 5.0, 5001)
    best_z = 0.0
    best_count = 0
    best_matches = []
    tolerance_frac = 0.002  # 0.2% wavelength tolerance

    for z in z_trials:
        shifted = rest_wavelengths * (1.0 + z)
        matches = []
        for j, sw in enumerate(shifted):
            diffs = np.abs(obs_peaks - sw)
            min_idx = np.argmin(diffs)
            if diffs[min_idx] < tolerance_frac * sw:
                matches.append({
                    "line": line_names[j],
                    "rest_wavelength": float(rest_wavelengths[j]),
                    "observed_wavelength": float(obs_peaks[min_idx]),
                })
        if len(matches) > best_count:
            best_count = len(matches)
            best_z = float(z)
            best_matches = matches

    # Refine z from matched lines
    if best_matches:
        z_values = [
            (m["observed_wavelength"] / m["rest_wavelength"]) - 1.0
            for m in best_matches
        ]
        best_z = float(np.mean(z_values))
        z_error = float(np.std(z_values)) if len(z_values) > 1 else 0.001
    else:
        z_error = -1.0

    confidence = min(1.0, best_count / max(len(rest_wavelengths), 1))

    return best_z, z_error, best_matches, confidence


def _xcorr_method(wavelength, flux, rest_wavelengths, line_names):
    """Cross-correlate observed spectrum with a synthetic template across redshifts."""
    # Build a template spectrum on the same wavelength grid at z=0
    template = np.zeros_like(flux)
    sigma_pix = max(1, len(wavelength) // 200)  # kernel width in pixels

    for rw in rest_wavelengths:
        if wavelength[0] <= rw <= wavelength[-1]:
            idx = np.argmin(np.abs(wavelength - rw))
            template[idx] += 1.0

    # Smooth the template with a Gaussian kernel
    kernel_x = np.arange(-3 * sigma_pix, 3 * sigma_pix + 1)
    kernel = np.exp(-0.5 * (kernel_x / max(sigma_pix, 1)) ** 2)
    kernel /= kernel.sum()
    template = np.convolve(template, kernel, mode="same")

    # Normalize
    flux_norm = flux - np.median(flux)
    template_norm = template - np.median(template)

    # Cross-correlate
    cc = correlate(flux_norm, template_norm, mode="full")
    cc_lags = np.arange(len(cc)) - (len(template_norm) - 1)

    peak_lag = cc_lags[np.argmax(cc)]

    # Convert pixel lag to redshift
    if len(wavelength) > 1:
        dlambda = np.median(np.diff(wavelength))
        lambda_shift = peak_lag * dlambda
        lambda_center = np.median(wavelength)
        best_z = float(lambda_shift / lambda_center)
    else:
        best_z = 0.0

    # Find matched lines at this redshift
    matched = []
    tolerance_frac = 0.003
    for j, rw in enumerate(rest_wavelengths):
        obs_w = rw * (1.0 + best_z)
        if wavelength[0] <= obs_w <= wavelength[-1]:
            idx = np.argmin(np.abs(wavelength - obs_w))
            # Check if there's a local peak near this wavelength
            local_start = max(0, idx - 3)
            local_end = min(len(flux), idx + 4)
            local_max_idx = local_start + np.argmax(flux[local_start:local_end])
            if abs(local_max_idx - idx) <= 3:
                matched.append({
                    "line": line_names[j],
                    "rest_wavelength": float(rw),
                    "observed_wavelength": float(wavelength[local_max_idx]),
                })

    cc_peak = float(np.max(cc))
    cc_std = float(np.std(cc))
    confidence = min(1.0, cc_peak / (cc_std * 5.0)) if cc_std > 0 else 0.0
    z_error = 0.001 if confidence > 0.5 else 0.01

    return best_z, z_error, matched, confidence
