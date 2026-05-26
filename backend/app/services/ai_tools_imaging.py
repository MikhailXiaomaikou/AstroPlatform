"""Imaging / image-reduction AI tools — extracted from ai_tools/__init__.py
(H1 split, 2026-05-26).

4 tools: reduce_ccd_image, solve_astrometry, extract_photometry, extract_sources.
Thin wrappers over app.analysis.image_reduction and astro_analysis.extract_sources.

NOTE: extract_sources resolves a fits path relative to app/data via __file__.
This sibling lives one directory shallower than the original
ai_tools/__init__.py, so the relative path was adjusted ("..","..","data" ->
"..","data") to preserve the same app/data target.

Dispatch mirrors the other sibling modules: ai_tools/__init__ routes
``tool_name in IMAGING_TOOL_NAMES`` to ``dispatch_imaging``.
"""
from __future__ import annotations

import asyncio

IMAGING_TOOL_NAMES = frozenset(
    {
        "reduce_ccd_image",
        "solve_astrometry",
        "extract_photometry",
        "extract_sources",
    }
)


IMAGING_TOOL_SCHEMAS = [
    {
        "name": "reduce_ccd_image",
        "description": (
            "Run a standard CCD reduction on a science FITS image using any available bias, dark, and flat frames. "
            "Returns the reduced output FITS path and a reduction log."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "science_fits_path": {"type": "string", "description": "Science FITS image to reduce"},
                "bias_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional bias frame paths"},
                "dark_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional dark frame paths"},
                "flat_paths": {"type": "array", "items": {"type": "string"}, "description": "Optional flat frame paths"},
                "cosmic_ray": {"type": "boolean", "description": "Whether to clean cosmic rays (default true)"},
            },
            "required": ["science_fits_path"],
        },
    },
    {
        "name": "solve_astrometry",
        "description": "Plate-solve a FITS image via astrometry.net and return WCS metadata when available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "FITS path to solve"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "extract_photometry",
        "description": (
            "Extract sources and run basic aperture photometry on a reduced FITS image. "
            "Returns a source catalog with x/y, fluxes, instrumental magnitudes, and RA/Dec if WCS is present."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {"type": "string", "description": "Reduced FITS path"},
                "aperture_radii": {"type": "array", "items": {"type": "integer"}, "description": "Aperture radii in pixels"},
            },
            "required": ["fits_path"],
        },
    },
    {
        "name": "extract_sources",
        "description": (
            "Extract sources from a FITS image using SEP (SExtractor in Python). "
            "Performs background subtraction, source detection, and Kron aperture photometry. "
            "Returns source positions, shape parameters, fluxes, and instrumental magnitudes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fits_path": {
                    "type": "string",
                    "description": "Path to the FITS image file in storage",
                },
                "threshold_sigma": {
                    "type": "number",
                    "description": "Detection threshold in sigma above background (default 3.0)",
                },
                "min_area": {
                    "type": "integer",
                    "description": "Minimum number of pixels for a detection (default 5)",
                },
            },
            "required": ["fits_path"],
        },
    },
]


async def _exec_reduce_ccd_image(inp: dict) -> dict:
    from app.analysis.image_reduction import full_reduction

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None,
        full_reduction,
        inp["science_fits_path"],
        inp.get("bias_paths"),
        inp.get("dark_paths"),
        inp.get("flat_paths"),
        inp.get("cosmic_ray", True),
    )
    return {
        "output_path": result.get("output_path"),
        "reduction_log": result.get("reduction_log", []),
        "cosmic_ray_mask_pixels": result.get("cosmic_ray_mask_pixels", 0),
        "header": result.get("header", {}),
    }


async def _exec_solve_astrometry(inp: dict) -> dict:
    from app.analysis.image_reduction import solve_astrometry

    return await solve_astrometry(inp["fits_path"])


async def _exec_extract_photometry(inp: dict) -> dict:
    from app.analysis.image_reduction import extract_and_photometer

    loop = asyncio.get_running_loop()
    table = await loop.run_in_executor(
        None,
        extract_and_photometer,
        inp["fits_path"],
        inp.get("aperture_radii"),
    )
    rows = table.where(table.notna(), None).to_dict(orient="records")
    return {
        "row_count": int(len(table)),
        "columns": list(table.columns),
        "rows": rows,
    }


async def _exec_extract_sources(inp: dict) -> dict:
    """Load a FITS image and extract sources using SEP."""
    import os
    from app.services.astro_analysis import extract_sources

    fits_path = inp.get("fits_path", "")
    threshold_sigma = inp.get("threshold_sigma", 3.0)
    min_area = inp.get("min_area", 5)

    if not fits_path:
        return {"error": "fits_path is required"}

    # Resolve path relative to data directory
    base_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    full_path = os.path.normpath(os.path.join(base_dir, fits_path))
    if not os.path.isfile(full_path):
        # Try as absolute path
        full_path = fits_path
    if not os.path.isfile(full_path):
        return {"error": f"FITS file not found: {fits_path}"}

    from astropy.io import fits as afits

    loop = asyncio.get_event_loop()

    def _do_extract():
        with afits.open(full_path) as hdul:
            # Find first image extension
            image_data = None
            for hdu in hdul:
                if hdu.data is not None and hdu.data.ndim == 2:
                    image_data = hdu.data
                    break
            if image_data is None:
                return {"error": "No 2D image extension found in FITS file"}
            return extract_sources(image_data, threshold_sigma=threshold_sigma, min_area=min_area)

    result = await loop.run_in_executor(None, _do_extract)
    return result


async def dispatch_imaging(tool_name: str, tool_input: dict) -> dict | None:
    """Route the 4 imaging tools; None for unknown (caller guards)."""
    if tool_name == "reduce_ccd_image":
        return await _exec_reduce_ccd_image(tool_input)
    elif tool_name == "solve_astrometry":
        return await _exec_solve_astrometry(tool_input)
    elif tool_name == "extract_photometry":
        return await _exec_extract_photometry(tool_input)
    elif tool_name == "extract_sources":
        return await _exec_extract_sources(tool_input)
    return None
