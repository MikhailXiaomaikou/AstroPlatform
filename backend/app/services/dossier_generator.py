"""Cross-match dossier generator.

Concurrently queries SIMBAD, Gaia DR3, SDSS, 2MASS, AllWISE, NED, and TNS
for a given sky position, then compiles the results into a structured dossier
with photometry, astrometry, redshift, host galaxy info, and prior classifications.
"""

import asyncio
import logging
import time
from collections import OrderedDict

import httpx

from app.connectors.simbad import _is_galactic_stellar_type
from app.services.transient_service import search_tns

logger = logging.getLogger(__name__)

# ── In-memory cache (max 100 entries, 1-hour TTL) ──

_CACHE_MAX = 100
_CACHE_TTL = 3600  # seconds

_dossier_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def _cache_key(ra: float, dec: float) -> str:
    return f"{ra:.4f}_{dec:.4f}"


def _cache_get(key: str) -> dict | None:
    entry = _dossier_cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _CACHE_TTL:
        _dossier_cache.pop(key, None)
        return None
    # Move to end (most recently used)
    _dossier_cache.move_to_end(key)
    return data


def _cache_put(key: str, data: dict) -> None:
    _dossier_cache[key] = (time.time(), data)
    _dossier_cache.move_to_end(key)
    while len(_dossier_cache) > _CACHE_MAX:
        _dossier_cache.popitem(last=False)


# ── Individual source queries ──

_TIMEOUT = 15.0


def _safe_float(val: object) -> float | None:
    if val is None or val == "" or val == "None":
        return None
    try:
        f = float(val)
        if f != f or f == float("inf") or f == float("-inf"):
            return None
        return f
    except (ValueError, TypeError):
        return None


async def _query_simbad(ra: float, dec: float) -> dict:
    """Query SIMBAD TAP for object type, cross-IDs, and spectral type."""
    params = {
        "Ident": "",
        "NbIdent": "1",
        "Radius": "10",
        "Radius.unit": "arcsec",
        "submit": "submit id",
        "output.format": "ASCII",
        "Coord": f"{ra}d {'+' if dec >= 0 else ''}{dec}d",
        "CooFrame": "ICRS",
        "CooEpoch": "2000",
        "CooEqui": "2000",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://simbad.cds.unistra.fr/simbad/sim-coo",
            params=params,
        )
        resp.raise_for_status()
        text = resp.text

    result: dict = {"status": "ok"}

    # Parse basic info from the text response
    lines = text.split("\n")
    for line in lines:
        stripped = line.strip()
        if "Object" in stripped and "---" not in stripped:
            # Try to extract the main ID
            parts = stripped.split("---")
            if len(parts) >= 2:
                result["main_id"] = parts[0].replace("Object", "").strip()
                result["object_type"] = (
                    parts[1].strip().split()[0] if parts[1].strip() else None
                )
            elif "Object" in stripped:
                after = stripped.split("Object")[-1].strip().lstrip(":").strip()
                if after:
                    result["main_id"] = after.split("---")[0].strip()
        if "Spectral type:" in stripped:
            result["spectral_type"] = (
                stripped.split("Spectral type:")[-1].strip().split()[0]
            )
        if "Identifiers (" in stripped:
            # e.g. "Identifiers (23):"
            pass

    # Try TAP for structured cross-IDs
    try:
        adql = (
            f"SELECT TOP 1 main_id, otype, otype_txt, sp_type, "
            f"rvz_redshift, rvz_radvel, plx_value, pmra, pmdec "
            f"FROM basic "
            f"WHERE CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra}, {dec}, {10.0 / 3600.0})) = 1"
        )
        tap_params = {
            "request": "doQuery",
            "lang": "ADQL",
            "format": "json",
            "query": adql,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            tap_resp = await client.get(
                "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
                params=tap_params,
            )
            if tap_resp.status_code == 200:
                tap_data = tap_resp.json()
                rows = tap_data.get("data", [])
                if rows:
                    row = rows[0]
                    cols = [c["name"] for c in tap_data.get("metadata", [])]
                    rec = dict(zip(cols, row))
                    result["main_id"] = rec.get("main_id") or result.get("main_id")
                    result["object_type"] = (
                        rec.get("otype_txt")
                        or rec.get("otype")
                        or result.get("object_type")
                    )
                    result["spectral_type"] = rec.get("sp_type") or result.get(
                        "spectral_type"
                    )
                    rvz_redshift = _safe_float(rec.get("rvz_redshift"))
                    radial_velocity = _safe_float(rec.get("rvz_radvel"))
                    if _is_galactic_stellar_type(
                        result.get("object_type"),
                        rec.get("otype_txt"),
                        result.get("spectral_type"),
                    ):
                        result["redshift"] = None
                        result["radial_velocity_km_s"] = radial_velocity
                        if rvz_redshift is not None:
                            result["redshift_note"] = (
                                "SIMBAD rvz_redshift is a velocity-derived "
                                "quantity for Galactic stellar objects; use "
                                "radial_velocity_km_s instead."
                            )
                    else:
                        result["redshift"] = rvz_redshift
                        result["radial_velocity_km_s"] = radial_velocity
    except Exception:
        pass  # Fall back to text-parsed data

    # Fetch identifiers
    main_id = result.get("main_id")
    if main_id:
        try:
            id_adql = (
                f"SELECT id FROM ident "
                f"WHERE oidref = (SELECT TOP 1 oid FROM basic "
                f"WHERE CONTAINS(POINT('ICRS', ra, dec), "
                f"CIRCLE('ICRS', {ra}, {dec}, {10.0 / 3600.0})) = 1) "
            )
            async with httpx.AsyncClient(
                timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                id_resp = await client.get(
                    "https://simbad.cds.unistra.fr/simbad/sim-tap/sync",
                    params={
                        "request": "doQuery",
                        "lang": "ADQL",
                        "format": "json",
                        "query": id_adql,
                    },
                )
                if id_resp.status_code == 200:
                    id_data = id_resp.json()
                    id_rows = id_data.get("data", [])
                    result["cross_ids"] = [r[0] for r in id_rows if r]
        except Exception:
            pass

    return result


async def _query_gaia(ra: float, dec: float) -> dict:
    """Query Gaia DR3 for parallax, proper motion, and G magnitude."""
    adql = (
        f"SELECT TOP 1 source_id, parallax, parallax_error, "
        f"pmra, pmdec, phot_g_mean_mag, phot_bp_mean_mag, phot_rp_mean_mag, "
        f"ruwe, "
        f"DISTANCE(POINT('ICRS', ra, dec), POINT('ICRS', {ra}, {dec})) AS dist "
        f"FROM gaiadr3.gaia_source "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), "
        f"CIRCLE('ICRS', {ra}, {dec}, {10.0 / 3600.0})) = 1 "
        f"ORDER BY dist ASC"
    )
    params = {
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "json",
        "QUERY": adql,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://gea.esac.esa.int/tap-server/tap/sync",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    rows = data.get("data", [])
    if not rows:
        return {"status": "no_match"}

    cols = [c["name"] for c in data.get("metadata", [])]
    raw_rec = dict(zip(cols, rows[0]))
    # Normalize column names to lowercase (TAP services return UPPERCASE)
    rec = {k.lower(): v for k, v in raw_rec.items()}

    parallax = _safe_float(rec.get("parallax"))
    distance_pc = None
    if parallax and parallax > 0:
        distance_pc = round(1000.0 / parallax, 2)

    return {
        "status": "ok",
        "source_id": rec.get("source_id"),
        "parallax_mas": parallax,
        "parallax_error_mas": _safe_float(rec.get("parallax_error")),
        "pm_ra": _safe_float(rec.get("pmra")),
        "pm_dec": _safe_float(rec.get("pmdec")),
        "g_mag": _safe_float(rec.get("phot_g_mean_mag")),
        "bp_mag": _safe_float(rec.get("phot_bp_mean_mag")),
        "rp_mag": _safe_float(rec.get("phot_rp_mean_mag")),
        "ruwe": _safe_float(rec.get("ruwe")),
        "distance_pc": distance_pc,
    }


async def _query_sdss(ra: float, dec: float) -> dict:
    """Query SDSS DR18 for ugriz photometry."""
    params = {
        "ra": ra,
        "dec": dec,
        "radius": 0.15,  # arcmin
        "format": "json",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://skyserver.sdss.org/dr18/SkyServerWS/SearchTools/RadialSearch",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    rows = data if isinstance(data, list) else data.get("Rows", data.get("rows", []))
    if not rows:
        return {"status": "no_match"}

    # Take closest match
    obj = rows[0] if isinstance(rows[0], dict) else {}
    lower_obj = {str(key).lower(): value for key, value in obj.items()}

    def _row_float(*keys: str) -> float | None:
        for key in keys:
            value = _safe_float(lower_obj.get(key.lower()))
            if value is not None:
                return value
        return None

    redshift = _row_float("redshift")
    return {
        "status": "ok",
        "objid": obj.get("objid") or obj.get("ObjID"),
        "u": _row_float("u"),
        "g": _row_float("g"),
        "r": _row_float("r"),
        "i": _row_float("i"),
        "z": _row_float("z"),
        # SkyServer deployments have used several spellings for the same
        # catalog-reported magnitude errors. Preserve whichever one is
        # present; never manufacture a substitute here.
        "u_err": _row_float("err_u", "u_err", "uErr", "psfMagErr_u"),
        "g_err": _row_float("err_g", "g_err", "gErr", "psfMagErr_g"),
        "r_err": _row_float("err_r", "r_err", "rErr", "psfMagErr_r"),
        "i_err": _row_float("err_i", "i_err", "iErr", "psfMagErr_i"),
        "z_err": _row_float("err_z", "z_err", "zErr", "psfMagErr_z"),
        "redshift": redshift,
        "type": obj.get("type"),
        "redshift_source": "spectroscopic" if redshift is not None else None,
    }


async def _query_2mass(ra: float, dec: float) -> dict:
    """Query 2MASS (VizieR II/246/out) for JHKs photometry."""
    adql = (
        f"SELECT TOP 1 RAJ2000, DEJ2000, Jmag, Hmag, Kmag, e_Jmag, e_Hmag, e_Kmag "
        f'FROM "II/246/out" '
        f"WHERE 1=CONTAINS(POINT('ICRS', RAJ2000, DEJ2000), "
        f"CIRCLE('ICRS', {ra}, {dec}, {10.0 / 3600.0}))"
    )
    params = {
        "request": "doQuery",
        "lang": "ADQL",
        "format": "json",
        "query": adql,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    rows = data.get("data", [])
    if not rows:
        return {"status": "no_match"}

    cols = [c["name"] for c in data.get("metadata", [])]
    rec = dict(zip(cols, rows[0]))
    return {
        "status": "ok",
        "J": _safe_float(rec.get("Jmag")),
        "H": _safe_float(rec.get("Hmag")),
        "Ks": _safe_float(rec.get("Kmag")),
        "J_err": _safe_float(rec.get("e_Jmag")),
        "H_err": _safe_float(rec.get("e_Hmag")),
        "Ks_err": _safe_float(rec.get("e_Kmag")),
    }


async def _query_allwise(ra: float, dec: float) -> dict:
    """Query AllWISE (VizieR II/328/allwise) for W1-W4 photometry."""
    adql = (
        f"SELECT TOP 1 RAJ2000, DEJ2000, W1mag, W2mag, W3mag, W4mag, "
        f"e_W1mag, e_W2mag, e_W3mag, e_W4mag "
        f'FROM "II/328/allwise" '
        f"WHERE 1=CONTAINS(POINT('ICRS', RAJ2000, DEJ2000), "
        f"CIRCLE('ICRS', {ra}, {dec}, {10.0 / 3600.0}))"
    )
    params = {
        "request": "doQuery",
        "lang": "ADQL",
        "format": "json",
        "query": adql,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    rows = data.get("data", [])
    if not rows:
        return {"status": "no_match"}

    cols = [c["name"] for c in data.get("metadata", [])]
    rec = dict(zip(cols, rows[0]))
    return {
        "status": "ok",
        "W1": _safe_float(rec.get("W1mag")),
        "W2": _safe_float(rec.get("W2mag")),
        "W3": _safe_float(rec.get("W3mag")),
        "W4": _safe_float(rec.get("W4mag")),
        "W1_err": _safe_float(rec.get("e_W1mag")),
        "W2_err": _safe_float(rec.get("e_W2mag")),
        "W3_err": _safe_float(rec.get("e_W3mag")),
        "W4_err": _safe_float(rec.get("e_W4mag")),
    }


async def _query_ned(ra: float, dec: float) -> dict:
    """Query NED for a galaxy match within 10 arcsec."""
    params = {
        "search_type": "Near Position Search",
        "RA": ra,
        "DEC": dec,
        "SR": 10.0 / 3600.0,  # degrees
        "of": "json",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(
            "https://ned.ipac.caltech.edu/srs/ObjectLookup",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

    # NED can return various structures
    results_list = []
    if isinstance(data, list):
        results_list = data
    elif isinstance(data, dict):
        results_list = data.get(
            "ResultList", data.get("objects", data.get("results", []))
        )
        # Might be nested
        if isinstance(results_list, dict):
            results_list = results_list.get("objects", [])

    if not results_list:
        return {"status": "no_match"}

    obj = results_list[0] if isinstance(results_list[0], dict) else {}
    return {
        "status": "ok",
        "name": obj.get("prefname", obj.get("name", obj.get("Name", ""))),
        "type": obj.get("type", obj.get("Type", "")),
        "redshift": _safe_float(obj.get("redshift", obj.get("Redshift"))),
        "morphology": obj.get("morphology", obj.get("Morphology", "")),
        "offset_arcsec": _safe_float(obj.get("separation", obj.get("dist_arcmin"))),
    }


async def _query_tns(ra: float, dec: float, name: str | None = None) -> dict:
    """Query TNS for existing transient classifications."""
    results = await search_tns(
        name=name,
        ra=ra,
        dec=dec,
        radius=10,
        days_back=365,
        max_results=5,
    )
    if not results:
        return {"status": "no_match"}
    # Filter out error/info-only entries
    classifications = [
        r
        for r in results
        if isinstance(r, dict) and not r.get("error") and not r.get("info")
    ]
    if not classifications:
        return {"status": "no_match"}
    return {
        "status": "ok",
        "classifications": classifications,
    }


# ── Main dossier generator ──


async def generate_dossier(
    ra: float,
    dec: float,
    name: str | None = None,
) -> dict:
    """Generate a comprehensive cross-match dossier for coordinates.

    Queries 7 sources concurrently (SIMBAD, Gaia DR3, SDSS, 2MASS,
    AllWISE, NED, TNS), each with a 15-second timeout and individual
    error handling. Results are compiled into a structured dossier.
    """
    cache_key = _cache_key(ra, dec)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    t_start = time.time()

    # Wrap each query with try/except and timeout
    async def _safe_query(label: str, coro):
        try:
            return label, await asyncio.wait_for(coro, timeout=_TIMEOUT)
        except asyncio.TimeoutError:
            return label, {
                "status": "unavailable",
                "error": f"{label} query timed out after {_TIMEOUT}s",
            }
        except Exception as e:
            return label, {"status": "unavailable", "error": str(e)}

    tasks = [
        _safe_query("simbad", _query_simbad(ra, dec)),
        _safe_query("gaia", _query_gaia(ra, dec)),
        _safe_query("sdss", _query_sdss(ra, dec)),
        _safe_query("twomass", _query_2mass(ra, dec)),
        _safe_query("allwise", _query_allwise(ra, dec)),
        _safe_query("ned", _query_ned(ra, dec)),
        _safe_query("tns", _query_tns(ra, dec, name=name)),
    ]

    raw_results = await asyncio.gather(*tasks)
    results = dict(raw_results)

    query_time = round(time.time() - t_start, 2)

    # Count responding sources
    sources_responded = sum(
        1
        for v in results.values()
        if isinstance(v, dict) and v.get("status") not in ("unavailable",)
    )

    # ── Compile structured dossier ──

    simbad = results.get("simbad", {})
    gaia = results.get("gaia", {})
    sdss = results.get("sdss", {})
    twomass = results.get("twomass", {})
    allwise = results.get("allwise", {})
    ned = results.get("ned", {})
    tns = results.get("tns", {})

    # Photometry
    photometry = {
        "optical": {
            "u": sdss.get("u"),
            "g": sdss.get("g"),
            "r": sdss.get("r"),
            "i": sdss.get("i"),
            "z": sdss.get("z"),
        },
        "nir": {
            "J": twomass.get("J"),
            "H": twomass.get("H"),
            "Ks": twomass.get("Ks"),
        },
        "mir": {
            "W1": allwise.get("W1"),
            "W2": allwise.get("W2"),
            "W3": allwise.get("W3"),
            "W4": allwise.get("W4"),
        },
    }
    # Preserve the catalog-reported magnitude uncertainties in a parallel
    # shape-compatible tree.  The former implementation queried 2MASS and
    # AllWISE errors but discarded them while compiling the dossier, forcing
    # downstream SED checks to invent a nominal uncertainty.
    photometry_errors = {
        "optical": {
            "u": sdss.get("u_err"),
            "g": sdss.get("g_err"),
            "r": sdss.get("r_err"),
            "i": sdss.get("i_err"),
            "z": sdss.get("z_err"),
        },
        "nir": {
            "J": twomass.get("J_err"),
            "H": twomass.get("H_err"),
            "Ks": twomass.get("Ks_err"),
        },
        "mir": {
            "W1": allwise.get("W1_err"),
            "W2": allwise.get("W2_err"),
            "W3": allwise.get("W3_err"),
            "W4": allwise.get("W4_err"),
        },
    }

    # Astrometry
    astrometry_warnings: list[str] = []
    ruwe = gaia.get("ruwe")
    if isinstance(ruwe, (int, float)) and ruwe > 1.4:
        astrometry_warnings.append(
            "Gaia RUWE > 1.4; astrometric solution may be affected by binarity, crowding, or variability."
        )
    astrometry = {
        "parallax_mas": gaia.get("parallax_mas"),
        "parallax_error_mas": gaia.get("parallax_error_mas"),
        "pm_ra": gaia.get("pm_ra"),
        "pm_dec": gaia.get("pm_dec"),
        "distance_pc": gaia.get("distance_pc"),
        "ruwe": ruwe,
        "warnings": astrometry_warnings,
    }

    # Redshift — prefer spectroscopic
    redshift_val = None
    redshift_source = None
    redshift_origin = None
    object_type = simbad.get("object_type") or ned.get("type") or "Unknown"
    spectral_type = simbad.get("spectral_type")
    is_galactic_stellar = _is_galactic_stellar_type(
        object_type,
        simbad.get("object_type_long"),
        spectral_type,
    )

    if is_galactic_stellar:
        redshift_source = "not_applicable_galactic_stellar"
        redshift_origin = None
    elif sdss.get("redshift") is not None:
        redshift_val = sdss["redshift"]
        redshift_source = sdss.get("redshift_source", "spectroscopic")
        redshift_origin = "SDSS"
    elif simbad.get("redshift") is not None:
        redshift_val = simbad["redshift"]
        redshift_source = "spectroscopic"
        redshift_origin = "SIMBAD"
    elif ned.get("redshift") is not None:
        redshift_val = ned["redshift"]
        redshift_source = "spectroscopic"
        redshift_origin = "NED"

    redshift = {
        "value": redshift_val,
        "source": redshift_source,
        "origin": redshift_origin,
    }
    redshift_note = simbad.get("redshift_note")
    if is_galactic_stellar and not redshift_note:
        redshift_note = (
            "Object is Galactic/stellar; small z-like values are not cosmological redshifts. "
            "Use radial_velocity_km_s when available."
        )
    if redshift_note:
        redshift["note"] = redshift_note
    if simbad.get("radial_velocity_km_s") is not None:
        redshift["radial_velocity_km_s"] = simbad.get("radial_velocity_km_s")

    # Host galaxy
    host_galaxy = {
        "name": ned.get("name") if ned.get("status") == "ok" else None,
        "offset_arcsec": ned.get("offset_arcsec")
        if ned.get("status") == "ok"
        else None,
        "redshift": ned.get("redshift") if ned.get("status") == "ok" else None,
        "morphology": ned.get("morphology") if ned.get("status") == "ok" else None,
    }

    # Cross-IDs
    cross_ids = simbad.get("cross_ids", [])

    warnings = []
    warnings.extend(astrometry_warnings)
    if redshift_note:
        warnings.append(redshift_note)

    # Prior classifications from TNS
    prior_classifications = []
    if tns.get("status") == "ok":
        for c in tns.get("classifications", []):
            prior_classifications.append(
                {
                    "name": c.get("name", ""),
                    "type": c.get("type", ""),
                    "discovery_date": c.get("discovery_date", ""),
                    "redshift": c.get("redshift"),
                }
            )

    dossier = {
        "object": {
            "name": name or simbad.get("main_id") or "Unknown",
            "ra": ra,
            "dec": dec,
        },
        "photometry": photometry,
        "photometry_errors": photometry_errors,
        "photometry_error_unit": "mag (catalog-reported 1-sigma)",
        "photometry_error_sources": {
            "optical": "SDSS DR18",
            "nir": "2MASS II/246/out",
            "mir": "AllWISE II/328/allwise",
        },
        "astrometry": astrometry,
        "redshift": redshift,
        "host_galaxy": host_galaxy,
        "cross_ids": cross_ids,
        "object_type": object_type,
        "spectral_type": spectral_type,
        "prior_classifications": prior_classifications,
        "sources_queried": 7,
        "sources_responded": sources_responded,
        "query_time_seconds": query_time,
        "warnings": warnings,
        "_raw": results,
    }

    _cache_put(cache_key, dossier)
    return dossier
