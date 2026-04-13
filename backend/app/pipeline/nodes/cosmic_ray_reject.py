"""CosmicRayReject pipeline node."""

from __future__ import annotations

import numpy as np

from app.analysis.image_reduction import _read_fits_array, cosmic_ray_reject


def cosmic_ray_reject_node(input_data: dict | None, params: dict) -> dict:
    payload = input_data or {}
    if payload.get("data") is not None or payload.get("image") is not None:
        image = np.asarray(payload.get("data") if payload.get("data") is not None else payload.get("image"))
    else:
        fits_path = str(params.get("fits_path") or payload.get("fits_path") or "")
        if not fits_path:
            raise ValueError("CosmicRayReject requires upstream image data or a fits_path")
        image = _read_fits_array(fits_path)[0]
    cleaned, mask = cosmic_ray_reject(
        image,
        sigclip=float(params.get("sigclip", 5.0) or 5.0),
        sigfrac=float(params.get("sigfrac", 0.3) or 0.3),
        objlim=float(params.get("objlim", 5.0) or 5.0),
    )
    return {"image": cleaned.tolist(), "data": cleaned.tolist(), "mask": mask.astype(int).tolist()}
