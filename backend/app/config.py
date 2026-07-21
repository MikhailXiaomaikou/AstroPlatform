import base64
import binascii
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from pydantic_settings import BaseSettings

_PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

_ENV = os.getenv("ENV", "dev").strip().lower()
if _ENV == "prod":
    _ENV = "production"

_APP_ROLES = {"api", "migration", "control_worker", "beat", "science_worker"}
_SCIENCE_EXECUTION_BACKENDS = {"celery", "https_worker"}
_FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_IMAGE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}", re.IGNORECASE)

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


def _runtime_git_sha() -> str:
    """Return the deployed revision only when it is a complete Git SHA."""

    raw = (
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or os.getenv("TOOL_VERSION")
        or ""
    )
    commit = str(raw).strip().lower()
    return commit if _FULL_GIT_SHA.fullmatch(commit) else ""


def _decode_ed25519_key(name: str, value: str) -> bytes:
    """Decode one canonical base64 Ed25519 seed/public key."""

    try:
        decoded = base64.b64decode(str(value or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{name} must be valid base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"{name} must encode exactly 32 bytes")
    return decoded


def _secret_reuses_ed25519_seed(seed: bytes, value: str) -> bool:
    """Detect direct or base64-encoded reuse across signing trust domains."""

    secret = str(value or "")
    if not secret:
        return False
    if secret.encode("utf-8") == seed:
        return True
    try:
        return base64.b64decode(secret, validate=True) == seed
    except (binascii.Error, ValueError):
        return False


class Settings(BaseSettings):
    # Each process declares a least-privilege startup role. The default remains
    # API for local development and backwards compatibility; production
    # manifests set APP_ROLE explicitly so missing role-specific configuration
    # fails before the process starts serving work.
    app_role: str = "" if _ENV == "production" else "api"
    science_execution_backend: str = "celery"

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
    research_workspace_enabled: bool = False
    arxiv_reader_enabled: bool = False
    union3_reproduction_enabled: bool = False
    evidence_pack_v2_enabled: bool = False
    local_science_worker_enabled: bool = False
    workflow_registry_v2_enabled: bool = False
    foundry_gap_tracking_enabled: bool = False
    foundry_ai_drafting_enabled: bool = False
    foundry_auto_demo_enabled: bool = False
    foundry_candidate_catalog_enabled: bool = False
    foundry_source_materialization_enabled: bool = False
    foundry_registration_enabled: bool = False
    # AI drafting and candidate execution are separate trust domains.  The
    # draft callback is used only by a host-side CI ingestion step after the
    # AI process has exited; it must never be an inference-provider key.
    foundry_draft_result_secret: str = ""
    foundry_draft_dispatch_backend: str = "disabled"
    foundry_draft_github_token: str = ""
    foundry_draft_github_repository: str = ""
    foundry_draft_github_workflow: str = "foundry-candidate-draft.yml"
    foundry_draft_github_ref: str = "main"
    foundry_validation_result_secret: str = ""
    foundry_validation_dispatch_backend: str = "disabled"
    foundry_validation_github_token: str = ""
    foundry_validation_github_repository: str = ""
    foundry_validation_github_workflow: str = "foundry-candidate-demo.yml"
    foundry_validation_github_ref: str = "main"
    # Source materialization has its own protected-workflow trust domain. Only
    # the public Ed25519 keys and a narrow callback bearer exist on Render.
    foundry_materialization_result_secret: str = ""
    foundry_materialization_attestation_verification_keys: str = ""
    foundry_materialization_dispatch_backend: str = "disabled"
    foundry_materialization_github_token: str = ""
    foundry_materialization_github_repository: str = ""
    foundry_materialization_github_workflow: str = "foundry-materialize-candidate.yml"
    foundry_materialization_finalize_github_workflow: str = "foundry-finalize-materialization.yml"
    foundry_materialization_github_ref: str = "main"
    # Success and failure callbacks are deliberately separate.  The failure
    # credential lives in an automatic, main-only Environment and can only
    # close an attempt; it cannot submit a formal build attestation.
    foundry_formal_build_result_secret: str = ""
    foundry_formal_build_failure_result_secret: str = ""
    foundry_formal_build_oidc_subject: str = ""
    # Dedicated public verification keys for protected formal-build receipts.
    # The matching private key exists only in the protected GitHub Environment
    # and is never the formal Registry signing key.
    foundry_formal_build_attestation_verification_keys: str = ""
    foundry_formal_build_dispatch_backend: str = "disabled"
    foundry_formal_build_github_token: str = ""
    foundry_formal_build_github_repository: str = ""
    foundry_formal_build_github_workflow: str = "foundry-formal-worker.yml"
    foundry_formal_build_github_ref: str = "main"
    # Narrow callback credential used only after the offline Registry signer.
    # This is not the Ed25519 signing key and cannot activate a live process.
    foundry_registry_import_result_secret: str = ""
    # Read-only credential used only to fetch one exact, hash-bound pending
    # release request. It is separate from both signer and import credentials.
    foundry_registry_export_secret: str = ""
    # Protected deployment workflow credential. It can export an already
    # verified public import and append an activation receipt, but cannot sign.
    foundry_registry_activation_result_secret: str = ""
    foundry_registry_dispatch_backend: str = "disabled"
    foundry_registry_github_token: str = ""
    foundry_registry_github_repository: str = ""
    foundry_registry_github_workflow: str = "foundry-registry-release.yml"
    foundry_registry_github_ref: str = "main"
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

    # The HTTPS science worker is deliberately independent from the control
    # plane's database, Redis and runtime secrets. These fields are dark until
    # APP_ROLE=science_worker is selected by its dedicated image/entrypoint.
    science_control_plane_url: str = ""
    science_worker_node_key: str = ""
    science_worker_cache_dir: str = str(_PROJECT_DIR / "data" / "science-worker")

    # HTTPS-worker task envelopes use a dedicated Ed25519 key, never an
    # Evidence Pack or login-token key. The API owns the private seed; science
    # workers receive only the current public key plus retired verification
    # keys needed during controlled rotation.
    worker_task_signing_private_key: str = ""
    worker_task_signing_key_id: str = ""
    worker_task_signing_public_key: str = ""
    worker_task_verification_keys: str = ""

    # Evidence Pack v2 has a second independent Ed25519 trust domain. These
    # fields intentionally do not reuse either the legacy HMAC Evidence key or
    # the HTTPS-worker task-envelope signing key.
    evidence_v2_signing_private_key: str = ""
    evidence_v2_signing_key_id: str = ""
    evidence_v2_signing_public_key: str = ""
    evidence_v2_verification_keys: str = ""

    # Formal workflow registry releases use a third Ed25519 trust domain.
    # Candidate-generation and Demo services never receive this private seed.
    workflow_registry_signing_private_key: str = ""
    workflow_registry_signing_key_id: str = ""
    workflow_registry_signing_public_key: str = ""
    workflow_registry_verification_keys: str = ""

    # Docker image digest for reproducibility
    docker_image_digest: str = ""

    # Runtime-only local keys (CLI commands and the Bot Console bridge) are
    # intentionally read with os.getenv at call time. Ignore those additional
    # keys when a self-hoster keeps them beside declared Settings fields in a
    # local .env file.
    model_config = {"env_file": ".env", "extra": "ignore"}

    def __init__(self, **kwargs):
        role_contract_explicit = (
            not kwargs or "app_role" in kwargs or "APP_ROLE" in os.environ
        )
        super().__init__(**kwargs)

        self.app_role = str(self.app_role or "").strip().lower()
        if not self.app_role:
            if _ENV == "production":
                raise ValueError("APP_ROLE must be set explicitly in production")
            self.app_role = "api"
        if self.app_role not in _APP_ROLES:
            raise ValueError(
                "APP_ROLE must be one of api, migration, control_worker, beat, "
                "or science_worker"
            )
        self.science_execution_backend = str(
            self.science_execution_backend or "celery"
        ).strip().lower()
        if self.science_execution_backend not in _SCIENCE_EXECUTION_BACKENDS:
            raise ValueError(
                "SCIENCE_EXECUTION_BACKEND must be 'celery' or 'https_worker'"
            )

        # Auto-fix PostgreSQL URL for async driver
        if self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        elif self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace(
                "postgres://", "postgresql+asyncpg://", 1
            )

        # Explicit APP_ROLE turns on the deployment startup contract. Keeping
        # the legacy implicit API default avoids surprising library callers,
        # while every checked-in production manifest opts into strict checks.
        if _ENV == "production" and role_contract_explicit:
            commit = _runtime_git_sha()
            if not commit:
                raise ValueError(
                    f"APP_ROLE={self.app_role} requires a full 40-character Git "
                    "SHA from RENDER_GIT_COMMIT, GIT_COMMIT, or TOOL_VERSION"
                )
            if self.app_role in {"api", "migration", "control_worker", "beat"}:
                if not self.database_url.startswith("postgresql+asyncpg://"):
                    raise ValueError(
                        f"APP_ROLE={self.app_role} requires a production "
                        "PostgreSQL DATABASE_URL"
                    )
            if self.app_role in {"api", "control_worker", "beat"}:
                redis = urlparse(self.redis_url)
                if redis.scheme not in {"redis", "rediss"} or redis.hostname in {
                    None,
                    "localhost",
                    "127.0.0.1",
                    "::1",
                }:
                    raise ValueError(
                        f"APP_ROLE={self.app_role} requires a non-local REDIS_URL"
                    )

        # Migration and Beat must not depend on HTTP, invitation, encryption,
        # deletion, evidence, storage or model-provider secrets merely to boot.
        if self.app_role in {"migration", "beat"}:
            return

        if self.app_role == "science_worker":
            if (
                _ENV == "production"
                and self.science_execution_backend != "https_worker"
            ):
                raise ValueError(
                    "APP_ROLE=science_worker requires "
                    "SCIENCE_EXECUTION_BACKEND=https_worker in production"
                )
            if _ENV == "production":
                control_plane = urlparse(self.science_control_plane_url)
                if control_plane.scheme != "https" or not control_plane.netloc:
                    raise ValueError(
                        "APP_ROLE=science_worker requires an HTTPS "
                        "SCIENCE_CONTROL_PLANE_URL"
                    )
                if len(self.science_worker_node_key.encode("utf-8")) < 32:
                    raise ValueError(
                        "APP_ROLE=science_worker requires a "
                        "SCIENCE_WORKER_NODE_KEY of at least 32 bytes"
                    )
                if not str(self.science_worker_cache_dir or "").strip():
                    raise ValueError(
                        "APP_ROLE=science_worker requires SCIENCE_WORKER_CACHE_DIR"
                    )
                if str(self.worker_task_signing_private_key or "").strip():
                    raise ValueError(
                        "APP_ROLE=science_worker must not receive "
                        "WORKER_TASK_SIGNING_PRIVATE_KEY"
                    )
                self.worker_task_signing_key_id = str(
                    self.worker_task_signing_key_id or ""
                ).strip()
                if not self.worker_task_signing_key_id or any(
                    ch.isspace() for ch in self.worker_task_signing_key_id
                ):
                    raise ValueError(
                        "APP_ROLE=science_worker requires a whitespace-free "
                        "WORKER_TASK_SIGNING_KEY_ID"
                    )
                verification_keys = self.worker_task_verification_keyring
                current_public_key = str(
                    self.worker_task_signing_public_key
                    or verification_keys.get(self.worker_task_signing_key_id, "")
                ).strip()
                if not current_public_key:
                    raise ValueError(
                        "APP_ROLE=science_worker requires "
                        "WORKER_TASK_SIGNING_PUBLIC_KEY or a current entry in "
                        "WORKER_TASK_VERIFICATION_KEYS"
                    )
                _decode_ed25519_key(
                    "WORKER_TASK_SIGNING_PUBLIC_KEY", current_public_key
                )
                keyring_current = verification_keys.get(
                    self.worker_task_signing_key_id
                )
                if keyring_current and keyring_current != current_public_key:
                    raise ValueError(
                        "WORKER_TASK_VERIFICATION_KEYS contains the current key id "
                        "with a different public key"
                    )
                self.worker_task_signing_public_key = current_public_key
            return

        if self.app_role == "api" and not self.jwt_secret:
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
        if self.app_role == "api" and not self.fernet_key:
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
                if self.fernet_key:
                    self.deletion_tombstone_key = self.fernet_key
                else:
                    import secrets as _s

                    self.deletion_tombstone_key = _s.token_hex(32)
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
        if (
            self.app_role == "api"
            and self.signup_mode not in {"public", "invite_only", "closed"}
        ):
            raise ValueError(
                "SIGNUP_MODE must be 'public', 'invite_only', or 'closed'"
            )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.signup_mode == "public"
        ):
            raise ValueError(
                "Production signup cannot fail open; set SIGNUP_MODE=invite_only "
                "or SIGNUP_MODE=closed"
            )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.signup_mode == "invite_only"
            and not str(self.admin_secret or "").strip()
        ):
            raise ValueError(
                "ADMIN_SECRET must be set for production invite-only signup"
            )
        self.claim_audit_execution_mode = str(
            self.claim_audit_execution_mode or ""
        ).strip().lower()
        if self.claim_audit_execution_mode not in {"celery", "inline", "https_worker"}:
            raise ValueError(
                "CLAIM_AUDIT_EXECUTION_MODE must be 'celery', 'inline', or "
                "'https_worker'"
            )
        if _ENV == "production" and self.claim_audit_execution_mode == "inline":
            raise ValueError(
                "Production Claim Audit cannot use the inline worker lifecycle"
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
        if self.app_role == "api" and _ENV == "production":
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
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.evidence_signing_key == self.jwt_secret
        ):
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

        if (
            self.app_role == "control_worker"
            and _ENV == "production"
            and str(self.worker_task_signing_private_key or "").strip()
        ):
            raise ValueError(
                "APP_ROLE=control_worker must not receive "
                "WORKER_TASK_SIGNING_PRIVATE_KEY"
            )

        control_worker_verification_required = (
            self.app_role == "control_worker"
            and _ENV == "production"
            and self.science_execution_backend == "https_worker"
        )
        control_worker_verification_configured = (
            self.app_role == "control_worker"
            and any(
                str(value or "").strip()
                for value in (
                    self.worker_task_signing_key_id,
                    self.worker_task_signing_public_key,
                    self.worker_task_verification_keys,
                )
            )
        )
        if control_worker_verification_required or control_worker_verification_configured:
            self.worker_task_signing_key_id = str(
                self.worker_task_signing_key_id or ""
            ).strip()
            if not self.worker_task_signing_key_id or any(
                ch.isspace() for ch in self.worker_task_signing_key_id
            ):
                raise ValueError(
                    "APP_ROLE=control_worker requires a whitespace-free "
                    "WORKER_TASK_SIGNING_KEY_ID"
                )
            verification_keys = self.worker_task_verification_keyring
            current_public_key = str(
                self.worker_task_signing_public_key
                or verification_keys.get(self.worker_task_signing_key_id, "")
            ).strip()
            if not current_public_key:
                raise ValueError(
                    "APP_ROLE=control_worker requires "
                    "WORKER_TASK_SIGNING_PUBLIC_KEY or a current entry in "
                    "WORKER_TASK_VERIFICATION_KEYS"
                )
            _decode_ed25519_key(
                "WORKER_TASK_SIGNING_PUBLIC_KEY", current_public_key
            )
            keyring_current = verification_keys.get(
                self.worker_task_signing_key_id
            )
            if keyring_current and keyring_current != current_public_key:
                raise ValueError(
                    "WORKER_TASK_VERIFICATION_KEYS contains the current key id "
                    "with a different public key"
                )
            self.worker_task_signing_public_key = current_public_key

        task_signing_configured = any(
            str(value or "").strip()
            for value in (
                self.worker_task_signing_private_key,
                self.worker_task_signing_key_id,
                self.worker_task_signing_public_key,
                self.worker_task_verification_keys,
            )
        )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.local_science_worker_enabled
            and self.science_execution_backend != "https_worker"
        ):
            raise ValueError(
                "LOCAL_SCIENCE_WORKER_ENABLED requires "
                "SCIENCE_EXECUTION_BACKEND=https_worker"
            )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.local_science_worker_enabled
            and _IMAGE_DIGEST.fullmatch(str(self.docker_image_digest or "")) is None
        ):
            raise ValueError(
                "LOCAL_SCIENCE_WORKER_ENABLED requires DOCKER_IMAGE_DIGEST to "
                "pin the signed multi-architecture worker image"
            )
        task_signing_required = (
            self.app_role == "api"
            and _ENV == "production"
            and (
                self.science_execution_backend == "https_worker"
                or self.local_science_worker_enabled
            )
        )
        if self.app_role == "api" and (
            task_signing_required or task_signing_configured
        ):
            private_seed = _decode_ed25519_key(
                "WORKER_TASK_SIGNING_PRIVATE_KEY",
                self.worker_task_signing_private_key,
            )
            self.worker_task_signing_key_id = str(
                self.worker_task_signing_key_id or ""
            ).strip()
            if not self.worker_task_signing_key_id or any(
                ch.isspace() for ch in self.worker_task_signing_key_id
            ):
                raise ValueError(
                    "WORKER_TASK_SIGNING_KEY_ID must be non-empty and contain no "
                    "whitespace"
                )
            if any(
                _secret_reuses_ed25519_seed(private_seed, secret)
                for secret in (
                    self.jwt_secret,
                    self.deletion_tombstone_key,
                    self.evidence_signing_key,
                )
            ):
                raise ValueError(
                    "WORKER_TASK_SIGNING_PRIVATE_KEY must be independent from "
                    "JWT, deletion, and Evidence signing keys"
                )

            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )

            derived_public_key = base64.b64encode(
                Ed25519PrivateKey.from_private_bytes(private_seed)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            ).decode("ascii")
            configured_public_key = str(
                self.worker_task_signing_public_key or ""
            ).strip()
            if configured_public_key:
                _decode_ed25519_key(
                    "WORKER_TASK_SIGNING_PUBLIC_KEY", configured_public_key
                )
                if configured_public_key != derived_public_key:
                    raise ValueError(
                        "WORKER_TASK_SIGNING_PUBLIC_KEY does not match the private "
                        "signing seed"
                    )
            self.worker_task_signing_public_key = derived_public_key
            verification_keys = self.worker_task_verification_keyring
            current_public_key = verification_keys.get(
                self.worker_task_signing_key_id
            )
            if current_public_key and current_public_key != derived_public_key:
                raise ValueError(
                    "WORKER_TASK_VERIFICATION_KEYS contains the current key id "
                    "with a different public key"
                )

        # API verifies/publishes trust roots; the control worker owns the
        # deterministic finalizer and therefore also needs the v2 signing
        # material. Beat and migration remain free of Evidence secrets.
        if self.app_role in {"api", "control_worker"} and self.evidence_pack_v2_enabled:
            evidence_v2_seed = _decode_ed25519_key(
                "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
                self.evidence_v2_signing_private_key,
            )
            self.evidence_v2_signing_key_id = str(
                self.evidence_v2_signing_key_id or ""
            ).strip()
            if not self.evidence_v2_signing_key_id or any(
                ch.isspace() for ch in self.evidence_v2_signing_key_id
            ):
                raise ValueError(
                    "EVIDENCE_V2_SIGNING_KEY_ID must be non-empty and contain no "
                    "whitespace"
                )
            if any(
                _secret_reuses_ed25519_seed(evidence_v2_seed, secret)
                for secret in (
                    self.jwt_secret,
                    self.deletion_tombstone_key,
                    self.evidence_signing_key,
                    self.worker_task_signing_private_key,
                )
            ):
                raise ValueError(
                    "EVIDENCE_V2_SIGNING_PRIVATE_KEY must be independent from "
                    "login, deletion, legacy Evidence, and worker-task keys"
                )
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )

            derived_evidence_v2_public_key = base64.b64encode(
                Ed25519PrivateKey.from_private_bytes(evidence_v2_seed)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            ).decode("ascii")
            configured_evidence_v2_public_key = str(
                self.evidence_v2_signing_public_key or ""
            ).strip()
            if configured_evidence_v2_public_key:
                _decode_ed25519_key(
                    "EVIDENCE_V2_SIGNING_PUBLIC_KEY",
                    configured_evidence_v2_public_key,
                )
                if (
                    configured_evidence_v2_public_key
                    != derived_evidence_v2_public_key
                ):
                    raise ValueError(
                        "EVIDENCE_V2_SIGNING_PUBLIC_KEY does not match the private "
                        "signing seed"
                    )
            self.evidence_v2_signing_public_key = derived_evidence_v2_public_key
            verification_keys = self.evidence_v2_verification_keyring
            current_record = verification_keys.get(self.evidence_v2_signing_key_id)
            current_public_key = (
                str(current_record.get("public_key") or "")
                if current_record is not None
                else ""
            )
            if (
                current_public_key
                and current_public_key != derived_evidence_v2_public_key
            ):
                raise ValueError(
                    "EVIDENCE_V2_VERIFICATION_KEYS contains the current key id "
                    "with a different public key"
                )
            if current_record is not None and current_record.get("status") != "active":
                raise ValueError(
                    "EVIDENCE_V2_VERIFICATION_KEYS cannot mark the current signing "
                    "key as retired or revoked"
                )

        if self.app_role == "api" and str(
            self.workflow_registry_signing_private_key or ""
        ).strip():
            raise ValueError(
                "APP_ROLE=api must not receive WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY; "
                "formal releases are signed by an offline protected release job"
            )
        configured_registry_public_key = str(
            self.workflow_registry_signing_public_key or ""
        ).strip()
        if configured_registry_public_key:
            _decode_ed25519_key(
                "WORKFLOW_REGISTRY_SIGNING_PUBLIC_KEY",
                configured_registry_public_key,
            )
            if configured_registry_public_key in {
                str(self.worker_task_signing_public_key or "").strip(),
                str(self.evidence_v2_signing_public_key or "").strip(),
            }:
                raise ValueError(
                    "WORKFLOW_REGISTRY_SIGNING_PUBLIC_KEY must not reuse Worker "
                    "or Evidence trust roots"
                )
        registry_key_id = str(self.workflow_registry_signing_key_id or "").strip()
        if registry_key_id and any(ch.isspace() for ch in registry_key_id):
            raise ValueError(
                "WORKFLOW_REGISTRY_SIGNING_KEY_ID must contain no whitespace"
            )
        current_registry_key = (
            self.workflow_registry_verification_keyring.get(registry_key_id)
            if registry_key_id
            else None
        )
        if (
            current_registry_key
            and configured_registry_public_key
            and current_registry_key != configured_registry_public_key
        ):
            raise ValueError(
                "WORKFLOW_REGISTRY_VERIFICATION_KEYS contains the current key id "
                "with a different public key"
            )

        if (
            _ENV == "production"
            and self.app_role != "migration"
            and self.workflow_registry_v2_enabled
        ):
            release_path = str(
                os.getenv("WORKFLOW_REGISTRY_RELEASE_PATH") or ""
            ).strip()
            keyring_json = str(
                os.getenv("WORKFLOW_REGISTRY_TRUSTED_KEYRING_JSON") or ""
            ).strip()
            keyring_path = str(
                os.getenv("WORKFLOW_REGISTRY_TRUSTED_KEYRING_PATH") or ""
            ).strip()
            packaged_activation = (
                Path(__file__).resolve().parent
                / "registry_releases"
                / "activation-manifest.json"
            ).is_file()
            explicit_activation_invalid = bool(release_path) and (
                bool(keyring_json) == bool(keyring_path)
            )
            partial_explicit_activation = any(
                (release_path, keyring_json, keyring_path)
            ) and not release_path
            if (
                explicit_activation_invalid
                or partial_explicit_activation
                or (not release_path and not packaged_activation)
            ):
                raise ValueError(
                    "WORKFLOW_REGISTRY_V2_ENABLED requires either a protected "
                    "repository-baked activation bundle or one fixed signed "
                    "WORKFLOW_REGISTRY_RELEASE_PATH with exactly one trusted "
                    "Registry keyring source in production"
                )

        # Only an offline/non-API release process may validate private signing
        # material. Candidate Catalog and normal API processes never read it.
        if self.app_role != "api" and str(
            self.workflow_registry_signing_private_key or ""
        ).strip():
            registry_seed = _decode_ed25519_key(
                "WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY",
                self.workflow_registry_signing_private_key,
            )
            self.workflow_registry_signing_key_id = str(
                self.workflow_registry_signing_key_id or ""
            ).strip()
            if not self.workflow_registry_signing_key_id or any(
                ch.isspace() for ch in self.workflow_registry_signing_key_id
            ):
                raise ValueError(
                    "WORKFLOW_REGISTRY_SIGNING_KEY_ID must be non-empty and "
                    "contain no whitespace"
                )
            if any(
                _secret_reuses_ed25519_seed(registry_seed, secret)
                for secret in (
                    self.jwt_secret,
                    self.deletion_tombstone_key,
                    self.evidence_signing_key,
                    self.worker_task_signing_private_key,
                    self.evidence_v2_signing_private_key,
                )
            ):
                raise ValueError(
                    "WORKFLOW_REGISTRY_SIGNING_PRIVATE_KEY must be independent "
                    "from login, deletion, Evidence, and Worker keys"
                )
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )

            derived_registry_public_key = base64.b64encode(
                Ed25519PrivateKey.from_private_bytes(registry_seed)
                .public_key()
                .public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            ).decode("ascii")
            if configured_registry_public_key:
                if configured_registry_public_key != derived_registry_public_key:
                    raise ValueError(
                        "WORKFLOW_REGISTRY_SIGNING_PUBLIC_KEY does not match the "
                        "private signing seed"
                    )
            self.workflow_registry_signing_public_key = derived_registry_public_key
            current_registry_key = self.workflow_registry_verification_keyring.get(
                self.workflow_registry_signing_key_id
            )
            if (
                current_registry_key
                and current_registry_key != derived_registry_public_key
            ):
                raise ValueError(
                    "WORKFLOW_REGISTRY_VERIFICATION_KEYS contains the current key "
                    "id with a different public key"
                )

        self.foundry_draft_dispatch_backend = str(
            self.foundry_draft_dispatch_backend or "disabled"
        ).strip().lower()
        if self.foundry_draft_dispatch_backend not in {
            "disabled",
            "github_actions",
        }:
            raise ValueError(
                "FOUNDRY_DRAFT_DISPATCH_BACKEND must be disabled or github_actions"
            )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.foundry_ai_drafting_enabled
        ):
            if len(self.foundry_draft_result_secret) < 32:
                raise ValueError(
                    "FOUNDRY_DRAFT_RESULT_SECRET must contain at least 32 "
                    "characters when AI drafting is enabled"
                )
            if (
                self.foundry_draft_dispatch_backend != "github_actions"
                or len(self.foundry_draft_github_token) < 32
                or self.foundry_draft_github_repository.count("/") != 1
                or str(self.foundry_draft_github_ref or "").strip() != "main"
                or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+\.ya?ml",
                    str(self.foundry_draft_github_workflow or ""),
                )
            ):
                raise ValueError(
                    "AI drafting requires a narrow GitHub Actions dispatch "
                    "configuration fixed to the protected main branch"
                )
        if self.foundry_draft_result_secret and any(
            self.foundry_draft_result_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.platform_deepseek_api_key,
                self.foundry_validation_result_secret,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_DRAFT_RESULT_SECRET must be independent from AI, "
                "admin, Demo, Worker, Evidence, and Registry credentials"
            )
        if self.foundry_draft_github_token and any(
            self.foundry_draft_github_token == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.platform_deepseek_api_key,
                self.foundry_draft_result_secret,
                self.foundry_validation_result_secret,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_DRAFT_GITHUB_TOKEN must be independent from AI, "
                "admin, callback, Worker, Evidence, and Registry credentials"
            )

        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.foundry_auto_demo_enabled
            and len(self.foundry_validation_result_secret) < 32
        ):
            raise ValueError(
                "FOUNDRY_VALIDATION_RESULT_SECRET must contain at least 32 "
                "characters when automatic Demo ingestion is enabled"
            )
        self.foundry_validation_dispatch_backend = str(
            self.foundry_validation_dispatch_backend or "disabled"
        ).strip().lower()
        if self.foundry_validation_dispatch_backend not in {
            "disabled",
            "github_actions",
        }:
            raise ValueError(
                "FOUNDRY_VALIDATION_DISPATCH_BACKEND must be disabled or github_actions"
            )
        if self.app_role == "api" and _ENV == "production" and self.foundry_auto_demo_enabled:
            if (
                self.foundry_validation_dispatch_backend != "github_actions"
                or len(self.foundry_validation_github_token) < 32
                or self.foundry_validation_github_repository.count("/") != 1
                or str(self.foundry_validation_github_ref or "").strip()
                != "main"
                or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+\.ya?ml",
                    str(self.foundry_validation_github_workflow or ""),
                )
            ):
                raise ValueError(
                    "Automatic Demo requires a narrow GitHub Actions dispatch "
                    "configuration fixed to the protected main branch"
                )
        if self.foundry_validation_result_secret and any(
            self.foundry_validation_result_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_VALIDATION_RESULT_SECRET must be independent from AI, "
                "admin, Worker, Evidence, and Registry credentials"
            )
        if self.foundry_validation_github_token and any(
            self.foundry_validation_github_token == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_VALIDATION_GITHUB_TOKEN must be independent from AI, "
                "admin, callback, Worker, Evidence, and Registry credentials"
            )
        self.foundry_materialization_dispatch_backend = str(
            self.foundry_materialization_dispatch_backend or "disabled"
        ).strip().lower()
        if self.foundry_materialization_dispatch_backend not in {
            "disabled",
            "github_actions",
        }:
            raise ValueError(
                "FOUNDRY_MATERIALIZATION_DISPATCH_BACKEND must be disabled or github_actions"
            )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.foundry_source_materialization_enabled
            and (
                len(self.foundry_materialization_result_secret) < 32
                or not self.foundry_materialization_attestation_verification_keyring
                or self.foundry_materialization_dispatch_backend != "github_actions"
                or len(self.foundry_materialization_github_token) < 32
                or self.foundry_materialization_github_repository.count("/") != 1
                or self.foundry_materialization_github_ref != "main"
                or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+\.ya?ml",
                    str(self.foundry_materialization_github_workflow or ""),
                )
                or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+\.ya?ml",
                    str(self.foundry_materialization_finalize_github_workflow or ""),
                )
            )
        ):
            raise ValueError(
                "Foundry source materialization requires a dedicated callback secret, "
                "public attestation keyring, and narrow protected-main GitHub dispatch"
            )
        if self.foundry_materialization_result_secret and any(
            self.foundry_materialization_result_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_validation_result_secret,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_export_secret,
                self.foundry_registry_import_result_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_MATERIALIZATION_RESULT_SECRET must be independent from "
                "AI, admin, Demo, formal-build, Worker, Evidence, and Registry credentials"
            )
        if self.foundry_materialization_github_token and any(
            self.foundry_materialization_github_token == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_validation_result_secret,
                self.foundry_materialization_result_secret,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_export_secret,
                self.foundry_registry_import_result_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_MATERIALIZATION_GITHUB_TOKEN must be independent from "
                "callbacks, AI, Worker, Evidence, and Registry credentials"
            )
        self.foundry_formal_build_dispatch_backend = str(
            self.foundry_formal_build_dispatch_backend or "disabled"
        ).strip().lower()
        if self.foundry_formal_build_dispatch_backend not in {
            "disabled",
            "github_actions",
        }:
            raise ValueError(
                "FOUNDRY_FORMAL_BUILD_DISPATCH_BACKEND must be disabled or "
                "github_actions"
            )
        self.foundry_registry_dispatch_backend = str(
            self.foundry_registry_dispatch_backend or "disabled"
        ).strip().lower()
        if self.foundry_registry_dispatch_backend not in {
            "disabled",
            "github_actions",
        }:
            raise ValueError(
                "FOUNDRY_REGISTRY_DISPATCH_BACKEND must be disabled or "
                "github_actions"
            )
        if (
            self.app_role == "api"
            and _ENV == "production"
            and self.foundry_registration_enabled
            and (
                len(self.foundry_formal_build_result_secret) < 32
                or len(self.foundry_formal_build_failure_result_secret) < 32
                or len(self.foundry_registry_import_result_secret) < 32
                or len(self.foundry_registry_export_secret) < 32
                or len(self.foundry_registry_activation_result_secret) < 32
                or not str(self.foundry_formal_build_oidc_subject or "").strip()
                or not self.foundry_formal_build_attestation_verification_keyring
                or not self.workflow_registry_verification_keyring
                or self.foundry_formal_build_dispatch_backend != "github_actions"
                or len(self.foundry_formal_build_github_token) < 32
                or self.foundry_formal_build_github_repository.count("/") != 1
                or str(self.foundry_formal_build_github_ref or "").strip()
                != "main"
                or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+\.ya?ml",
                    str(self.foundry_formal_build_github_workflow or ""),
                )
                or self.foundry_registry_dispatch_backend != "github_actions"
                or len(self.foundry_registry_github_token) < 32
                or self.foundry_registry_github_repository.count("/") != 1
                or str(self.foundry_registry_github_ref or "").strip() != "main"
                or not re.fullmatch(
                    r"[A-Za-z0-9_.-]+\.ya?ml",
                    str(self.foundry_registry_github_workflow or ""),
                )
            )
        ):
            raise ValueError(
                "Foundry registration requires dedicated 32-character formal-build "
                "success, formal-build failure, Registry-export, Registry-import, "
                "and Registry-activation callback secrets, an exact "
                "formal-build OIDC subject, public formal-build attestation and "
                "Registry verification keyrings, and narrow formal-build and "
                "Registry GitHub Actions dispatches fixed to protected main"
            )
        if self.foundry_formal_build_github_token and any(
            self.foundry_formal_build_github_token == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_export_secret,
                self.foundry_registry_import_result_secret,
                self.foundry_registry_github_token,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_FORMAL_BUILD_GITHUB_TOKEN must be independent from AI, "
                "admin, callback, Worker, Evidence, and Registry credentials"
            )
        if self.foundry_formal_build_result_secret and any(
            self.foundry_formal_build_result_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_formal_build_failure_result_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_FORMAL_BUILD_RESULT_SECRET must be independent from "
                "AI, admin, Demo, Worker, Evidence, and Registry credentials"
            )
        if self.foundry_formal_build_failure_result_secret and any(
            self.foundry_formal_build_failure_result_secret
            == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_materialization_result_secret,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_github_token,
                self.foundry_registry_export_secret,
                self.foundry_registry_import_result_secret,
                self.foundry_registry_activation_result_secret,
                self.foundry_registry_github_token,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_FORMAL_BUILD_FAILURE_RESULT_SECRET must be independent "
                "from success, AI, admin, Demo, Worker, Evidence, and Registry "
                "credentials"
            )
        if self.foundry_registry_import_result_secret and any(
            self.foundry_registry_import_result_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_export_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_REGISTRY_IMPORT_RESULT_SECRET must be independent from "
                "AI, admin, Demo, formal-build, Worker, Evidence, and Registry "
                "credentials"
            )
        if self.foundry_registry_export_secret and any(
            self.foundry_registry_export_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_import_result_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_REGISTRY_EXPORT_SECRET must be independent from AI, "
                "admin, Demo, formal-build, import, Worker, Evidence, and "
                "Registry credentials"
            )
        if self.foundry_registry_activation_result_secret and any(
            self.foundry_registry_activation_result_secret == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_export_secret,
                self.foundry_registry_import_result_secret,
                self.foundry_registry_github_token,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_REGISTRY_ACTIVATION_RESULT_SECRET must be independent "
                "from AI, admin, Demo, formal-build, import/export, Worker, "
                "Evidence, Registry, and GitHub credentials"
            )
        if self.foundry_registry_github_token and any(
            self.foundry_registry_github_token == str(secret or "")
            for secret in (
                self.jwt_secret,
                self.admin_secret,
                self.foundry_draft_result_secret,
                self.foundry_draft_github_token,
                self.foundry_validation_result_secret,
                self.foundry_validation_github_token,
                self.foundry_formal_build_result_secret,
                self.foundry_formal_build_failure_result_secret,
                self.foundry_registry_export_secret,
                self.foundry_registry_import_result_secret,
                self.platform_deepseek_api_key,
                self.worker_task_signing_private_key,
                self.evidence_v2_signing_private_key,
                self.workflow_registry_signing_private_key,
            )
            if secret
        ):
            raise ValueError(
                "FOUNDRY_REGISTRY_GITHUB_TOKEN must be independent from AI, "
                "admin, callback, Worker, Evidence, and Registry credentials"
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
    def worker_task_verification_keyring(self) -> dict[str, str]:
        """Parse retired Ed25519 task-verification keys without private data."""

        raw = str(self.worker_task_verification_keys or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "WORKER_TASK_VERIFICATION_KEYS must be a JSON object mapping key "
                "ids to base64 public keys"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "WORKER_TASK_VERIFICATION_KEYS must be a JSON object mapping key "
                "ids to base64 public keys"
            )
        normalized: dict[str, str] = {}
        for raw_key_id, raw_public_key in parsed.items():
            key_id = str(raw_key_id or "").strip()
            public_key = str(raw_public_key or "").strip()
            if not key_id or any(ch.isspace() for ch in key_id):
                raise ValueError(
                    "WORKER_TASK_VERIFICATION_KEYS requires non-empty, "
                    "whitespace-free key ids"
                )
            _decode_ed25519_key(
                f"WORKER_TASK_VERIFICATION_KEYS[{key_id}]", public_key
            )
            normalized[key_id] = public_key
        return normalized

    @property
    def evidence_v2_verification_keyring(self) -> dict[str, dict[str, object]]:
        """Parse Evidence v2 trust records, including rotation windows."""

        raw = str(self.evidence_v2_verification_keys or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "EVIDENCE_V2_VERIFICATION_KEYS must be a JSON object mapping key "
                "ids to public-key trust records"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "EVIDENCE_V2_VERIFICATION_KEYS must be a JSON object mapping key "
                "ids to public-key trust records"
            )
        normalized: dict[str, dict[str, object]] = {}
        for raw_key_id, raw_record in parsed.items():
            key_id = str(raw_key_id or "").strip()
            if not key_id or any(ch.isspace() for ch in key_id):
                raise ValueError(
                    "EVIDENCE_V2_VERIFICATION_KEYS requires non-empty, "
                    "whitespace-free key ids"
                )
            if not isinstance(raw_record, dict):
                raise ValueError(
                    f"EVIDENCE_V2_VERIFICATION_KEYS[{key_id}] must include "
                    "public_key, status, and any historical signing window"
                )
            public_key = str(raw_record.get("public_key") or "").strip()
            _decode_ed25519_key(
                f"EVIDENCE_V2_VERIFICATION_KEYS[{key_id}]", public_key
            )
            status = str(raw_record.get("status") or "").strip().lower()
            if status not in {"active", "retired", "revoked"}:
                raise ValueError(
                    f"EVIDENCE_V2_VERIFICATION_KEYS[{key_id}] has an invalid status"
                )
            not_before = raw_record.get("not_before")
            not_after = raw_record.get("not_after")
            if status == "retired" and (not not_before or not not_after):
                raise ValueError(
                    f"EVIDENCE_V2_VERIFICATION_KEYS[{key_id}] retired keys require "
                    "not_before and not_after"
                )
            normalized[key_id] = {
                "key_id": key_id,
                "algorithm": "ed25519",
                "public_key": public_key,
                "status": status,
                "not_before": not_before,
                "not_after": not_after,
            }
        return normalized

    @property
    def workflow_registry_verification_keyring(self) -> dict[str, str]:
        """Parse registry release verification keys without private material."""

        raw = str(self.workflow_registry_verification_keys or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "WORKFLOW_REGISTRY_VERIFICATION_KEYS must map key ids to base64 "
                "Ed25519 public keys"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "WORKFLOW_REGISTRY_VERIFICATION_KEYS must be a JSON object"
            )
        normalized: dict[str, str] = {}
        for raw_key_id, raw_public_key in parsed.items():
            key_id = str(raw_key_id or "").strip()
            public_key = str(raw_public_key or "").strip()
            if not key_id or any(ch.isspace() for ch in key_id):
                raise ValueError(
                    "WORKFLOW_REGISTRY_VERIFICATION_KEYS requires non-empty, "
                    "whitespace-free key ids"
                )
            _decode_ed25519_key(
                f"WORKFLOW_REGISTRY_VERIFICATION_KEYS[{key_id}]", public_key
            )
            normalized[key_id] = public_key
        return normalized

    @property
    def foundry_formal_build_attestation_verification_keyring(self) -> dict[str, str]:
        """Parse dedicated formal-build receipt verification keys.

        This trust domain is deliberately separate from the Registry signer,
        Worker task signer, Evidence signer, and callback bearer secret.
        """

        raw = str(
            self.foundry_formal_build_attestation_verification_keys or ""
        ).strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "FOUNDRY_FORMAL_BUILD_ATTESTATION_VERIFICATION_KEYS must map "
                "key ids to base64 Ed25519 public keys"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "FOUNDRY_FORMAL_BUILD_ATTESTATION_VERIFICATION_KEYS must be a "
                "JSON object"
            )
        normalized: dict[str, str] = {}
        for raw_key_id, raw_public_key in parsed.items():
            key_id = str(raw_key_id or "").strip()
            public_key = str(raw_public_key or "").strip()
            if not key_id or any(ch.isspace() for ch in key_id):
                raise ValueError(
                    "FOUNDRY_FORMAL_BUILD_ATTESTATION_VERIFICATION_KEYS requires "
                    "non-empty, whitespace-free key ids"
                )
            _decode_ed25519_key(
                "FOUNDRY_FORMAL_BUILD_ATTESTATION_VERIFICATION_KEYS"
                f"[{key_id}]",
                public_key,
            )
            normalized[key_id] = public_key
        return normalized

    @property
    def foundry_materialization_attestation_verification_keyring(self) -> dict[str, str]:
        """Parse public keys for the source-materialization trust domain."""

        raw = str(
            self.foundry_materialization_attestation_verification_keys or ""
        ).strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "FOUNDRY_MATERIALIZATION_ATTESTATION_VERIFICATION_KEYS must map "
                "key ids to base64 Ed25519 public keys"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                "FOUNDRY_MATERIALIZATION_ATTESTATION_VERIFICATION_KEYS must be a JSON object"
            )
        normalized: dict[str, str] = {}
        for raw_key_id, raw_public_key in parsed.items():
            key_id = str(raw_key_id or "").strip()
            public_key = str(raw_public_key or "").strip()
            if not key_id or any(ch.isspace() for ch in key_id):
                raise ValueError(
                    "FOUNDRY_MATERIALIZATION_ATTESTATION_VERIFICATION_KEYS requires "
                    "non-empty, whitespace-free key ids"
                )
            _decode_ed25519_key(
                "FOUNDRY_MATERIALIZATION_ATTESTATION_VERIFICATION_KEYS"
                f"[{key_id}]",
                public_key,
            )
            normalized[key_id] = public_key
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
