"""Exoplanet M0 (2026-05-20): 8 LLM-callable tools集中文件.

Architecture mirrors ai_tools_solar_system.py:
- SOLAR_SYSTEM_TOOL_SCHEMAS analogue: EXOPLANET_TOOL_SCHEMAS
- dispatch_exoplanet(tool_name, tool_input) router
- _failed() helper returning the FAILED contract dict

Only 2 injection points into ai_tools.py:
  1. TOOLS.extend(EXOPLANET_TOOL_SCHEMAS)
  2. elif tool_name in EXOPLANET_TOOL_NAMES: return await dispatch_exoplanet(...)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── shared helpers (mirror ai_tools_solar_system) ───────────────────


def _failed(error: str, error_class: str, do_not_claim_msg: str | None = None) -> dict[str, Any]:
    return {
        "success": False,
        "__tool_status__": "FAILED",
        "analysis_status": "FAILED",
        "data_origin": "failed",
        "error": error,
        "error_class": error_class,
        "__do_not_claim__": True,
        "__message_to_model__": do_not_claim_msg or (
            f"Tool failed ({error_class}): {error}. Do not claim any numeric "
            f"value derived from this call."
        ),
    }


def _provenance_for(service_key: str, archive_version: str | None = None) -> dict | None:
    try:
        from app.services.provenance_v2.registry_loader import dataset_from_registry
        return dataset_from_registry(
            service_key, source_authority="datacenter_ivoa_compliant",
            archive_version=archive_version or f"{service_key}-2026", supplements={},
        )
    except Exception:
        return None


def _citation(label: str, year: str | int, bibcode: str | None = None) -> dict[str, str]:
    out = {"label": label, "year": str(year)}
    if bibcode:
        out["bibcode"] = bibcode
    return out


# ── 8 tool schemas ──────────────────────────────────────────────────


EXOPLANET_TOOL_SCHEMAS: list[dict] = [
    {
        "name": "query_exoplanet_archive",
        "description": (
            "Query NASA Exoplanet Archive (pscomppars composite-parameters table) for "
            "a confirmed planet or host system. Returns planet + stellar parameters. "
            "Reference: Akeson+ 2013 PASP 125, 989 (2013PASP..125..989A)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Planet name (e.g. 'HD 209458 b') or host star name (e.g. 'HD 209458').",
                },
            },
            "required": ["target"],
        },
    },
    {
        "name": "query_confirmed_planets",
        "description": (
            "Query NASA Exoplanet Archive with a filter clause (TAP-like WHERE). "
            "Returns matching confirmed planets. Use this for population queries "
            "(e.g. 'pl_rade < 2 AND pl_eqt BETWEEN 200 AND 350' for habitable-zone rocky planets)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "where_clause": {
                    "type": "string",
                    "description": "TAP-style WHERE clause on pscomppars columns. NO 'WHERE' keyword.",
                },
                "max_rows": {"type": "integer", "default": 50},
            },
            "required": ["where_clause"],
        },
    },
    {
        "name": "fetch_tess_lightcurve",
        "description": (
            "Fetch a TESS light curve via lightkurve (MAST). Returns time + flux + "
            "flux_err arrays. For PDC-SAP-corrected normalized data. Reference: "
            "Ricker+ 2015 JATIS 1, 014003 (TESS mission paper)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target name or TIC ID."},
                "sector": {"type": "integer", "description": "Optional TESS sector number."},
                "max_points": {"type": "integer", "default": 10000, "description": "Cap returned points."},
            },
            "required": ["target"],
        },
    },
    {
        "name": "fit_transit",
        "description": (
            "Fit a trapezoidal transit model to time + flux arrays. Returns period, "
            "t0, duration, depth, R_p/R_star. NOTE: simplified fast fit (Nelder-Mead). "
            "For publication-grade limb-darkening fits, use batman/pytransit downstream."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time": {"type": "array", "items": {"type": "number"}},
                "flux": {"type": "array", "items": {"type": "number"}},
                "flux_err": {"type": "array", "items": {"type": "number"}},
                "period_guess": {"type": "number"},
                "t0_guess": {"type": "number"},
                "duration_guess": {"type": "number", "default": 0.1},
                "depth_guess": {"type": "number", "default": 0.01},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["time", "flux"],
        },
    },
    {
        "name": "compute_equilibrium_temperature",
        "description": (
            "Compute planet equilibrium temperature from stellar radiation balance. "
            "T_eq = T_star * sqrt(R_star / 2a) * [f * (1-A_B)]^(1/4). Reference: "
            "Seager & Mallén-Ornelas 2003 ApJ 585, 1038 (2003ApJ...585.1038S)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "T_star_K": {"type": "number"},
                "R_star_solar": {"type": "number"},
                "a_au": {"type": "number"},
                "albedo": {"type": "number", "default": 0.3},
                "redistribution_factor": {"type": "number", "default": 1.0},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["T_star_K", "R_star_solar", "a_au"],
        },
    },
    {
        "name": "compute_transit_depth",
        "description": (
            "Compute geometric transit depth (R_p/R_star)². No limb darkening. "
            "Reference: Seager & Mallén-Ornelas 2003 ApJ 585, 1038."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "R_p_earth": {"type": "number"},
                "R_star_solar": {"type": "number"},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["R_p_earth", "R_star_solar"],
        },
    },
    {
        "name": "compute_planet_density",
        "description": (
            "Compute mean planet density rho = 3M / (4πR³) (g/cm³). Earth=5.51, "
            "Jupiter=1.33, Saturn=0.69. Reference: Seager & Mallén-Ornelas 2003."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "M_earth": {"type": "number"},
                "R_earth": {"type": "number"},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["M_earth", "R_earth"],
        },
    },
    {
        "name": "query_tess_target_list",
        "description": (
            "Query the TESS Input Catalog (TIC) for bright targets by magnitude range. "
            "Useful for survey-planning queries. Goes through astroquery.mast.Catalogs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_mag": {"type": "number", "default": 6.0},
                "max_mag": {"type": "number", "default": 10.0},
                "max_rows": {"type": "integer", "default": 100},
            },
            "required": [],
        },
    },
]


EXOPLANET_TOOL_NAMES = frozenset({s["name"] for s in EXOPLANET_TOOL_SCHEMAS})


# ── tool implementations ────────────────────────────────────────────


async def _exec_query_exoplanet_archive(inp: dict) -> dict:
    target = str(inp.get("target") or "").strip()
    if not target:
        return _failed("missing target", "missing_argument")
    try:
        from app.connectors.registry import get_connector
        connector = get_connector("nasa_exoplanet_archive")
        results = await connector.search(target)
        if not results:
            return {
                "success": True,
                "__tool_status__": "EMPTY",
                "__do_not_claim__": True,
                "__message_to_model__": (
                    f"NASA Exoplanet Archive returned no confirmed-planet match for "
                    f"{target!r}. Check spelling or try the host star name instead."
                ),
                "data_origin": "real_archive",
                "results": [],
                "target": target,
                "n_results": 0,
            }
        rows = []
        prov = None
        for obj in results:
            row = {"name": obj.name, "ra": obj.ra, "dec": obj.dec}
            row.update({k: v for k, v in (obj.extra or {}).items() if not k.startswith("_")})
            rows.append(row)
            if not prov and "_provenance_dataset" in (obj.extra or {}):
                prov = obj.extra["_provenance_dataset"]
        return {
            "success": True,
            "data_origin": "real_archive",
            "results": rows,
            "n_results": len(rows),
            "target": target,
            "_provenance_dataset": prov,
            "archive_ids": [r["name"] for r in rows if r.get("name")],
            "source_urls": ["https://exoplanetarchive.ipac.caltech.edu/"],
            "reference": "Akeson+ 2013 PASP 125, 989 (2013PASP..125..989A)",
            "bibcode": "2013PASP..125..989A",
        }
    except Exception as exc:
        return _failed(str(exc)[:200], type(exc).__name__)


async def _exec_query_confirmed_planets(inp: dict) -> dict:
    where = str(inp.get("where_clause") or "").strip()
    if not where:
        return _failed("missing where_clause", "missing_argument")
    max_rows = int(inp.get("max_rows", 50))
    try:
        from astroquery.ipac.nexsci.nasa_exoplanet_archive import NasaExoplanetArchive

        def _query():
            return NasaExoplanetArchive.query_criteria(
                table="pscomppars",
                where=where,
                select="pl_name,hostname,pl_orbper,pl_rade,pl_bmasse,pl_orbsmax,pl_eqt,st_teff,st_rad,discoverymethod",
            )

        table = await asyncio.wait_for(asyncio.to_thread(_query), timeout=60.0)
        if table is None or len(table) == 0:
            return {
                "success": True, "__tool_status__": "EMPTY", "__do_not_claim__": True,
                "__message_to_model__": f"No confirmed planets match {where!r}.",
                "results": [], "n_results": 0, "where_clause": where,
            }
        rows = [dict(row) for row in table[:max_rows]]
        prov = _provenance_for("nasa_exoplanet_archive")
        return {
            "success": True,
            "data_origin": "real_archive",
            "results": rows,
            "n_results": len(rows),
            "n_total": len(table),
            "where_clause": where,
            "_provenance_dataset": prov,
            "source_urls": ["https://exoplanetarchive.ipac.caltech.edu/"],
            "reference": "Akeson+ 2013 PASP 125, 989 (2013PASP..125..989A)",
            "bibcode": "2013PASP..125..989A",
        }
    except Exception as exc:
        return _failed(str(exc)[:200], type(exc).__name__)


async def _exec_fetch_tess_lightcurve(inp: dict) -> dict:
    target = str(inp.get("target") or "").strip()
    if not target:
        return _failed("missing target", "missing_argument")
    sector = inp.get("sector")
    max_points = int(inp.get("max_points", 10000))
    try:
        import lightkurve as lk

        def _search_and_download():
            search = lk.search_lightcurve(target, mission="TESS", sector=sector)
            if len(search) == 0:
                return None
            lc = search[0].download()
            if lc is None:
                return None
            lc = lc.remove_nans().normalize()
            return lc

        lc = await asyncio.wait_for(asyncio.to_thread(_search_and_download), timeout=120.0)
        if lc is None:
            return {
                "success": True, "__tool_status__": "EMPTY", "__do_not_claim__": True,
                "__message_to_model__": f"No TESS lightcurve found for {target!r}.",
                "target": target, "n_points": 0,
            }
        time_arr = list(lc.time.value)[:max_points]
        flux_arr = list(lc.flux.value)[:max_points]
        flux_err_arr = list(lc.flux_err.value)[:max_points] if lc.flux_err is not None else None
        return {
            "success": True,
            "data_origin": "real_archive",
            "target": target,
            "sector": sector,
            "time": time_arr,
            "flux": flux_arr,
            "flux_err": flux_err_arr,
            "n_points": len(time_arr),
            "n_total": len(lc.time),
            "_provenance_dataset": _provenance_for("nasa_exoplanet_archive", "tess-spoc"),
            "source_urls": ["https://archive.stsci.edu/missions-and-data/tess"],
            "reference": "Ricker+ 2015 JATIS 1, 014003 (TESS mission)",
            "bibcode": "2015JATIS...1a4003R",
        }
    except Exception as exc:
        return _failed(str(exc)[:200], type(exc).__name__)


async def _exec_fit_transit(inp: dict) -> dict:
    time_arr = inp.get("time") or []
    flux_arr = inp.get("flux") or []
    if not time_arr or not flux_arr:
        return _failed("missing time or flux arrays", "missing_argument")
    try:
        from app.services.exoplanet_transit import fit_transit_lightcurve

        result = fit_transit_lightcurve(
            time=time_arr, flux=flux_arr,
            flux_err=inp.get("flux_err"),
            period_guess=inp.get("period_guess"),
            t0_guess=inp.get("t0_guess"),
            duration_guess=float(inp.get("duration_guess", 0.1)),
            depth_guess=float(inp.get("depth_guess", 0.01)),
        )
        data_source = str(inp.get("data_source", "archive_cache"))
        result["success"] = True
        result["data_origin"] = "user_uploaded" if data_source == "user_supplied" else "cached_real"
        result["bibcode"] = "2002ApJ...580L.171M"
        result["citations"] = [_citation("Mandel & Agol", 2002, "2002ApJ...580L.171M")]
        return result
    except ValueError as exc:
        return _failed(str(exc), "invalid_argument")
    except Exception as exc:
        return _failed(str(exc)[:200], type(exc).__name__)


async def _exec_compute_equilibrium_temperature(inp: dict) -> dict:
    try:
        from app.services.exoplanet_physical import compute_equilibrium_temperature

        result = compute_equilibrium_temperature(
            T_star_K=float(inp["T_star_K"]),
            R_star_solar=float(inp["R_star_solar"]),
            a_au=float(inp["a_au"]),
            albedo=float(inp.get("albedo", 0.3)),
            redistribution_factor=float(inp.get("redistribution_factor", 1.0)),
        )
        data_source = str(inp.get("data_source", "archive_cache"))
        result["success"] = True
        result["data_origin"] = "user_uploaded" if data_source == "user_supplied" else "cached_real"
        result["citations"] = [_citation("Seager & Mallén-Ornelas", 2003, "2003ApJ...585.1038S")]
        return result
    except (KeyError, ValueError) as exc:
        return _failed(str(exc), "invalid_argument")


async def _exec_compute_transit_depth(inp: dict) -> dict:
    try:
        from app.services.exoplanet_physical import compute_transit_depth

        result = compute_transit_depth(
            R_p_earth=float(inp["R_p_earth"]),
            R_star_solar=float(inp["R_star_solar"]),
        )
        data_source = str(inp.get("data_source", "archive_cache"))
        result["success"] = True
        result["data_origin"] = "user_uploaded" if data_source == "user_supplied" else "cached_real"
        result["citations"] = [_citation("Seager & Mallén-Ornelas", 2003, "2003ApJ...585.1038S")]
        return result
    except (KeyError, ValueError) as exc:
        return _failed(str(exc), "invalid_argument")


async def _exec_compute_planet_density(inp: dict) -> dict:
    try:
        from app.services.exoplanet_physical import compute_planet_density

        result = compute_planet_density(
            M_earth=float(inp["M_earth"]),
            R_earth=float(inp["R_earth"]),
        )
        data_source = str(inp.get("data_source", "archive_cache"))
        result["success"] = True
        result["data_origin"] = "user_uploaded" if data_source == "user_supplied" else "cached_real"
        result["citations"] = [_citation("Seager & Mallén-Ornelas", 2003, "2003ApJ...585.1038S")]
        return result
    except (KeyError, ValueError) as exc:
        return _failed(str(exc), "invalid_argument")


async def _exec_query_tess_target_list(inp: dict) -> dict:
    min_mag = float(inp.get("min_mag", 6.0))
    max_mag = float(inp.get("max_mag", 10.0))
    max_rows = int(inp.get("max_rows", 100))
    try:
        from astroquery.mast import Catalogs

        def _query():
            return Catalogs.query_criteria(
                catalog="TIC",
                Tmag=[min_mag, max_mag],
            )

        table = await asyncio.wait_for(asyncio.to_thread(_query), timeout=60.0)
        if table is None or len(table) == 0:
            return {
                "success": True, "__tool_status__": "EMPTY", "__do_not_claim__": True,
                "__message_to_model__": "No TIC targets in that magnitude range.",
                "results": [], "n_results": 0,
            }
        rows = []
        for row in table[:max_rows]:
            r = {}
            for key in ("ID", "Tmag", "ra", "dec", "Teff", "rad", "mass"):
                if key in row.colnames:
                    try:
                        r[key.lower()] = float(row[key])
                    except (ValueError, TypeError):
                        r[key.lower()] = str(row[key])
            rows.append(r)
        return {
            "success": True,
            "data_origin": "real_archive",
            "results": rows,
            "n_results": len(rows),
            "n_total": len(table),
            "min_mag": min_mag,
            "max_mag": max_mag,
            "source_urls": ["https://archive.stsci.edu/missions-and-data/tess/data-products"],
            "reference": "Stassun+ 2019 AJ 158, 138 (TIC v8)",
            "bibcode": "2019AJ....158..138S",
        }
    except Exception as exc:
        return _failed(str(exc)[:200], type(exc).__name__)


# ── dispatch ────────────────────────────────────────────────────────


_DISPATCH = {
    "query_exoplanet_archive": _exec_query_exoplanet_archive,
    "query_confirmed_planets": _exec_query_confirmed_planets,
    "fetch_tess_lightcurve": _exec_fetch_tess_lightcurve,
    "fit_transit": _exec_fit_transit,
    "compute_equilibrium_temperature": _exec_compute_equilibrium_temperature,
    "compute_transit_depth": _exec_compute_transit_depth,
    "compute_planet_density": _exec_compute_planet_density,
    "query_tess_target_list": _exec_query_tess_target_list,
}


async def dispatch_exoplanet(tool_name: str, tool_input: dict) -> dict:
    handler = _DISPATCH.get(tool_name)
    if handler is None:
        return _failed(f"unknown exoplanet tool: {tool_name}", "unknown_tool")
    return await handler(tool_input)
