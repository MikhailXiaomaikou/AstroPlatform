import os
import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

_ENV = os.getenv("ENV", "dev")

# ---------------------------------------------------------------------------
# Ensure astropy / astroquery have a writable cache & config directory.
# In containerised environments (Docker, Render) HOME is often set to a
# non-existent path like /nonexistent, which causes "[Errno 13] Permission
# denied" when astroquery tries to write cache files.
# We check early — before any astropy import — and redirect XDG dirs to a
# writable temp location when the home directory is not usable.
# ---------------------------------------------------------------------------
def _ensure_writable_home():
    home = os.environ.get("HOME", "")
    home_ok = home and os.path.isdir(home) and os.access(home, os.W_OK)
    if not home_ok:
        fallback = os.path.join(tempfile.gettempdir(), "standard_astro_home")
        os.makedirs(fallback, exist_ok=True)
        # Force-set HOME (not setdefault) — HOME may exist but point to /nonexistent
        os.environ["HOME"] = fallback
        cache_dir = os.path.join(fallback, ".cache")
        config_dir = os.path.join(fallback, ".config")
        os.environ["XDG_CACHE_HOME"] = cache_dir
        os.environ["XDG_CONFIG_HOME"] = config_dir
        os.makedirs(cache_dir, exist_ok=True)
        os.makedirs(config_dir, exist_ok=True)

_ensure_writable_home()


class Settings(BaseSettings):
    database_url: str = f"sqlite+aiosqlite:///{_PROJECT_DIR / 'data' / 'astro.db'}"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24  # 24 hours
    fernet_key: str = ""
    admin_secret: str = ""

    # Local-mode: use filesystem instead of MinIO
    # In Docker: /app is WORKDIR, so use /app/data/fits
    local_storage_dir: str = str(Path("/app/data/fits") if os.getenv("ENV") == "production" else _PROJECT_DIR / "data" / "fits")

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
                import secrets as _s
                self.jwt_secret = _s.token_hex(32)
                import logging as _log
                _log.getLogger(__name__).warning("JWT_SECRET not set — using random dev secret (tokens won't survive restarts)")
            else:
                raise ValueError(
                    "JWT_SECRET environment variable must be set in production. "
                    "Set ENV=dev to use the development fallback."
                )
        if not self.fernet_key:
            import secrets as _s
            self.fernet_key = _s.token_urlsafe(32)

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
