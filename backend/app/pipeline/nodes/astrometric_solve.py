"""AstrometricSolve pipeline node."""

from __future__ import annotations

import asyncio

from app.analysis.image_reduction import solve_astrometry


def astrometric_solve(input_data: dict | None, params: dict) -> dict:
    fits_path = str(params.get("fits_path") or (input_data or {}).get("fits_path") or "")
    if not fits_path:
        raise ValueError("AstrometricSolve requires fits_path")
    return asyncio.run(solve_astrometry(fits_path))

