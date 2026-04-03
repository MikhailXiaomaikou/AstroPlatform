"""Enhanced health-check endpoints for the Astro Research Platform."""

import logging
import os

from fastapi import APIRouter
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health/detailed")
async def health_detailed():
    """Return granular health status for database, Redis, and object storage."""
    checks: dict[str, str] = {}
    overall = "ok"

    # --- Database ---
    try:
        from app.models.database import async_session
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        overall = "degraded"

    # --- Redis ---
    try:
        import redis.asyncio as aioredis
        from app.config import settings
        kwargs = {}
        if settings.redis_ssl:
            kwargs["ssl_cert_reqs"] = "none"
        r = aioredis.from_url(settings.redis_url, **kwargs)
        try:
            await r.ping()
            checks["redis"] = "ok"
        finally:
            await r.aclose()
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        overall = "degraded"

    # --- MinIO / Object Storage ---
    try:
        from minio import Minio
        endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        client.list_buckets()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = f"error: {exc}"
        overall = "degraded"

    return {"status": overall, "checks": checks}
