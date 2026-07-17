import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from DATABASE_URL env var if set
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Render injects a conventional synchronous PostgreSQL URL, while this
    # Alembic environment uses SQLAlchemy's async engine. Match app.config's
    # normalization so pre-deploy migrations load asyncpg instead of psycopg2.
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://", "postgresql+asyncpg://", 1
        )
    config.set_main_option("sqlalchemy.url", database_url)

# Import Base and all models so Alembic sees the metadata
from app.models.database import Base  # noqa: E402
from app.models.schemas import (  # noqa: E402, F401
    DataFile,
    DataNote,
    DataTag,
    PipelineComment,
    PipelineRun,
    PipelineTemplateDB,
    PipelineVersion,
    RunResult,
    ScheduledRun,
    SharedDataset,
    SharedPipeline,
    TeamMember,
    User,
)
from app.models.research_records import ProvenanceRecord, ResearchJob  # noqa: E402, F401
from app.models.claim_audit_records import (  # noqa: E402, F401
    AccountDeletionTombstone,
    ClaimAudit,
    EvidencePack,
    Invitation,
    PrivacyPreference,
)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Calls to context.execute() here emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations using an async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
