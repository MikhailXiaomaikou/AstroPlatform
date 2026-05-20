"""Solar-system module ai_tool implementations (M0 Commit 4, 2026-05-18).

12 个 LLM-callable tools 覆盖 asteroid + comet 主流工作流:
    数据查询 (走 connector / HTTP API):
        - query_mpc_orbit
        - fetch_horizons_ephemeris
        - query_sbdb_orbit
        - query_sbdb_close_approaches
        - query_sentry_risk
        - query_damit_shape_model
    公式计算 (纯算, data_origin = cached_real):
        - compute_hg_magnitude
        - compute_afrho
        - fit_neatm_diameter_albedo
        - compute_neo_collision_probability
    分类 (纯算):
        - classify_asteroid_busdemeo
        - classify_asteroid_sdss_colors

设计原则 (镜像 cosmology ai_tool 错误路径 contract):
- 失败时返 {"success": False, "__tool_status__": "FAILED",
    "__do_not_claim__": True, "error": ..., "error_class": ...,
    "__message_to_model__": ...}
- 成功时返结构化 dict + provenance dataset

References cited inline by tool; consolidated list in solar_system/prompt.md。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import httpx

from app.services.provenance_v2.registry_loader import dataset_from_registry

logger = logging.getLogger(__name__)


# ── 公共错误辅助 ──────────────────────────────────────────────────


def _failed(
    error: str, error_class: str, do_not_claim_msg: str | None = None,
) -> dict[str, Any]:
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
    """返回该 service 的 provenance dataset(从 fallback_registry)。"""
    return dataset_from_registry(
        service_key, source_authority="datacenter_ivoa_compliant",
        archive_version=archive_version or f"{service_key}-2026", supplements={},
    )


def _citation(
    label: str,
    year: str | int,
    bibcode: str | None = None,
    *,
    authors: list[str] | None = None,
) -> dict[str, Any]:
    """Small structured citation object consumed by claim_validator."""
    result: dict[str, Any] = {
        "label": label,
        "year": str(year),
        "authors": authors or [label.split()[0]],
    }
    if bibcode:
        result["bibcode"] = bibcode
        result["article"] = bibcode
    return result


def _attach_citations(result: dict[str, Any], citations: list[dict[str, Any]]) -> dict[str, Any]:
    result["citations"] = citations
    if citations:
        first = citations[0]
        if first.get("bibcode"):
            result["bibcode"] = first["bibcode"]
            result["article"] = first["bibcode"]
    return result


def _normalize_horizons_target(target: str) -> str:
    """Normalize common asteroid name forms before sending them to Horizons.

    Horizons accepts either a small-body number ("99942") or a resolvable
    name ("Apophis"), but mixed forms like "99942 Apophis" and
    "(99942) Apophis" can fail under astroquery's default resolver. Users and
    LLMs naturally produce those mixed forms, so reduce them to the number.
    """
    text = str(target or "").strip()
    m = re.match(r"^\(?\s*(\d{1,7})\s*\)?(?:\s+.+)?$", text)
    if m:
        return m.group(1)
    return text


def _normalize_horizons_location(location: str) -> str:
    """Normalize common observer aliases to Horizons location codes."""
    text = str(location or "").strip()
    key = text.lower().replace("_", "").replace("-", "").replace(" ", "")
    if key in {"geocenter", "geocentre", "geocentric", "earthcenter", "earthcentre"}:
        return "500"
    if key in {"heliocenter", "heliocentre", "heliocentric", "sun"}:
        return "500@10"
    return text or "500@10"


def _parse_iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _step_is_daily_or_finer(step: str) -> bool:
    text = str(step or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([dhm])", text)
    if not m:
        return False
    value = float(m.group(1))
    unit = m.group(2)
    if unit in {"h", "m"}:
        return True
    return unit == "d" and value <= 1.0


# ── 工具 schemas ──────────────────────────────────────────────────


SOLAR_SYSTEM_TOOL_SCHEMAS: list[dict] = [
    # ─ 数据查询 (6) ─
    {
        "name": "query_mpc_orbit",
        "description": (
            "Query the IAU Minor Planet Center (MPC) for an asteroid/comet's "
            "official designation and osculating orbital elements (a, e, i, ω, "
            "Ω, M, H, G, perihelion/aphelion). 接受常见命名: '(3200) Phaethon', "
            "'Phaethon', '1983 TB', 'C/2014 UN271' 等. 失败时返 FAILED banner. "
            "Reference: IAU Minor Planet Center."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "designation": {
                    "type": "string",
                    "description": "Object name or designation, e.g. 'Phaethon' or '1983 TB'.",
                },
            },
            "required": ["designation"],
        },
    },
    {
        "name": "fetch_horizons_ephemeris",
        "description": (
            "Fetch JPL Horizons ephemeris time series for a solar-system body. "
            "返回逐时间步的 RA/Dec/V/r_au/Δ_au/phase_angle/light_time. 时间标尺: "
            "Horizons 默认 TDB,但输出表面层是 UTC. **不要**用 run_python 直接 "
            "call astroquery.jplhorizons—会绕开 retry/provenance/cache. "
            "Reference: Giorgini+ 1996 BAAS 28, 1158 (bibcode 1996DPS....28.2504G)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Designation or name."},
                "start": {"type": "string", "description": "Start date 'YYYY-MM-DD' (UTC)."},
                "stop": {"type": "string", "description": "Stop date 'YYYY-MM-DD' (UTC)."},
                "step": {
                    "type": "string", "default": "1d",
                    "description": "Step size, e.g. '1d', '6h', '1h'. 大范围+短 step 会被 Horizons 限速.",
                },
                "location": {
                    "type": "string", "default": "500@10",
                    "description": "Horizons observer code. 'geocenter'=500, heliocenter=500@10, MPC observatory codes also accepted.",
                },
            },
            "required": ["target", "start", "stop"],
        },
    },
    {
        "name": "query_sbdb_orbit",
        "description": (
            "Query JPL Small-Body Database (SBDB) for orbital elements + physical "
            "properties of an asteroid/comet. 比 MPC 多: derived (q, Q, P), "
            "uncertainties, close-approach summary, family. Reference: JPL SSD."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "designation": {
                    "type": "string",
                    "description": "Object designation, e.g. '99942', 'Apophis', '2024 YR4'.",
                },
            },
            "required": ["designation"],
        },
    },
    {
        "name": "query_sbdb_close_approaches",
        "description": (
            "Query JPL Close-Approach Data (CAD) API for Earth/planet close approaches "
            "of a small body within a date range. 返回每次 encounter 的 distance "
            "(au, lunar distance), v_rel (km/s), 时间. 用于 NEO 风险评估的初筛."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "designation": {"type": "string", "description": "Optional — limit to one body."},
                "date_min": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                "date_max": {"type": "string", "description": "ISO date YYYY-MM-DD."},
                "dist_max_au": {
                    "type": "number", "default": 0.05,
                    "description": "Maximum approach distance in au (default 0.05 ≈ 19 LD).",
                },
                "body": {
                    "type": "string", "default": "Earth",
                    "description": "Planet/body for which to find approaches (Earth/Mars/Venus/...).",
                },
            },
            "required": ["date_min", "date_max"],
        },
    },
    {
        "name": "query_sentry_risk",
        "description": (
            "Query JPL CNEOS Sentry-II 100-yr impact-risk table for a specific NEO. "
            "返回 cumulative impact probability, Palermo/Torino scale, virtual impactor "
            "数. 这是 publication-grade NEO 风险评估的权威源 (Sentry-II 2021)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "designation": {
                    "type": "string",
                    "description": "Object designation, e.g. '99942' for Apophis or '2024 YR4'.",
                },
            },
            "required": ["designation"],
        },
    },
    {
        "name": "query_damit_shape_model",
        "description": (
            "Query DAMIT (Database of Asteroid Models from Inversion Techniques) "
            "for 3D convex shape models derived from lightcurve inversion. 返回 "
            "spin period, pole solution, model URL. Reference: Ďurech+ 2010 A&A 513, "
            "A46 (bibcode 2010A&A...513A..46D)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "designation": {
                    "type": "string",
                    "description": "Asteroid number or name (e.g. '21' for Lutetia, '433' for Eros).",
                },
            },
            "required": ["designation"],
        },
    },
    # ─ 公式计算 (4) ─
    {
        "name": "compute_hg_magnitude",
        "description": (
            "Compute apparent V magnitude V(α,r,Δ) via Bowell+ 1989 H-G "
            "two-parameter phase function: V = H + 5 log₁₀(r Δ) - 2.5 log₁₀ Φ(α,G). "
            "G typical defaults: S-type ~0.15, C-type ~0.05, V-type ~0.30. "
            "Reference: Bowell+ 1989 'Asteroids II' (bibcode 1989aste.conf..524B)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "H": {"type": "number", "description": "Absolute V magnitude (mag)."},
                "G": {
                    "type": "number", "default": 0.15,
                    "description": "Phase slope (default 0.15 for unknown asteroid).",
                },
                "phase_angles_deg": {
                    "type": "array", "items": {"type": "number"},
                    "description": "Phase angle(s) α in deg, ∈ [0, 180].",
                },
                "r_au": {"type": "array", "items": {"type": "number"}, "description": "Heliocentric distance (au) for each phase angle."},
                "delta_au": {"type": "array", "items": {"type": "number"}, "description": "Geocentric distance (au) for each phase angle."},
                "data_source": {
                    "type": "string", "enum": ["archive_cache", "user_supplied"],
                    "default": "archive_cache",
                    "description": "'archive_cache' if H/G/r/Δ traced to query_mpc_orbit or fetch_horizons_ephemeris output; 'user_supplied' if inline.",
                },
            },
            "required": ["H", "phase_angles_deg", "r_au", "delta_au"],
        },
    },
    {
        "name": "compute_afrho",
        "description": (
            "Compute Afρ (cm) — comet dust production proxy at observed magnitude. "
            "Afρ = 4 Δ² r² (1 au [cm])² / ρ × 10^(0.4 (m_sun - m_obs)). 可选择 "
            "Halley-Marcus phase correction (Schleicher 2010) → Afρ(0°). "
            "Reference: A'Hearn+ 1984 AJ 89, 579 (bibcode 1984AJ.....89..579A)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "m_comet": {"type": "number", "description": "Comet apparent V mag inside aperture."},
                "r_au": {"type": "number", "description": "Heliocentric distance (au)."},
                "delta_au": {"type": "number", "description": "Geocentric distance (au)."},
                "aperture_arcsec": {"type": "number", "description": "Aperture angular radius (arcsec)."},
                "alpha_deg": {
                    "type": "number", "default": 0.0,
                    "description": "Phase angle for Halley-Marcus correction. 0 = no correction.",
                },
                "m_sun": {
                    "type": "number", "default": -26.74,
                    "description": "Sun V mag at 1 au (default -26.74).",
                },
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["m_comet", "r_au", "delta_au", "aperture_arcsec"],
        },
    },
    {
        "name": "fit_neatm_diameter_albedo",
        "description": (
            "NEATM 单波段拟合: 给 H + observed thermal flux F_λ at λ + r,Δ → 估 D (km), "
            "p_V, T_ss. 用 Harris 1998 formula (D = 1329/√p_V × 10^(-H/5)) + NEATM "
            "subsolar temperature. **简化版**, 多波段+自洽 η 拟合走 sbpy.thermal 或 "
            "Mainzer+ 2011 完整管线. References: Harris 1998 Icarus 131, 291 "
            "(1998Icar..131..291H); Mainzer+ 2011 ApJ 743, 156 (2011ApJ...743..156M)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "H": {"type": "number", "description": "Absolute V magnitude."},
                "observed_flux_jy": {"type": "number", "description": "Thermal flux density at λ in Jy."},
                "lambda_um": {"type": "number", "description": "Wavelength in μm (e.g. 12.0 for WISE W3)."},
                "r_au": {"type": "number", "description": "Heliocentric distance (au)."},
                "delta_au": {"type": "number", "description": "Geocentric distance (au)."},
                "G": {"type": "number", "default": 0.15, "description": "Phase slope (default 0.15)."},
                "eta": {
                    "type": "number", "default": 1.4,
                    "description": "Beaming parameter η (1.4 for NEAs, 0.756 for MBAs per Pravec & Harris 2007).",
                },
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["H", "observed_flux_jy", "lambda_um", "r_au", "delta_au"],
        },
    },
    {
        "name": "compute_neo_collision_probability",
        "description": (
            "**Öpik 几何上限**: 100-yr cumulative encounter rate × cross section "
            "for an Earth-crossing NEO. **不是真实 impact probability** — 真实值 "
            "(Sentry-II 给的)通常小 10⁴-10⁶ 倍, 因为有 orbit uncertainty + "
            "Yarkovsky + keyhole. Publication-grade 风险评估必须用 query_sentry_risk "
            "查 Sentry-II 数值. References: Öpik 1951 PRIA 54A, 165; Wetherill 1967 "
            "JGR 72, 2429; Morbidelli+ 2002 Icarus 158, 329 (2002Icar..158..329M)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "moid_au": {"type": "number", "description": "MOID (au), Earth-asteroid."},
                "a_au": {"type": "number", "description": "Semi-major axis (au)."},
                "e": {"type": "number", "description": "Eccentricity, ∈ [0, 1)."},
                "i_deg": {"type": "number", "description": "Inclination (deg)."},
                "target_radius_km": {"type": "number", "default": 6371.0, "description": "Default Earth radius."},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["moid_au", "a_au", "e", "i_deg"],
        },
    },
    # ─ 分类 (2) ─
    {
        "name": "classify_asteroid_busdemeo",
        "description": (
            "Bus-DeMeo nearest-class chi-sq taxonomy (12 main types) from "
            "(visible slope, 1-μm band depth, NIR slope) keystone features. "
            "如果只给 wavelengths+reflectance,先内部提取 features. **简化版**, "
            "不区分 25 个 subclass (Sa/Sk/Sl/Sq/Sr/Sv) — 那些走完整 PCA, 留 M2. "
            "Reference: DeMeo+ 2009 Icarus 202, 160 (2009Icar..202..160D)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "visible_slope": {"type": "number", "description": "0.45-0.74 μm 谱斜率 (per 0.1 μm). 选填."},
                "band1_depth": {"type": "number", "description": "1-μm 吸收带 fractional depth (0-0.5). 选填."},
                "nir_slope": {"type": "number", "default": 0.0, "description": "0.85-2.45 μm 斜率 (per μm). 选填."},
                "wavelengths_um": {"type": "array", "items": {"type": "number"}, "description": "或: full spectrum wavelengths. 给了 wavelengths+reflectance 则内部提 features."},
                "reflectance": {"type": "array", "items": {"type": "number"}, "description": "Normalised reflectance."},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": [],
        },
    },
    {
        "name": "classify_asteroid_sdss_colors",
        "description": (
            "Carvano+ 2010 SDSS 4-color nearest-center taxonomy (C/X/S/V/D/A/Q/L/O). "
            "用于 SDSS MOC catalog 里的 photometric asteroid classification. "
            "Reference: Carvano+ 2010 A&A 510, A43 (2010A&A...510A..43C)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "u_g": {"type": "number", "description": "u-g color (mag)."},
                "g_r": {"type": "number", "description": "g-r color (mag)."},
                "r_i": {"type": "number", "description": "r-i color (mag)."},
                "i_z": {"type": "number", "description": "i-z color (mag)."},
                "data_source": {"type": "string", "enum": ["archive_cache", "user_supplied"], "default": "archive_cache"},
            },
            "required": ["u_g", "g_r", "r_i", "i_z"],
        },
    },
]

SOLAR_SYSTEM_TOOL_NAMES = frozenset(t["name"] for t in SOLAR_SYSTEM_TOOL_SCHEMAS)


# ── 数据查询 _exec_* ──────────────────────────────────────────────


async def _exec_query_mpc_orbit(inp: dict) -> dict:
    designation = str(inp.get("designation") or "").strip()
    if not designation:
        return _failed("missing designation", "missing_argument")
    try:
        from app.connectors.registry import get_connector

        connector = get_connector("mpc")
        objects = await connector.search(designation)
        if not objects:
            return {
                "success": True,
                "__tool_status__": "EMPTY",
                "analysis_status": "EMPTY",
                "data_origin": "real_archive",
                "__do_not_claim__": True,
                "__message_to_model__": (
                    f"MPC returned no orbit for designation {designation!r}. 检查命名 "
                    f"(MPC 接受 '(3200)' 或 'Phaethon' 或 '1983 TB' 形式)."
                ),
                "designation": designation,
                "results": [],
            }
        obj = objects[0]
        return {
            "success": True,
            "data_origin": "real_archive",
            "analysis_status": "COMPLETED",
            "designation": obj.name,
            "orbital_elements": {
                k: obj.extra.get(k)
                for k in (
                    "a", "e", "i", "argument_of_perihelion", "ascending_node",
                    "mean_anomaly", "perihelion_distance", "aphelion_distance",
                    "perihelion_date_jd", "epoch_jd", "absolute_magnitude",
                    "phase_slope", "orbital_period",
                )
                if obj.extra.get(k) is not None
            },
            "_provenance_dataset": obj.extra.get("_provenance_dataset"),
            "source_urls": ["https://minorplanetcenter.net/"],
            "archive_ids": [obj.name],
        }
    except Exception as exc:
        logger.warning("query_mpc_orbit failed: %s", exc)
        return _failed(str(exc), exc.__class__.__name__)


async def _exec_fetch_horizons_ephemeris(inp: dict) -> dict:
    target = _normalize_horizons_target(str(inp.get("target") or "").strip())
    start = str(inp.get("start") or "").strip()
    stop = str(inp.get("stop") or "").strip()
    step = str(inp.get("step") or "1d").strip()
    location = _normalize_horizons_location(str(inp.get("location") or "500@10").strip())
    if not all([target, start, stop]):
        return _failed("missing target/start/stop", "missing_argument")
    start_date = _parse_iso_date(start)
    stop_date = _parse_iso_date(stop)
    if start_date and stop_date and stop_date > start_date:
        span_days = (stop_date - start_date).days
        if span_days > 370 and _step_is_daily_or_finer(step):
            return _failed(
                (
                    f"Horizons request spans {span_days} days with step={step!r}; "
                    "daily-or-finer ephemerides are limited to <=1 year per call."
                ),
                "range_too_large",
                (
                    "Do not split a multi-year daily ephemeris into many automatic "
                    "archive calls. Ask the user to choose a coarser cadence "
                    "(weekly/monthly) or a shorter date window."
                ),
            )
    try:
        from astroquery.jplhorizons import Horizons
        from app.services.provenance_v2.ivoa_dataorigin_resolver import resolve_ivoa_dataorigin

        def _fetch():
            obj = Horizons(
                id=target, location=location,
                epochs={"start": start, "stop": stop, "step": step},
            )
            return obj.ephemerides()

        eph = await asyncio.wait_for(asyncio.to_thread(_fetch), timeout=90.0)
        if eph is None or len(eph) == 0:
            return {
                "success": True,
                "__tool_status__": "EMPTY",
                "data_origin": "real_archive",
                "__do_not_claim__": True,
                "__message_to_model__": f"Horizons returned no ephemeris for {target!r}.",
                "target": target,
                "rows": [],
            }
        provenance = resolve_ivoa_dataorigin(
            eph, service_hint="jpl", archive_version="horizons-2026",
        )
        rows = []
        for row in eph:
            r: dict[str, Any] = {}
            for col in (
                "datetime_str", "datetime_jd", "RA", "DEC", "V", "r", "delta",
                "alpha", "lighttime", "EclLon", "EclLat",
            ):
                if col in row.colnames:
                    try:
                        val = row[col]
                        r[col] = float(val) if isinstance(val, (int, float)) else str(val)
                    except (ValueError, TypeError):
                        try:
                            r[col] = str(row[col])
                        except Exception:
                            pass
            rows.append(r)
        result = {
            "success": True,
            "data_origin": "real_archive",
            "analysis_status": "COMPLETED",
            "target": target,
            "epochs": {"start": start, "stop": stop, "step": step},
            "location": location,
            "n_rows": len(rows),
            "rows": rows[:1000],  # cap at 1000 rows
            "rows_truncated": len(rows) > 1000,
            "_provenance_dataset": provenance,
            "source_urls": ["https://ssd.jpl.nasa.gov/horizons/"],
            "archive_ids": [target],
            "warnings": [
                "Horizons epoch 是 TDB; UTC 在表面层. 引用时间必须显式标注 scale.",
            ] if rows else [],
        }
        return _attach_citations(result, [
            _citation("Giorgini et al.", 1996, "1996DPS....28.2504G", authors=["Giorgini"]),
            _citation("Ginsburg et al.", 2019, "2019AJ....157...98G", authors=["Ginsburg"]),
        ])
    except asyncio.TimeoutError:
        return _failed(
            "Horizons request timed out after 90s",
            "timeout",
            "Horizons 超时,可能服务器忙或时间范围过大。 检查 step + 时间范围.",
        )
    except Exception as exc:
        logger.warning("fetch_horizons_ephemeris failed: %s", exc)
        return _failed(str(exc), exc.__class__.__name__)


async def _exec_query_sbdb_orbit(inp: dict) -> dict:
    designation = str(inp.get("designation") or "").strip()
    if not designation:
        return _failed("missing designation", "missing_argument")
    url = "https://ssd-api.jpl.nasa.gov/sbdb.api"
    params = {"sstr": designation, "full-prec": "true", "phys-par": "true"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return _failed(f"SBDB HTTP {exc.response.status_code}: {exc}", "http_error")
    except Exception as exc:
        return _failed(str(exc), exc.__class__.__name__)

    if "code" in data and data.get("code") != 200:
        return _failed(
            f"SBDB rejected query: {data.get('message', 'unknown')}",
            "sbdb_rejected",
        )
    object_block = data.get("object") or {}
    orbit_block = data.get("orbit") or {}
    phys_pars = data.get("phys_par") or []
    return {
        "success": True,
        "data_origin": "real_archive",
        "analysis_status": "COMPLETED",
        "designation": object_block.get("fullname") or designation,
        "object": object_block,
        "orbit": orbit_block,
        "phys_par": phys_pars,
        "_provenance_dataset": _provenance_for("sbdb", "sbdb-2026"),
        "source_urls": [f"https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={designation}"],
        "archive_ids": [object_block.get("spkid") or object_block.get("des") or designation],
    }


async def _exec_query_sbdb_close_approaches(inp: dict) -> dict:
    designation = inp.get("designation")
    date_min = str(inp.get("date_min") or "").strip()
    date_max = str(inp.get("date_max") or "").strip()
    dist_max = inp.get("dist_max_au", 0.05)
    body = str(inp.get("body") or "Earth")
    if not date_min or not date_max:
        return _failed("missing date_min/date_max", "missing_argument")
    url = "https://ssd-api.jpl.nasa.gov/cad.api"
    params: dict[str, Any] = {
        "date-min": date_min, "date-max": date_max, "dist-max": str(dist_max),
        "body": body, "sort": "date",
    }
    if designation:
        params["des"] = str(designation)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return _failed(f"CAD HTTP {exc.response.status_code}", "http_error")
    except Exception as exc:
        return _failed(str(exc), exc.__class__.__name__)

    fields = data.get("fields") or []
    raw_rows = data.get("data") or []
    rows: list[dict[str, Any]] = []
    for row in raw_rows[:500]:
        rows.append(dict(zip(fields, row)))
    return {
        "success": True,
        "data_origin": "real_archive",
        "analysis_status": "COMPLETED" if rows else "EMPTY",
        "__tool_status__": "EMPTY" if not rows else "OK",
        "n_approaches": len(rows),
        "approaches": rows,
        "_provenance_dataset": _provenance_for("sbdb", "cad-2026"),
        "source_urls": [f"https://ssd-api.jpl.nasa.gov/cad.api?{httpx.QueryParams(params)}"],
        "archive_ids": [str(designation) if designation else f"cad-{date_min}-{date_max}"],
        "request_params": params,
        "__do_not_claim__": not bool(rows),
        "__message_to_model__": (
            "No close approaches in given window." if not rows else
            "Close approach data from JPL CAD API."
        ),
    }


async def _exec_query_sentry_risk(inp: dict) -> dict:
    designation = str(inp.get("designation") or "").strip()
    if not designation:
        return _failed("missing designation", "missing_argument")
    url = "https://ssd-api.jpl.nasa.gov/sentry.api"
    params = {"des": designation}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 404:
                return {
                    "success": True,
                    "__tool_status__": "EMPTY",
                    "data_origin": "real_archive",
                    "__do_not_claim__": True,
                    "__message_to_model__": (
                        f"Sentry-II 未列出 {designation!r} — 该对象目前没有 virtual "
                        "impactor (cumulative impact probability < 1e-10 或非 NEO)."
                    ),
                    "designation": designation,
                    "result": "not_on_sentry_list",
                    "_provenance_dataset": _provenance_for("sentry"),
                    "source_urls": ["https://cneos.jpl.nasa.gov/sentry/"],
                }
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        return _failed(f"Sentry HTTP {exc.response.status_code}", "http_error")
    except Exception as exc:
        return _failed(str(exc), exc.__class__.__name__)
    summary = data.get("summary") or {}
    return {
        "success": True,
        "data_origin": "real_archive",
        "analysis_status": "COMPLETED",
        "designation": designation,
        "summary": summary,
        "impacts": data.get("data") or [],
        "_provenance_dataset": _provenance_for("sentry"),
        "source_urls": [f"https://cneos.jpl.nasa.gov/sentry/details.html#?des={designation}"],
        "archive_ids": [designation],
    }


async def _exec_query_damit_shape_model(inp: dict) -> dict:
    designation = str(inp.get("designation") or "").strip()
    if not designation:
        return _failed("missing designation", "missing_argument")
    # DAMIT 公开 HTTP API: https://astro.troja.mff.cuni.cz/projects/damit/asteroids/list/asteroid_name/<name>
    url = "https://astro.troja.mff.cuni.cz/projects/damit/asteroids/exportAll/json"
    params = {"name": designation}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, params=params)
            if resp.status_code in (404, 204):
                return {
                    "success": True,
                    "__tool_status__": "EMPTY",
                    "data_origin": "real_archive",
                    "__do_not_claim__": True,
                    "__message_to_model__": (
                        f"DAMIT 没有 {designation!r} 的 shape model (lightcurve "
                        "inversion 尚未做过, 或命名匹配失败)."
                    ),
                    "designation": designation,
                    "_provenance_dataset": _provenance_for("damit"),
                }
            resp.raise_for_status()
            try:
                data = resp.json()
            except Exception:
                data = {"raw_text": resp.text[:2000]}
    except httpx.HTTPStatusError as exc:
        return _failed(f"DAMIT HTTP {exc.response.status_code}", "http_error")
    except Exception as exc:
        return _failed(str(exc), exc.__class__.__name__)
    return {
        "success": True,
        "data_origin": "real_archive",
        "analysis_status": "COMPLETED",
        "designation": designation,
        "models": data,
        "_provenance_dataset": _provenance_for("damit"),
        "source_urls": [
            f"https://astro.troja.mff.cuni.cz/projects/damit/asteroids/view/{designation}",
        ],
        "archive_ids": [designation],
    }


# ── 公式计算 _exec_* ──────────────────────────────────────────────


def _data_origin_from_source(data_source: str | None) -> str:
    return "user_uploaded" if data_source == "user_supplied" else "cached_real"


async def _exec_compute_hg_magnitude(inp: dict) -> dict:
    try:
        from app.services.solar_system_phot import hg_apparent_magnitude_curve

        H = float(inp["H"])
        G = float(inp.get("G", 0.15))
        alphas = list(inp["phase_angles_deg"])
        rs = list(inp["r_au"])
        deltas = list(inp["delta_au"])
        V_curve = hg_apparent_magnitude_curve(H, G, alphas, rs, deltas)
        data_source = inp.get("data_source", "archive_cache")
        result = {
            "success": True,
            "data_origin": _data_origin_from_source(data_source),
            "analysis_status": "COMPLETED",
            "H": H, "G": G,
            "phase_angles_deg": alphas,
            "r_au": rs, "delta_au": deltas,
            "V_apparent": V_curve,
            "reference": "Bowell+ 1989 'Asteroids II' (1989aste.conf..524B)",
        }
        return _attach_citations(result, [
            _citation("Bowell et al.", 1989, "1989aste.conf..524B", authors=["Bowell"]),
        ])
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(str(exc), exc.__class__.__name__)


async def _exec_compute_afrho(inp: dict) -> dict:
    try:
        from app.services.solar_system_phot import afrho_cm, afrho_phase_corrected

        m_comet = float(inp["m_comet"])
        r_au = float(inp["r_au"])
        delta_au = float(inp["delta_au"])
        aperture_arcsec = float(inp["aperture_arcsec"])
        alpha = float(inp.get("alpha_deg", 0.0))
        m_sun = float(inp.get("m_sun", -26.74))
        data_source = inp.get("data_source", "archive_cache")

        af = afrho_cm(m_comet, r_au, delta_au, aperture_arcsec, m_sun=m_sun)
        af_0 = afrho_phase_corrected(af, alpha) if alpha > 0 else af
        result = {
            "success": True,
            "data_origin": _data_origin_from_source(data_source),
            "analysis_status": "COMPLETED",
            "Afrho_cm": af,
            "Afrho_0deg_cm": af_0,
            "phase_correction_applied": alpha > 0,
            "phase_angle_deg": alpha,
            "input": {
                "m_comet": m_comet, "r_au": r_au, "delta_au": delta_au,
                "aperture_arcsec": aperture_arcsec, "m_sun": m_sun,
            },
            "reference": "A'Hearn+ 1984 AJ 89, 579 (1984AJ.....89..579A)",
        }
        return _attach_citations(result, [
            _citation("A'Hearn et al.", 1984, "1984AJ.....89..579A", authors=["A'Hearn", "Hearn"]),
        ])
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(str(exc), exc.__class__.__name__)


async def _exec_fit_neatm_diameter_albedo(inp: dict) -> dict:
    try:
        from app.services.solar_system_thermo import fit_diameter_albedo_neatm

        H = float(inp["H"])
        flux_jy = float(inp["observed_flux_jy"])
        lambda_um = float(inp["lambda_um"])
        r_au = float(inp["r_au"])
        delta_au = float(inp["delta_au"])
        G = float(inp.get("G", 0.15))
        eta = float(inp.get("eta", 1.4))
        data_source = inp.get("data_source", "archive_cache")

        result = fit_diameter_albedo_neatm(
            H=H, observed_flux_jy=flux_jy, lambda_um=lambda_um,
            r_au=r_au, delta_au=delta_au, G=G, eta=eta,
        )
        result["data_origin"] = _data_origin_from_source(data_source)
        result["analysis_status"] = "COMPLETED"
        result["success"] = True
        result["reference"] = (
            "Harris 1998 Icarus 131, 291 (1998Icar..131..291H); "
            "Mainzer+ 2011 ApJ 743, 156 (2011ApJ...743..156M)"
        )
        return _attach_citations(result, [
            _citation("Harris", 1998, "1998Icar..131..291H", authors=["Harris"]),
            _citation("Mainzer et al.", 2011, "2011ApJ...743..156M", authors=["Mainzer"]),
        ])
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(str(exc), exc.__class__.__name__)


async def _exec_compute_neo_collision_probability(inp: dict) -> dict:
    try:
        from app.services.solar_system_dynamics import (
            estimate_100yr_collision_probability,
        )

        moid = float(inp["moid_au"])
        a = float(inp["a_au"])
        e = float(inp["e"])
        i = float(inp["i_deg"])
        target_r = float(inp.get("target_radius_km", 6371.0))
        data_source = inp.get("data_source", "archive_cache")

        result = estimate_100yr_collision_probability(
            moid_au=moid, a_target_au=a, e_target=e, i_target_deg=i,
            target_radius_km=target_r,
        )
        result["data_origin"] = _data_origin_from_source(data_source)
        result["analysis_status"] = "COMPLETED"
        result["success"] = True
        # warning 已经在 result 里, surface 到 __message_to_model__
        result["__message_to_model__"] = result["warning"]
        return _attach_citations(result, [
            _citation("Morbidelli et al.", 2002, "2002Icar..158..329M", authors=["Morbidelli"]),
            _citation("Wetherill", 1967, None, authors=["Wetherill"]),
        ])
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(str(exc), exc.__class__.__name__)


# ── 分类 _exec_* ──────────────────────────────────────────────────


async def _exec_classify_asteroid_busdemeo(inp: dict) -> dict:
    try:
        from app.services.solar_system_taxonomy import (
            classify_bus_demeo_from_features, spectrum_to_features,
        )

        wavelengths = inp.get("wavelengths_um")
        reflectance = inp.get("reflectance")
        if wavelengths and reflectance:
            features = spectrum_to_features(wavelengths, reflectance)
            vis = features["visible_slope"]
            band1 = features["band1_depth"]
            nir = features["nir_slope"]
        else:
            vis = float(inp.get("visible_slope", 0.0))
            band1 = float(inp.get("band1_depth", 0.0))
            nir = float(inp.get("nir_slope", 0.0))
            features = {
                "visible_slope": vis, "band1_depth": band1, "nir_slope": nir,
            }

        clf = classify_bus_demeo_from_features(vis, band1, nir)
        data_source = inp.get("data_source", "archive_cache")
        clf["features"] = features
        clf["data_origin"] = _data_origin_from_source(data_source)
        clf["analysis_status"] = "COMPLETED"
        clf["success"] = True
        return _attach_citations(clf, [
            _citation("DeMeo et al.", 2009, "2009Icar..202..160D", authors=["DeMeo"]),
        ])
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(str(exc), exc.__class__.__name__)


async def _exec_classify_asteroid_sdss_colors(inp: dict) -> dict:
    try:
        from app.services.solar_system_taxonomy import classify_carvano_sdss_colors

        ug = float(inp["u_g"])
        gr = float(inp["g_r"])
        ri = float(inp["r_i"])
        iz = float(inp["i_z"])
        data_source = inp.get("data_source", "archive_cache")

        clf = classify_carvano_sdss_colors(ug, gr, ri, iz)
        clf["data_origin"] = _data_origin_from_source(data_source)
        clf["analysis_status"] = "COMPLETED"
        clf["success"] = True
        return _attach_citations(clf, [
            _citation("Carvano et al.", 2010, "2010A&A...510A..43C", authors=["Carvano"]),
        ])
    except (KeyError, TypeError, ValueError) as exc:
        return _failed(str(exc), exc.__class__.__name__)


# ── Dispatch ──────────────────────────────────────────────────────


_DISPATCH = {
    "query_mpc_orbit": _exec_query_mpc_orbit,
    "fetch_horizons_ephemeris": _exec_fetch_horizons_ephemeris,
    "query_sbdb_orbit": _exec_query_sbdb_orbit,
    "query_sbdb_close_approaches": _exec_query_sbdb_close_approaches,
    "query_sentry_risk": _exec_query_sentry_risk,
    "query_damit_shape_model": _exec_query_damit_shape_model,
    "compute_hg_magnitude": _exec_compute_hg_magnitude,
    "compute_afrho": _exec_compute_afrho,
    "fit_neatm_diameter_albedo": _exec_fit_neatm_diameter_albedo,
    "compute_neo_collision_probability": _exec_compute_neo_collision_probability,
    "classify_asteroid_busdemeo": _exec_classify_asteroid_busdemeo,
    "classify_asteroid_sdss_colors": _exec_classify_asteroid_sdss_colors,
}


async def dispatch_solar_system(tool_name: str, tool_input: dict) -> dict:
    """ai_tools.py 的统一入口: 把 12 个 solar_system 工具的 dispatch 集中到此处。"""
    handler = _DISPATCH.get(tool_name)
    if handler is None:
        return _failed(
            f"unknown solar_system tool {tool_name!r}", "unknown_tool",
        )
    return await handler(tool_input)
