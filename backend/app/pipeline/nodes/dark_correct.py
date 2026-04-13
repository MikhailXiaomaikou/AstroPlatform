"""DarkCorrect pipeline node."""

from __future__ import annotations

import numpy as np

from app.analysis.image_reduction import _read_fits_array, dark_subtract


def _resolve_science(input_data: dict | None, params: dict) -> np.ndarray:
    payload = input_data or {}
    if payload.get("data") is not None or payload.get("image") is not None:
        return np.asarray(payload.get("data") if payload.get("data") is not None else payload.get("image"))
    fits_path = str(params.get("science_fits_path") or payload.get("fits_path") or "")
    if not fits_path:
        raise ValueError("DarkCorrect requires upstream image data or a science_fits_path")
    return _read_fits_array(fits_path)[0]


def dark_correct(input_data: dict | None, params: dict) -> dict:
    science = _resolve_science(input_data, params)
    dark_frames = params.get("dark_frames") or []
    dark_paths = params.get("dark_paths") or []
    if not dark_frames and not dark_paths:
        raise ValueError("DarkCorrect requires dark_frames or dark_paths")
    corrected = dark_subtract(
        science,
        [np.asarray(frame) for frame in dark_frames] + [_read_fits_array(str(path))[0] for path in dark_paths],
        science_exptime=float(params.get("science_exptime", 1.0) or 1.0),
        dark_exptime=float(params.get("dark_exptime", 1.0) or 1.0),
    )
    return {"image": corrected.tolist(), "data": corrected.tolist()}
