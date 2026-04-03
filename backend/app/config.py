import os
from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

_ENV = os.getenv("ENV", "dev")


class Settings(BaseSettings):
    database_url: str = f"sqlite+aiosqlite:///{_PROJECT_DIR / 'data' / 'astro.db'}"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours

    # Local-mode: use filesystem instead of MinIO
    local_storage_dir: str = str(_PROJECT_DIR / "data" / "fits")

    # Pipeline execution mode: "sync" or "celery"
    pipeline_mode: str = "sync"

    # Max FITS upload size in bytes (default 100 MB)
    max_upload_size: int = 100 * 1024 * 1024

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

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

    @property
    def redis_ssl(self) -> bool:
        """True when Redis URL uses TLS (rediss://, e.g. Upstash)."""
        return self.redis_url.startswith("rediss://")

    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL with TLS query params for Upstash/rediss."""
        if self.redis_ssl:
            sep = "&" if "?" in self.redis_url else "?"
            return f"{self.redis_url}{sep}ssl_cert_reqs=CERT_NONE"
        return self.redis_url


settings = Settings()
