"""Denoise node — applies sigma-clipping to spectral data."""

import numpy as np
from astropy.stats import sigma_clip


def denoise(input_data: dict, params: dict) -> dict:
    """Sigma-clip noisy flux values.

    params:
        sigma: float — clipping threshold (default 3.0)
        max_iters: int — maximum iterations (default 5)
        flux_key: str — key in input_data["data"] for flux array (default "flux")
    """
    sigma_thresh = params.get("sigma", 3.0)
    max_iters = params.get("max_iters", 5)
    flux_key = params.get("flux_key", "flux")

    data = input_data.get("data", {})
    flux = data.get(flux_key)
    if flux is None:
        raise ValueError(f"Denoise: no '{flux_key}' column in input data")

    arr = np.array(flux, dtype=float)
    clipped = sigma_clip(arr, sigma=sigma_thresh, maxiters=max_iters)

    # Replace masked (clipped) values with interpolated neighbors
    mask = clipped.mask if hasattr(clipped, "mask") else np.zeros(len(arr), dtype=bool)
    cleaned = arr.copy()
    if np.any(mask):
        good_idx = np.where(~mask)[0]
        bad_idx = np.where(mask)[0]
        if len(good_idx) > 1:
            cleaned[bad_idx] = np.interp(bad_idx, good_idx, arr[good_idx])

    output_data = dict(data)
    output_data[flux_key] = cleaned.tolist()

    return {
        **input_data,
        "data": output_data,
        "denoise_stats": {
            "clipped_count": int(np.sum(mask)),
            "total_count": len(arr),
            "sigma": sigma_thresh,
        },
    }
