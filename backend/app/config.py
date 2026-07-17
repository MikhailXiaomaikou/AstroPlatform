import json
import os
import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

_ENV = os.getenv("ENV", "dev").strip().lower()
if _ENV == "prod":
    _ENV = "production"

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
    deletion_tombstone_key: str = ""
    deletion_tombstone_key_id: str = ""
    deletion_tombstone_verification_keys: str = ""
    admin_secret: str = ""

    # Scientific evidence is signed independently from login tokens.  The
    # current key signs new records; the JSON keyring retains retired keys so
    # historical paper evidence remains verifiable after an intentional
    # rotation.  Legacy schema-v1 records had no key id and were signed with
    # JWT_SECRET; their old JWT key can be retained in the same keyring.
    evidence_signing_key: str = ""
    evidence_signing_key_id: str = ""
    evidence_verification_keys: str = ""

    # Durable research object storage.  Local is appropriate for development
    # and Docker Compose with a mounted volume.  Hosted production should use
    # an S3-compatible store so uploads and exported artifacts survive deploys.
    storage_backend: str = "local"
    storage_require_integrity: bool = _ENV == "production"
    local_storage_dir: str = str(
        Path("/app/data/fits")
        if _ENV == "production"
        else _PROJECT_DIR / "data" / "fits"
    )
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
        Path("/app/data/gate_events.jsonl") if _ENV == "production"
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

    # Subscription-authenticated CLIs execute as local child processes and may
    # access the operator's login state.  They are a single-user development
    # convenience, never a hosted/multi-tenant production backend.
    openai_cli_enabled: bool = False
    claude_cli_enabled: bool = False

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
    #   "render"      trust the first X-Forwarded-For entry emitted by
    #                 Render's edge. Enable only after validating that the
    #                 deployed service cannot be reached around that edge.
    #   "1".."9"      that many trusted reverse proxies in front; the real
    #                 client is the Nth-from-the-right X-Forwarded-For entry
    #                 (each trusted proxy appends the peer it accepted).
    #   "cloudflare"  Cloudflare in front; trust CF-Connecting-IP.
    # Any other value fails closed to "none".
    # Production also defaults to "none": proxy trust is an explicit operator
    # opt-in because a mistaken topology assumption makes client-IP quotas
    # spoofable.
    trusted_proxy_mode: str = "none"

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

    # Optional server-funded DeepSeek fallback for public/anonymous chat. The
    # key is read only server-side; never expose it to the browser or commit
    # it. Sharing is fail-closed and requires an explicit operator opt-in.
    platform_deepseek_api_key: str = ""
    deepseek_api_key: str = ""
    shared_deepseek_api_key_enabled: bool = False

    # The hosted research alpha is invitation-only and the claim-audit product
    # stays dark until its scientific and privacy gates have passed. Local
    # development can opt in explicitly without changing production defaults.
    signup_mode: str = "invite_only" if _ENV == "production" else "public"
    claim_audit_enabled: bool = False
    claim_audit_execution_mode: str = (
        "celery" if _ENV == "production" else "inline"
    )
    claim_audit_registered_timeout_seconds: int = 30 * 60
    claim_audit_worker_lease_seconds: int = 120
    claim_audit_heartbeat_seconds: int = 30
    claim_audit_max_active_per_user: int = 2
    artifact_cleanup_grace_seconds: int = 24 * 60 * 60
    product_analytics_retention_days: int = 30
    privacy_operator_name: str = ""
    privacy_contact: str = ""
    privacy_jurisdiction: str = ""

    # Docker image digest for reproducibility
    docker_image_digest: str = ""

    # Runtime-only local keys (CLI commands and the Bot Console bridge) are
    # intentionally read with os.getenv at call time. Ignore those additional
    # keys when a self-hoster keeps them beside declared Settings fields in a
    # local .env file.
    model_config = {"env_file": ".env", "extra": "ignore"}

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
        if not self.deletion_tombstone_key:
            if _ENV == "dev":
                self.deletion_tombstone_key = self.fernet_key
            else:
                raise ValueError(
                    "DELETION_TOMBSTONE_KEY must be set in production and "
                    "escrowed independently of database backups"
                )
        self.deletion_tombstone_key_id = str(
            self.deletion_tombstone_key_id or ""
        ).strip()
        if not self.deletion_tombstone_key_id:
            if _ENV == "dev":
                self.deletion_tombstone_key_id = "dev-current"
            else:
                raise ValueError(
                    "DELETION_TOMBSTONE_KEY_ID must be set in production"
                )
        if any(ch.isspace() for ch in self.deletion_tombstone_key_id):
            raise ValueError("DELETION_TOMBSTONE_KEY_ID must not contain whitespace")
        deletion_keyring = self.deletion_tombstone_verification_keyring
        current_deletion_key = deletion_keyring.get(self.deletion_tombstone_key_id)
        if (
            current_deletion_key is not None
            and current_deletion_key != self.deletion_tombstone_key
        ):
            raise ValueError(
                "DELETION_TOMBSTONE_VERIFICATION_KEYS contains the current key "
                "id with a different secret"
            )
        if (
            _ENV == "production"
            and len(self.deletion_tombstone_key.encode("utf-8")) < 32
        ):
            raise ValueError(
                "DELETION_TOMBSTONE_KEY must contain at least 32 bytes in production"
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
        if _ENV == "production" and (
            self.openai_cli_enabled or self.claude_cli_enabled
        ):
            raise ValueError(
                "OPENAI_CLI_ENABLED and CLAUDE_CLI_ENABLED are local-only. "
                "Hosted production must use an isolated model service or a "
                "provider API backend."
            )

        self.signup_mode = str(self.signup_mode or "public").strip().lower()
        if self.signup_mode not in {"public", "invite_only", "closed"}:
            raise ValueError(
                "SIGNUP_MODE must be 'public', 'invite_only', or 'closed'"
            )
        if _ENV == "production" and self.signup_mode == "public":
            raise ValueError(
                "Production signup cannot fail open; set SIGNUP_MODE=invite_only "
                "or SIGNUP_MODE=closed"
            )
        if (
            _ENV == "production"
            and self.signup_mode == "invite_only"
            and not str(self.admin_secret or "").strip()
        ):
            raise ValueError(
                "ADMIN_SECRET must be set for production invite-only signup"
            )
        self.claim_audit_execution_mode = str(
            self.claim_audit_execution_mode or ""
        ).strip().lower()
        if self.claim_audit_execution_mode not in {"celery", "inline"}:
            raise ValueError(
                "CLAIM_AUDIT_EXECUTION_MODE must be 'celery' or 'inline'"
            )
        if _ENV == "production" and self.claim_audit_execution_mode != "celery":
            raise ValueError(
                "Production Claim Audit must use the Celery worker lifecycle"
            )
        if not 30 <= int(self.claim_audit_registered_timeout_seconds) <= 12 * 60 * 60:
            raise ValueError(
                "CLAIM_AUDIT_REGISTERED_TIMEOUT_SECONDS must be between 30 and 43200"
            )
        if not 60 <= int(self.claim_audit_worker_lease_seconds) <= 15 * 60:
            raise ValueError(
                "CLAIM_AUDIT_WORKER_LEASE_SECONDS must be between 60 and 900"
            )
        if not 5 <= int(self.claim_audit_heartbeat_seconds) < int(
            self.claim_audit_worker_lease_seconds
        ) / 2:
            raise ValueError(
                "CLAIM_AUDIT_HEARTBEAT_SECONDS must be at least 5 and less "
                "than half CLAIM_AUDIT_WORKER_LEASE_SECONDS"
            )
        if not 1 <= int(self.claim_audit_max_active_per_user) <= 20:
            raise ValueError(
                "CLAIM_AUDIT_MAX_ACTIVE_PER_USER must be between 1 and 20"
            )
        if not 3600 <= int(self.artifact_cleanup_grace_seconds) <= 7 * 24 * 60 * 60:
            raise ValueError(
                "ARTIFACT_CLEANUP_GRACE_SECONDS must be between 3600 and 604800"
            )
        if not 1 <= int(self.product_analytics_retention_days) <= 365:
            raise ValueError(
                "PRODUCT_ANALYTICS_RETENTION_DAYS must be between 1 and 365"
            )
        if _ENV == "production" and int(self.product_analytics_retention_days) > 30:
            raise ValueError(
                "Production product analytics retention cannot exceed 30 days"
            )
        if _ENV == "production":
            missing_privacy_fields = [
                name
                for name, value in (
                    ("PRIVACY_OPERATOR_NAME", self.privacy_operator_name),
                    ("PRIVACY_CONTACT", self.privacy_contact),
                    ("PRIVACY_JURISDICTION", self.privacy_jurisdiction),
                )
                if not str(value or "").strip()
            ]
            if missing_privacy_fields:
                raise ValueError(
                    "A production user entry point requires "
                    + ", ".join(missing_privacy_fields)
                )

        self.evidence_signing_key_id = str(
            self.evidence_signing_key_id or ""
        ).strip()
        if not self.evidence_signing_key:
            if _ENV == "dev":
                import secrets as _s
                import logging as _log

                self.evidence_signing_key = _s.token_hex(32)
                self.evidence_signing_key_id = (
                    self.evidence_signing_key_id or "dev-ephemeral"
                )
                _log.getLogger(__name__).warning(
                    "EVIDENCE_SIGNING_KEY not set — using a separate random dev "
                    "key (paper evidence won't survive restarts)"
                )
            else:
                raise ValueError(
                    "EVIDENCE_SIGNING_KEY must be set in production so scientific "
                    "evidence remains verifiable across restarts and JWT rotation."
                )
        if not self.evidence_signing_key_id:
            if _ENV == "dev":
                self.evidence_signing_key_id = "dev-ephemeral"
            else:
                raise ValueError(
                    "EVIDENCE_SIGNING_KEY_ID must be set in production."
                )
        if any(ch.isspace() for ch in self.evidence_signing_key_id):
            raise ValueError("EVIDENCE_SIGNING_KEY_ID must not contain whitespace")
        if _ENV == "production" and self.evidence_signing_key == self.jwt_secret:
            raise ValueError(
                "EVIDENCE_SIGNING_KEY must be independent from JWT_SECRET in production."
            )
        keyring = self.evidence_verification_keyring
        current_in_ring = keyring.get(self.evidence_signing_key_id)
        if current_in_ring is not None and current_in_ring != self.evidence_signing_key:
            raise ValueError(
                "EVIDENCE_VERIFICATION_KEYS contains the current key id with a "
                "different secret"
            )
        if (
            _ENV == "production"
            and len(self.evidence_signing_key.encode("utf-8")) < 32
        ):
            raise ValueError(
                "EVIDENCE_SIGNING_KEY must contain at least 32 bytes in production."
            )

    @property
    def deletion_tombstone_verification_keyring(self) -> dict[str, str]:
        """Parse retired deletion-ledger keys used only for verification."""

        raw = str(self.deletion_tombstone_verification_keys or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "DELETION_TOMBSTONE_VERIFICATION_KEYS must be a JSON object "
                "mapping key ids to secrets"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "DELETION_TOMBSTONE_VERIFICATION_KEYS must be a JSON object "
                "mapping key ids to secrets"
            )
        normalized: dict[str, str] = {}
        for raw_key_id, raw_secret in parsed.items():
            key_id = str(raw_key_id or "").strip()
            secret = str(raw_secret or "")
            if not key_id or any(ch.isspace() for ch in key_id) or not secret:
                raise ValueError(
                    "DELETION_TOMBSTONE_VERIFICATION_KEYS requires non-empty, "
                    "whitespace-free key ids and non-empty secrets"
                )
            normalized[key_id] = secret
        return normalized

    @property
    def evidence_verification_keyring(self) -> dict[str, str]:
        """Parse the retired evidence-key map without ever logging its values."""

        raw = str(self.evidence_verification_keys or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "EVIDENCE_VERIFICATION_KEYS must be a JSON object mapping key ids "
                "to secrets"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "EVIDENCE_VERIFICATION_KEYS must be a JSON object mapping key ids "
                "to secrets"
            )
        normalized: dict[str, str] = {}
        for raw_key_id, raw_secret in parsed.items():
            key_id = str(raw_key_id or "").strip()
            secret = str(raw_secret or "")
            if not key_id or any(ch.isspace() for ch in key_id) or not secret:
                raise ValueError(
                    "EVIDENCE_VERIFICATION_KEYS requires non-empty, whitespace-free "
                    "key ids and non-empty secrets"
                )
            normalized[key_id] = secret
        return normalized

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
