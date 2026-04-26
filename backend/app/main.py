import asyncio
import logging
import os as _os

from contextlib import asynccontextmanager

if _os.getenv("ENV") == "production":
    from app.logging_config import setup_logging
    setup_logging(json_format=True, level="INFO")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.alerts import router as alerts_router
from app.api.anomalies import router as anomalies_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.data import router as data_router
from app.api.dossier import router as dossier_router
from app.api.events import router as events_router, admin_router as admin_events_router
from app.api.export import router as export_router
from app.api.followup import router as followup_router
from app.api.health import router as health_router
from app.api.integration import router as integration_router
from app.api.isochrones import router as isochrones_router
from app.api.inference import router as inference_router
from app.api.arxiv import router as arxiv_router
from app.api.pipeline import router as pipeline_router
from app.api.paper import router as paper_router
from app.api.research import router as research_router
from app.api.settings import router as settings_router
from app.api.scheduler import router as scheduler_router
from app.api.sessions import router as sessions_router, shared_router as shared_sessions_router
from app.api.team import router as team_router
from app.api.visualization import router as viz_router
from app.api.citation_graph import router as citation_graph_router
from app.api.citations import router as citations_router
from app.api.comments import router as comments_router
from app.api.admin_stats import router as admin_stats_router
from app.api.admin_trending import admin_router as admin_trending_router, public_router as trending_public_router
from app.api.admin_sandbox import router as admin_sandbox_router
from app.api.admin_literature import router as admin_literature_router
from app.api.crossmatch import router as crossmatch_router
from app.api.workspace import router as workspace_router
from app.api.ws import router as ws_router, redis_subscriber
from app.api.provenance import router as provenance_router
from app.cors import get_cors_origins
from app.logging_config import CorrelationIdMiddleware
from app.models.database import engine, Base
from app.rate_limit import limiter
from app.middleware.event_tracking import EventTrackingMiddleware
from app.services.event_collector import event_collector, periodic_flush
from app.services.provenance_v2.registry_loader import check_freshness

logger = logging.getLogger(__name__)


def _migrate_add_columns(connection):
    """Add new columns to existing tables (SQLite create_all won't do this).

    NOTE: This function is kept for SQLite dev-server compatibility, where
    ``Base.metadata.create_all`` cannot add columns to pre-existing tables.
    It is fully idempotent (every operation checks first or catches errors).
    For production deployments, prefer running Alembic migrations instead:
        alembic upgrade head
    The equivalent Alembic migration is versions/002_consolidate_manual_migrations.py.
    """
    import sqlalchemy
    from app.utils.usernames import normalize_username

    inspector = sqlalchemy.inspect(connection)
    if "users" in inspector.get_table_names():
        existing = {c["name"] for c in inspector.get_columns("users")}
        username_column_present = "username" in existing
        migrations = [
            ("username", "VARCHAR(255)"),
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
                    if col_name == "username":
                        username_column_present = True
                    existing.add(col_name)
                except Exception as e:
                    # Column may have been added by a concurrent worker
                    if col_name == "username":
                        username_column_present = True
                    logger.debug("Migration users.%s skipped: %s", col_name, e)
        if username_column_present:
            try:
                rows = connection.execute(
                    sqlalchemy.text(
                        "SELECT id, username, email, display_name FROM users ORDER BY created_at ASC"
                    )
                ).fetchall()
                used_usernames: set[str] = set()
                for row in rows:
                    current = normalize_username(row.username or "")
                    if not current:
                        current = normalize_username(row.display_name or "") or normalize_username(row.email or "")
                    if not current:
                        current = "user"
                    base = current[:32]
                    candidate = base
                    suffix = 2
                    while candidate in used_usernames:
                        suffix_str = str(suffix)
                        trimmed = base[: max(1, 32 - len(suffix_str))].rstrip("._-") or "user"
                        candidate = f"{trimmed}{suffix_str}"
                        suffix += 1
                    used_usernames.add(candidate)
                    if row.username != candidate:
                        connection.execute(
                            sqlalchemy.text("UPDATE users SET username = :username WHERE id = :id"),
                            {"username": candidate, "id": str(row.id)},
                        )
                existing.add("username")
            except Exception as e:
                logger.debug("Username backfill skipped: %s", e)
        # Add unique index on google_id if not already present
        existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")}
        if "ix_users_username" not in existing_indexes:
            try:
                connection.execute(sqlalchemy.text(
                    "CREATE UNIQUE INDEX ix_users_username ON users (username)"
                ))
                logger.info("Created unique index ix_users_username")
            except Exception:
                pass
        if "ix_users_google_id" not in existing_indexes:
            try:
                connection.execute(sqlalchemy.text(
                    "CREATE UNIQUE INDEX ix_users_google_id ON users (google_id)"
                ))
                logger.info("Created unique index ix_users_google_id")
            except Exception:
                pass  # Index may already exist under a different name

    # --- RunResult reproducibility metadata columns ---
    if "run_results" in inspector.get_table_names():
        existing_rr = {c["name"] for c in inspector.get_columns("run_results")}
        rr_migrations = [
            ("input_hash", "VARCHAR(64)"),
            ("output_checksum", "VARCHAR(64)"),
            ("execution_time_ms", "INTEGER"),
        ]
        for col_name, col_type in rr_migrations:
            if col_name not in existing_rr:
                try:
                    connection.execute(sqlalchemy.text(
                        f"ALTER TABLE run_results ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info("Added column run_results.%s", col_name)
                except Exception:
                    pass

    # --- PipelineRun environment snapshot column ---
    if "pipeline_runs" in inspector.get_table_names():
        existing_pr = {c["name"] for c in inspector.get_columns("pipeline_runs")}
        if "environment" not in existing_pr:
            try:
                connection.execute(sqlalchemy.text("ALTER TABLE pipeline_runs ADD COLUMN environment TEXT"))
                logger.info("Added column pipeline_runs.environment")
            except Exception:
                pass

    # --- ChatSession audit_log column (R7 thinking-stream audit) ---
    if "chat_sessions" in inspector.get_table_names():
        existing_cs = {c["name"] for c in inspector.get_columns("chat_sessions")}
        if "audit_log" not in existing_cs:
            try:
                connection.execute(sqlalchemy.text(
                    "ALTER TABLE chat_sessions ADD COLUMN audit_log TEXT"
                ))
                logger.info("Added column chat_sessions.audit_log")
            except Exception:
                pass

    # --- InferenceLog manual model selection metadata ---
    if "inference_logs" in inspector.get_table_names():
        existing_il = {c["name"] for c in inspector.get_columns("inference_logs")}
        for col_name in ["model_name", "model_profile", "fallback_from"]:
            if col_name not in existing_il:
                try:
                    connection.execute(sqlalchemy.text(
                        f"ALTER TABLE inference_logs ADD COLUMN {col_name} VARCHAR(255)"
                    ))
                    logger.info("Added column inference_logs.%s", col_name)
                except Exception:
                    pass

    # --- PaperDraft explicit publication controls ---
    if "paper_drafts" in inspector.get_table_names():
        existing_pd = {c["name"] for c in inspector.get_columns("paper_drafts")}
        paper_migrations = [
            ("is_public", "BOOLEAN DEFAULT 0 NOT NULL"),
            ("public_token", "VARCHAR(64)"),
            ("published_at", "TIMESTAMP"),
        ]
        for col_name, col_type in paper_migrations:
            if col_name not in existing_pd:
                try:
                    connection.execute(sqlalchemy.text(
                        f"ALTER TABLE paper_drafts ADD COLUMN {col_name} {col_type}"
                    ))
                    logger.info("Added column paper_drafts.%s", col_name)
                except Exception:
                    pass
        try:
            connection.execute(sqlalchemy.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_paper_drafts_public_token ON paper_drafts (public_token)"
            ))
            connection.execute(sqlalchemy.text(
                "CREATE INDEX IF NOT EXISTS ix_paper_drafts_is_public ON paper_drafts (is_public)"
            ))
        except Exception:
            pass

    # --- PipelineComment parent_comment_id column ---
    if "pipeline_comments" in inspector.get_table_names():
        existing_pc = {c["name"] for c in inspector.get_columns("pipeline_comments")}
        if "parent_comment_id" not in existing_pc:
            try:
                connection.execute(sqlalchemy.text(
                    "ALTER TABLE pipeline_comments ADD COLUMN parent_comment_id VARCHAR(36)"
                ))
                logger.info("Added column pipeline_comments.parent_comment_id")
            except Exception:
                pass

    # --- Performance indexes for data_files and pipeline_runs ---
    perf_indexes = [
        ("idx_datafile_source", "data_files", "(source)"),
        ("idx_datafile_object_id", "data_files", "(object_id)"),
        ("idx_datafile_user_source", "data_files", "(user_id, source)"),
        ("idx_pipelinerun_status", "pipeline_runs", "(status)"),
        ("idx_pipelinerun_user_status", "pipeline_runs", "(user_id, status)"),
        ("idx_chatsession_user", "chat_sessions", "(user_id)"),
        ("idx_chatsession_user_created", "chat_sessions", "(user_id, created_at)"),
    ]
    for idx_name, table, cols in perf_indexes:
        if table in inspector.get_table_names():
            try:
                connection.execute(sqlalchemy.text(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} {cols}"
                ))
            except Exception:
                pass


def _enforce_provenance_registry_freshness(*, warn_days: int = 180) -> None:
    """Block startup when fallback provenance entries are stale."""
    warnings = check_freshness(warn_days=warn_days)
    if not warnings:
        logger.info("Provenance registry freshness check passed")
        return

    for warning in warnings:
        logger.error("provenance_registry_freshness_blocker %s", warning)
    raise RuntimeError(
        "Provenance registry freshness check failed: " + "; ".join(warnings)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _enforce_provenance_registry_freshness()

    # M8: gate create_all on non-production environments.  In production the
    # schema is managed via Alembic; re-running create_all on every startup
    # takes DDL locks (noticeable stalls on PostgreSQL with a large schema)
    # and can mask drift between the model layer and the migration history.
    _env = _os.getenv("ENV", "dev")
    if _env != "production":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Runtime migration is only needed for SQLite dev servers.
            await conn.run_sync(_migrate_add_columns)
        logger.info("Database tables created (dev)")
    else:
        logger.info("Skipping create_all in production; use `alembic upgrade head`")

    # 专项小 migration: 新 `comments` 表.  Production 走 Alembic 的规矩
    # 下我们仍需要在表不存在时 create 一次(未来追加新表都走这个路径,
    # 避免每次发布都先写 Alembic).  只对 Comment.__table__ 一张表做
    # CREATE TABLE IF NOT EXISTS, 不碰其他表.
    try:
        from app.models.schemas import Comment, TrendingVisibility
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync_conn: Comment.__table__.create(sync_conn, checkfirst=True))
            await conn.run_sync(lambda sync_conn: TrendingVisibility.__table__.create(sync_conn, checkfirst=True))
        logger.info("Ensured `comments` + `trending_visibility` tables exist")
    except Exception as e:
        logger.warning("Failed to ensure new tables (%s); evaluate Alembic migration", e)

    # R18: lightkurve/MAST 偶尔会留下半截 FITS 缓存，后续下载同一目标
    # 时会被坏文件反复污染。启动时做一次有限扫描，只删除 astropy 无法
    # 打开的缓存 FITS，正常缓存保留。
    try:
        from app.services.astro_analysis import cleanup_lightkurve_cache
        cache_cleanup = cleanup_lightkurve_cache(max_files=500)
        removed = cache_cleanup.get("removed") or []
        if removed:
            logger.warning("Removed %d corrupted lightkurve cache file(s)", len(removed))
        else:
            logger.info("Lightkurve cache check complete (%s file(s) checked)", cache_cleanup.get("checked", 0))
    except Exception as e:
        logger.debug("Lightkurve cache check skipped: %s", e)

    # Start Redis subscriber for WebSocket relay (best-effort)
    subscriber_task = asyncio.create_task(redis_subscriber())
    event_flush_task = asyncio.create_task(periodic_flush(event_collector, interval=event_collector.FLUSH_INTERVAL))

    yield

    subscriber_task.cancel()
    event_flush_task.cancel()
    try:
        await subscriber_task
    except asyncio.CancelledError:
        pass
    try:
        await event_collector.flush()
        await event_flush_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Standard Astro",
    version="0.4.0",
    description="AI-native platform for professional astronomers — data discovery, analysis & pipelines",
    lifespan=lifespan,
)

# ── Request monitoring ──
import time as _time  # noqa: E402
from collections import defaultdict as _defaultdict  # noqa: E402

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
    if request.method == "OPTIONS":
        return await call_next(request)
    _api_stats["requests_total"] += 1
    import re as _re
    path = _re.sub(r'[0-9a-f-]{36}', '{id}', request.url.path)
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
    except Exception:
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
    allow_headers=[
        "Authorization", "Content-Type", "X-Page-Name", "X-Tracking-Session",
        # N'-1 hotfix: 桌面 admin HTML 发 X-Admin-Secret header, 必须在
        # preflight 白名单里, 否则浏览器 CORS block ("Disallowed CORS headers" 400).
        "X-Admin-Secret",
    ],
)
app.add_middleware(EventTrackingMiddleware)
app.add_middleware(CorrelationIdMiddleware)

# ── Security response headers ──
MAX_REQUEST_BODY = 1_048_576  # 1 MB for non-upload endpoints

# Endpoints that legitimately accept >1 MB request bodies. The default
# 1 MB cap protects /api/chat / /api/adql / etc. from oversized payloads,
# but chat export packages an entire transcript (text + actions +
# embedded figure metadata) and often crosses 1 MB on long sessions.
# Bug reproducer: M5 export → frontend reports "network error" because
# the middleware 413'd the POST before it reached the export handler.
_LARGE_BODY_PREFIXES = (
    "/api/data/fits/upload",   # FITS uploads have their own MAX_UPLOAD_SIZE
    "/api/data/upload",        # generic uploads
    "/api/export/",            # chat -> markdown / notebook / latex / bibtex
    "/api/integration/jupyter/export",  # notebook bundles
    "/api/workspace/batch-upload",  # workspace bulk upload
    "/api/paper/",             # AI-drafted paper assembly with figures
    "/api/research/",          # full research report bundles
)


# T5 (PART T): `null` origin (file:// / data: URL) 只允许 admin 和公开
# comments 两个前缀. 防止用户被诱导打开恶意 file:// HTML 后, 该 HTML
# 跨源调其他敏感 API (/api/chat, /api/adql 等). 桌面 admin HTML 仍可用.
_NULL_ORIGIN_ALLOWED_PREFIXES = (
    "/api/admin/",
    "/api/comments",
    "/admin",          # static HTML route
    "/metrics",        # prometheus scrape from anywhere OK
    "/health",
)


@app.middleware("http")
async def security_headers(request, call_next):
    """Set security headers on every response and enforce a 1 MB body-size
    limit for endpoints other than the FITS upload route (which uses its own
    ``MAX_UPLOAD_SIZE`` setting)."""
    # --- T5: null-origin path restriction (before body-size check) ---
    origin = request.headers.get("origin", "")
    if origin == "null":
        path = request.url.path
        if not any(path.startswith(p) for p in _NULL_ORIGIN_ALLOWED_PREFIXES):
            from starlette.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "null origin (file:// / data:) only permitted for admin "
                        "/ comments / metrics / health endpoints"
                    )
                },
            )

    # --- request body size gate (skip large-body endpoints) ---
    path = request.url.path
    if not any(path.startswith(p) for p in _LARGE_BODY_PREFIXES):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BODY:
                    from starlette.responses import JSONResponse
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                pass  # malformed header — let downstream handle it

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=()"
    )
    return response


# Routers
app.include_router(alerts_router)
app.include_router(anomalies_router)
app.include_router(arxiv_router)
app.include_router(auth_router)
app.include_router(citation_graph_router)
app.include_router(citations_router)
app.include_router(comments_router)
app.include_router(admin_stats_router)
app.include_router(admin_trending_router)
app.include_router(trending_public_router)
app.include_router(admin_sandbox_router)
app.include_router(admin_literature_router)


# ── 桌面 admin HTML 托管 ─────────────────────────────────────────────
# 把原本用户 "cp 到桌面双击" 的 astro_admin.html 搬到 backend 下,
# 用户现在 bookmark `{BACKEND}/admin` 一次, 每次打开就是最新.
# HTML 里默认 backend URL 用 window.location.origin 自动匹配.
@app.get("/admin", include_in_schema=False)
@app.get("/admin.html", include_in_schema=False)
async def admin_dashboard_html():
    """Serve the desktop admin dashboard HTML from backend/app/static/.

    Cache-Control: no-store — admin 页不能被浏览器缓存, 否则 push 新版
    用户硬刷新也看不到.
    """
    from fastapi.responses import FileResponse
    import pathlib as _pl
    path = _pl.Path(__file__).parent / "static" / "astro_admin.html"
    return FileResponse(
        path,
        media_type="text/html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )
app.include_router(crossmatch_router)
app.include_router(chat_router)
app.include_router(data_router)
app.include_router(dossier_router)
app.include_router(events_router)
app.include_router(admin_events_router)
app.include_router(export_router)
app.include_router(followup_router)
app.include_router(health_router)
app.include_router(inference_router)
app.include_router(integration_router)
app.include_router(isochrones_router)
app.include_router(pipeline_router)
app.include_router(paper_router)
app.include_router(research_router)
app.include_router(scheduler_router)
app.include_router(sessions_router)
app.include_router(settings_router)
app.include_router(shared_sessions_router)
app.include_router(team_router)
app.include_router(viz_router)
app.include_router(workspace_router)
app.include_router(provenance_router)
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.4.0"}


@app.get("/metrics")
async def metrics():
    """Prometheus text-format exposition of stdlib metrics registry."""
    from fastapi.responses import PlainTextResponse
    from app.observability import render_prometheus
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


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
