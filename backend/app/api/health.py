"""Enhanced health-check endpoints for the Astro Research Platform."""

import asyncio
import logging
import os
from pathlib import Path
import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from app.auth import get_current_user
from app.models.schemas import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_CELERY_PING_TIMEOUT_SECONDS = 2.0
_READINESS_DEADLINE_SECONDS = 4.0
_STORAGE_PROBE_TIMEOUT_SECONDS = 8.0


def _expected_alembic_heads() -> set[str]:
    """Read the migration heads shipped in the running image."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


async def _probe_database() -> bool:
    try:
        from app.models.database import async_session

        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("health/deep: db probe failed: %s", exc)
        return False


async def _probe_schema_head() -> tuple[bool, str]:
    """Verify that the database revision exactly matches every shipped head."""
    try:
        from app.models.database import async_session

        # Alembic walks migration files synchronously. Keep that disk work off
        # the event loop so the public readiness deadline remains enforceable.
        expected = await asyncio.to_thread(_expected_alembic_heads)
        async with async_session() as session:
            rows = await session.execute(text("SELECT version_num FROM alembic_version"))
            current = {str(row[0]) for row in rows}
        if current == expected and current:
            return True, "ok"
        logger.warning(
            "health/deep: schema revision mismatch (current=%s expected=%s)",
            sorted(current),
            sorted(expected),
        )
        return False, "revision_mismatch"
    except Exception as exc:
        # Do not return SQL text or connection details on the unauthenticated
        # health endpoint; the full exception remains in server logs.
        logger.warning("health/deep: schema probe failed: %s", exc)
        return False, "unversioned_or_unreachable"


def _runtime_version() -> dict[str, str]:
    """Return only non-secret deployment identifiers."""
    return {
        "commit": os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "unknown",
        "branch": os.getenv("RENDER_GIT_BRANCH") or os.getenv("GIT_BRANCH") or "unknown",
        "service": os.getenv("RENDER_SERVICE_NAME") or "unknown",
    }


def _consume_probe_result(task: asyncio.Task) -> None:
    """Retrieve late probe results after a deadline without leaking exceptions."""
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


async def _probe_broker() -> bool:
    try:
        import redis.asyncio as aioredis
        from app.config import settings

        kwargs = dict(settings.redis_tls_kwargs())
        client = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            **kwargs,
        )
        try:
            await client.ping()
        finally:
            await client.aclose()
        return True
    except Exception as exc:
        logger.warning("health/deep: broker probe failed: %s", exc)
        return False


async def _probe_celery_workers() -> int:
    """Return the number of workers that answer Celery's control ping."""
    try:
        from celery_worker import celery_app

        def _ping() -> list[dict]:
            return celery_app.control.ping(timeout=_CELERY_PING_TIMEOUT_SECONDS) or []

        replies = await asyncio.wait_for(
            asyncio.to_thread(_ping),
            timeout=_CELERY_PING_TIMEOUT_SECONDS + 1.0,
        )
        return sum(
            1
            for reply in replies
            if isinstance(reply, dict)
            and any(payload == {"ok": "pong"} for payload in reply.values())
        )
    except Exception as exc:
        logger.warning("health/deep: Celery worker probe failed: %s", exc)
        return 0


def _round_trip_storage() -> dict:
    """Run the storage probe and remove its empty local namespace."""
    from app.config import settings
    from app.storage import storage_healthcheck

    result = storage_healthcheck()
    if settings.storage_backend == "local":
        try:
            (Path(settings.local_storage_dir) / ".health").rmdir()
        except OSError:
            # Non-empty means another concurrent probe still owns an object.
            pass
    return result


def _probe_storage() -> tuple[bool, str]:
    """Round-trip the configured object store and verify local mount placement."""
    from app.config import settings

    try:
        expected_mount_raw = os.getenv("PERSISTENT_STORAGE_MOUNT", "").strip()
        if expected_mount_raw:
            expected_mount = Path(expected_mount_raw).resolve()
            if not expected_mount.is_mount():
                return False, "persistent_mount_missing"
            runtime_probe = expected_mount / f".runtime_health_{os.getpid()}"
            try:
                payload = os.urandom(32)
                with runtime_probe.open("wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                if runtime_probe.read_bytes() != payload:
                    return False, "persistent_mount_read_mismatch"
            finally:
                runtime_probe.unlink(missing_ok=True)

        if settings.storage_backend == "local":
            root = Path(settings.local_storage_dir)
            root.mkdir(parents=True, exist_ok=True)
            if expected_mount_raw:
                root.resolve().relative_to(expected_mount)
            elif os.getenv("ENV", "dev").lower() == "production":
                return False, "persistent_mount_not_configured"

        probe = _round_trip_storage()
        if not probe.get("ok"):
            return False, "round_trip_failed"
        return True, "ok"
    except Exception as exc:
        logger.warning("health/deep: storage probe failed: %s", exc)
        return False, "unavailable"


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

    # --- Durable research-object storage ---
    t0 = time.monotonic()
    try:
        storage_result = await asyncio.wait_for(
            asyncio.to_thread(_round_trip_storage),
            timeout=_STORAGE_PROBE_TIMEOUT_SECONDS,
        )
        checks["storage"] = {
            "status": "ok",
            "backend": storage_result.get("backend"),
            "response_time_ms": int((time.monotonic() - t0) * 1000),
        }
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


@router.get("/health/ready")
async def health_ready():
    """Fast, fail-closed readiness probe for high-frequency platform polling.

    Database reachability and the exact Alembic head are checked concurrently.
    Expensive storage round trips and Redis/Celery control calls intentionally
    remain exclusive to ``/health/deep``.
    """
    tasks = {
        "db": asyncio.create_task(_probe_database()),
        "schema": asyncio.create_task(_probe_schema_head()),
    }
    try:
        done, pending = await asyncio.wait(
            set(tasks.values()),
            timeout=_READINESS_DEADLINE_SECONDS,
        )
    except asyncio.CancelledError:
        # Client disconnects and server shutdown cancel the request coroutine,
        # not the child tasks created above. Explicitly cancel and consume both
        # probes so repeated disconnects cannot accumulate DB work.
        for task in tasks.values():
            if not task.done():
                task.add_done_callback(_consume_probe_result)
                task.cancel()
        raise

    for task in pending:
        task.add_done_callback(_consume_probe_result)
        task.cancel()

    components = {"db": "fail", "schema": "fail"}
    db_ok = False
    schema_ok = False

    db_task = tasks["db"]
    if db_task in pending:
        components["db"] = "timeout"
    elif db_task in done:
        try:
            db_ok = db_task.result() is True
        except asyncio.CancelledError:
            logger.warning("health/ready: db probe was cancelled")
        except Exception as exc:
            logger.warning("health/ready: db probe failed: %s", exc)
        components["db"] = "ok" if db_ok else "fail"

    schema_task = tasks["schema"]
    if schema_task in pending:
        components["schema"] = "timeout"
    elif schema_task in done:
        try:
            schema_result = schema_task.result()
            schema_ok = (
                isinstance(schema_result, tuple)
                and len(schema_result) == 2
                and schema_result[0] is True
            )
            if schema_ok:
                components["schema"] = "ok"
        except asyncio.CancelledError:
            logger.warning("health/ready: schema probe was cancelled")
        except Exception as exc:
            logger.warning("health/ready: schema probe failed: %s", exc)

    ready = db_ok and schema_ok and not pending
    result = {
        "status": "ready" if ready else "not_ready",
        "components": components,
        "version": _runtime_version(),
    }
    if not ready:
        raise HTTPException(status_code=503, detail=result)
    return result


@router.get("/health/deep")
async def health_deep():
    """Full post-deploy infrastructure probe used by operators and Compose.

    Operational acceptance requires the DB at the image's Alembic head,
    durable storage mounted and writable, and the configured Celery broker to
    have at least one live worker. Render's high-frequency five-second traffic
    gate uses ``/health/ready`` instead. This endpoint is unauthenticated, so it
    returns only short status labels; detailed failures stay in server logs.
    """
    from app.config import settings

    result: dict[str, object] = {"ok": True, "components": {}}
    # G1 (PART AD): deploy version transparency. Render injects these git env
    # vars automatically; they read "unknown" locally. Lets a dashboard or a
    # deploy check reconcile which commit is actually serving traffic.
    result["version"] = _runtime_version()

    db_ok = await _probe_database()
    result["components"]["db"] = "ok" if db_ok else "fail"
    if not db_ok:
        result["ok"] = False

    schema_ok, schema_status = await _probe_schema_head()
    if not schema_ok and os.getenv("ENV", "dev").lower() != "production":
        # Local SQLite is intentionally create_all-managed. Still expose the
        # mismatch, but reserve deploy blocking for Alembic-managed production.
        schema_status = "unmanaged_dev"
    elif not schema_ok:
        result["ok"] = False
    result["components"]["schema"] = schema_status

    try:
        storage_ok, storage_status = await asyncio.wait_for(
            asyncio.to_thread(_probe_storage),
            timeout=_STORAGE_PROBE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        storage_ok, storage_status = False, "timeout"
        logger.warning("health/deep: storage probe timed out")
    result["components"]["storage"] = storage_status
    if not storage_ok:
        result["ok"] = False

    if settings.pipeline_mode == "celery":
        broker_ok = await _probe_broker()
        result["components"]["broker"] = "ok" if broker_ok else "fail"
        if not broker_ok:
            result["ok"] = False
            result["components"]["celery_worker"] = "unreachable"
        else:
            worker_count = await _probe_celery_workers()
            result["components"]["celery_worker"] = (
                "ok" if worker_count > 0 else "none"
            )
            result["worker_count"] = worker_count
            if worker_count < 1:
                result["ok"] = False
    else:
        result["components"]["broker"] = "not_required"
        result["components"]["celery_worker"] = "not_required"

    # At least one AI provider key configured.  Scanning env so we
    # don't inadvertently read a user's localStorage-sent key.
    try:
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
