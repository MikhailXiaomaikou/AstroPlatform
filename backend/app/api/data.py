import asyncio
import io
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_optional_user
from app.cache import cache_get, cache_key, cache_set
from app.rate_limit import limiter
from app.connectors.base import AstroObject
from app.connectors.registry import CONNECTORS_KEYS, get_connector
from app.models.database import get_db
from app.models.schemas import DataFile, SearchHistory, User
from app.search.query_parser import (
    SPECTRAL_LINES,
    get_spectral_lines_list,
    parse_natural_query,
    suggest_sources,
)
from app.storage import download_fits, upload_fits

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])


def _classify_error(exc: Exception) -> str:
    """Classify an exception into a user-facing error type."""
    exc_type_name = type(exc).__name__.lower()
    exc_msg = str(exc).lower()

    if "timeout" in exc_type_name or "timeout" in exc_msg:
        return "timeout"
    if any(kw in exc_type_name for kw in ("connection", "connect", "dns", "resolve")):
        return "connection"
    if any(kw in exc_msg for kw in ("connection", "connect", "dns", "unreachable")):
        return "connection"
    if any(kw in exc_msg for kw in ("401", "403", "auth", "forbidden", "unauthorized")):
        return "auth"
    if any(kw in exc_msg for kw in ("429", "rate limit", "too many requests")):
        return "rate_limit"
    if any(kw in exc_msg for kw in ("500", "502", "503", "504", "server error")):
        return "server_error"
    return "unknown"


class SearchResult(BaseModel):
    source: str
    object_id: str
    name: str
    ra: float
    dec: float
    object_type: str = ""
    magnitude: float | None = None
    redshift: float | None = None
    extra: dict = {}
    error_type: str | None = None


class FetchResult(BaseModel):
    source: str
    object_id: str
    fits_path: str
    filename: str
    file_id: str | None = None


class FITSHeaderResponse(BaseModel):
    fits_path: str
    headers: list[dict]
    hdus: list[dict]


def _safe_float(val: float | None) -> float | None:
    """Return None for NaN/Inf values to ensure JSON serialization."""
    if val is None:
        return None
    if val != val or val == float("inf") or val == float("-inf"):
        return None
    return val


def _sanitize_extra(d: dict) -> dict:
    """Replace NaN/Inf values in extra dict for JSON safety."""
    out = {}
    for k, v in d.items():
        if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
            out[k] = None
        else:
            out[k] = v
    return out


def _astro_to_result(obj: AstroObject) -> SearchResult:
    return SearchResult(
        source=obj.source,
        object_id=obj.object_id,
        name=obj.name,
        ra=_safe_float(obj.ra) or 0.0,
        dec=_safe_float(obj.dec) or 0.0,
        object_type=obj.object_type,
        magnitude=_safe_float(obj.magnitude),
        redshift=_safe_float(obj.redshift),
        extra=_sanitize_extra(obj.extra) if obj.extra else {},
    )


@router.get("/search", response_model=list[SearchResult])
@limiter.limit("30/minute")
async def search_data(
    request: Request,
    q: str = Query(..., description="Object name or coordinates"),
    sources: str = Query("sdss,gaia,simbad", description="Comma-separated data sources"),
    ra: float | None = Query(None, description="Right ascension in degrees"),
    dec: float | None = Query(None, description="Declination in degrees"),
    radius: float = Query(0.1, description="Search radius in degrees"),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Search multiple astronomical databases concurrently."""
    source_list = [s.strip() for s in sources.split(",") if s.strip()]

    for s in source_list:
        if s not in CONNECTORS_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown source: {s}")

    # Check Redis cache
    ck = cache_key(
        "search", q=q, sources=sorted(source_list), ra=ra, dec=dec, radius=radius,
    )
    cached = await cache_get(ck)
    if cached is not None:
        logger.debug("Cache hit for search key %s", ck)
        return [SearchResult(**r) for r in cached]

    async def _search_with_timeout(source: str):
        return await asyncio.wait_for(
            get_connector(source).search(q, ra=ra, dec=dec, radius=radius),
            timeout=45.0,
        )

    tasks = [_search_with_timeout(s) for s in source_list]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[SearchResult] = []
    for source_name, result in zip(source_list, results_per_source):
        if isinstance(result, Exception):
            error_type = _classify_error(result)
            logger.warning(
                "Search failed for source %s (error_type=%s, retries=3): %s",
                source_name, error_type, result,
            )
            all_results.append(
                SearchResult(
                    source=source_name,
                    object_id="error",
                    name=f"Error querying {source_name}: {result}",
                    ra=0,
                    dec=0,
                    error_type=error_type,
                    extra={"retries_attempted": 3},
                )
            )
            continue
        all_results.extend(_astro_to_result(obj) for obj in result)

    # Cache results for 5 minutes
    await cache_set(ck, [r.model_dump() for r in all_results], ttl=300)

    # Save to search history for authenticated users
    if user:
        try:
            valid_count = sum(1 for r in all_results if r.object_id != "error")
            db.add(SearchHistory(
                user_id=user.id,
                query=q,
                sources=sources,
                result_count=valid_count,
                params={"ra": ra, "dec": dec, "radius": radius},
            ))
            await db.commit()
        except Exception:
            pass  # Don't fail the search if history save fails

    return all_results


# ── Advanced Search ──


class AdvancedSearchRequest(BaseModel):
    """Structured scientific search criteria for astronomical data."""
    # Coordinate constraints (optional)
    ra: Optional[float] = None
    dec: Optional[float] = None
    radius: float = Field(default=1.0, description="Search radius in degrees")

    # Science filters
    redshift_min: Optional[float] = None
    redshift_max: Optional[float] = None
    spectral_line: Optional[str] = Field(
        default=None,
        description='Spectral line key, e.g. "cii", "lyman-alpha", "h-alpha", "co32"',
    )
    wavelength_min: Optional[float] = Field(
        default=None, description="Minimum wavelength in microns"
    )
    wavelength_max: Optional[float] = Field(
        default=None, description="Maximum wavelength in microns"
    )
    observation_type: Optional[str] = Field(
        default=None,
        description='e.g. "spectroscopy", "imaging", "interferometry"',
    )
    object_type: Optional[str] = Field(
        default=None,
        description='e.g. "AGN", "quasar", "galaxy", "star"',
    )
    instrument: Optional[str] = None

    # Which sources to query
    sources: list[str] = Field(
        default=["sdss", "gaia", "simbad", "alma", "mast"],
        description="Data sources to query",
    )

    # Natural language query (parsed by backend)
    natural_query: Optional[str] = Field(
        default=None,
        description='Free-text query, e.g. "high redshift CII line data"',
    )


class AdvancedSearchMeta(BaseModel):
    """Metadata about what the search interpreted from the query."""
    parsed_filters: dict = {}
    suggested_sources: list[str] = []
    matched_keywords: list[str] = []
    observed_freq_min_ghz: Optional[float] = None
    observed_freq_max_ghz: Optional[float] = None
    observed_wavelength_min_um: Optional[float] = None
    observed_wavelength_max_um: Optional[float] = None


class AdvancedSearchResponse(BaseModel):
    """Response from the advanced search endpoint."""
    results: list[SearchResult]
    meta: AdvancedSearchMeta


def _merge_parsed_into_request(
    req: AdvancedSearchRequest, parsed: dict
) -> AdvancedSearchRequest:
    """Merge parsed natural-language filters into the request, with explicit
    filters taking priority over parsed values."""
    if req.redshift_min is None and "redshift_min" in parsed:
        req.redshift_min = parsed["redshift_min"]
    if req.redshift_max is None and "redshift_max" in parsed:
        req.redshift_max = parsed["redshift_max"]
    if req.spectral_line is None and "spectral_line" in parsed:
        req.spectral_line = parsed["spectral_line"]
    if req.object_type is None and "object_type" in parsed:
        req.object_type = parsed["object_type"]
    if req.observation_type is None and "observation_type" in parsed:
        req.observation_type = parsed["observation_type"]
    if req.ra is None and "ra" in parsed:
        req.ra = parsed["ra"]
    if req.dec is None and "dec" in parsed:
        req.dec = parsed["dec"]
    if "radius" in parsed:
        req.radius = parsed["radius"]
    if req.wavelength_min is None and "wavelength_min" in parsed:
        req.wavelength_min = parsed["wavelength_min"]
    if req.wavelength_max is None and "wavelength_max" in parsed:
        req.wavelength_max = parsed["wavelength_max"]
    return req


def _build_query_text(req: AdvancedSearchRequest) -> str:
    """Build a textual query string for connectors that only support basic search.

    For advanced searches without coordinates, we try to extract a resolvable
    object name. If the query is purely scientific criteria (spectral lines,
    redshift ranges), we use a generic catalog-friendly term.
    """
    # If the natural query contains a known object name, use it
    if req.natural_query:
        # Try to extract object names (M31, NGC 1234, etc.)
        import re
        obj_match = re.search(
            r'\b(M\s*\d+|NGC\s*\d+|IC\s*\d+|Mrk\s*\d+|3C\s*\d+|Arp\s*\d+|'
            r'UGC\s*\d+|ESO\s*[\d-]+|SDSS\s*J[\d.+-]+)\b',
            req.natural_query, re.IGNORECASE
        )
        if obj_match:
            return obj_match.group(0)

    # For pure science queries, use object_type as a SIMBAD-resolvable term
    if req.object_type:
        return req.object_type

    # Fallback: generic term that won't crash coordinate resolution
    return "survey"


def _post_filter_results(
    results: list[AstroObject], req: AdvancedSearchRequest
) -> list[AstroObject]:
    """Apply science filters to results from connectors that don't support them natively."""
    filtered = results
    if req.redshift_min is not None:
        filtered = [
            r for r in filtered
            if r.redshift is not None and r.redshift >= req.redshift_min
        ]
    if req.redshift_max is not None:
        filtered = [
            r for r in filtered
            if r.redshift is not None and r.redshift <= req.redshift_max
        ]
    if req.object_type:
        obj_lower = req.object_type.lower()
        filtered = [
            r for r in filtered
            if obj_lower in r.object_type.lower()
            or obj_lower in r.extra.get("object_type", "").lower()
        ]
    return filtered


@router.post("/advanced-search", response_model=AdvancedSearchResponse)
@limiter.limit("20/minute")
async def advanced_search(
    request: Request,
    body: AdvancedSearchRequest,
):
    """Search astronomical databases using structured scientific criteria.

    Accepts redshift ranges, spectral lines, object types, observation types,
    and/or a natural language query. The backend parses the query, determines
    the optimal sources, builds source-specific queries, and post-filters
    results by science criteria.
    """
    parsed: dict = {}
    matched_keywords: list[str] = []

    # Parse natural language query if provided
    if body.natural_query:
        parsed = parse_natural_query(body.natural_query)
        matched_keywords = parsed.get("matched_keywords", [])
        body = _merge_parsed_into_request(body, parsed)

    # Resolve spectral line info if we have a line key but not yet in parsed
    if body.spectral_line and "spectral_line_info" not in parsed:
        line_info = SPECTRAL_LINES.get(body.spectral_line)
        if line_info:
            parsed["spectral_line_info"] = line_info
            parsed["spectral_line"] = body.spectral_line
            # Recompute observed ranges
            from app.search.query_parser import _compute_observed_ranges, ParsedQuery
            pq = ParsedQuery(
                redshift_min=body.redshift_min,
                redshift_max=body.redshift_max,
                spectral_line=body.spectral_line,
                spectral_line_info=line_info,
            )
            _compute_observed_ranges(pq)
            if pq.observed_freq_min_ghz is not None:
                parsed["observed_freq_min_ghz"] = pq.observed_freq_min_ghz
            if pq.observed_freq_max_ghz is not None:
                parsed["observed_freq_max_ghz"] = pq.observed_freq_max_ghz
            if pq.observed_wavelength_min_um is not None:
                parsed["observed_wavelength_min_um"] = pq.observed_wavelength_min_um
            if pq.observed_wavelength_max_um is not None:
                parsed["observed_wavelength_max_um"] = pq.observed_wavelength_max_um

    # Smart source selection
    suggested = suggest_sources(parsed)

    # Use explicitly provided sources, or fall back to suggested top 5
    source_list = body.sources
    for s in source_list:
        if s not in CONNECTORS_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown source: {s}")

    # Build the query text for basic connectors
    query_text = _build_query_text(body)

    # Check cache
    cache_params = {
        "query": query_text,
        "sources": sorted(source_list),
        "ra": body.ra,
        "dec": body.dec,
        "radius": body.radius,
        "redshift_min": body.redshift_min,
        "redshift_max": body.redshift_max,
        "spectral_line": body.spectral_line,
        "object_type": body.object_type,
        "observation_type": body.observation_type,
        "wavelength_min": body.wavelength_min,
        "wavelength_max": body.wavelength_max,
        "natural_query": body.natural_query,
    }
    ck = cache_key("adv_search", **cache_params)
    cached = await cache_get(ck)
    if cached is not None:
        logger.debug("Cache hit for advanced search key %s", ck)
        return AdvancedSearchResponse(
            results=[SearchResult(**r) for r in cached["results"]],
            meta=AdvancedSearchMeta(**cached["meta"]),
        )

    # For no-coordinate science queries, try to resolve a reference position
    # from the query text so coordinate-based connectors can work
    search_ra = body.ra
    search_dec = body.dec
    has_coords = search_ra is not None and search_dec is not None

    if not has_coords:
        # Try to resolve query_text as coordinates
        try:
            from astropy.coordinates import SkyCoord
            coord = SkyCoord.from_name(query_text)
            search_ra = coord.ra.deg
            search_dec = coord.dec.deg
            has_coords = True
        except Exception:
            pass

    # Sources that require sky coordinates to function
    COORD_REQUIRED_SOURCES = {
        "sdss", "gaia", "vizier", "2mass", "chandra", "allwise",
        "irsa", "eso", "lamost", "mast", "jwst", "alma", "ned",
    }
    # Sources that support criteria-based search without coordinates
    CRITERIA_SOURCES = {"simbad"}

    # Without coordinates, only use sources that support criteria-based search
    if not has_coords:
        skipped = [s for s in source_list if s not in CRITERIA_SOURCES]
        source_list = [s for s in source_list if s in CRITERIA_SOURCES]
        if skipped:
            logger.info(
                "No coordinates — limiting to criteria-capable sources. Skipped: %s",
                skipped,
            )

    # Check if we have science criteria that SIMBAD can handle via TAP
    has_science_criteria = any([
        body.redshift_min, body.redshift_max, body.object_type,
    ])

    # Execute search across remaining sources
    async def _search_source(source: str):
        connector = get_connector(source)

        # Use SIMBAD's criteria-based TAP search when we have science filters
        if source == "simbad" and has_science_criteria and hasattr(connector, "search_by_criteria"):
            return await asyncio.wait_for(
                connector.search_by_criteria(
                    object_type=body.object_type,
                    redshift_min=body.redshift_min,
                    redshift_max=body.redshift_max,
                    ra=search_ra,
                    dec=search_dec,
                    radius=body.radius,
                ),
                timeout=45.0,
            )

        return await asyncio.wait_for(
            connector.search(
                query_text, ra=search_ra, dec=search_dec, radius=body.radius
            ),
            timeout=45.0,
        )

    tasks = [_search_source(s) for s in source_list]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[SearchResult] = []
    for source_name, result in zip(source_list, results_per_source):
        if isinstance(result, Exception):
            error_type = _classify_error(result)
            logger.warning(
                "Advanced search failed for source %s (error_type=%s): %s",
                source_name, error_type, result,
            )
            all_results.append(
                SearchResult(
                    source=source_name,
                    object_id="error",
                    name=f"Error querying {source_name}: {result}",
                    ra=0,
                    dec=0,
                    error_type=error_type,
                )
            )
            continue

        # Post-filter results by science criteria
        filtered = _post_filter_results(result, body)
        all_results.extend(_astro_to_result(obj) for obj in filtered)

    meta = AdvancedSearchMeta(
        parsed_filters={
            k: v for k, v in parsed.items()
            if k not in ("matched_keywords", "spectral_line_info")
        },
        suggested_sources=suggested[:5],
        matched_keywords=matched_keywords,
        observed_freq_min_ghz=parsed.get("observed_freq_min_ghz"),
        observed_freq_max_ghz=parsed.get("observed_freq_max_ghz"),
        observed_wavelength_min_um=parsed.get("observed_wavelength_min_um"),
        observed_wavelength_max_um=parsed.get("observed_wavelength_max_um"),
    )

    response = AdvancedSearchResponse(results=all_results, meta=meta)

    # Cache for 5 minutes
    await cache_set(
        ck,
        {
            "results": [r.model_dump() for r in all_results],
            "meta": meta.model_dump(),
        },
        ttl=300,
    )

    return response


@router.get("/spectral-lines")
async def list_spectral_lines():
    """Return available spectral lines for the frontend dropdown."""
    return get_spectral_lines_list()


@router.get("/workspace")
async def list_workspace(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """List user's saved data files."""
    if user is None:
        return []
    result = await db.execute(
        select(DataFile)
        .where(DataFile.user_id == user.id)
        .order_by(DataFile.created_at.desc())
        .limit(100)
    )
    files = result.scalars().all()
    return [
        {
            "id": str(f.id),
            "source": f.source,
            "object_id": f.object_id,
            "fits_path": f.fits_path,
            "metadata": f.metadata_,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in files
    ]


@router.get("/fits-header", response_model=FITSHeaderResponse)
async def get_fits_header(
    fits_path: str = Query(..., description="Storage path to FITS file"),
):
    """Read FITS file headers and HDU info for preview."""
    from astropy.io import fits

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    hdul = fits.open(io.BytesIO(raw))

    headers = []
    hdus = []

    for i, hdu in enumerate(hdul):
        # Collect header key-value pairs
        header_items = []
        for key in hdu.header:
            if key:
                val = hdu.header[key]
                try:
                    comment = hdu.header.comments[key]
                except (KeyError, IndexError):
                    comment = ""
                header_items.append({
                    "key": key,
                    "value": str(val),
                    "comment": str(comment) if comment else "",
                })
        headers.append({"hdu_index": i, "cards": header_items})

        # HDU summary
        hdu_info: dict = {
            "index": i,
            "name": hdu.name or f"HDU{i}",
            "type": type(hdu).__name__,
        }
        if hdu.data is not None:
            if hasattr(hdu.data, "shape"):
                hdu_info["shape"] = list(hdu.data.shape)
                hdu_info["dtype"] = str(hdu.data.dtype)
            if hasattr(hdu, "columns") and hdu.columns is not None:
                hdu_info["columns"] = [
                    {"name": col.name, "format": col.format}
                    for col in hdu.columns
                ]
        hdus.append(hdu_info)

    hdul.close()

    return FITSHeaderResponse(fits_path=fits_path, headers=headers, hdus=hdus)


@router.get("/fits-spectrum")
async def get_fits_spectrum(
    fits_path: str = Query(..., description="Storage path to FITS file"),
    max_points: int = Query(2000, description="Max data points to return"),
):
    """Extract spectrum data from FITS for interactive preview."""
    from astropy.io import fits
    from astropy.table import Table
    import numpy as np

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    hdul = fits.open(io.BytesIO(raw))

    # Try to find spectrum data
    for hdu in hdul:
        if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU)):
            table = Table.read(hdu)
            columns = {}
            for col in table.colnames:
                arr = table[col]
                try:
                    if hasattr(arr, "filled"):
                        if arr.dtype.kind in ("i", "u"):
                            arr = arr.filled(-9999)
                        else:
                            arr = arr.filled(np.nan)
                    float_arr = np.array(arr, dtype=float)
                    # Replace NaN/sentinel with None for JSON compatibility
                    data = [None if (np.isnan(v) or np.isinf(v) or v == -9999) else v for v in float_arr]
                except (ValueError, TypeError):
                    # Skip non-numeric columns
                    continue
                # Downsample if too many points
                if len(data) > max_points:
                    step = len(data) // max_points
                    data = data[::step]
                columns[col] = data
            hdul.close()
            return {"type": "table", "columns": list(columns.keys()), "data": columns}

    # Fall back to primary HDU
    if hdul[0].data is not None:
        data = hdul[0].data.astype(float)
        if data.ndim == 1:
            flux = [None if np.isnan(v) else v for v in data]
            if len(flux) > max_points:
                step = len(flux) // max_points
                flux = flux[::step]
            hdul.close()
            return {
                "type": "spectrum",
                "columns": ["index", "flux"],
                "data": {
                    "index": list(range(len(flux))),
                    "flux": flux,
                },
            }
        elif data.ndim == 2:
            # Return image statistics and a downsampled version
            hdul.close()
            h, w = data.shape
            # Downsample image to max 256x256
            step = max(1, max(h, w) // 256)
            small = np.where(np.isnan(data[::step, ::step]), 0, data[::step, ::step])
            return {
                "type": "image",
                "shape": [h, w],
                "min": float(np.nanmin(data)),
                "max": float(np.nanmax(data)),
                "mean": float(np.nanmean(data)),
                "thumbnail": small.tolist(),
            }

    hdul.close()
    return {"type": "empty", "columns": [], "data": {}}


@router.get("/fits-wcs")
async def get_fits_wcs(
    fits_path: str = Query(..., description="Storage path to FITS file"),
    grid_steps: int = Query(10, description="Number of grid lines per axis"),
):
    """Extract WCS coordinate grid from FITS image for overlay."""
    from astropy.io import fits
    from astropy.wcs import WCS
    import numpy as np

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    hdul = fits.open(io.BytesIO(raw))

    # Find HDU with WCS info (usually primary or first image)
    wcs = None
    img_shape = None
    for hdu in hdul:
        if hdu.data is not None and hdu.data.ndim == 2:
            try:
                w = WCS(hdu.header, naxis=2)
                if w.has_celestial:
                    wcs = w
                    img_shape = hdu.data.shape
                    break
            except Exception:
                continue

    if wcs is None or img_shape is None:
        hdul.close()
        return {"has_wcs": False, "grid_lines": [], "labels": []}

    h, w_px = img_shape

    # Compute RA/Dec at corners to determine grid range
    corners_pix = np.array([[0, 0], [w_px, 0], [0, h], [w_px, h]], dtype=float)
    corners_world = wcs.pixel_to_world_values(corners_pix[:, 0], corners_pix[:, 1])
    ra_corners = corners_world[0]
    dec_corners = corners_world[1]

    ra_min, ra_max = float(np.min(ra_corners)), float(np.max(ra_corners))
    dec_min, dec_max = float(np.min(dec_corners)), float(np.max(dec_corners))

    grid_lines = []
    labels = []

    # RA grid lines (vertical in image)
    ra_values = np.linspace(ra_min, ra_max, grid_steps)
    for ra_val in ra_values:
        dec_range = np.linspace(dec_min, dec_max, 50)
        ra_arr = np.full_like(dec_range, ra_val)
        try:
            pix = wcs.world_to_pixel_values(ra_arr, dec_range)
            points = []
            for px, py in zip(pix[0], pix[1]):
                if 0 <= px <= w_px and 0 <= py <= h:
                    points.append([float(px) / w_px, float(py) / h])  # normalized 0-1
            if len(points) > 1:
                grid_lines.append({"type": "ra", "value": float(ra_val), "points": points})
                # Label at midpoint
                mid = points[len(points) // 2]
                hours = ra_val / 15.0
                h_int = int(hours)
                m_int = int((hours - h_int) * 60)
                s_flt = ((hours - h_int) * 60 - m_int) * 60
                labels.append({
                    "text": f"{h_int}h{m_int:02d}m{s_flt:04.1f}s",
                    "x": mid[0], "y": mid[1], "type": "ra"
                })
        except Exception:
            continue

    # Dec grid lines (horizontal in image)
    dec_values = np.linspace(dec_min, dec_max, grid_steps)
    for dec_val in dec_values:
        ra_range = np.linspace(ra_min, ra_max, 50)
        dec_arr = np.full_like(ra_range, dec_val)
        try:
            pix = wcs.world_to_pixel_values(ra_range, dec_arr)
            points = []
            for px, py in zip(pix[0], pix[1]):
                if 0 <= px <= w_px and 0 <= py <= h:
                    points.append([float(px) / w_px, float(py) / h])
            if len(points) > 1:
                grid_lines.append({"type": "dec", "value": float(dec_val), "points": points})
                mid = points[len(points) // 2]
                d_int = int(dec_val)
                m_abs = abs(dec_val - d_int) * 60
                m_int = int(m_abs)
                s_flt = (m_abs - m_int) * 60
                sign = "+" if dec_val >= 0 else "-"
                labels.append({
                    "text": f"{sign}{abs(d_int)}\u00b0{m_int:02d}'{s_flt:04.1f}\"",
                    "x": mid[0], "y": mid[1], "type": "dec"
                })
        except Exception:
            continue

    hdul.close()

    return {
        "has_wcs": True,
        "ra_range": [ra_min, ra_max],
        "dec_range": [dec_min, dec_max],
        "image_shape": [h, w_px],
        "grid_lines": grid_lines,
        "labels": labels,
    }


@router.get("/{source}/{object_id}", response_model=FetchResult)
async def fetch_object(
    source: str,
    object_id: str,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Fetch FITS data for a specific object and store."""
    try:
        connector = get_connector(source)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        fits_file = await connector.fetch(object_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch from {source}: {e}")

    fits_path = f"{source}/{object_id}/{uuid.uuid4().hex}.fits"
    upload_fits(fits_path, fits_file.data)

    # Save to database if user is authenticated
    file_id = None
    if user:
        data_file = DataFile(
            user_id=user.id,
            source=source,
            object_id=object_id,
            fits_path=fits_path,
        )
        db.add(data_file)
        await db.commit()
        await db.refresh(data_file)
        file_id = str(data_file.id)

    return FetchResult(
        source=source,
        object_id=object_id,
        fits_path=fits_path,
        filename=fits_file.filename,
        file_id=file_id,
    )
