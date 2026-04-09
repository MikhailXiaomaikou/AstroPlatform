"""Shared test fixtures for the standard-astro backend tests."""

import os
os.environ["RATE_LIMIT_ENABLED"] = "false"

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.database import Base, get_db
from app.auth import create_access_token, hash_password
from app.models.schemas import User


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
        email="test@astro.example.com",
        password_hash=hash_password("securepassword123"),
        subscription_tier="solo",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    token = create_access_token(user.id)
    return user, token
