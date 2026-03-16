import os
from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

_ENV = os.getenv("ENV", "dev")


class Settings(BaseSettings):
    database_url: str = f"sqlite+aiosqlite:///{_PROJECT_DIR / 'data' / 'astro.db'}"
    redis_url: str = "redis://localhost:6379/0"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "astro-fits"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Local-mode: use filesystem instead of MinIO
    local_storage_dir: str = str(_PROJECT_DIR / "data" / "fits")

    # Pipeline execution mode: "sync" or "celery"
    pipeline_mode: str = "sync"

    model_config = {"env_file": ".env"}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-fix PostgreSQL URL for async driver
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        if not self.jwt_secret:
            if _ENV == "dev":
                self.jwt_secret = "dev-secret-change-me"
            else:
                raise ValueError(
                    "JWT_SECRET environment variable must be set in production. "
                    "Set ENV=dev to use the development fallback."
                )


settings = Settings()
