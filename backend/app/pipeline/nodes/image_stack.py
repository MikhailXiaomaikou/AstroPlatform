"""Image stacking node — combine multiple 2D arrays using mean, median, or sigma-clip."""

import numpy as np
from astropy.stats import sigma_clip


def image_stack(input_data: dict, params: dict) -> dict:
    """Stack/combine multiple 2D image arrays.

    input_data["data"]["images"] should be a list of 2D arrays (lists of lists),
    all with the same dimensions.

    params:
        method: str — "mean" (default), "median", or "sigma_clip"
        sigma: float — clipping threshold for sigma_clip method (default 3.0)
        images_key: str — key for the list of images (default "images")
    """
    method = params.get("method", "mean")
    sigma_thresh = float(params.get("sigma", 3.0))
    images_key = params.get("images_key", "images")

    data = input_data.get("data", {})
    images_raw = data.get(images_key, [])

    if not images_raw:
        raise ValueError(f"ImageStack: no '{images_key}' found in input data")

    if len(images_raw) < 2:
        raise ValueError("ImageStack: need at least 2 images to stack")

    # Convert to 3D numpy array: (N_images, rows, cols)
    try:
        cube = np.array(images_raw, dtype=float)
    except ValueError:
        raise ValueError("ImageStack: images must all have the same dimensions")

    if cube.ndim != 3:
        raise ValueError(
            f"ImageStack: expected 3D image cube, got {cube.ndim}D array"
        )

    n_images, n_rows, n_cols = cube.shape

    if method == "mean":
        stacked = np.mean(cube, axis=0)
    elif method == "median":
        stacked = np.median(cube, axis=0)
    elif method == "sigma_clip":
        # Apply sigma clipping along the image axis, then take the mean
        clipped = sigma_clip(cube, sigma=sigma_thresh, axis=0, masked=True)
        stacked = np.ma.mean(clipped, axis=0).data
    else:
        raise ValueError(
            f"Unknown stacking method '{method}'. Choose 'mean', 'median', or 'sigma_clip'."
        )

    # Compute per-pixel statistics
    pixel_std = np.std(cube, axis=0)

    output_data = dict(data)
    output_data["stacked_image"] = stacked.tolist()
    output_data["pixel_std"] = pixel_std.tolist()

    return {
        **input_data,
        "data": output_data,
        "image_stack_result": {
            "method": method,
            "n_images": n_images,
            "image_shape": [n_rows, n_cols],
            "stacked_mean": float(np.mean(stacked)),
            "stacked_median": float(np.median(stacked)),
            "stacked_std": float(np.std(stacked)),
            "stacked_min": float(np.min(stacked)),
            "stacked_max": float(np.max(stacked)),
        },
    }
