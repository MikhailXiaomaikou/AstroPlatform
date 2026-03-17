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
from app.api.workspace import router as workspace_router
from app.api.ws import router as ws_router, redis_subscriber
from app.cors import get_cors_origins
from app.models.database import engine, Base
from app.rate_limit import limiter

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    version="0.3.0",
    description="SaaS platform for professional astronomers — data ingestion & pipeline editor",
    lifespan=lifespan,
)

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
    return {"status": "ok"}
