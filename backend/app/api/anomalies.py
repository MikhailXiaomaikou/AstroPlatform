"""API endpoints for anomaly detection feed and management."""

import logging

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/anomalies", tags=["anomalies"])


@router.get("/feed")
async def anomaly_feed(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    min_score: float = Query(0.0, ge=0.0, le=1.0, description="Minimum anomaly score"),
    source: str | None = Query(
        None, description="Filter by source: Query, Alert, Cross-wavelength"
    ),
    sort: str = Query("score", description="Sort by: score, date"),
):
    """Return paginated anomaly feed.

    Placeholder — data will come from scanner integration later.
    """
    return {
        "items": [],
        "page": page,
        "per_page": per_page,
        "total": 0,
    }


@router.get("/stats")
async def anomaly_stats():
    """Return summary statistics for anomalies.

    Placeholder — will be computed from real data once the scanner is integrated.
    """
    return {
        "total": 0,
        "high_confidence": 0,
    }


@router.patch("/{anomaly_id}/dismiss")
async def dismiss_anomaly(anomaly_id: str):
    """Dismiss an anomaly from the feed.

    Placeholder — will persist dismissal once the anomaly table exists.
    """
    return {"id": anomaly_id, "dismissed": True}
