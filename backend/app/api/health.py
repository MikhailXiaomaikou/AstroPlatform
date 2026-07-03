"""Enhanced health-check endpoints for the Astro Research Platform."""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.auth import get_current_user
from app.models.schemas import User

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
async def health_detailed(_user: User = Depends(get_current_user)):
    """Return granular health status for database, Redis, file storage, and external services.

    M31: auth-gated.  Previous behaviour leaked internal URL / credential
    prefixes via error messages to any unauthenticated caller probing
    for hosting details (MinIO endpoint, Redis URL shape, etc.).
    """
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

    # --- Storage (local filesystem — see app/storage.py; MinIO was removed) ---
    t0 = time.monotonic()
    try:
        from pathlib import Path

        from app.config import settings
        root = Path(settings.local_storage_dir)
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".health_probe"
        probe.write_bytes(b"ok")
        probe.unlink()
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


@router.get("/health/deep")
async def health_deep():
    """Deep health probe used by Render auto-rollback (Phase 5 / R18).

    Returns 200 only when the backend can actually serve chat requests:
    DB reachable, Redis reachable (if configured), and at least one AI
    backend key is present.  Unauthenticated so Render's health checker
    can poll it; it does NOT leak credential shapes — just booleans +
    short non-sensitive status strings.
    """
    import os
    result: dict[str, object] = {"ok": True, "components": {}}
    # G1 (PART AD): deploy version transparency. Render injects these git env
    # vars automatically; they read "unknown" locally. Lets a dashboard or a
    # deploy check reconcile which commit is actually serving traffic.
    result["version"] = {
        "commit": os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown",
        "branch": os.getenv("RENDER_GIT_BRANCH") or os.getenv("GIT_BRANCH") or "unknown",
        "service": os.getenv("RENDER_SERVICE_NAME") or "unknown",
    }

    # DB
    try:
        from app.models.database import async_session
        async with async_session() as s:
            await s.execute(text("SELECT 1"))
        result["components"]["db"] = "ok"
    except Exception as exc:
        result["ok"] = False
        result["components"]["db"] = "fail"
        logger.warning("health/deep: db probe failed: %s", exc)

    # Redis is optional — don't fail the gate just because dev has no Redis.
    try:
        import redis.asyncio as aioredis
        from app.config import settings
        if settings.redis_url and "localhost" not in settings.redis_url:
            kwargs = dict(settings.redis_tls_kwargs())
            r = aioredis.from_url(settings.redis_url, **kwargs)
            try:
                await r.ping()
                result["components"]["redis"] = "ok"
            finally:
                await r.aclose()
        else:
            result["components"]["redis"] = "skipped"
    except Exception as exc:
        # Non-fatal for now; Redis being down shouldn't block the deploy
        # gate because the backend degrades gracefully when Redis is absent.
        result["components"]["redis"] = "degraded"
        logger.warning("health/deep: redis probe failed: %s", exc)

    # At least one AI provider key configured.  Scanning env so we
    # don't inadvertently read a user's localStorage-sent key.
    try:
        from app.config import settings

        settings_deepseek_key = (
            str(getattr(settings, "platform_deepseek_api_key", "") or "").strip()
            or str(getattr(settings, "deepseek_api_key", "") or "").strip()
        )
    except Exception:
        settings_deepseek_key = ""
    provider_keys = [
        os.getenv("ANTHROPIC_API_KEY"),
        os.getenv("OPENAI_API_KEY"),
        os.getenv("PLATFORM_DEEPSEEK_API_KEY"),
        os.getenv("DEEPSEEK_API_KEY"),
        settings_deepseek_key,
        os.getenv("LOCAL_MODEL_API_KEY"),
        os.getenv("OPENAI_CLI_ENABLED"),
    ]
    if any(k for k in provider_keys):
        result["components"]["ai_backend"] = "ok"
    else:
        # Keys may be supplied per-request; still pass the gate, but flag.
        result["components"]["ai_backend"] = "per_request_only"

    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result)
    return result
