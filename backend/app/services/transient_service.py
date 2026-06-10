"""Transient/alert stream integration — TNS, Lasair (ZTF), and GCN."""

import asyncio
import logging
import math

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import TransientAlert
from app.services.alert_ingestion import fetch_ztf_lightcurve
from app.services.transient_classifier import (
    TransientClassifier,
    extract_lc_features,
)

logger = logging.getLogger(__name__)


async def search_tns(
    name: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    radius: float = 10,
    days_back: int = 30,
    obj_type: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Search Transient Name Server (TNS) for recent transients.

    Uses the TNS public search API.
    Returns list of dicts with keys:
        name, ra, dec, type, discovery_date, discovery_mag, discoverer, redshift, host_name
    """
    results: list[dict] = []
    url = "https://www.wis-tns.org/search"

    params: dict = {
        "format": "csv",
        "num_page": str(min(max_results, 50)),
        "discovered_period_value": str(days_back),
        "discovered_period_units": "days",
    }

    if name:
        params["name"] = name
    if ra is not None and dec is not None:
        params["ra"] = str(ra)
        params["decl"] = str(dec)
        params["radius"] = str(radius)
        params["coords_unit"] = "arcsec"
    if obj_type:
        params["type"] = obj_type

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            text = resp.text

            # Parse CSV response
            lines = text.strip().split("\n")
            if len(lines) < 2:
                return []

            headers = [h.strip().strip('"') for h in lines[0].split(",")]
            for line in lines[1 : max_results + 1]:
                if not line.strip():
                    continue
                # Simple CSV parse (TNS fields don't contain commas in values typically)
                vals = [v.strip().strip('"') for v in line.split(",")]
                row = dict(zip(headers, vals))
                results.append({
                    "name": row.get("Name", row.get("name", "")),
                    "ra": _safe_float(row.get("RA", row.get("ra"))),
                    "dec": _safe_float(row.get("DEC", row.get("decl", row.get("dec")))),
                    "type": row.get("Obj. Type", row.get("type", "")),
                    "discovery_date": row.get("Discovery Date (UT)", row.get("discoverydate", "")),
                    "discovery_mag": _safe_float(row.get("Discovery Mag/Flux", row.get("discoverymag"))),
                    "discoverer": row.get("Discovering Group/s", row.get("reporting_group", "")),
                    "redshift": _safe_float(row.get("Redshift", row.get("redshift"))),
                    "host_name": row.get("Host Name", row.get("hostname", "")),
                })

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("TNS rate limit hit — try again later")
            return [{"error": "TNS rate limit reached. Please wait a minute and retry."}]
        logger.warning("TNS HTTP error: %s", e)
    except Exception as e:
        logger.warning("TNS search failed: %s", e)
        # Fallback message
        if not results:
            return [{
                "info": (
                    "TNS public search is currently unavailable. "
                    "You can search manually at https://www.wis-tns.org/search . "
                    "For programmatic API access, register for a TNS bot key at "
                    "https://www.wis-tns.org/bots ."
                )
            }]

    return results


async def search_recent_transients(
    days_back: int = 7,
    max_results: int = 50,
) -> list[dict]:
    """Get recently reported transients from TNS.

    Returns list sorted by discovery date (newest first).
    """
    return await search_tns(days_back=days_back, max_results=max_results)


async def search_lasair(
    ra: float | None = None,
    dec: float | None = None,
    radius: float = 10,
    object_id: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    """Search Lasair (ZTF alert broker) for transients near coordinates.

    Uses Lasair public API: https://lasair-ztf.lsst.ac.uk/api/
    Returns list of dicts with keys:
        object_id, ra, dec, classification, last_mag, last_mjd
    """
    results: list[dict] = []
    base = "https://lasair-ztf.lsst.ac.uk/api"

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            if object_id:
                # Look up specific object
                resp = await client.get(f"{base}/objects/{object_id}/")
                resp.raise_for_status()
                obj = resp.json()
                results.append({
                    "object_id": obj.get("objectId", object_id),
                    "ra": _safe_float(obj.get("ramean")),
                    "dec": _safe_float(obj.get("decmean")),
                    "classification": obj.get("classification", ""),
                    "last_mag": _safe_float(obj.get("maglast")),
                    "last_mjd": _safe_float(obj.get("mjdlast")),
                })
            elif ra is not None and dec is not None:
                # Cone search
                resp = await client.get(
                    f"{base}/cone/",
                    params={
                        "ra": ra,
                        "dec": dec,
                        "radius": radius,  # arcsec
                        "pagesize": min(max_results, 100),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                objects = data if isinstance(data, list) else data.get("results", data.get("objects", []))
                for obj in objects[:max_results]:
                    results.append({
                        "object_id": obj.get("objectId", obj.get("object_id", "")),
                        "ra": _safe_float(obj.get("ramean", obj.get("ra"))),
                        "dec": _safe_float(obj.get("decmean", obj.get("dec"))),
                        "classification": obj.get("classification", ""),
                        "last_mag": _safe_float(obj.get("maglast", obj.get("mag"))),
                        "last_mjd": _safe_float(obj.get("mjdlast", obj.get("mjd"))),
                    })
            else:
                return [{"info": "Lasair cone search requires ra and dec coordinates, or an object_id."}]

    except httpx.HTTPStatusError as e:
        logger.warning("Lasair HTTP error %s: %s", e.response.status_code, e)
        if e.response.status_code == 404 and object_id:
            return [{"error": f"Object '{object_id}' not found in Lasair/ZTF."}]
    except Exception as e:
        logger.warning("Lasair search failed: %s", e)
        if not results:
            return [{
                "info": (
                    "Lasair ZTF broker is currently unavailable. "
                    "You can search manually at https://lasair-ztf.lsst.ac.uk/ ."
                )
            }]

    return results


async def latest_gcn_circulars(
    keywords: str | None = None,
    max_results: int = 10,
) -> list[dict]:
    """Fetch recent GCN circulars.

    Uses the GCN API at https://gcn.nasa.gov/circulars .
    Returns list of dicts with keys:
        title, date, authors, body_preview, url
    """
    results: list[dict] = []

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            # GCN circulars API
            params: dict = {"limit": min(max_results, 100)}
            if keywords:
                params["query"] = keywords

            resp = await client.get(
                "https://gcn.nasa.gov/circulars",
                params=params,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()

            items = data if isinstance(data, list) else data.get("items", data.get("circulars", []))
            for circ in items[:max_results]:
                body = circ.get("body", "")
                results.append({
                    "title": circ.get("subject", circ.get("title", "")),
                    "date": circ.get("createdOn", circ.get("date", "")),
                    "authors": circ.get("submitter", circ.get("authors", "")),
                    "body_preview": body[:400] if body else "",
                    "url": f"https://gcn.nasa.gov/circulars/{circ.get('circularId', circ.get('id', ''))}",
                })

    except Exception as e:
        logger.warning("GCN circulars fetch failed: %s", e)
        if not results:
            return [{
                "info": (
                    "GCN circulars API is currently unavailable. "
                    "Browse circulars at https://gcn.nasa.gov/circulars ."
                )
            }]

    return results


async def query_transients(
    name: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    radius_arcsec: float = 10,
    days_back: int = 30,
    obj_type: str | None = None,
    db: AsyncSession | None = None,
) -> dict:
    """Unified transient query — searches local cache, TNS, and Lasair in parallel.

    When a database session is provided, local TransientAlert cache is checked
    first for speed.  Remote results from TNS and Lasair supplement the local data.

    Returns dict with 'tns', 'lasair', and 'local' result lists plus a summary.
    """
    tasks = []

    # Always search TNS
    tasks.append(
        search_tns(
            name=name,
            ra=ra,
            dec=dec,
            radius=radius_arcsec,
            days_back=days_back,
            obj_type=obj_type,
        )
    )

    # Search Lasair if we have coordinates or an object ID
    if ra is not None and dec is not None:
        tasks.append(
            search_lasair(ra=ra, dec=dec, radius=radius_arcsec)
        )
    elif name:
        # Try using name as a ZTF object ID if it looks like one (e.g., ZTF21abc...)
        if name.upper().startswith("ZTF"):
            tasks.append(search_lasair(object_id=name))

    gathered = await asyncio.gather(*tasks, return_exceptions=True)

    tns_results = gathered[0] if not isinstance(gathered[0], Exception) else []
    lasair_results = gathered[1] if len(gathered) > 1 and not isinstance(gathered[1], Exception) else []

    # Query local TransientAlert cache for fast, recent results
    local_results: list[dict] = []
    if db is not None:
        try:
            local_results = await _query_local_alerts(
                db, name=name, ra=ra, dec=dec,
                radius_arcsec=radius_arcsec, obj_type=obj_type,
            )
        except Exception as exc:
            logger.warning("Local alert query failed: %s", exc)

    # -- ZTF public light curve lookup + optional classification -----------
    ztf_lightcurve: dict | None = None
    ztf_classification: dict | None = None

    if ra is not None and dec is not None:
        try:
            ztf_lightcurve = await fetch_ztf_lightcurve(ra, dec, radius_arcsec)

            if ztf_lightcurve and not ztf_lightcurve.get("error"):
                bands = ztf_lightcurve.get("bands", {})
                # Pick the best-sampled band for classification.
                best_band: str | None = None
                best_count = 0
                for band_name, band_data in bands.items():
                    n = len(band_data.get("times", []))
                    if n > best_count:
                        best_count = n
                        best_band = band_name

                if best_band is not None and best_count > 5:
                    bd = bands[best_band]
                    features = extract_lc_features(
                        bd["times"], bd["mags"], bd["mag_errs"], band=best_band
                    )
                    ztf_classification = TransientClassifier.classify_transient(features)
                    ztf_classification["band_used"] = best_band
                    ztf_classification["n_epochs"] = best_count
        except Exception as exc:
            logger.warning("ZTF lightcurve/classification failed: %s", exc)

    total = len(tns_results) + len(lasair_results) + len(local_results)
    parts = []
    if local_results:
        parts.append(f"{len(local_results)} cached alert(s)")
    parts.append(f"{len(tns_results)} TNS result(s)")
    if lasair_results:
        parts.append(f"{len(lasair_results)} Lasair/ZTF result(s)")
    summary = "Found " + " and ".join(parts)

    result: dict = {
        "summary": summary,
        "total": total,
        "local": local_results,
        "tns": tns_results,
        "lasair": lasair_results,
    }

    if ztf_lightcurve is not None:
        result["ztf_lightcurve"] = ztf_lightcurve
    if ztf_classification is not None:
        result["ztf_classification"] = ztf_classification

    return result


async def _query_local_alerts(
    db: AsyncSession,
    name: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    radius_arcsec: float = 10,
    obj_type: str | None = None,
) -> list[dict]:
    """Query the local TransientAlert cache."""
    stmt = select(TransientAlert).order_by(TransientAlert.created_at.desc())

    if name:
        stmt = stmt.where(TransientAlert.source_id.ilike(f"%{name}%"))
    if ra is not None and dec is not None:
        radius_deg = radius_arcsec / 3600.0
        # Widen the RA half-window by 1/cos(dec): a fixed RA span covers fewer
        # arcsec of sky at high declination, so a flat box under-returns by
        # 1/cos(dec). Clamp cos(dec) to a small floor near the poles to avoid a
        # divide-by-~0 blowing the window up to the whole sky.
        cos_dec = max(math.cos(math.radians(dec)), 1e-3)
        ra_half = radius_deg / cos_dec
        ra_lo = ra - ra_half
        ra_hi = ra + ra_half
        if ra_lo < 0.0 or ra_hi > 360.0:
            # RA box straddles the 0/360 wraparound — match either side.
            stmt = stmt.where(
                or_(
                    TransientAlert.ra.between(ra_lo % 360.0, 360.0),
                    TransientAlert.ra.between(0.0, ra_hi % 360.0),
                ),
                TransientAlert.dec.between(dec - radius_deg, dec + radius_deg),
            )
        else:
            stmt = stmt.where(
                TransientAlert.ra.between(ra_lo, ra_hi),
                TransientAlert.dec.between(dec - radius_deg, dec + radius_deg),
            )
    if obj_type:
        stmt = stmt.where(TransientAlert.classification == obj_type)

    stmt = stmt.limit(50)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "source_id": row.source_id,
            "ra": row.ra,
            "dec": row.dec,
            "classification": row.classification,
            "magnitude": row.magnitude,
            "discovery_date": row.discovery_date.isoformat() if row.discovery_date else None,
            "redshift": row.redshift,
            "host_galaxy": row.host_galaxy,
            "source": "local_cache",
        }
        for row in rows
    ]


def _safe_float(val: object) -> float | None:
    """Convert a value to float, returning None on failure."""
    if val is None or val == "" or val == "None":
        return None
    try:
        f = float(val)  # type: ignore[arg-type]
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None
