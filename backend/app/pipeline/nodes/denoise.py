"""Denoise node — multi-method noise reduction for spectra, images, and light curves.

Supported methods:
  sigma_clip  — iterative sigma-clipping with interpolation (default, good for outliers)
  savgol      — Savitzky-Golay polynomial smoothing (preserves spectral line shapes)
  wavelet     — discrete wavelet transform thresholding (multi-scale denoising)
  gaussian    — Gaussian kernel convolution (simple smoothing)
  median      — sliding median filter (robust against spikes)
"""

import numpy as np
from astropy.stats import sigma_clip


# ---------------------------------------------------------------------------
# Individual denoising methods
# ---------------------------------------------------------------------------

def _denoise_sigma_clip(arr: np.ndarray, sigma: float, max_iters: int) -> tuple[np.ndarray, dict]:
    """Iterative sigma-clipping: flag outliers, replace with interpolated values."""
    clipped = sigma_clip(arr, sigma=sigma, maxiters=max_iters)
    mask = clipped.mask if hasattr(clipped, "mask") else np.zeros(len(arr), dtype=bool)
    cleaned = arr.copy()
    if np.any(mask):
        good_idx = np.where(~mask)[0]
        bad_idx = np.where(mask)[0]
        if len(good_idx) > 1:
            cleaned[bad_idx] = np.interp(bad_idx, good_idx, arr[good_idx])
    return cleaned, {
        "clipped_count": int(np.sum(mask)),
        "clipped_fraction": round(float(np.sum(mask)) / len(arr), 4) if len(arr) > 0 else 0,
    }


def _denoise_savgol(arr: np.ndarray, window_length: int, polyorder: int) -> tuple[np.ndarray, dict]:
    """Savitzky-Golay polynomial filter — preserves line shapes and continuum features."""
    from scipy.signal import savgol_filter

    # Ensure window_length is odd and <= len(arr)
    wl = min(window_length, len(arr))
    if wl % 2 == 0:
        wl -= 1
    wl = max(wl, polyorder + 2)
    if wl % 2 == 0:
        wl += 1
    cleaned = savgol_filter(arr, window_length=wl, polyorder=polyorder)
    residual_rms = float(np.sqrt(np.mean((arr - cleaned) ** 2)))
    return cleaned, {
        "window_length": wl,
        "polyorder": polyorder,
        "residual_rms": round(residual_rms, 6),
    }


def _denoise_wavelet(arr: np.ndarray, wavelet: str, level: int | None, threshold_sigma: float) -> tuple[np.ndarray, dict]:
    """Discrete wavelet transform with soft thresholding — multi-scale denoising."""
    try:
        import pywt
    except ImportError:
        # Fallback: use scipy's basic wavelet (less capable but always available)
        return _denoise_savgol(arr, window_length=11, polyorder=3)

    # M25: refuse to operate on arrays shorter than the minimum wavelet
    # decomposition window — otherwise `pywt.dwt_max_level` returns 0 and we
    # later force level=1, producing an all-NaN or degenerate reconstruction.
    if len(arr) < 16:
        raise ValueError(
            f"Denoise (wavelet): array too short ({len(arr)} samples) for "
            f"decomposition — use a simpler method like savgol for small arrays."
        )

    # Determine decomposition level
    max_level = pywt.dwt_max_level(len(arr), pywt.Wavelet(wavelet))
    use_level = min(level or max_level, max_level)
    use_level = max(use_level, 1)

    coeffs = pywt.wavedec(arr, wavelet, level=use_level)

    # Estimate noise from finest detail coefficients (MAD estimator)
    detail_finest = coeffs[-1]
    noise_sigma = float(np.median(np.abs(detail_finest))) / 0.6745

    # Soft threshold all detail coefficients
    threshold = threshold_sigma * noise_sigma
    thresholded = [coeffs[0]]  # keep approximation unchanged
    n_zeroed = 0
    n_total = 0
    for detail in coeffs[1:]:
        n_total += len(detail)
        zeroed = np.sum(np.abs(detail) < threshold)
        n_zeroed += int(zeroed)
        thresholded.append(pywt.threshold(detail, threshold, mode="soft"))

    cleaned = pywt.waverec(thresholded, wavelet)[:len(arr)]
    return cleaned, {
        "wavelet": wavelet,
        "level": use_level,
        "noise_sigma": round(noise_sigma, 6),
        "threshold": round(threshold, 6),
        "coefficients_zeroed": n_zeroed,
        "coefficients_total": n_total,
    }


def _denoise_gaussian(arr: np.ndarray, kernel_size: float) -> tuple[np.ndarray, dict]:
    """Gaussian kernel convolution — simple but effective broadband smoothing."""
    from scipy.ndimage import gaussian_filter1d

    cleaned = gaussian_filter1d(arr, sigma=kernel_size)
    residual_rms = float(np.sqrt(np.mean((arr - cleaned) ** 2)))
    return cleaned, {
        "kernel_sigma": kernel_size,
        "residual_rms": round(residual_rms, 6),
    }


def _denoise_median(arr: np.ndarray, kernel_size: int) -> tuple[np.ndarray, dict]:
    """Sliding median filter — robust against sharp spikes and cosmic rays."""
    from scipy.ndimage import median_filter

    ks = max(3, kernel_size)
    if ks % 2 == 0:
        ks += 1
    cleaned = median_filter(arr, size=ks).astype(float)
    n_changed = int(np.sum(np.abs(arr - cleaned) > 1e-10))
    return cleaned, {
        "kernel_size": ks,
        "pixels_changed": n_changed,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def denoise(input_data: dict, params: dict) -> dict:
    """Multi-method denoising for astronomical data.

    params:
        method:   str — "sigma_clip" (default), "savgol", "wavelet", "gaussian", "median"
        flux_key: str — column name for the data array (default "flux")

      Method-specific params:
        sigma_clip: sigma (3.0), max_iters (5)
        savgol:     window_length (11), polyorder (3)
        wavelet:    wavelet ("db4"), level (auto), threshold_sigma (2.0)
        gaussian:   kernel_size (2.0)
        median:     kernel_size (5)
    """
    method = params.get("method", "sigma_clip")
    flux_key = params.get("flux_key", "flux")

    data = input_data.get("data", {})
    flux = data.get(flux_key)
    if flux is None:
        raise ValueError(f"Denoise: no '{flux_key}' column in input data")

    arr = np.array(flux, dtype=float)

    # Handle NaN values: remember positions, interpolate for processing, restore after
    nan_mask = np.isnan(arr)
    if np.any(nan_mask):
        good = np.where(~nan_mask)[0]
        if len(good) > 1:
            arr[nan_mask] = np.interp(np.where(nan_mask)[0], good, arr[good])

    if method == "sigma_clip":
        cleaned, stats = _denoise_sigma_clip(
            arr,
            sigma=float(params.get("sigma", 3.0)),
            max_iters=int(params.get("max_iters", 5)),
        )
    elif method == "savgol":
        cleaned, stats = _denoise_savgol(
            arr,
            window_length=int(params.get("window_length", 11)),
            polyorder=int(params.get("polyorder", 3)),
        )
    elif method == "wavelet":
        cleaned, stats = _denoise_wavelet(
            arr,
            wavelet=str(params.get("wavelet", "db4")),
            level=int(params["level"]) if "level" in params else None,
            threshold_sigma=float(params.get("threshold_sigma", 2.0)),
        )
    elif method == "gaussian":
        cleaned, stats = _denoise_gaussian(
            arr,
            kernel_size=float(params.get("kernel_size", 2.0)),
        )
    elif method == "median":
        cleaned, stats = _denoise_median(
            arr,
            kernel_size=int(params.get("kernel_size", 5)),
        )
    else:
        raise ValueError(
            f"Unknown denoise method '{method}'. "
            "Choose: sigma_clip, savgol, wavelet, gaussian, median"
        )

    # Restore NaN positions
    if np.any(nan_mask):
        cleaned[nan_mask] = np.nan

    # Compute SNR improvement estimate
    original_noise = float(np.std(arr - np.convolve(arr, np.ones(5) / 5, mode="same")))
    cleaned_noise = float(np.std(cleaned - np.convolve(cleaned, np.ones(5) / 5, mode="same")))
    snr_improvement = round(original_noise / cleaned_noise, 2) if cleaned_noise > 0 else 0.0

    output_data = dict(data)
    output_data[flux_key] = cleaned.tolist()

    return {
        **input_data,
        "data": output_data,
        "denoise_stats": {
            "method": method,
            "total_points": len(arr),
            "nan_count": int(np.sum(nan_mask)),
            "snr_improvement": snr_improvement,
            **stats,
        },
    }
