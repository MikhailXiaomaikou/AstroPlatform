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
    jwt_expire_minutes: int = 60 * 4  # 4 hours; WebSocket puts JWT in the URL where proxy logs can capture it — shorter expiry limits the window after theft
    fernet_key: str = ""
    admin_secret: str = ""

    # Durable research object storage.  Local is appropriate for development
    # and Docker Compose with a mounted volume.  Hosted production should use
    # an S3-compatible store so uploads and exported artifacts survive deploys.
    storage_backend: str = "local"
    storage_require_integrity: bool = _ENV == "production"
    local_storage_dir: str = str(Path("/app/data/fits") if os.getenv("ENV") == "production" else _PROJECT_DIR / "data" / "fits")
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "us-east-1"
    s3_addressing_style: str = "path"

    # Structured validation-gate event JSONL (false-positive triage sink;
    # see app/observability/gate_events.py). Empty string disables. NOTE:
    # The production Blueprint mounts /app/data on a persistent disk. Hosted
    # deployments without that mount must export these events to their log/
    # metrics backend instead of assuming the container filesystem is durable.
    gate_events_jsonl_path: str = str(
        Path("/app/data/gate_events.jsonl") if os.getenv("ENV") == "production"
        else _PROJECT_DIR / "data" / "gate_events.jsonl"
    )

    # Pipeline execution mode: "sync" (dev only) or "celery" (default).
    # Heavy nodes (MCMC / nested sampling / IFU kinematics / image stacking)
    # refuse to run in sync mode to avoid blocking the FastAPI event loop.
    pipeline_mode: str = "celery"

    # Dynamic Python is disabled by default.  Neither the legacy in-process
    # executor nor the crash-isolated subprocess is an OS security boundary:
    # Python object-graph tricks and native imports can reach host files.  A
    # developer may explicitly opt into one of those backends on a trusted
    # single-user machine, but hosted production must stay disabled until a
    # separate no-secrets/no-mounts container or equivalent OS sandbox exists.
    sandbox_backend: str = "disabled"
    sandbox_memory_bytes: int = 1024 * 1024 * 1024  # 1 GB per call
    sandbox_timeout_seconds: int = 75

    # Raw connector response cache backend:
    # "auto" picks Redis → SQLite → Null based on availability,
    # "null" disables the cache entirely.
    connector_cache_backend: str = "auto"

    # Client-IP trust chain for rate limiting and audit logs
    # (app/rate_limit.py get_client_ip). Forwarded headers are attacker-
    # controlled unless a trusted reverse proxy sets them, so:
    #   "none"        trust only the transport peer (request.client.host);
    #                 ignore every forwarded header. Use when clients reach
    #                 uvicorn directly (bare local dev, docker-compose
    #                 without a proxy in front).
    #   "1".."9"      that many trusted reverse proxies in front; the real
    #                 client is the Nth-from-the-right X-Forwarded-For entry
    #                 (each trusted proxy appends the peer it accepted).
    #                 Render terminates traffic with exactly one proxy, so
    #                 production defaults to "1".
    #   "cloudflare"  Cloudflare in front; trust CF-Connecting-IP.
    # Any other value fails closed to "none".
    trusted_proxy_mode: str = "1" if _ENV == "production" else "none"

    # Max FITS upload size in bytes (default 100 MB)
    max_upload_size: int = 100 * 1024 * 1024

    # Database connection pool settings
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: int = 10
    db_pool_recycle: int = 300

    # Pipeline node cache TTL in seconds (default 24 hours)
    pipeline_cache_ttl: int = 86400

    # H3: Redis TLS verification.  Default to verified TLS on rediss:// (safe);
    # set REDIS_TLS_INSECURE=1 only when working with a hosted Redis that uses
    # a self-signed certificate and you accept the MITM risk.
    redis_tls_insecure: bool = False

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Server-funded DeepSeek fallback for public/anonymous chat.  The key is
    # read only server-side; never expose it to the browser or commit it.
    platform_deepseek_api_key: str = ""
    deepseek_api_key: str = ""
    shared_deepseek_api_key_enabled: bool = True

    # Docker image digest for reproducibility
    docker_image_digest: str = ""

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
            if _ENV == "dev":
                import secrets as _s
                self.fernet_key = _s.token_urlsafe(32)
                import logging as _log
                _log.getLogger(__name__).warning(
                    "FERNET_KEY not set — using random dev secret "
                    "(stored API keys won't survive a restart)"
                )
            else:
                raise ValueError(
                    "FERNET_KEY environment variable must be set in production. "
                    "Without a stable key, encrypted user API keys become unreadable "
                    "after every restart. Set ENV=dev to use the development fallback."
                )
        self.storage_backend = str(self.storage_backend or "local").strip().lower()
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("STORAGE_BACKEND must be 'local' or 's3'")
        if self.storage_backend == "s3":
            missing = [
                name
                for name, value in (
                    ("S3_BUCKET", self.s3_bucket),
                    ("S3_ACCESS_KEY_ID", self.s3_access_key_id),
                    ("S3_SECRET_ACCESS_KEY", self.s3_secret_access_key),
                )
                if not str(value or "").strip()
            ]
            if missing:
                raise ValueError(
                    "STORAGE_BACKEND=s3 requires " + ", ".join(missing)
                )
        self.sandbox_backend = str(self.sandbox_backend or "disabled").strip().lower()
        if self.sandbox_backend not in {"disabled", "inprocess", "subprocess"}:
            raise ValueError(
                "SANDBOX_BACKEND must be 'disabled', 'inprocess', or 'subprocess'"
            )
        if _ENV == "production" and self.sandbox_backend != "disabled":
            raise ValueError(
                "Production run_python is disabled: inprocess/subprocess provide "
                "crash containment, not an OS security boundary. Use "
                "SANDBOX_BACKEND=disabled until an external isolated runner is "
                "implemented."
            )

    @property
    def redis_ssl(self) -> bool:
        """True when Redis URL uses TLS (rediss://, e.g. Upstash)."""
        return self.redis_url.startswith("rediss://")

    def redis_tls_kwargs(self) -> dict:
        """Return the connection kwargs callers should merge for TLS behaviour.

        H3: centralised so that CERT_REQUIRED is the default and opting into
        insecure TLS (CERT_NONE) requires an explicit env flag.
        """
        if not self.redis_ssl:
            return {}
        if self.redis_tls_insecure:
            return {"ssl_cert_reqs": "none"}
        return {"ssl_cert_reqs": "required"}

    @property
    def celery_broker_url(self) -> str:
        """Celery broker URL with TLS query params for Upstash/rediss."""
        if self.redis_ssl:
            sep = "&" if "?" in self.redis_url else "?"
            mode = "CERT_NONE" if self.redis_tls_insecure else "CERT_REQUIRED"
            return f"{self.redis_url}{sep}ssl_cert_reqs={mode}"
        return self.redis_url


settings = Settings()
