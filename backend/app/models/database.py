from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,          # Base pool size (paid Render supports many more)
    max_overflow=5,        # Extra connections under burst load
    pool_timeout=10,       # Wait max 10s for a connection from the pool
    pool_recycle=300,      # Recycle connections every 5 minutes (prevents stale connections)
    pool_pre_ping=True,    # Verify connection is alive before using it
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
