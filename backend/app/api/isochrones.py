"""API endpoints for cached PARSEC isochrones."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db
from app.models.schemas import IsochroneCache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/isochrones", tags=["isochrones"])


@router.get("/")
async def list_cached_isochrones(
    photometric_system: str = Query("gaia", description="Filter by photometric system"),
    db: AsyncSession = Depends(get_db),
):
    """List available cached isochrones (age, metallicity, photometric_system)."""
    stmt = (
        select(
            IsochroneCache.log_age,
            IsochroneCache.metallicity,
            IsochroneCache.photometric_system,
            IsochroneCache.created_at,
        )
        .where(IsochroneCache.photometric_system == photometric_system)
        .order_by(IsochroneCache.metallicity, IsochroneCache.log_age)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "log_age": row.log_age,
            "metallicity": row.metallicity,
            "photometric_system": row.photometric_system,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/get")
async def get_isochrone(
    log_age: float = Query(..., description="log10(age/yr), e.g. 8.0 for 100 Myr"),
    metallicity: float = Query(..., description="[M/H], e.g. 0.0 for solar"),
    photometric_system: str = Query("gaia", description="Photometric system"),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific isochrone. Returns cached data if available,
    otherwise fetches from CMD API and caches it."""
    # Round for consistent cache lookup
    rounded_age = round(log_age, 1)
    rounded_met = round(metallicity, 2)

    # Try cache first
    stmt = select(IsochroneCache).where(
        IsochroneCache.log_age == rounded_age,
        IsochroneCache.metallicity == rounded_met,
        IsochroneCache.photometric_system == photometric_system,
    )
    result = await db.execute(stmt)
    cached = result.scalar_one_or_none()

    if cached is not None:
        return {
            "source": "cache",
            "log_age": cached.log_age,
            "metallicity": cached.metallicity,
            "photometric_system": cached.photometric_system,
            "num_rows": len(cached.data) if isinstance(cached.data, list) else 0,
            "data": cached.data,
        }

    # Cache miss — fetch from CMD API
    logger.info(
        "Cache miss for log_age=%.1f, [M/H]=%.2f, phot=%s — fetching from CMD.",
        rounded_age, rounded_met, photometric_system,
    )

    try:
        from app.services.parsec_fetcher import fetch_isochrone
        import asyncio
        from functools import partial

        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            partial(
                fetch_isochrone,
                log_age=rounded_age,
                metallicity=rounded_met,
                photometric_system=photometric_system,
                timeout=30.0,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to fetch isochrone from CMD API.")
        raise HTTPException(
            status_code=502,
            detail=f"CMD API error: {exc}",
        )

    data_records = df.to_dict(orient="records")

    # Store in cache
    cache_entry = IsochroneCache(
        log_age=rounded_age,
        metallicity=rounded_met,
        photometric_system=photometric_system,
        data=data_records,
    )
    db.add(cache_entry)
    try:
        await db.commit()
    except Exception:
        # Unique constraint violation — another request cached it first
        await db.rollback()
        logger.debug("Cache write conflict (likely concurrent request), ignoring.")

    return {
        "source": "cmd_api",
        "log_age": rounded_age,
        "metallicity": rounded_met,
        "photometric_system": photometric_system,
        "num_rows": len(data_records),
        "data": data_records,
    }
