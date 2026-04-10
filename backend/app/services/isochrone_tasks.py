"""Celery tasks for pre-downloading PARSEC isochrone grids."""

import logging

logger = logging.getLogger(__name__)

# Standard grid parameters
LOG_AGE_MIN = 6.0
LOG_AGE_MAX = 10.3
LOG_AGE_STEP = 0.1
METALLICITY_MIN = -2.0
METALLICITY_MAX = 0.5
METALLICITY_STEP = 0.25


def _frange(start: float, stop: float, step: float) -> list[float]:
    """Generate a list of floats from start to stop (inclusive) with given step."""
    result = []
    val = start
    while val <= stop + step * 0.01:  # small epsilon for float comparison
        result.append(round(val, 2))
        val += step
    return result


def prefetch_isochrone_grid(photometric_system: str = "gaia") -> dict:
    """Pre-download standard isochrone grid.

    log(age) 6.0 -> 10.3, step 0.1 (~44 ages)
    [M/H] -2.0 -> +0.5, step 0.25 (~11 metallicities)

    Total: ~484 records. We batch by metallicity (one CMD request per
    metallicity covers all ages at once).

    This function can be called directly or dispatched as a Celery task.

    Returns:
        dict with counts of cached, skipped, and failed entries.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.config import settings
    from app.models.schemas import IsochroneCache
    from app.services.parsec_fetcher import fetch_isochrone_grid

    # Use a sync engine since this runs in a Celery worker (or standalone)
    db_url = settings.database_url
    if db_url.startswith("sqlite+aiosqlite"):
        db_url = db_url.replace("sqlite+aiosqlite", "sqlite", 1)
    elif db_url.startswith("postgresql+asyncpg"):
        db_url = db_url.replace("postgresql+asyncpg", "postgresql+psycopg2", 1)

    sync_engine = create_engine(db_url)

    metallicities = _frange(METALLICITY_MIN, METALLICITY_MAX, METALLICITY_STEP)
    log_ages = _frange(LOG_AGE_MIN, LOG_AGE_MAX, LOG_AGE_STEP)

    stats = {"cached": 0, "skipped": 0, "failed": 0}

    for met in metallicities:
        # Check which ages are already cached for this metallicity
        with Session(sync_engine) as session:
            existing = session.execute(
                select(IsochroneCache.log_age).where(
                    IsochroneCache.metallicity == met,
                    IsochroneCache.photometric_system == photometric_system,
                )
            ).scalars().all()
            existing_ages = {round(a, 1) for a in existing}

        ages_to_fetch = [a for a in log_ages if round(a, 1) not in existing_ages]
        if not ages_to_fetch:
            logger.info(
                "[M/H]=%.2f: all %d ages already cached, skipping.",
                met, len(log_ages),
            )
            stats["skipped"] += len(log_ages)
            continue

        logger.info(
            "[M/H]=%.2f: fetching %d ages from CMD API...",
            met, len(ages_to_fetch),
        )

        try:
            grid = fetch_isochrone_grid(
                log_ages=ages_to_fetch,
                metallicity=met,
                photometric_system=photometric_system,
                timeout=120.0,
            )
        except Exception:
            logger.exception("[M/H]=%.2f: CMD API request failed.", met)
            stats["failed"] += len(ages_to_fetch)
            continue

        # Store each (age, metallicity) isochrone as a cache row
        with Session(sync_engine) as session:
            for age, df in grid.items():
                rounded_age = round(age, 1)
                if rounded_age in existing_ages:
                    stats["skipped"] += 1
                    continue

                cache_entry = IsochroneCache(
                    log_age=rounded_age,
                    metallicity=met,
                    photometric_system=photometric_system,
                    data=df.to_dict(orient="records"),
                )
                session.add(cache_entry)
                stats["cached"] += 1

            session.commit()

        logger.info(
            "[M/H]=%.2f: stored %d isochrones.", met, len(grid),
        )

    logger.info(
        "Prefetch complete: cached=%d, skipped=%d, failed=%d",
        stats["cached"], stats["skipped"], stats["failed"],
    )
    return stats


def _get_celery_app():
    from celery_worker import celery_app
    return celery_app


@_get_celery_app().task(name="isochrone.prefetch_grid")
def prefetch_isochrone_grid_task(photometric_system: str = "gaia"):
    """Celery task wrapper for prefetch_isochrone_grid."""
    return prefetch_isochrone_grid(photometric_system=photometric_system)
