"""Least-privilege startup contracts for every deployed process role."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
FULL_SHA = "a" * 40
PUBLIC_KEY = base64.b64encode(b"p" * 32).decode("ascii")

_ROLE_KEYS = {
    "ADMIN_SECRET",
    "APP_ROLE",
    "ARXIV_READER_ENABLED",
    "DATABASE_URL",
    "DELETION_TOMBSTONE_KEY",
    "DELETION_TOMBSTONE_KEY_ID",
    "DELETION_TOMBSTONE_VERIFICATION_KEYS",
    "EVIDENCE_PACK_V2_ENABLED",
    "ENV",
    "EVIDENCE_SIGNING_KEY",
    "EVIDENCE_SIGNING_KEY_ID",
    "EVIDENCE_VERIFICATION_KEYS",
    "EVIDENCE_V2_SIGNING_KEY_ID",
    "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
    "EVIDENCE_V2_SIGNING_PUBLIC_KEY",
    "EVIDENCE_V2_VERIFICATION_KEYS",
    "FERNET_KEY",
    "GIT_COMMIT",
    "JWT_SECRET",
    "LOCAL_SCIENCE_WORKER_ENABLED",
    "PRIVACY_CONTACT",
    "PRIVACY_JURISDICTION",
    "PRIVACY_OPERATOR_NAME",
    "POSTGRES_BACKUP_ENABLED",
    "POSTGRES_BACKUP_ENCRYPTION_KEY",
    "POSTGRES_BACKUP_ENCRYPTION_KEY_ID",
    "POSTGRES_BACKUP_RETENTION_DAYS",
    "POSTGRES_BACKUP_S3_PREFIX",
    "RESEARCH_WORKSPACE_ENABLED",
    "REDIS_URL",
    "RENDER_GIT_COMMIT",
    "SANDBOX_BACKEND",
    "SCIENCE_CONTROL_PLANE_URL",
    "SCIENCE_EXECUTION_BACKEND",
    "SCIENCE_WORKER_CACHE_DIR",
    "SCIENCE_WORKER_NODE_KEY",
    "SIGNUP_MODE",
    "TOOL_VERSION",
    "UNION3_REPRODUCTION_ENABLED",
    "WORKER_TASK_SIGNING_KEY_ID",
    "WORKER_TASK_SIGNING_PRIVATE_KEY",
    "WORKER_TASK_SIGNING_PUBLIC_KEY",
    "WORKER_TASK_VERIFICATION_KEYS",
}


def _base_env(tmp_path: Path, role: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in _ROLE_KEYS}
    env.update(
        {
            "APP_ROLE": role,
            "ENV": "production",
            "HOME": str(tmp_path),
            "PYTHONPATH": str(BACKEND_DIR),
            "SCIENCE_EXECUTION_BACKEND": "celery",
            "TOOL_VERSION": FULL_SHA,
        }
    )
    if role in {"api", "migration", "control_worker", "beat"}:
        env["DATABASE_URL"] = "postgresql+asyncpg://astro:astro@db/astro"
    if role in {"api", "control_worker", "beat"}:
        env["REDIS_URL"] = "rediss://redis.example.invalid/0"
    return env


def _api_env(tmp_path: Path) -> dict[str, str]:
    env = _base_env(tmp_path, "api")
    env.update(
        {
            "ADMIN_SECRET": "admin-secret",
            "DELETION_TOMBSTONE_KEY": "deletion-key-that-is-at-least-32-bytes",
            "DELETION_TOMBSTONE_KEY_ID": "deletion-v1",
            "EVIDENCE_SIGNING_KEY": "evidence-key-that-is-at-least-32-bytes",
            "EVIDENCE_SIGNING_KEY_ID": "evidence-v1",
            "FERNET_KEY": "fernet-key-that-is-at-least-32-bytes",
            "JWT_SECRET": "jwt-key-that-is-at-least-32-bytes",
            "PRIVACY_CONTACT": "privacy@example.invalid",
            "PRIVACY_JURISDICTION": "Test Jurisdiction",
            "PRIVACY_OPERATOR_NAME": "Test Operator",
            "SANDBOX_BACKEND": "disabled",
            "SIGNUP_MODE": "invite_only",
        }
    )
    return env


def _boot(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    code = (
        "import json; from app.config import settings; "
        "print(json.dumps({'role': settings.app_role, "
        "'jwt': bool(settings.jwt_secret), "
        "'fernet': bool(settings.fernet_key), "
        "'admin': bool(settings.admin_secret), "
        "'evidence': bool(settings.evidence_signing_key), "
        "'public_key': settings.worker_task_signing_public_key}))"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=env["HOME"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _assert_boots(env: dict[str, str]) -> dict[str, object]:
    result = _boot(env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_migration_boots_with_database_and_release_only(tmp_path):
    env = _base_env(tmp_path, "migration")

    configured = _assert_boots(env)

    assert configured == {
        "role": "migration",
        "jwt": False,
        "fernet": False,
        "admin": False,
        "evidence": False,
        "public_key": "",
    }

    # Alembic imports every model to construct metadata. Reproduce that import
    # without connecting to PostgreSQL and prove it needs no runtime secrets.
    metadata_code = (
        "from app.models.database import Base; "
        "import app.models.schemas, app.models.research_records, "
        "app.models.claim_audit_records; "
        "print(len(Base.metadata.tables))"
    )
    result = subprocess.run(
        [sys.executable, "-c", metadata_code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) > 0


def test_beat_boots_without_runtime_secrets(tmp_path):
    env = _base_env(tmp_path, "beat")
    configured = _assert_boots(env)

    assert configured["role"] == "beat"
    assert configured["jwt"] is False
    assert configured["evidence"] is False
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import celery_worker; "
            "celery_worker.celery_app.loader.import_default_modules(); "
            "print('beat-ok')",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr


def test_control_worker_needs_deletion_and_evidence_but_not_api_secrets(tmp_path):
    env = _base_env(tmp_path, "control_worker")
    env.update(
        {
            "DELETION_TOMBSTONE_KEY": "deletion-key-that-is-at-least-32-bytes",
            "DELETION_TOMBSTONE_KEY_ID": "deletion-v1",
            "EVIDENCE_SIGNING_KEY": "evidence-key-that-is-at-least-32-bytes",
            "EVIDENCE_SIGNING_KEY_ID": "evidence-v1",
            "SANDBOX_BACKEND": "disabled",
        }
    )

    configured = _assert_boots(env)

    assert configured["role"] == "control_worker"
    assert configured["jwt"] is False
    assert configured["fernet"] is False
    assert configured["admin"] is False
    assert configured["evidence"] is True
    imported = subprocess.run(
        [
            sys.executable,
            "-c",
            "import celery_worker; "
            "celery_worker.celery_app.loader.import_default_modules(); "
            "print(celery_worker.celery_app.conf.task_default_queue)",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "science.short"


def test_https_control_worker_requires_public_task_trust_root(tmp_path):
    env = _base_env(tmp_path, "control_worker")
    env.update(
        {
            "DELETION_TOMBSTONE_KEY": "deletion-key-that-is-at-least-32-bytes",
            "DELETION_TOMBSTONE_KEY_ID": "deletion-v1",
            "EVIDENCE_SIGNING_KEY": "evidence-key-that-is-at-least-32-bytes",
            "EVIDENCE_SIGNING_KEY_ID": "evidence-v1",
            "SANDBOX_BACKEND": "disabled",
            "SCIENCE_EXECUTION_BACKEND": "https_worker",
        }
    )

    missing = _boot(env)
    assert missing.returncode != 0
    assert "WORKER_TASK_SIGNING_KEY_ID" in missing.stderr

    env.update(
        {
            "WORKER_TASK_SIGNING_KEY_ID": "tasks-v1",
            "WORKER_TASK_SIGNING_PUBLIC_KEY": PUBLIC_KEY,
        }
    )
    configured = _assert_boots(env)
    assert configured["role"] == "control_worker"
    assert configured["public_key"] == PUBLIC_KEY
    assert configured["jwt"] is False
    assert configured["admin"] is False

    env["WORKER_TASK_SIGNING_PRIVATE_KEY"] = base64.b64encode(b"s" * 32).decode(
        "ascii"
    )
    rejected = _boot(env)
    assert rejected.returncode != 0
    assert "must not receive WORKER_TASK_SIGNING_PRIVATE_KEY" in rejected.stderr


def test_api_still_fails_closed_without_invitation_admin_secret(tmp_path):
    env = _api_env(tmp_path)
    del env["ADMIN_SECRET"]

    result = _boot(env)

    assert result.returncode != 0
    assert "ADMIN_SECRET" in result.stderr


def test_science_worker_has_only_https_identity_and_public_verification_key(tmp_path):
    env = _base_env(tmp_path, "science_worker")
    env.update(
        {
            "SCIENCE_CONTROL_PLANE_URL": "https://control.example.invalid",
            "SCIENCE_EXECUTION_BACKEND": "https_worker",
            "SCIENCE_WORKER_CACHE_DIR": str(tmp_path / "cache"),
            "SCIENCE_WORKER_NODE_KEY": "node-key-that-is-at-least-32-bytes",
            "WORKER_TASK_SIGNING_KEY_ID": "tasks-v1",
            "WORKER_TASK_SIGNING_PUBLIC_KEY": PUBLIC_KEY,
        }
    )

    configured = _assert_boots(env)

    assert configured["role"] == "science_worker"
    assert configured["jwt"] is False
    assert configured["evidence"] is False
    assert configured["public_key"] == PUBLIC_KEY

    env["WORKER_TASK_SIGNING_PRIVATE_KEY"] = base64.b64encode(b"s" * 32).decode(
        "ascii"
    )
    rejected = _boot(env)
    assert rejected.returncode != 0
    assert "must not receive WORKER_TASK_SIGNING_PRIVATE_KEY" in rejected.stderr


def test_https_backend_validates_and_derives_independent_task_public_key(tmp_path):
    env = _api_env(tmp_path)
    env.update(
        {
            "SCIENCE_EXECUTION_BACKEND": "https_worker",
            "WORKER_TASK_SIGNING_KEY_ID": "tasks-v1",
            "WORKER_TASK_SIGNING_PRIVATE_KEY": base64.b64encode(b"s" * 32).decode(
                "ascii"
            ),
        }
    )

    configured = _assert_boots(env)

    assert configured["public_key"]
    assert configured["public_key"] != env["WORKER_TASK_SIGNING_PRIVATE_KEY"]


def test_evidence_v2_key_is_dark_and_independent_until_enabled(tmp_path):
    env = _api_env(tmp_path)
    evidence_v2_private_key = base64.b64encode(b"e" * 32).decode("ascii")
    env.update(
        {
            "EVIDENCE_PACK_V2_ENABLED": "true",
            "EVIDENCE_V2_SIGNING_KEY_ID": "evidence-v2",
            "EVIDENCE_V2_SIGNING_PRIVATE_KEY": evidence_v2_private_key,
        }
    )
    code = (
        "from app.config import settings; "
        "print(settings.evidence_v2_signing_public_key)"
    )
    configured = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert configured.returncode == 0, configured.stderr
    assert configured.stdout.strip()
    assert configured.stdout.strip() != evidence_v2_private_key

    # A migration can inherit the dark flag without receiving any signing key.
    migration_env = _base_env(tmp_path, "migration")
    migration_env["EVIDENCE_PACK_V2_ENABLED"] = "true"
    assert _boot(migration_env).returncode == 0

    # Once enabled on the API, reusing the legacy HMAC secret fails closed.
    env["EVIDENCE_SIGNING_KEY"] = evidence_v2_private_key
    rejected = _boot(env)
    assert rejected.returncode != 0
    assert "must be independent" in rejected.stderr


def test_role_backend_and_release_identity_fail_closed(tmp_path):
    missing_role = _base_env(tmp_path, "migration")
    del missing_role["APP_ROLE"]
    assert "APP_ROLE must be set explicitly" in _boot(missing_role).stderr

    invalid_role = _base_env(tmp_path, "typo")
    assert "APP_ROLE must be one of" in _boot(invalid_role).stderr

    invalid_backend = _base_env(tmp_path, "migration")
    invalid_backend["SCIENCE_EXECUTION_BACKEND"] = "inline"
    assert "SCIENCE_EXECUTION_BACKEND" in _boot(invalid_backend).stderr

    inconsistent_api = _api_env(tmp_path)
    inconsistent_api["LOCAL_SCIENCE_WORKER_ENABLED"] = "true"
    assert "SCIENCE_EXECUTION_BACKEND=https_worker" in _boot(inconsistent_api).stderr

    short_release = _base_env(tmp_path, "migration")
    short_release["TOOL_VERSION"] = "abc123"
    assert "full 40-character Git SHA" in _boot(short_release).stderr
