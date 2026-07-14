import asyncio
import io
import logging
import uuid
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File as FastAPIFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_current_user, get_optional_user
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
from app.storage import (
    StorageOwnerRequired,
    StorageOwnershipError,
    download_fits,
    resolve_owned_storage_key,
    upload_fits,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/data", tags=["data"])

COORD_REQUIRED_SOURCES = {
    "sdss", "gaia", "vizier", "2mass", "chandra", "allwise",
    "irsa", "eso", "lamost", "mast", "jwst", "alma", "ned", "desi",
}
SOURCE_TIMEOUTS = {
    "ned": 75.0,
}


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
    z_source: str | None = None
    photo_z: float | None = None
    photo_z_err: float | None = None


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
    fits_type: str | None = None
    suggested_analysis: str | None = None


def _safe_float(val: float | None) -> float | None:
    """Return None for NaN/Inf values to ensure JSON serialization."""
    if val is None:
        return None
    if val != val or val == float("inf") or val == float("-inf"):
        return None
    return val


def detect_fits_type(hdul) -> dict:
    """Auto-classify FITS file type from header and data structure."""
    primary = hdul[0]

    if primary.data is not None:
        if primary.data.ndim == 3:
            return {"fits_type": "ifu_cube", "suggested_analysis": "IFU cube analysis: extract spectra, velocity maps"}
        if primary.data.ndim == 2:
            return {"fits_type": "image", "suggested_analysis": "CCD reduction, source extraction, photometry"}
        if primary.data.ndim == 1:
            header = primary.header
            ctype1 = str(header.get("CTYPE1", ""))
            if "WAVE" in ctype1.upper() or "AWAV" in ctype1.upper() or "CRVAL1" in header:
                return {"fits_type": "spectrum", "suggested_analysis": "Spectral analysis: line identification, fitting, redshift"}

    # Check extensions
    for hdu in hdul[1:]:
        if hasattr(hdu, "columns") and hdu.columns:
            cols = [c.name.upper() for c in hdu.columns]
            if any(w in cols for w in ["WAVE", "WAVELENGTH", "LOGLAM", "LAMBDA"]):
                return {"fits_type": "spectrum", "suggested_analysis": "Spectral analysis: line identification, fitting, redshift"}
            if any(w in cols for w in ["MJD", "TIME", "BJD", "JD", "BARYTIME"]):
                return {"fits_type": "lightcurve", "suggested_analysis": "Time-domain: period search, transit fit, flare detection"}
            return {"fits_type": "catalog", "suggested_analysis": "Catalog analysis: cross-match, photometric calibration"}

    return {"fits_type": "unknown", "suggested_analysis": None}


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
    redshift = _safe_float(obj.redshift)
    extra = _sanitize_extra(obj.extra) if obj.extra else {}
    raw_z_source = extra.get("z_source")
    z_source = raw_z_source if raw_z_source in {"spectroscopic", "photometric"} else None
    photo_z = _safe_float(extra.get("photo_z"))
    if z_source == "photometric" and photo_z is None:
        photo_z = redshift
    return SearchResult(
        source=obj.source,
        object_id=obj.object_id,
        name=obj.name,
        ra=_safe_float(obj.ra) or 0.0,
        dec=_safe_float(obj.dec) or 0.0,
        object_type=obj.object_type,
        magnitude=_safe_float(obj.magnitude),
        redshift=redshift,
        extra=extra,
        z_source=z_source,
        photo_z=photo_z,
        photo_z_err=_safe_float(extra.get("photo_z_err")),
    )


# Bands accepted by the photo-z template fitter.
_PHOTO_Z_BANDS = {"u", "g", "r", "i", "z", "J", "H", "Ks", "W1", "W2"}
_MIN_PHOTO_Z_BANDS = 3


def _maybe_compute_photo_z(result: SearchResult) -> SearchResult:
    """Estimate photometric redshift for a result that lacks spec-z.

    Requires at least ``_MIN_PHOTO_Z_BANDS`` magnitude values in the
    ``extra`` dict (keyed by band name from ugriz / JHKs / W1W2).
    """
    if result.z_source == "spectroscopic":
        return result

    mags: dict[str, float] = {}
    errs: dict[str, float] = {}
    for band in _PHOTO_Z_BANDS:
        val = result.extra.get(band) or result.extra.get(f"mag_{band}")
        if val is not None:
            try:
                mag_f = float(val)
                if mag_f != mag_f:  # NaN check
                    continue
                mags[band] = mag_f
                # Use error if available, otherwise default 0.1 mag
                err_val = result.extra.get(f"{band}_err") or result.extra.get(f"mag_{band}_err")
                errs[band] = float(err_val) if err_val is not None else 0.1
            except (ValueError, TypeError):
                continue

    if len(mags) < _MIN_PHOTO_Z_BANDS:
        return result

    try:
        from app.services.photo_z import estimate_photo_z_template

        pz = estimate_photo_z_template(mags, errs)
        result.photo_z = _safe_float(pz.get("z_phot"))
        result.photo_z_err = _safe_float(pz.get("z_err"))
        if result.photo_z is not None:
            result.redshift = result.photo_z
            result.z_source = "photometric"
    except Exception:
        logger.debug("photo-z estimation failed for %s/%s", result.source, result.object_id, exc_info=True)

    return result


async def _resolve_search_coordinates(
    query: str,
    ra: float | None,
    dec: float | None,
) -> tuple[float | None, float | None]:
    """Resolve a search target to coordinates without blocking the event loop."""
    if ra is not None and dec is not None:
        return ra, dec

    try:
        from astropy.coordinates import SkyCoord

        loop = asyncio.get_running_loop()
        coord = await loop.run_in_executor(None, partial(SkyCoord.from_name, query))
        return coord.ra.deg, coord.dec.deg
    except Exception as e:
        logger.debug("SkyCoord.from_name fallback for %s: %s", query, e)

    try:
        simbad = get_connector("simbad")
        matches = await asyncio.wait_for(simbad.search(query, radius=0.05), timeout=10.0)
        for obj in matches:
            if obj.ra is not None and obj.dec is not None:
                return float(obj.ra), float(obj.dec)
    except Exception as exc:
        logger.debug("SIMBAD fallback coordinate resolution failed for %s: %s", query, exc)

    return ra, dec


def _search_timeout_for_source(source: str) -> float:
    """Return a per-source search timeout budget."""
    return SOURCE_TIMEOUTS.get(source, 20.0)


_CONNECTOR_CACHE_TTL = 600  # 10 minutes


async def _cached_search(connector, source: str, query: str, ra: float | None, dec: float | None, radius: float, **kwargs):
    """Search a connector with per-connector Redis caching.

    Returns ``(results, from_cache)`` where *from_cache* indicates whether the
    results were served from Redis.  If Redis is unavailable the connector is
    called directly and ``from_cache`` is always ``False``.
    """
    cache_ra = f"{ra:.4f}" if ra is not None else "none"
    cache_dec = f"{dec:.4f}" if dec is not None else "none"
    ck = f"connector:{source}:{query}:{cache_ra}:{cache_dec}:{radius:.2f}"

    # Try cache first
    cached = await cache_get(ck)
    if cached is not None:
        logger.debug("Connector cache hit for %s (key=%s)", source, ck)
        return cached, True

    # Cache miss — call the connector
    results = await connector.search(query, ra=ra, dec=dec, radius=radius, **kwargs)

    # Serialise AstroObject list to dicts for storage
    serialised = [
        {
            "source": obj.source,
            "object_id": obj.object_id,
            "name": obj.name,
            "ra": obj.ra,
            "dec": obj.dec,
            "object_type": obj.object_type,
            "magnitude": obj.magnitude,
            "redshift": obj.redshift,
            "extra": obj.extra if obj.extra else {},
        }
        for obj in results
    ]
    await cache_set(ck, serialised, ttl=_CONNECTOR_CACHE_TTL)

    return results, False


def _rebuild_astro_objects(cached_dicts: list[dict]) -> list[AstroObject]:
    """Re-hydrate a list of cached dicts back into AstroObject instances."""
    return [AstroObject(**d) for d in cached_dicts]


def _build_source_error_name(source_name: str, error_type: str, result: Exception) -> str:
    """Format a user-facing per-source search error."""
    if source_name == "ned" and error_type == "timeout":
        return "NED is responding slowly right now; try again in a moment or narrow the search."
    if source_name == "sdss" and error_type == "timeout":
        return "SDSS SkyServer is responding slowly right now; try again in a moment or narrow the search radius."
    detail = str(result).strip()
    if detail:
        return detail
    return f"Error querying {source_name}"


async def _require_owned_file_by_path(
    db: AsyncSession, user: User, path: str
) -> str:
    """Return the canonical key after path validation + owner authorization."""
    try:
        return await resolve_owned_storage_key(path, owner_id=user.id, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    except (StorageOwnershipError, StorageOwnerRequired):
        raise HTTPException(status_code=404, detail="File not found")


def _dedup_by_position(results: list[SearchResult], sep_arcsec: float = 3.0) -> list[SearchResult]:
    """Merge results from different sources that refer to the same object.

    Uses position matching within `sep_arcsec` arcseconds.
    Keeps the result with the most complete data (prefer the one with redshift).
    Merges source info into the extra dict.
    """
    if len(results) <= 1:
        return results

    deduped: list[SearchResult] = []
    used = [False] * len(results)

    import math

    for i, r in enumerate(results):
        if used[i]:
            continue
        group = [r]
        used[i] = True

        for j in range(i + 1, len(results)):
            if used[j]:
                continue
            # Position match: angular separation in arcseconds (with cos(dec) correction)
            cos_dec = math.cos(math.radians((r.dec + results[j].dec) / 2.0))
            dra = (r.ra - results[j].ra) * cos_dec * 3600.0
            ddec = (r.dec - results[j].dec) * 3600.0
            sep = (dra**2 + ddec**2) ** 0.5
            if sep < sep_arcsec:
                group.append(results[j])
                used[j] = True

        # Pick the best from the group: prefer one with redshift, then most extra fields
        group.sort(key=lambda x: (
            x.redshift is not None,
            len(x.extra),
            x.object_type != "",
        ), reverse=True)

        best = group[0]
        # Merge sources info
        if len(group) > 1:
            other_sources = [g.source for g in group[1:]]
            best.extra = {**best.extra, "also_in": other_sources}
            # Fill in missing data from other sources
            for g in group[1:]:
                if best.redshift is None and g.redshift is not None:
                    best.redshift = g.redshift
                if not best.object_type and g.object_type:
                    best.object_type = g.object_type
                if best.magnitude is None and g.magnitude is not None:
                    best.magnitude = g.magnitude

        deduped.append(best)

    return deduped


@router.get("/search", response_model=list[SearchResult])
@limiter.limit("30/minute")
async def search_data(
    request: Request,
    q: str = Query(..., description="Object name or coordinates"),
    sources: str = Query("sdss,gaia,simbad", description="Comma-separated data sources"),
    ra: float | None = Query(None, description="Right ascension in degrees"),
    dec: float | None = Query(None, description="Declination in degrees"),
    radius: float = Query(0.1, description="Search radius in degrees"),
    compute_photo_z: bool = Query(False, description="Estimate photometric redshift for results without spec-z"),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Search multiple astronomical databases concurrently."""
    source_list = [s.strip() for s in sources.split(",") if s.strip()]

    for s in source_list:
        if s not in CONNECTORS_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown source: {s}")

    # Check Redis cache. compute_photo_z mutates result.redshift/z_source, so it
    # must be part of the key — otherwise photo-z-estimated results leak to
    # requests that asked for spec-z only (and vice versa).
    ck = cache_key(
        "search", q=q, sources=sorted(source_list), ra=ra, dec=dec, radius=radius,
        compute_photo_z=compute_photo_z,
    )
    cached = await cache_get(ck)
    if cached is not None:
        logger.debug("Cache hit for search key %s", ck)
        return [SearchResult(**r) for r in cached]

    search_ra, search_dec = await _resolve_search_coordinates(q, ra, dec)

    async def _search_with_timeout(source: str):
        source_ra = search_ra if source in COORD_REQUIRED_SOURCES else ra
        source_dec = search_dec if source in COORD_REQUIRED_SOURCES else dec
        connector = get_connector(source)
        return await asyncio.wait_for(
            _cached_search(connector, source, q, source_ra, source_dec, radius),
            timeout=_search_timeout_for_source(source),
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
                    name=_build_source_error_name(source_name, error_type, result),
                    ra=0,
                    dec=0,
                    error_type=error_type,
                    extra={"retries_attempted": 3},
                )
            )
            continue
        objects, from_cache = result
        if from_cache:
            objects = _rebuild_astro_objects(objects)
        all_results.extend(_astro_to_result(obj) for obj in objects)

    # Deduplicate by position across sources
    all_results = _dedup_by_position(all_results)

    # Optionally estimate photometric redshifts for results missing spec-z
    if compute_photo_z:
        all_results = [_maybe_compute_photo_z(r) for r in all_results]

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
    cached: bool = False


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
    results: list[AstroObject], req: AdvancedSearchRequest,
    source: str = "",
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
    # Skip object_type filtering for SIMBAD — it already filters by otype in the TAP query.
    # The user-facing name ("Seyfert galaxy") doesn't match SIMBAD codes ("Sy1","Sy2").
    if req.object_type and source != "simbad":
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
        search_ra, search_dec = await _resolve_search_coordinates(query_text, None, None)
        has_coords = search_ra is not None and search_dec is not None

    # Sources that support criteria-based search without coordinates
    CRITERIA_SOURCES = {"simbad"}

    # Check if we have science criteria that SIMBAD can handle via TAP
    has_science_criteria = any([
        body.redshift_min, body.redshift_max, body.object_type,
        body.observation_type, body.natural_query,
    ])

    # Without coordinates, only use sources that support criteria-based search
    if not has_coords:
        skipped = [s for s in source_list if s not in CRITERIA_SOURCES]
        source_list = [s for s in source_list if s in CRITERIA_SOURCES]
        # Auto-add SIMBAD for criteria search if not already in list
        if has_science_criteria and "simbad" not in source_list:
            source_list.append("simbad")
        if skipped:
            logger.info(
                "No coordinates — limiting to criteria-capable sources. Skipped: %s",
                skipped,
            )

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
                timeout=20.0,
            ), False  # search_by_criteria bypasses connector cache

        return await asyncio.wait_for(
            _cached_search(
                connector, source, query_text, search_ra, search_dec, body.radius
            ),
            timeout=20.0,
        )

    tasks = [_search_source(s) for s in source_list]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

    any_cached = False
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
                    name=_build_source_error_name(source_name, error_type, result),
                    ra=0,
                    dec=0,
                    error_type=error_type,
                )
            )
            continue

        objects, from_cache = result
        if from_cache:
            any_cached = True
            objects = _rebuild_astro_objects(objects)

        # Post-filter results by science criteria
        filtered = _post_filter_results(objects, body, source=source_name)
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
        cached=any_cached,
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
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
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
        .offset(skip)
        .limit(limit)
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
    hdu: int = Query(0, description="HDU index to focus on (0-based, default 0). All HDUs are always returned."),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Read FITS file headers and HDU info for preview."""
    fits_path = await _require_owned_file_by_path(db, user, fits_path)

    from astropy.io import fits

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    # E1.3: wrap the full parse in try/except so the reviewer sees a
    # typed 422 (with the actual astropy exception message) instead of
    # a generic "Failed to load headers" when the file is truncated,
    # has unusual HDU types, or triggers a known astropy bug.
    try:
        hdul = fits.open(io.BytesIO(raw))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Could not parse FITS file: {type(exc).__name__}: {exc}",
        )

    try:
        headers = []
        hdus = []

        # Phase 2A: Auto-detect FITS file type
        try:
            fits_type_info = detect_fits_type(hdul)
        except Exception as exc:
            logger.warning("detect_fits_type failed on %s: %s", fits_path, exc)
            fits_type_info = {"fits_type": None, "suggested_analysis": None}

        for i, hdu in enumerate(hdul):
            # Collect header key-value pairs
            header_items = []
            try:
                for key in hdu.header:
                    if not key:
                        continue
                    try:
                        val = hdu.header[key]
                    except Exception:
                        val = "(unreadable)"
                    try:
                        comment = hdu.header.comments[key]
                    except (KeyError, IndexError, Exception):
                        comment = ""
                    header_items.append({
                        "key": str(key),
                        "value": str(val),
                        "comment": str(comment) if comment else "",
                    })
            except Exception as exc:
                logger.warning("Header walk failed for HDU %d in %s: %s", i, fits_path, exc)
                header_items.append({
                    "key": "_ERROR",
                    "value": f"{type(exc).__name__}: {exc}",
                    "comment": "",
                })
            headers.append({"hdu_index": i, "cards": header_items})

            # HDU summary
            hdu_info: dict = {
                "index": i,
                "name": hdu.name or f"HDU{i}",
                "type": type(hdu).__name__,
            }
            try:
                if hdu.data is not None:
                    if hasattr(hdu.data, "shape"):
                        hdu_info["shape"] = list(hdu.data.shape)
                        hdu_info["dtype"] = str(hdu.data.dtype)
                    if hasattr(hdu, "columns") and hdu.columns is not None:
                        hdu_info["columns"] = [
                            {"name": col.name, "format": col.format}
                            for col in hdu.columns
                        ]
            except Exception as exc:
                hdu_info["data_error"] = f"{type(exc).__name__}: {exc}"
            hdus.append(hdu_info)
    finally:
        try:
            hdul.close()
        except Exception as e:
            logger.debug("hdul.close failed in get_fits_header: %s", e)

    return FITSHeaderResponse(
        fits_path=fits_path,
        headers=headers,
        hdus=hdus,
        fits_type=fits_type_info.get("fits_type"),
        suggested_analysis=fits_type_info.get("suggested_analysis"),
    )


@router.get("/fits-spectrum")
async def get_fits_spectrum(
    fits_path: str = Query(..., description="Storage path to FITS file"),
    max_points: int = Query(2000, ge=1, description="Max data points to return"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Extract spectrum data from FITS for interactive preview."""
    fits_path = await _require_owned_file_by_path(db, user, fits_path)

    from astropy.io import fits
    from astropy.table import Table
    import numpy as np

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    hdul = fits.open(io.BytesIO(raw))

    try:
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
                return {"type": "table", "columns": list(columns.keys()), "data": columns}

        # Fall back to primary HDU
        if hdul[0].data is not None:
            data = hdul[0].data.astype(float)
            if data.ndim == 1:
                flux = [None if np.isnan(v) else v for v in data]
                if len(flux) > max_points:
                    step = len(flux) // max_points
                    flux = flux[::step]
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

        return {"type": "empty", "columns": [], "data": {}}
    finally:
        hdul.close()


@router.get("/fits-wcs")
async def get_fits_wcs(
    fits_path: str = Query(..., description="Storage path to FITS file"),
    grid_steps: int = Query(10, description="Number of grid lines per axis"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Extract WCS coordinate grid from FITS image for overlay."""
    fits_path = await _require_owned_file_by_path(db, user, fits_path)

    from astropy.io import fits
    from astropy.wcs import WCS
    import numpy as np

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    hdul = fits.open(io.BytesIO(raw))

    try:
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
                except Exception as e:
                    logger.debug("WCS parse skipped for HDU: %s", e)
                    continue

        if wcs is None or img_shape is None:
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
            except Exception as e:
                logger.debug("RA grid line projection failed: %s", e)
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
            except Exception as e:
                logger.debug("Dec grid line projection failed: %s", e)
                continue

        return {
            "has_wcs": True,
            "ra_range": [ra_min, ra_max],
            "dec_range": [dec_min, dec_max],
            "image_shape": [h, w_px],
            "grid_lines": grid_lines,
            "labels": labels,
        }
    finally:
        hdul.close()


# ── Object Detail (Aggregation) ──


class CrossId(BaseModel):
    name: str


class SurveyStatus(BaseModel):
    source: str
    has_data: bool
    count: int


class Reference(BaseModel):
    bibcode: str
    title: str
    authors: list[str]
    year: str


class ObjectDetailResponse(BaseModel):
    name: str
    ra: float
    dec: float
    object_type: str = ""
    object_type_long: str = ""
    redshift: float | None = None
    radial_velocity: float | None = None
    spectral_type: str | None = None
    morphology: str | None = None
    parallax: float | None = None
    proper_motion_ra: float | None = None
    proper_motion_dec: float | None = None
    cross_ids: list[CrossId] = []
    surveys: list[SurveyStatus] = []
    references: list[Reference] = []
    all_data: dict[str, list[dict]] = {}


@router.get("/object-detail", response_model=ObjectDetailResponse)
@limiter.limit("30/minute")
async def get_object_detail(
    request: Request,
    name: str = Query(..., description="Object name (e.g. M31, NGC 1068)"),
    ra: float | None = Query(None),
    dec: float | None = Query(None),
):
    """Aggregate all available information about an astronomical object."""
    # Cache
    ck = cache_key("object_detail", name=name, ra=ra, dec=dec)
    cached = await cache_get(ck)
    if cached is not None:
        return ObjectDetailResponse(**cached)

    from app.connectors.simbad import SIMBADConnector

    simbad = SIMBADConnector()

    # 1. Get canonical info from SIMBAD
    detail = await simbad.get_object_detail(name)
    if detail is None:
        # Try resolving coordinates
        if ra is not None and dec is not None:
            detail = {
                "name": name, "ra": ra, "dec": dec,
                "object_type": "", "object_type_long": "",
            }
        else:
            raise HTTPException(status_code=404, detail=f"Object '{name}' not found in SIMBAD")

    obj_ra = detail.get("ra") or ra or 0.0
    obj_dec = detail.get("dec") or dec or 0.0

    # 2. Parallel: cross-IDs + cone searches + ADS
    # Fast sources only — skip chandra (404 retries) and mast (frequent timeouts)
    survey_sources = ["sdss", "gaia", "ned", "2mass", "allwise"]
    search_radius = 0.005  # ~18 arcsec

    async def _cone_search(src: str):
        try:
            conn = get_connector(src)
            results = await asyncio.wait_for(
                conn.search(name, ra=obj_ra, dec=obj_dec, radius=search_radius),
                timeout=8.0,
            )
            return src, results
        except Exception:
            return src, []

    async def _get_ids():
        try:
            return await asyncio.wait_for(simbad.get_identifiers(detail.get("name", name)), timeout=10.0)
        except Exception:
            return []

    async def _get_refs():
        try:
            from app.api.citations import _search_ads_sync
            loop = asyncio.get_running_loop()
            refs = await loop.run_in_executor(None, _search_ads_sync, detail.get("name", name))
            return refs[:10] if refs else []
        except Exception:
            return []

    # Run all in parallel
    tasks = [_cone_search(s) for s in survey_sources]
    tasks.append(_get_ids())  # type: ignore
    tasks.append(_get_refs())  # type: ignore
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Parse results
    survey_results = all_results[:len(survey_sources)]
    cross_ids_raw = all_results[len(survey_sources)] if not isinstance(all_results[len(survey_sources)], Exception) else []
    refs_raw = all_results[len(survey_sources) + 1] if not isinstance(all_results[len(survey_sources) + 1], Exception) else []

    surveys: list[SurveyStatus] = []
    all_data: dict[str, list[dict]] = {}
    for r in survey_results:
        if isinstance(r, Exception):
            continue
        src, objects = r
        surveys.append(SurveyStatus(source=src, has_data=len(objects) > 0, count=len(objects)))
        if objects:
            all_data[src] = [_astro_to_result(o).model_dump() for o in objects[:10]]

    cross_ids = [CrossId(name=ci["name"]) for ci in (cross_ids_raw if isinstance(cross_ids_raw, list) else [])]

    references: list[Reference] = []
    if isinstance(refs_raw, list):
        for ref in refs_raw:
            if isinstance(ref, dict):
                references.append(Reference(
                    bibcode=ref.get("bibcode", ""),
                    title=ref.get("title", ""),
                    authors=ref.get("authors", [])[:5],
                    year=ref.get("year", ""),
                ))

    response = ObjectDetailResponse(
        name=detail.get("name", name),
        ra=_safe_float(obj_ra) or 0.0,
        dec=_safe_float(obj_dec) or 0.0,
        object_type=detail.get("object_type", ""),
        object_type_long=detail.get("object_type_long", ""),
        redshift=_safe_float(detail.get("redshift")),
        radial_velocity=_safe_float(detail.get("radial_velocity")),
        spectral_type=detail.get("spectral_type"),
        morphology=detail.get("morphology"),
        parallax=_safe_float(detail.get("parallax")),
        proper_motion_ra=_safe_float(detail.get("proper_motion_ra")),
        proper_motion_dec=_safe_float(detail.get("proper_motion_dec")),
        cross_ids=cross_ids,
        surveys=surveys,
        references=references,
        all_data=all_data,
    )

    await cache_set(ck, response.model_dump(), ttl=600)
    return response


# ── FITS Upload & Browse ──


class FITSFileInfo(BaseModel):
    id: str
    filename: str
    fits_path: str
    size_bytes: int
    source: str
    object_id: str
    created_at: str | None = None
    metadata: dict = {}


@router.post("/fits/upload", response_model=FITSFileInfo)
@limiter.limit("10/minute")
async def upload_fits_file(
    request: Request,
    file: UploadFile = FastAPIFile(..., description="FITS file to upload"),
    object_id: str = Query("", description="Optional object identifier"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a FITS file for use in pipelines and visualization."""
    from app.config import settings as app_settings

    if not file.filename or not file.filename.lower().endswith((".fits", ".fit", ".fts", ".fits.gz")):
        raise HTTPException(status_code=400, detail="Only FITS files are accepted (.fits, .fit, .fts, .fits.gz)")

    max_size = app_settings.max_upload_size
    # Check declared size first to reject obviously too-large uploads early
    if file.size and file.size > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {max_size // (1024*1024)} MB",
        )

    # Read with a hard limit to prevent memory exhaustion
    contents = await file.read(max_size + 1)
    if len(contents) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {max_size // (1024*1024)} MB",
        )

    # Validate it's actually a FITS file
    from astropy.io import fits
    fits_type_info = {}
    try:
        hdul = fits.open(io.BytesIO(contents))
        n_hdus = len(hdul)
        primary_header = dict(hdul[0].header) if hdul[0].header else {}
        # Phase 2A: Auto-detect FITS file type
        fits_type_info = detect_fits_type(hdul)
        hdul.close()
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a valid FITS file")

    # Store file
    file_uuid = uuid.uuid4().hex
    safe_name = file.filename.replace("/", "_").replace("\\", "_")
    fits_path = f"uploads/{str(user.id)[:8]}/{file_uuid}_{safe_name}"
    upload_fits(fits_path, contents)
    from app.storage import get_storage_metadata
    storage_meta = get_storage_metadata(fits_path)

    # Sanitize header for JSON storage
    meta: dict = {}
    for k, v in primary_header.items():
        if k and k.strip():
            try:
                import json as _json
                _json.dumps(v)
                meta[k] = v
            except (TypeError, ValueError):
                meta[k] = str(v)

    data_file = DataFile(
        user_id=user.id,
        source="upload",
        object_id=object_id or safe_name,
        fits_path=fits_path,
        metadata_={
            "n_hdus": n_hdus,
            "size_bytes": len(contents),
            "original_filename": safe_name,
            "sha256": storage_meta.get("sha256"),
            "storage_backend": storage_meta.get("backend"),
            "storage_version_id": storage_meta.get("version_id"),
            **fits_type_info,
            **meta,
        },
    )
    db.add(data_file)
    await db.commit()
    await db.refresh(data_file)

    return FITSFileInfo(
        id=str(data_file.id),
        filename=safe_name,
        fits_path=fits_path,
        size_bytes=len(contents),
        source="upload",
        object_id=data_file.object_id,
        created_at=data_file.created_at.isoformat() if data_file.created_at else None,
        metadata=data_file.metadata_ or {},
    )


class AnalyzeRequest(BaseModel):
    fits_path: str
    api_key: str | None = None


@router.post("/fits/analyze")
@limiter.limit("10/minute")
async def analyze_fits_spectrum(
    request: Request,
    req: AnalyzeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """AI-powered spectrum analysis: extract features + Claude interpretation."""
    from app.services.spectrum_analyzer import (
        extract_spectrum_from_fits,
        analyze_spectrum,
        ai_interpret,
    )
    from app.pipeline.nodes.redshift import redshift_estimate

    # IDOR protection: canonicalize the storage key and require its DataFile
    # row to belong to the current user before any scientific parser sees it.
    try:
        req.fits_path = await _require_owned_file_by_path(db, user, req.fits_path)
    except HTTPException as exc:
        if exc.status_code == 404:
            exc.detail = "FITS file not found"
        raise

    # 1. Extract spectrum
    try:
        spec_data = extract_spectrum_from_fits(req.fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    wavelength = spec_data["wavelength"]
    flux = spec_data["flux"]

    # 2. Automated analysis
    try:
        summary = analyze_spectrum(wavelength, flux)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. Automated redshift estimation
    redshift_result = None
    if len(wavelength) >= 50:
        try:
            rz = redshift_estimate(
                {"data": {"wavelength": wavelength, "flux": flux}},
                {"method": "chi2_grid", "z_min": 0.0, "z_max": 2.0, "z_step": 0.001},
            )
            redshift_result = rz.get("redshift_result")
        except Exception as e:
            logger.warning("Redshift estimation failed: %s", e)

    # 4. Build response with automated results
    result: dict = {
        "peaks": [
            {"wavelength": p.wavelength, "flux": p.flux, "snr": p.snr, "is_emission": p.is_emission}
            for p in summary.peaks
        ],
        "continuum_shape": summary.continuum_shape,
        "redshift_auto": redshift_result,
        "wavelength_range": [summary.wavelength_min, summary.wavelength_max],
        "n_points": summary.n_points,
    }

    # 5. AI interpretation (optional, requires API key)
    # BYOK: use only req.api_key or the user's stored key; do not fall back to the platform env key.
    api_key = req.api_key
    if not api_key:
        user_keys = (user.api_keys or {}) if user else {}
        api_key = (
            user_keys.get("anthropic")
            or (user.anthropic_api_key if user and user.anthropic_api_key else None)
        )

    if api_key:
        try:
            ai_result = await ai_interpret(summary, redshift_result, api_key)
            result["ai_classification"] = ai_result.get("classification", "unknown")
            result["ai_confidence"] = ai_result.get("confidence", "low")
            result["ai_redshift"] = ai_result.get("redshift")
            result["ai_lines"] = ai_result.get("lines", [])
            result["ai_special_features"] = ai_result.get("special_features", [])
            result["ai_summary"] = ai_result.get("summary", "")
            result["ai_narrative"] = ai_result.get("narrative", "")
            result["ai_next_steps"] = ai_result.get("next_steps", [])
        except Exception:
            logger.warning("AI interpretation failed", exc_info=True)
            result["ai_error"] = "AI interpretation is temporarily unavailable."
    else:
        result["ai_error"] = "No API key available for AI analysis. Set your Anthropic key in Settings."

    return result


@router.get("/fits/browse", response_model=list[FITSFileInfo])
async def browse_fits_files(
    source: str | None = Query(None, description="Filter by source (upload, sdss, gaia, ...)"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all FITS files in the user's workspace."""
    query = select(DataFile).where(DataFile.user_id == user.id)
    if source:
        query = query.where(DataFile.source == source)
    query = query.order_by(DataFile.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    files = result.scalars().all()

    out = []
    for f in files:
        meta = f.metadata_ or {}
        out.append(FITSFileInfo(
            id=str(f.id),
            filename=meta.get("original_filename", f.object_id),
            fits_path=f.fits_path,
            size_bytes=meta.get("size_bytes", 0),
            source=f.source,
            object_id=f.object_id,
            created_at=f.created_at.isoformat() if f.created_at else None,
            metadata=meta,
        ))
    return out


@router.get("/fits/download")
async def download_fits_file(
    fits_path: str = Query(..., description="Storage path to FITS file"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download a FITS file for local use."""
    from fastapi.responses import Response

    fits_path = await _require_owned_file_by_path(db, user, fits_path)

    try:
        raw = download_fits(fits_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="FITS file not found")

    filename = fits_path.split("/")[-1]
    return Response(
        content=raw,
        media_type="application/fits",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(raw)),
        },
    )


@router.post("/files/upload")
@limiter.limit("10/minute")
async def upload_general_file(
    request: Request,
    file: UploadFile = FastAPIFile(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload any research file (CSV, VOTable, TXT, FITS, PDF, etc.)."""
    from app.config import settings as app_settings

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    max_size = app_settings.max_upload_size
    if file.size and file.size > max_size:
        raise HTTPException(status_code=413, detail=f"File too large. Max {max_size // (1024*1024)} MB")

    contents = await file.read(max_size + 1)
    if len(contents) > max_size:
        raise HTTPException(status_code=413, detail=f"File too large. Max {max_size // (1024*1024)} MB")

    file_uuid = uuid.uuid4().hex
    # Quotes/control chars also sanitized (2026-06-11): the chat composer
    # embeds the returned path inside a quoted user_file="..." string, and a
    # filename like my"data.csv would break that quoting.
    safe_name = file.filename.replace("/", "_").replace("\\", "_").replace('"', "_").replace("'", "_")
    safe_name = "".join(ch if ch.isprintable() else "_" for ch in safe_name)
    storage_path = f"uploads/{str(user.id)[:8]}/{file_uuid}_{safe_name}"
    upload_fits(storage_path, contents)  # reuse storage function (works for any file)
    from app.storage import get_storage_metadata
    storage_meta = get_storage_metadata(storage_path)

    data_file = DataFile(
        user_id=user.id,
        source="upload",
        object_id=safe_name,
        fits_path=storage_path,
        metadata_={
            "original_filename": safe_name,
            "size_bytes": len(contents),
            "content_type": file.content_type or "application/octet-stream",
            "sha256": storage_meta.get("sha256"),
            "storage_backend": storage_meta.get("backend"),
            "storage_version_id": storage_meta.get("version_id"),
        },
    )
    db.add(data_file)
    await db.commit()
    await db.refresh(data_file)

    return {
        "id": str(data_file.id),
        "filename": safe_name,
        "path": storage_path,
        "size_bytes": len(contents),
    }


@router.get("/files/download")
async def download_general_file(
    path: str = Query(..., description="Storage path"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Download any file from storage."""
    from fastapi.responses import Response

    path = await _require_owned_file_by_path(db, user, path)

    try:
        raw = download_fits(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    filename = path.split("/")[-1]
    # Guess content type from extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_types = {
        "fits": "application/fits", "fit": "application/fits", "fts": "application/fits",
        "csv": "text/csv", "tsv": "text/tab-separated-values",
        "json": "application/json", "txt": "text/plain",
        "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
        "vot": "application/xml", "xml": "application/xml",
        "ipynb": "application/x-ipynb+json",
    }
    ctype = content_types.get(ext, "application/octet-stream")

    return Response(
        content=raw,
        media_type=ctype,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(raw)),
        },
    )


@router.delete("/fits/{file_id}")
async def delete_fits_file(
    file_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a FITS file from the user's workspace."""
    try:
        fid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID")

    result = await db.execute(
        select(DataFile).where(DataFile.id == fid, DataFile.user_id == user.id)
    )
    data_file = result.scalar_one_or_none()
    if data_file is None:
        raise HTTPException(status_code=404, detail="File not found")

    # Delete through the configured backend (local volume or S3-compatible
    # object store) so database and object lifecycle stay aligned.
    from app.storage import delete_fits
    if data_file.fits_path:
        delete_fits(data_file.fits_path)

    await db.delete(data_file)
    await db.commit()

    return {"deleted": True}


# ── Saved Objects / Bookmarks ──


class SaveObjectRequest(BaseModel):
    name: str
    ra: float = 0.0
    dec: float = 0.0
    object_type: str = ""
    source: str = ""
    redshift: float | None = None
    notes: str = ""
    project: str = "Default"


class SavedObjectInfo(BaseModel):
    id: str
    name: str
    ra: float
    dec: float
    object_type: str
    source: str
    redshift: float | None
    notes: str | None
    project: str
    created_at: str | None


@router.post("/saved-objects")
async def save_object(
    req: SaveObjectRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bookmark an astronomical object."""
    from app.models.schemas import SavedObject
    obj = SavedObject(
        user_id=user.id,
        name=req.name,
        ra=req.ra,
        dec=req.dec,
        object_type=req.object_type,
        source=req.source,
        redshift=req.redshift,
        notes=req.notes,
        project=req.project,
    )
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return {"id": str(obj.id), "saved": True}


@router.get("/saved-objects", response_model=list[SavedObjectInfo])
async def list_saved_objects(
    project: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List user's saved objects, optionally filtered by project."""
    from app.models.schemas import SavedObject
    q = select(SavedObject).where(SavedObject.user_id == user.id)
    if project:
        q = q.where(SavedObject.project == project)
    q = q.order_by(SavedObject.created_at.desc()).limit(200)
    result = await db.execute(q)
    return [
        SavedObjectInfo(
            id=str(o.id), name=o.name, ra=o.ra, dec=o.dec,
            object_type=o.object_type, source=o.source,
            redshift=o.redshift, notes=o.notes, project=o.project,
            created_at=o.created_at.isoformat() if o.created_at else None,
        )
        for o in result.scalars().all()
    ]


@router.get("/saved-objects/projects")
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List distinct project names for the user."""
    from app.models.schemas import SavedObject
    from sqlalchemy import distinct
    result = await db.execute(
        select(distinct(SavedObject.project)).where(SavedObject.user_id == user.id)
    )
    return [row[0] for row in result.all()]


@router.delete("/saved-objects/{obj_id}")
async def delete_saved_object(
    obj_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a saved object."""
    from app.models.schemas import SavedObject
    try:
        oid = uuid.UUID(obj_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID")
    result = await db.execute(
        select(SavedObject).where(SavedObject.id == oid, SavedObject.user_id == user.id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(status_code=404, detail="Saved object not found")
    await db.delete(obj)
    await db.commit()
    return {"deleted": True}


# ── Batch Object Lookup ──


class BatchLookupRequest(BaseModel):
    names: list[str]


@router.post("/batch-lookup")
@limiter.limit("5/minute")
async def batch_object_lookup(
    request: Request,
    req: BatchLookupRequest,
):
    """Look up multiple objects in parallel via SIMBAD. Max 50 per request."""
    if len(req.names) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 objects per batch request")

    from app.connectors.simbad import SIMBADConnector
    simbad = SIMBADConnector()

    async def _lookup(name: str):
        try:
            detail = await asyncio.wait_for(simbad.get_object_detail(name), timeout=10.0)
            return {"name": name, "found": detail is not None, **(detail or {})}
        except Exception as e:
            return {"name": name, "found": False, "error": str(e)}

    tasks = [_lookup(n) for n in req.names]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    return {
        "results": [
            r if not isinstance(r, Exception) else {"name": "?", "found": False, "error": str(r)}
            for r in results
        ],
        "total": len(req.names),
        "found": sum(1 for r in results if not isinstance(r, Exception) and r.get("found")),
    }


# ── Object Fetch (catch-all) ──
# This route matches any two-segment path under /api/data, so it MUST be
# registered last — otherwise it shadows concrete two-segment GET routes such
# as /saved-objects/projects (Starlette matches in registration order).


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
        fits_file = await asyncio.wait_for(connector.fetch(object_id), timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Timeout fetching from {source} — the external service took too long")
    except NotImplementedError as exc:
        # Some searchable archives require product staging/authentication that
        # this connector does not implement.  Report that boundary explicitly
        # instead of returning a metadata-only empty FITS file or labelling it
        # as an upstream outage.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Fetch from %s failed for %s", source, object_id)
        raise HTTPException(status_code=502, detail=f"Failed to fetch from {source}: {type(e).__name__}: {e}")

    fits_path = f"{source}/{object_id.replace('/', '_')}/{uuid.uuid4().hex}.fits"
    try:
        upload_fits(fits_path, fits_file.data)
        from app.storage import get_storage_metadata
        storage_meta = get_storage_metadata(fits_path)
    except Exception as e:
        logger.warning("Failed to store FITS locally (%s): %s", fits_path, e)
        # Still return success — the data was fetched even if storage failed
        return FetchResult(
            source=source,
            object_id=object_id,
            fits_path="",
            filename=fits_file.filename,
            file_id=None,
        )

    # Save to database if user is authenticated
    file_id = None
    if user:
        data_file = DataFile(
            user_id=user.id,
            source=source,
            object_id=object_id,
            fits_path=fits_path,
            metadata_={
                "original_filename": fits_file.filename,
                "size_bytes": len(fits_file.data),
                "sha256": storage_meta.get("sha256"),
                "storage_backend": storage_meta.get("backend"),
                "storage_version_id": storage_meta.get("version_id"),
            },
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
