"""Redshift estimation node — cross-correlate spectrum against emission line templates."""

import numpy as np
from scipy.signal import correlate


# Common rest-frame emission/absorption lines in Angstroms
DEFAULT_LINES = {
    "Lyman-alpha": 1216.0,
    "CIV": 1549.0,
    "CIII]": 1909.0,
    "MgII": 2798.0,
    "OII": 3727.0,
    "CaII_K": 3934.0,
    "CaII_H": 3969.0,
    "H-delta": 4102.0,
    "H-gamma": 4341.0,
    "H-beta": 4861.0,
    "OIII_4959": 4959.0,
    "OIII": 5007.0,
    "NII_6548": 6548.0,
    "H-alpha": 6563.0,
    "NII_6583": 6583.0,
    "SII_6717": 6717.0,
    "SII_6731": 6731.0,
}


def redshift_estimate(input_data: dict, params: dict) -> dict:
    """Estimate redshift by matching observed spectral lines to rest-frame templates.

    params:
        line_list: dict — override rest-frame lines {name: wavelength_angstrom}
        flux_key: str — key for flux array (default "flux")
        wavelength_key: str — key for wavelength array (default "wavelength")
        method: str — "peak" (find peaks and match), "xcorr" (cross-correlation),
                      or "chi2_grid" (chi-squared minimization)
        z_min: float — minimum redshift to search (default 0.0)
        z_max: float — maximum redshift to search (default 5.0)
        z_step: float — redshift grid step (default 0.001 for chi2_grid, 0.001 for peak)
    """
    flux_key = params.get("flux_key", "flux")
    wave_key = params.get("wavelength_key", "wavelength")
    method = params.get("method", "peak")
    line_list = params.get("line_list", DEFAULT_LINES)
    z_min = float(params.get("z_min", 0.0))
    z_max = float(params.get("z_max", 5.0))

    # Validate method early
    valid_methods = ("peak", "xcorr", "chi2_grid", "vote")
    if method not in valid_methods:
        raise ValueError(f"Unknown method '{method}'. Choose 'peak', 'xcorr', or 'chi2_grid'.")

    data = input_data.get("data", {})
    flux = np.array(data.get(flux_key, []), dtype=float)
    wavelength = np.array(data.get(wave_key, []), dtype=float)

    if len(flux) == 0 or len(wavelength) == 0:
        raise ValueError("RedshiftEstimate: empty flux or wavelength array")

    # Clean NaN/Inf
    valid_mask = np.isfinite(flux) & np.isfinite(wavelength)
    if np.sum(valid_mask) < 10:
        raise ValueError("RedshiftEstimate: fewer than 10 valid data points")
    flux = flux[valid_mask]
    wavelength = wavelength[valid_mask]

    rest_wavelengths = np.array(list(line_list.values()))
    line_names = list(line_list.keys())

    if method == "vote":
        # Multi-method voting: run all three, pick highest confidence
        results_all = []
        for m_name, m_func_args in [
            ("peak", lambda: _peak_method(wavelength, flux, rest_wavelengths, line_names, z_min, z_max)),
            ("xcorr", lambda: _xcorr_method(wavelength, flux, rest_wavelengths, line_names)),
            ("chi2_grid", lambda: _chi2_grid_method(wavelength, flux, rest_wavelengths, line_names, z_min, z_max, 0.001)),
        ]:
            try:
                bz, ze, ml, cf = m_func_args()
                results_all.append({"method": m_name, "z": bz, "error": ze, "matched": ml, "confidence": cf})
            except Exception:
                pass

        if not results_all:
            best_z, z_error, matched, confidence = 0.0, -1.0, [], 0.0
        else:
            # Pick the result with highest confidence
            best = max(results_all, key=lambda r: r["confidence"])
            best_z = best["z"]
            z_error = best["error"]
            matched = best["matched"]
            confidence = best["confidence"]
            method = f"vote({best['method']})"
    elif method == "peak":
        best_z, z_error, matched, confidence = _peak_method(
            wavelength, flux, rest_wavelengths, line_names, z_min, z_max
        )
    elif method == "xcorr":
        best_z, z_error, matched, confidence = _xcorr_method(
            wavelength, flux, rest_wavelengths, line_names
        )
    else:  # chi2_grid
        z_step = float(params.get("z_step", 0.0005))
        best_z, z_error, matched, confidence = _chi2_grid_method(
            wavelength, flux, rest_wavelengths, line_names, z_min, z_max, z_step
        )

    # Quality flag
    if confidence > 0.7 and len(matched) >= 3:
        quality = "high"
    elif confidence > 0.4 and len(matched) >= 2:
        quality = "medium"
    else:
        quality = "low"

    return {
        **input_data,
        "redshift_result": {
            "best_z": float(best_z),
            "z_error": float(z_error),
            "matched_lines": matched,
            "confidence": float(confidence),
            "method": method,
            "quality": quality,
            "n_lines_matched": len(matched),
        },
    }


def _find_peaks_simple(wavelength: np.ndarray, flux: np.ndarray, n_peaks: int = 20):
    """Find local maxima in the flux array, with significance filtering."""
    if len(flux) < 3:
        return np.array([]), np.array([])

    # Compute median and MAD for significance
    med = np.median(flux)
    mad = np.median(np.abs(flux - med))
    threshold = med + 3.0 * mad * 1.4826  # 3-sigma above median

    peaks = []
    for i in range(1, len(flux) - 1):
        if flux[i] > flux[i - 1] and flux[i] > flux[i + 1] and flux[i] > threshold:
            peaks.append(i)

    if not peaks:
        # Fall back to simple peak detection without threshold
        for i in range(1, len(flux) - 1):
            if flux[i] > flux[i - 1] and flux[i] > flux[i + 1]:
                peaks.append(i)

    if not peaks:
        return np.array([]), np.array([])

    peaks = np.array(peaks)
    order = np.argsort(flux[peaks])[::-1]
    top = peaks[order[:n_peaks]]

    return wavelength[top], flux[top]


def _peak_method(wavelength, flux, rest_wavelengths, line_names, z_min=0.0, z_max=5.0):
    """Match observed peaks to rest-frame lines across a grid of trial redshifts."""
    obs_peaks, _ = _find_peaks_simple(wavelength, flux)

    if len(obs_peaks) == 0:
        return 0.0, -1.0, [], 0.0

    n_trials = min(10001, int((z_max - z_min) / 0.001) + 1)
    z_trials = np.linspace(z_min, z_max, n_trials)
    best_z = 0.0
    best_count = 0
    best_matches = []
    tolerance_frac = 0.002

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

    confidence = min(1.0, best_count / max(len(rest_wavelengths) * 0.3, 1))

    return best_z, z_error, best_matches, confidence


def _xcorr_method(wavelength, flux, rest_wavelengths, line_names):
    """Cross-correlate observed spectrum with a synthetic template across redshifts."""
    template = np.zeros_like(flux)
    sigma_pix = max(1, len(wavelength) // 200)

    for rw in rest_wavelengths:
        if wavelength[0] <= rw <= wavelength[-1]:
            idx = np.argmin(np.abs(wavelength - rw))
            template[idx] += 1.0

    kernel_x = np.arange(-3 * sigma_pix, 3 * sigma_pix + 1)
    kernel = np.exp(-0.5 * (kernel_x / max(sigma_pix, 1)) ** 2)
    kernel /= kernel.sum()
    template = np.convolve(template, kernel, mode="same")

    flux_norm = flux - np.median(flux)
    template_norm = template - np.median(template)

    cc = correlate(flux_norm, template_norm, mode="full")
    cc_lags = np.arange(len(cc)) - (len(template_norm) - 1)

    peak_lag = cc_lags[np.argmax(cc)]

    if len(wavelength) > 1:
        dlambda = np.median(np.diff(wavelength))
        lambda_shift = peak_lag * dlambda
        lambda_center = np.median(wavelength)
        best_z = float(lambda_shift / lambda_center)
    else:
        best_z = 0.0

    matched = []
    for j, rw in enumerate(rest_wavelengths):
        obs_w = rw * (1.0 + best_z)
        if wavelength[0] <= obs_w <= wavelength[-1]:
            idx = np.argmin(np.abs(wavelength - obs_w))
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


def _chi2_grid_method(wavelength, flux, rest_wavelengths, line_names,
                      z_min, z_max, z_step):
    """Chi-squared template fitting on a redshift grid.

    Builds a synthetic emission template at each trial redshift and computes
    chi2 against the observed spectrum. More robust than peak matching for
    noisy or low-SNR spectra.
    """
    # Normalize flux
    flux_norm = flux - np.median(flux)
    flux_std = np.std(flux_norm)
    if flux_std > 0:
        flux_norm = flux_norm / flux_std

    z_grid = np.arange(z_min, z_max + z_step, z_step)
    chi2_arr = np.full(len(z_grid), np.inf)
    sigma_pix = max(1, len(wavelength) // 300)

    kernel_x = np.arange(-3 * sigma_pix, 3 * sigma_pix + 1)
    kernel = np.exp(-0.5 * (kernel_x / max(sigma_pix, 1)) ** 2)
    kernel /= kernel.sum()

    for iz, z in enumerate(z_grid):
        template = np.zeros_like(flux)
        n_lines_in_range = 0
        for rw in rest_wavelengths:
            obs_w = rw * (1.0 + z)
            if wavelength[0] <= obs_w <= wavelength[-1]:
                idx = np.argmin(np.abs(wavelength - obs_w))
                template[idx] += 1.0
                n_lines_in_range += 1

        if n_lines_in_range < 2:
            continue

        template = np.convolve(template, kernel, mode="same")
        t_std = np.std(template)
        if t_std > 0:
            template = template / t_std

        # Scale template to match flux
        scale = np.dot(flux_norm, template) / np.dot(template, template) if np.dot(template, template) > 0 else 0
        residuals = flux_norm - scale * template
        chi2_arr[iz] = float(np.sum(residuals**2))

    # Find minimum chi2
    finite_mask = np.isfinite(chi2_arr)
    if not np.any(finite_mask):
        # No valid redshift could be evaluated (no lines in wavelength range)
        return 0.0, -1.0, [], 0.0

    best_idx = np.argmin(chi2_arr)
    best_z = float(z_grid[best_idx])

    # Estimate error from chi2 curve (delta chi2 = 1 for 1-sigma)
    min_chi2 = chi2_arr[best_idx]
    above_threshold = chi2_arr <= min_chi2 + 1.0
    if np.sum(above_threshold) > 1:
        z_above = z_grid[above_threshold]
        z_error = float((z_above[-1] - z_above[0]) / 2.0)
    else:
        z_error = z_step

    # Find matched lines at best z
    matched = []
    for j, rw in enumerate(rest_wavelengths):
        obs_w = rw * (1.0 + best_z)
        if wavelength[0] <= obs_w <= wavelength[-1]:
            idx = np.argmin(np.abs(wavelength - obs_w))
            local_start = max(0, idx - 5)
            local_end = min(len(flux), idx + 6)
            local_max_idx = local_start + np.argmax(flux[local_start:local_end])
            if abs(local_max_idx - idx) <= 5:
                matched.append({
                    "line": line_names[j],
                    "rest_wavelength": float(rw),
                    "observed_wavelength": float(wavelength[local_max_idx]),
                })

    # Confidence from chi2 improvement over median
    finite_chi2 = chi2_arr[finite_mask]
    median_chi2 = float(np.median(finite_chi2)) if len(finite_chi2) > 0 else 0.0
    if median_chi2 > 0:
        confidence = min(1.0, (median_chi2 - min_chi2) / median_chi2)
    else:
        confidence = 0.0

    return best_z, z_error, matched, confidence
