"""QueryData node — query astronomical catalog services inside a pipeline."""

from __future__ import annotations

import asyncio

from app.api.data import _astro_to_result
from app.connectors.registry import CONNECTORS_KEYS, get_connector


def _coerce_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows_to_columns(rows: list[dict]) -> dict[str, list]:
    columns: dict[str, list] = {}
    for index, row in enumerate(rows):
        for key, value in row.items():
            column = columns.get(key)
            if column is None:
                # Key first seen at this row: backfill None for all earlier rows.
                column = [None] * index
                columns[key] = column
            column.append(value)
        for key in columns.keys() - row.keys():
            columns[key].append(None)
    return columns


def query_data(input_data: dict | None, params: dict) -> dict:
    """Search one or more astronomy data sources and return tabular results.

    params:
        query: object name or free-text target description
        sources: comma-separated list of connectors (e.g. "gaia,simbad,sdss")
        radius: search radius in degrees
        ra / dec: optional explicit coordinates in degrees
    """
    query = str(params.get("query") or "").strip()
    sources_raw = str(params.get("sources") or "simbad").strip()
    radius = float(params.get("radius", 0.1) or 0.1)
    max_results = int(params.get("max_results", 100) or 100)
    ra = _coerce_float(params.get("ra"))
    dec = _coerce_float(params.get("dec"))

    if not query and (ra is None or dec is None):
        raise ValueError("QueryData requires either a target name or explicit ra/dec coordinates")

    sources = [source.strip().lower() for source in sources_raw.split(",") if source.strip()]
    invalid_sources = [source for source in sources if source not in CONNECTORS_KEYS]
    if invalid_sources:
        raise ValueError(f"Unknown data source(s): {', '.join(invalid_sources)}")
    if not sources:
        sources = ["simbad"]

    async def _run_searches() -> list[object]:
        tasks = [
            get_connector(source).search(query or "coordinates", ra=ra, dec=dec, radius=radius)
            for source in sources
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    results_per_source = asyncio.run(_run_searches())

    rows: list[dict] = []
    errors: dict[str, str] = {}
    for source, result in zip(sources, results_per_source):
        if isinstance(result, Exception):
            errors[source] = str(result)
            continue
        for obj in result:
            entry = _astro_to_result(obj).model_dump()
            extra = entry.pop("extra", {}) or {}
            flattened = {**entry, **extra}
            rows.append(flattened)
            if len(rows) >= max_results:
                break
        if len(rows) >= max_results:
            break

    return {
        "type": "catalog",
        "query": query or "coordinate_search",
        "sources": sources,
        "radius": radius,
        "row_count": len(rows),
        "columns": sorted(_rows_to_columns(rows).keys()),
        "rows": rows,
        "data": _rows_to_columns(rows),
        "errors": errors,
    }
