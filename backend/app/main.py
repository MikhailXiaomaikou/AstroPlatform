import asyncio
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.data import router as data_router
from app.api.export import router as export_router
from app.api.health import router as health_router
from app.api.integration import router as integration_router
from app.api.arxiv import router as arxiv_router
from app.api.pipeline import router as pipeline_router
from app.api.settings import router as settings_router
from app.api.scheduler import router as scheduler_router
from app.api.team import router as team_router
from app.api.visualization import router as viz_router
from app.api.citations import router as citations_router
from app.api.crossmatch import router as crossmatch_router
from app.api.workspace import router as workspace_router
from app.api.ws import router as ws_router, redis_subscriber
from app.cors import get_cors_origins
from app.models.database import engine, Base
from app.rate_limit import limiter

logger = logging.getLogger(__name__)


def _migrate_add_columns(connection):
    """Add new columns to existing tables (SQLite create_all won't do this)."""
    import sqlalchemy
    inspector = sqlalchemy.inspect(connection)
    if "users" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("users")}
        migrations = [
            ("google_id", "VARCHAR(255)"),
            ("avatar_url", "TEXT"),
            ("display_name", "VARCHAR(255)"),
        ]
        for col_name, col_type in migrations:
            if col_name not in existing:
                try:
                    connection.execute(sqlalchemy.text(
                        f"ALTER TABLE users ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info("Added column users.%s", col_name)
                except Exception as e:
                    # Column may have been added by a concurrent worker
                    logger.debug("Migration users.%s skipped: %s", col_name, e)
        # Add unique index on google_id if not already present
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
        if "ix_users_google_id" not in existing_indexes:
            try:
                connection.execute(sqlalchemy.text(
                    "CREATE UNIQUE INDEX ix_users_google_id ON users (google_id)"
                ))
                logger.info("Created unique index ix_users_google_id")
            except Exception:
                pass  # Index may already exist under a different name


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_add_columns)
    logger.info("Database tables created")

    # Start Redis subscriber for WebSocket relay (best-effort)
    subscriber_task = asyncio.create_task(redis_subscriber())

    yield

    subscriber_task.cancel()
    try:
        await subscriber_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Astro Research Platform",
    version="0.4.0",
    description="AI-native platform for professional astronomers — data discovery, analysis & pipelines",
    lifespan=lifespan,
)

# ── Request monitoring ──
import time as _time
from collections import defaultdict as _defaultdict

_api_stats = {
    "requests_total": 0,
    "errors_total": 0,
    "start_time": _time.time(),
    "endpoint_counts": _defaultdict(int),
    "endpoint_errors": _defaultdict(int),
    "last_errors": [],  # last 20 errors
}


@app.middleware("http")
async def monitor_requests(request, call_next):
    _api_stats["requests_total"] += 1
    path = request.url.path
    _api_stats["endpoint_counts"][path] += 1
    try:
        response = await call_next(request)
        if response.status_code >= 400:
            _api_stats["errors_total"] += 1
            _api_stats["endpoint_errors"][path] += 1
            if response.status_code >= 500:
                _api_stats["last_errors"].append({
                    "time": _time.strftime("%H:%M:%S"),
                    "path": path,
                    "status": response.status_code,
                })
                _api_stats["last_errors"] = _api_stats["last_errors"][-20:]
        return response
    except Exception as e:
        _api_stats["errors_total"] += 1
        _api_stats["endpoint_errors"][path] += 1
        raise


# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Routers
app.include_router(arxiv_router)
app.include_router(auth_router)
app.include_router(citations_router)
app.include_router(crossmatch_router)
app.include_router(chat_router)
app.include_router(data_router)
app.include_router(export_router)
app.include_router(health_router)
app.include_router(integration_router)
app.include_router(pipeline_router)
app.include_router(scheduler_router)
app.include_router(settings_router)
app.include_router(team_router)
app.include_router(viz_router)
app.include_router(workspace_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.4.0"}


@app.get("/health/stats")
async def health_stats():
    """API usage statistics and recent errors."""
    uptime = _time.time() - _api_stats["start_time"]
    top_endpoints = sorted(
        _api_stats["endpoint_counts"].items(), key=lambda x: x[1], reverse=True
    )[:10]
    return {
        "uptime_seconds": int(uptime),
        "requests_total": _api_stats["requests_total"],
        "errors_total": _api_stats["errors_total"],
        "error_rate": round(_api_stats["errors_total"] / max(_api_stats["requests_total"], 1), 4),
        "top_endpoints": [{"path": p, "count": c} for p, c in top_endpoints],
        "recent_errors": _api_stats["last_errors"][-10:],
    }
