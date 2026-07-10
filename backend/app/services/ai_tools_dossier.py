"""Dossier / follow-up / cross-wavelength AI tools — extracted from
ai_tools/__init__.py (H1 split, 2026-05-26).

The 3 dossier-family tools (get_object_dossier, get_followup_recommendation,
analyze_cross_wavelength). Implementations are thin wrappers over
dossier_generator / followup_recommender / cross_wavelength / name_resolver;
no shared ai_tools helpers are needed.

Dispatch mirrors the other sibling modules: ai_tools/__init__ routes
``tool_name in DOSSIER_TOOL_NAMES`` to ``dispatch_dossier``.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DOSSIER_TOOL_NAMES = frozenset(
    {
        "get_object_dossier",
        "get_followup_recommendation",
        "analyze_cross_wavelength",
    }
)


DOSSIER_TOOL_SCHEMAS = [
    {
        "name": "get_object_dossier",
        "description": (
            "Generate a comprehensive cross-match dossier for a sky position. "
            "Queries SIMBAD, Gaia DR3, SDSS, 2MASS, AllWISE, NED, and TNS concurrently "
            "and returns structured photometry (ugriz, JHKs, W1-W4), a parallel "
            "tree of catalog-reported 1-sigma magnitude errors, astrometry "
            "(parallax, proper motion, distance), redshift, host galaxy info, "
            "cross-identifications, and prior transient classifications."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Object name (optional, e.g. 'SN 2024abc')"},
                "ra": {"type": "number", "description": "Right ascension in degrees"},
                "dec": {"type": "number", "description": "Declination in degrees"},
            },
        },
    },
    {
        "name": "get_followup_recommendation",
        "description": (
            "Generate follow-up observation recommendations for a transient alert. "
            "Analyses the classification, confidence, and available ancillary data to "
            "produce a prioritised list of spectroscopy, photometry, X-ray, UV, or "
            "archival follow-up observations with facility suggestions, exposure times, "
            "and science goals."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "alert_id": {
                    "type": "string",
                    "description": "Alert ID (UUID or source_id) to look up from the database",
                },
                "ra": {
                    "type": "number",
                    "description": "Right ascension in degrees (used if alert_id not found or for ad-hoc queries)",
                },
                "dec": {
                    "type": "number",
                    "description": "Declination in degrees",
                },
                "magnitude": {
                    "type": "number",
                    "description": "Apparent magnitude of the transient",
                },
                "classification": {
                    "type": "string",
                    "description": "Classification type (e.g. SN_Ia, TDE, AGN, CV, Unknown)",
                },
            },
        },
    },
    {
        "name": "analyze_cross_wavelength",
        "description": (
            "Run a non-publication cross-wavelength screening analysis on a sky position. "
            "Checks IR excess (W1-W2), X-ray/optical ratio, astrometric anomalies, "
            "optical color consistency, SED shape vs blackbody, and variability. "
            "Returns ANOMALY/NORMAL/SKIPPED checks plus catalog uncertainty "
            "provenance. If all checks are skipped the result is EMPTY and must "
            "not be described as anomaly-free. Any SED screen that had to assume "
            "10% flux errors is preliminary, non-claimable, and does not expose a "
            "claimable significance or goodness-of-fit statistic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Object name for coordinate resolution (optional if ra/dec provided)",
                },
                "ra": {
                    "type": "number",
                    "description": "Right ascension in degrees",
                },
                "dec": {
                    "type": "number",
                    "description": "Declination in degrees",
                },
            },
        },
    },
]


async def _exec_get_dossier(inp: dict) -> dict:
    """Generate a cross-match dossier for sky coordinates."""
    from app.services.dossier_generator import generate_dossier

    ra = inp.get("ra")
    dec = inp.get("dec")
    name = inp.get("name")

    if ra is None or dec is None:
        if name:
            from app.services.name_resolver import resolve_name
            resolved = await resolve_name(name)
            if not resolved.resolved:
                return {"error": f"Could not resolve '{name}': tried {resolved.aliases_tried}"}
            ra, dec = resolved.ra, resolved.dec
        else:
            return {"error": "ra and dec are required (or provide a name for resolution)"}

    dossier = await generate_dossier(ra=ra, dec=dec, name=name)
    # Remove _raw to keep context window manageable
    dossier.pop("_raw", None)
    return dossier


async def _exec_get_followup(inp: dict) -> dict:
    """Generate follow-up observation recommendations for a transient."""
    from app.services.followup_recommender import generate_followup

    alert_id = inp.get("alert_id")
    ra = inp.get("ra")
    dec = inp.get("dec")
    magnitude = inp.get("magnitude")
    classification = inp.get("classification")

    alert_dict: dict | None = None

    # Try to look up from DB if alert_id is provided
    if alert_id:
        try:
            import uuid as _uuid
            from app.models.database import async_session
            from app.models.schemas import TransientAlert
            from sqlalchemy import select as sa_select

            async with async_session() as session:
                try:
                    uid = _uuid.UUID(alert_id)
                    stmt = sa_select(TransientAlert).where(TransientAlert.id == uid)
                except ValueError:
                    stmt = sa_select(TransientAlert).where(TransientAlert.source_id == alert_id)
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    alert_dict = {
                        "id": str(row.id),
                        "source_id": row.source_id,
                        "ra": row.ra,
                        "dec": row.dec,
                        "magnitude": row.magnitude,
                        "classification": row.classification,
                        "classification_confidence": row.classification_confidence,
                        "redshift": row.redshift,
                        "host_galaxy": row.host_galaxy,
                        "discovery_date": row.discovery_date.isoformat() if row.discovery_date else None,
                        "raw_data": row.raw_data,
                    }
        except Exception as exc:
            logger.debug("Alert DB lookup in tool failed: %s", exc)

    # Build alert dict from params if no DB record found
    if alert_dict is None:
        if ra is None or dec is None:
            return {
                "error": (
                    "Could not find alert in database. "
                    "Provide ra and dec for ad-hoc recommendations."
                )
            }
        alert_dict = {
            "source_id": alert_id or "ad-hoc",
            "ra": ra,
            "dec": dec,
            "magnitude": magnitude,
            "classification": classification,
        }
    else:
        # Apply any overrides
        if ra is not None:
            alert_dict["ra"] = ra
        if dec is not None:
            alert_dict["dec"] = dec
        if magnitude is not None:
            alert_dict["magnitude"] = magnitude
        if classification is not None:
            alert_dict["classification"] = classification

    # Generate dossier for enrichment (best-effort)
    dossier = None
    try:
        from app.services.dossier_generator import generate_dossier
        dossier = await generate_dossier(ra=alert_dict["ra"], dec=alert_dict["dec"])
        dossier.pop("_raw", None)
    except Exception:
        pass

    result = await generate_followup(alert=alert_dict, dossier=dossier)
    return result


async def _exec_cross_wavelength(inp: dict) -> dict:
    """Run cross-wavelength anomaly detection on a sky position."""
    from app.services.cross_wavelength import cross_wavelength_analysis

    ra = inp.get("ra")
    dec = inp.get("dec")
    name = inp.get("name")

    if ra is None or dec is None:
        if name:
            from app.services.name_resolver import resolve_name
            resolved = await resolve_name(name)
            if not resolved.resolved:
                return {"error": f"Could not resolve '{name}': tried {resolved.aliases_tried}"}
            ra, dec = resolved.ra, resolved.dec
        else:
            return {"error": "ra and dec are required (or provide a name for resolution)"}

    result = await cross_wavelength_analysis(ra=ra, dec=dec)
    checks = result.get("results")
    all_skipped = bool(checks) and all(
        isinstance(check, dict) and check.get("status") == "SKIPPED"
        for check in checks
    )
    if result.get("checks_run") == 0 or all_skipped:
        # Boundary-level fail-closed guard: even if the implementation changes,
        # the AI-facing tool must never turn zero evaluated checks into a clean
        # bill of health.
        result.update(
            {
                "__tool_status__": "EMPTY",
                "__do_not_claim__": True,
                "__message_to_model__": (
                    "No cross-wavelength check was evaluated. Do not describe "
                    "the source as normal, consistent, anomaly-free, or discrepant."
                ),
                "publication_ready": False,
                "preliminary_ready": False,
                "claim_scope": "empty_no_executed_checks",
                "data_origin": "real_archive",
                "analysis_status": "empty",
                "anomalies_found": None,
                "briefing": (
                    "No cross-wavelength checks could be evaluated because the "
                    "required measurements were unavailable. No discrepancy or "
                    "normality conclusion is supported."
                ),
            }
        )
    return result


async def dispatch_dossier(tool_name: str, tool_input: dict) -> dict | None:
    """Route the 3 dossier-family tools; None for unknown (caller guards)."""
    if tool_name == "get_object_dossier":
        return await _exec_get_dossier(tool_input)
    elif tool_name == "get_followup_recommendation":
        return await _exec_get_followup(tool_input)
    elif tool_name == "analyze_cross_wavelength":
        return await _exec_cross_wavelength(tool_input)
    return None
