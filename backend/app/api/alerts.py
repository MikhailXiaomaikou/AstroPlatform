"""API endpoints for ZTF transient alert ingestion and queries."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import TransientAlert
from app.services.alert_ingestion import AlertIngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

_ingestion_service = AlertIngestionService()


@router.get("/")
async def list_alerts(
    source: str | None = Query(None, description="Filter by source: ztf, tns"),
    classification: str | None = Query(None, description="Filter by classification type"),
    min_mag: float | None = Query(None, description="Minimum (brightest) magnitude"),
    max_mag: float | None = Query(None, description="Maximum (faintest) magnitude"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: AsyncSession = Depends(get_db),
):
    """List recent transient alerts with optional filters."""
    stmt = select(TransientAlert).order_by(TransientAlert.created_at.desc())

    if source:
        stmt = stmt.where(TransientAlert.source == source)
    if classification:
        stmt = stmt.where(TransientAlert.classification == classification)
    if min_mag is not None:
        stmt = stmt.where(TransientAlert.magnitude >= min_mag)
    if max_mag is not None:
        stmt = stmt.where(TransientAlert.magnitude <= max_mag)

    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return {
        "count": len(rows),
        "alerts": [_alert_to_dict(r) for r in rows],
    }


@router.get("/stats")
async def alert_stats(
    db: AsyncSession = Depends(get_db),
):
    """Summary statistics for cached transient alerts."""
    total_stmt = select(func.count(TransientAlert.id))
    total = (await db.execute(total_stmt)).scalar() or 0

    by_source_stmt = (
        select(TransientAlert.source, func.count(TransientAlert.id))
        .group_by(TransientAlert.source)
    )
    by_source = {row[0]: row[1] for row in (await db.execute(by_source_stmt)).all()}

    by_class_stmt = (
        select(TransientAlert.classification, func.count(TransientAlert.id))
        .where(TransientAlert.classification.isnot(None))
        .group_by(TransientAlert.classification)
        .order_by(func.count(TransientAlert.id).desc())
        .limit(20)
    )
    by_classification = {row[0]: row[1] for row in (await db.execute(by_class_stmt)).all()}

    latest_stmt = (
        select(TransientAlert.created_at)
        .order_by(TransientAlert.created_at.desc())
        .limit(1)
    )
    latest_row = (await db.execute(latest_stmt)).scalar()

    return {
        "total_alerts": total,
        "by_source": by_source,
        "by_classification": by_classification,
        "latest_ingestion": latest_row.isoformat() if latest_row else None,
    }


@router.get("/search/cone")
async def cone_search(
    ra: float = Query(..., description="Right ascension in degrees"),
    dec: float = Query(..., description="Declination in degrees"),
    radius_arcsec: float = Query(60, ge=1, le=3600, description="Search radius in arcseconds"),
    db: AsyncSession = Depends(get_db),
):
    """Cone search: find alerts near given coordinates.

    First checks local DB cache, then supplements with live Lasair query.
    New results from Lasair are ingested into the local cache.
    """
    # Convert radius from arcsec to degrees for the SQL query
    radius_deg = radius_arcsec / 3600.0

    # Query local DB (simple bounding-box filter, sufficient for small radii)
    local_stmt = (
        select(TransientAlert)
        .where(
            TransientAlert.ra.between(ra - radius_deg, ra + radius_deg),
            TransientAlert.dec.between(dec - radius_deg, dec + radius_deg),
        )
        .limit(200)
    )
    result = await db.execute(local_stmt)
    local_rows = result.scalars().all()

    # Also fetch from Lasair (live) and ingest new ones
    live_alerts = await _ingestion_service.fetch_by_region(ra, dec, radius_arcsec)
    ingested = 0
    if live_alerts:
        ingested = await _ingestion_service.ingest(live_alerts, db)

    # Combine: re-query local DB to include any newly ingested alerts
    if ingested > 0:
        result = await db.execute(local_stmt)
        local_rows = result.scalars().all()

    return {
        "count": len(local_rows),
        "live_fetched": len(live_alerts),
        "newly_ingested": ingested,
        "alerts": [_alert_to_dict(r) for r in local_rows],
    }


@router.get("/{alert_id}")
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single alert by ID (UUID) or source_id."""
    # Try UUID first
    try:
        uid = uuid.UUID(alert_id)
        stmt = select(TransientAlert).where(TransientAlert.id == uid)
    except ValueError:
        # Fall back to source_id lookup
        stmt = select(TransientAlert).where(TransientAlert.source_id == alert_id)

    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")

    return _alert_to_dict(row)


def _alert_to_dict(alert: TransientAlert) -> dict:
    """Serialise a TransientAlert row to a JSON-safe dict."""
    return {
        "id": str(alert.id),
        "source": alert.source,
        "source_id": alert.source_id,
        "ra": alert.ra,
        "dec": alert.dec,
        "discovery_date": alert.discovery_date.isoformat() if alert.discovery_date else None,
        "magnitude": alert.magnitude,
        "mag_band": alert.mag_band,
        "classification": alert.classification,
        "classification_confidence": alert.classification_confidence,
        "redshift": alert.redshift,
        "host_galaxy": alert.host_galaxy,
        "raw_data": alert.raw_data,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
        "updated_at": alert.updated_at.isoformat() if alert.updated_at else None,
    }
