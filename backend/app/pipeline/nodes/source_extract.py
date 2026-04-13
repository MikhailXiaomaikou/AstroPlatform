"""SourceExtract pipeline node."""

from __future__ import annotations

from app.analysis.image_reduction import extract_and_photometer


def source_extract(input_data: dict | None, params: dict) -> dict:
    fits_path = str(params.get("fits_path") or (input_data or {}).get("output_path") or (input_data or {}).get("fits_path") or "")
    if not fits_path:
        raise ValueError("SourceExtract requires a FITS path")
    table = extract_and_photometer(
        fits_path,
        aperture_radii=params.get("aperture_radii") or [3, 5, 7],
    )
    return {
        "type": "catalog",
        "row_count": int(len(table)),
        "columns": list(table.columns),
        "rows": table.where(table.notna(), None).to_dict(orient="records"),
        "data": table.where(table.notna(), None).to_dict(orient="list"),
    }
