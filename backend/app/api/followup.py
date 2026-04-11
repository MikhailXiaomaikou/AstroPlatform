"""Follow-up recommendation API endpoint."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import TransientAlert
from app.services.followup_recommender import generate_followup

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["followup"])


@router.get("/{alert_id}/followup")
async def get_followup_recommendations(
    alert_id: str,
    ra: float | None = Query(None, description="Right ascension in degrees (override)"),
    dec: float | None = Query(None, description="Declination in degrees (override)"),
    magnitude: float | None = Query(None, description="Apparent magnitude (override)"),
    classification: str | None = Query(
        None, description="Classification type (override)"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Generate follow-up observation recommendations for a transient alert.

    Looks up the alert by ID (UUID or source_id) from the database.
    Query parameters can override or supplement the stored alert fields,
    which is useful for quick ad-hoc recommendations without a DB record.
    """
    alert_dict: dict | None = None

    # Try to look up the alert in the database
    try:
        try:
            uid = uuid.UUID(alert_id)
            stmt = select(TransientAlert).where(TransientAlert.id == uid)
        except ValueError:
            stmt = select(TransientAlert).where(TransientAlert.source_id == alert_id)

        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row:
            alert_dict = {
                "id": str(row.id),
                "source": row.source,
                "source_id": row.source_id,
                "ra": row.ra,
                "dec": row.dec,
                "discovery_date": row.discovery_date.isoformat()
                if row.discovery_date
                else None,
                "magnitude": row.magnitude,
                "classification": row.classification,
                "classification_confidence": row.classification_confidence,
                "redshift": row.redshift,
                "host_galaxy": row.host_galaxy,
                "raw_data": row.raw_data,
            }
    except Exception as exc:
        logger.debug("Alert DB lookup failed for %s: %s", alert_id, exc)

    # If no DB record, build a minimal alert from query params
    if alert_dict is None:
        if ra is None or dec is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Alert '{alert_id}' not found in database. "
                    "Provide ra and dec query parameters for ad-hoc recommendations."
                ),
            )
        alert_dict = {
            "id": alert_id,
            "source_id": alert_id,
            "ra": ra,
            "dec": dec,
            "magnitude": magnitude,
            "classification": classification,
        }
    else:
        # Apply overrides from query params
        if ra is not None:
            alert_dict["ra"] = ra
        if dec is not None:
            alert_dict["dec"] = dec
        if magnitude is not None:
            alert_dict["magnitude"] = magnitude
        if classification is not None:
            alert_dict["classification"] = classification

    # Optionally fetch dossier in the background
    dossier = None
    try:
        from app.services.dossier_generator import generate_dossier

        dossier = await generate_dossier(
            ra=alert_dict["ra"],
            dec=alert_dict["dec"],
        )
    except Exception as exc:
        logger.debug("Dossier generation failed: %s", exc)

    # Optionally classify if raw_data is available and no classification yet
    cls_result = None
    if not alert_dict.get("classification") and alert_dict.get("raw_data"):
        try:
            from app.services.transient_classifier import TransientClassifier

            cls_result = TransientClassifier.classify_from_alert(alert_dict)
            if cls_result and cls_result.get("classification"):
                alert_dict["classification"] = cls_result["classification"]
                alert_dict["classification_confidence"] = cls_result.get("confidence")
        except Exception as exc:
            logger.debug("Auto-classification failed: %s", exc)

    result = await generate_followup(
        alert=alert_dict,
        dossier=dossier,
        classification=cls_result,
    )
    return result
