"""Shared test fixtures for the standard-astro backend tests."""

import os
from pathlib import Path
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["RATE_LIMIT_ENABLED"] = "false"
# Tests intentionally exercise the legacy local executor.  Runtime defaults
# remain fail-closed; this opt-in is confined to the test process.
os.environ.setdefault("SANDBOX_BACKEND", "inprocess")

# Prod engine (app/models/database.py) is created at import time with
# settings.database_url, default "sqlite+aiosqlite:///<repo>/data/astro.db"
# (config.py: _PROJECT_DIR is the repo root, three .parent's up from app/config.py).
# CI runners ship without that data/ directory, so the first connect raises
# OperationalError: unable to open database file. Tests that import the prod
# engine (TestSessionPaper, etc.) need this directory to exist before SQLAlchemy
# tries to open it.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
(_REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)

# Application imports intentionally follow the test-only environment and data
# directory setup above because both are consumed during module import.
from app.models.database import Base, get_db  # noqa: E402
from app.auth import create_access_token, hash_password  # noqa: E402
from app.models.schemas import User  # noqa: E402
from app.utils.usernames import username_from_email  # noqa: E402


@pytest.fixture(autouse=True)
def stable_code_executor_memory(monkeypatch):
    """Keep code-executor tests independent of full-suite pytest RSS.

    The production sandbox memory guard intentionally checks the current
    process RSS. A full pytest run imports heavy astronomy/ML libraries before
    many sandbox tests execute, which can push the test runner itself above the
    production hard limit. These tests exercise sandbox behavior, not pytest's
    accumulated memory footprint.
    """
    from app.services import code_executor

    monkeypatch.setattr(code_executor, "_get_memory_usage_bytes", lambda: 64 * 1024 * 1024)


@pytest.fixture(autouse=True)
def isolated_kv_store():
    """Force the shared KV store onto an in-memory backend for every test.

    Without this, ``workflow_checkpoint`` / ``cosmology_mcmc`` / the upcoming
    ``async_tool_runtime`` would try to reach Redis at ``localhost:6379`` (2 s
    connect timeout × hundreds of tests) and otherwise write a real
    ``data/kv_store.db`` SQLite file. Tests want full read/write semantics
    without either external dependency.
    """
    from app.services import _kv_store

    _kv_store.use_memory_backend_for_testing()
    yield
    _kv_store.reset_backend()


@pytest.fixture(autouse=True)
def isolated_async_dispatcher():
    """Default-mock the Celery dispatcher so tests don't reach a real broker.

    Tests that explicitly want to exercise the dispatcher (e.g.
    ``test_async_tool_runtime``) install their own with
    ``atr.set_dispatcher``; this fixture resets afterwards. Submissions
    from unrelated tests therefore see the queued banner without trying
    to enqueue against Redis.
    """
    from app.services import async_tool_runtime as atr

    atr.set_dispatcher(lambda *args, **kwargs: None)
    # Unit tests that are not explicitly persistence tests should not depend on
    # the developer's on-disk DB revision. Production keeps the strict default;
    # durable_research_records tests opt back into it explicitly.
    atr.set_persister(lambda _job: None)
    yield
    atr.reset_dispatcher()
    atr.reset_persister()


# ── In-memory SQLite engine (per-test-session) ──

TEST_DB_URL = "sqlite+aiosqlite://"

engine = create_async_engine(TEST_DB_URL, echo=False)
TestSession = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def db_session():
    """Create all tables, yield a session, then drop everything."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSession() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def app_client(db_session: AsyncSession):
    """AsyncClient wired to the FastAPI app with the test DB session."""
    from app.main import app
    from app.api.visualization import router as viz_router

    # Include the visualization router if not already registered
    existing_prefixes = {r.prefix for r in app.routes if hasattr(r, "prefix")}
    if "/api/viz" not in existing_prefixes:
        app.include_router(viz_router)

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(db_session: AsyncSession):
    """Insert a user into the DB and return (user, token) tuple."""
    user = User(
        id=uuid.uuid4(),
        username=username_from_email("test@astro.example.com"),
        email="test@astro.example.com",
        password_hash=hash_password("securepassword123"),
        subscription_tier="solo",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = create_access_token(user.id)
    return user, token
