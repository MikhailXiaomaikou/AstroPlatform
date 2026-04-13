"""FlatField pipeline node."""

from __future__ import annotations

import numpy as np

from app.analysis.image_reduction import _read_fits_array, flat_correct


def _resolve_science(input_data: dict | None, params: dict) -> np.ndarray:
    payload = input_data or {}
    if payload.get("data") is not None or payload.get("image") is not None:
        return np.asarray(payload.get("data") if payload.get("data") is not None else payload.get("image"))
    fits_path = str(params.get("science_fits_path") or payload.get("fits_path") or "")
    if not fits_path:
        raise ValueError("FlatField requires upstream image data or a science_fits_path")
    return _read_fits_array(fits_path)[0]


def flat_field(input_data: dict | None, params: dict) -> dict:
    science = _resolve_science(input_data, params)
    flat_frames = params.get("flat_frames") or []
    flat_paths = params.get("flat_paths") or []
    if not flat_frames and not flat_paths:
        raise ValueError("FlatField requires flat_frames or flat_paths")
    corrected = flat_correct(science, [np.asarray(frame) for frame in flat_frames] + [_read_fits_array(str(path))[0] for path in flat_paths])
    return {"image": corrected.tolist(), "data": corrected.tolist()}
