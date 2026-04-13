"""BiasSubtract pipeline node."""

from __future__ import annotations

import numpy as np

from app.analysis.image_reduction import _read_fits_array, bias_subtract as _bias_subtract


def _resolve_science(input_data: dict | None, params: dict) -> np.ndarray:
    payload = input_data or {}
    if payload.get("data") is not None or payload.get("image") is not None:
        return np.asarray(payload.get("data") if payload.get("data") is not None else payload.get("image"))
    fits_path = str(params.get("science_fits_path") or payload.get("fits_path") or "")
    if not fits_path:
        raise ValueError("BiasSubtract requires upstream image data or a science_fits_path")
    return _read_fits_array(fits_path)[0]


def bias_subtract_node(input_data: dict | None, params: dict) -> dict:
    science = _resolve_science(input_data, params)
    bias_frames = params.get("bias_frames") or []
    bias_paths = params.get("bias_paths") or []
    if not bias_frames and not bias_paths:
        raise ValueError("BiasSubtract requires bias_frames or bias_paths")
    resolved_bias_frames = [np.asarray(frame) for frame in bias_frames] + [
        _read_fits_array(str(path))[0] for path in bias_paths
    ]
    corrected = _bias_subtract(science, resolved_bias_frames)
    return {"image": corrected.tolist(), "data": corrected.tolist()}
