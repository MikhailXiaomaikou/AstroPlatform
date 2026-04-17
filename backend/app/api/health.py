"""Enhanced health-check endpoints for the Astro Research Platform."""

import logging
import os
import time

from fastapi import APIRouter
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


async def _probe_url(url: str, timeout: float = 2.0) -> tuple[str, int]:
    """Probe an external URL. Returns (status, response_time_ms)."""
    import httpx
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code < 400:
                return "ok", ms
            return f"http_{resp.status_code}", ms
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        return f"error: {exc}", ms


@router.get("/health/detailed")
async def health_detailed():
    """Return granular health status for database, Redis, object storage, and external services."""
    checks: dict[str, dict] = {}
    overall = "ok"

    # --- Database ---
    t0 = time.monotonic()
    try:
        from app.models.database import async_session
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok", "response_time_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        checks["database"] = {"status": f"error: {exc}", "response_time_ms": int((time.monotonic() - t0) * 1000)}
        overall = "degraded"

    # --- Redis ---
    t0 = time.monotonic()
    try:
        import redis.asyncio as aioredis
        from app.config import settings
        kwargs = dict(settings.redis_tls_kwargs())
        r = aioredis.from_url(settings.redis_url, **kwargs)
        try:
            await r.ping()
            checks["redis"] = {"status": "ok", "response_time_ms": int((time.monotonic() - t0) * 1000)}
        finally:
            await r.aclose()
    except Exception as exc:
        checks["redis"] = {"status": f"error: {exc}", "response_time_ms": int((time.monotonic() - t0) * 1000)}
        overall = "degraded"

    # --- MinIO / Object Storage ---
    t0 = time.monotonic()
    try:
        from minio import Minio
        endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)
        client.list_buckets()
        checks["storage"] = {"status": "ok", "response_time_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        checks["storage"] = {"status": f"error: {exc}", "response_time_ms": int((time.monotonic() - t0) * 1000)}
        overall = "degraded"

    # --- External Astronomy Services (degraded does not affect overall if local is ok) ---
    external_probes = {
        "simbad": "https://simbad.u-strasbg.fr/simbad/sim-tap/sync?QUERY=SELECT+1&FORMAT=csv",
        "gaia_tap": "https://gea.esac.esa.int/tap-server/tap/availability",
        "vizier": "https://vizier.u-strasbg.fr/viz-bin/nph-sesame/-oI/A?M31",
    }
    for name, url in external_probes.items():
        status, ms = await _probe_url(url, timeout=2.0)
        checks[name] = {"status": status, "response_time_ms": ms}
        if status != "ok" and overall == "ok":
            overall = "ok"  # external service down doesn't degrade local health

    return {"status": overall, "checks": checks}
