"""Fail-closed tests for encrypted PostgreSQL backup automation."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts.ops import encrypted_postgres_backup as backup


BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
NOW = datetime(2026, 7, 20, 3, 15, tzinfo=timezone.utc)
KEY = bytes(range(32))
KEY_B64 = base64.b64encode(KEY).decode("ascii")


def _environment(**overrides: str) -> dict[str, str]:
    env = {
        "DATABASE_URL": "postgresql://astro:secret@db/astro",
        "POSTGRES_BACKUP_ENCRYPTION_KEY": KEY_B64,
        "POSTGRES_BACKUP_ENCRYPTION_KEY_ID": "postgres-backup-test-v1",
        "POSTGRES_BACKUP_RETENTION_DAYS": "30",
        "POSTGRES_BACKUP_S3_PREFIX": "backups/postgresql",
        "POSTGRES_BACKUP_SCRIPT": str(BACKEND / "scripts" / "ops" / "backup.sh"),
        "S3_BUCKET": "backup-bucket",
        "S3_ENDPOINT_URL": "https://objects.example.invalid",
        "S3_ACCESS_KEY_ID": "access-id",
        "S3_SECRET_ACCESS_KEY": "secret-key",
        "S3_REGION": "auto",
        "S3_ADDRESSING_STYLE": "path",
        "FERNET_KEY_ID": "fernet-test-v1",
        "EVIDENCE_SIGNING_KEY_ID": "evidence-test-v1",
        "RENDER_GIT_COMMIT": "a" * 40,
    }
    env.update(overrides)
    return env


def _portable_name() -> str:
    return "standard-astro-20260720T031500Z-aaaaaaaaaaaa.tar.gz"


def test_aes_gcm_envelope_round_trip_has_no_secret_and_rejects_tampering(tmp_path):
    portable = tmp_path / _portable_name()
    portable.write_bytes((b"portable-pg-dump\0" * 100_000) + b"end")
    encrypted = tmp_path / f"{portable.name}.aesgcm"

    metadata = backup.encrypt_bundle(
        portable,
        encrypted,
        key=KEY,
        key_id="postgres-backup-test-v1",
        now=NOW,
    )

    raw = encrypted.read_bytes()
    assert KEY not in raw
    assert KEY_B64.encode("ascii") not in raw
    assert metadata["algorithm"] == "AES-256-GCM"
    assert metadata["plaintext_size"] == portable.stat().st_size
    restored = tmp_path / "restored.tar.gz"
    header = backup.decrypt_envelope(
        encrypted,
        restored,
        key=KEY,
        expected_key_id="postgres-backup-test-v1",
    )
    assert restored.read_bytes() == portable.read_bytes()
    assert header["plaintext_sha256"] == metadata["plaintext_sha256"]
    assert restored.stat().st_mode & 0o777 == 0o600

    tampered = tmp_path / "tampered.aesgcm"
    changed = bytearray(raw)
    changed[-backup._TAG_BYTES - 5] ^= 1
    tampered.write_bytes(changed)
    rejected_output = tmp_path / "must-not-exist.tar.gz"
    with pytest.raises(backup.BackupIntegrityError, match="authentication failed"):
        backup.decrypt_envelope(tampered, rejected_output, key=KEY)
    assert not rejected_output.exists()


def test_configuration_is_strict_and_backup_key_cannot_reuse_other_secrets():
    configuration = backup.configuration_from_environment(_environment())
    assert configuration.encryption_key == KEY
    assert configuration.retention_days == 30
    assert configuration.prefix == "backups/postgresql"
    assert "secret@db" not in repr(configuration)
    assert KEY_B64 not in repr(configuration)
    assert "secret-key" not in repr(configuration)

    with pytest.raises(backup.BackupConfigurationError, match="exactly 32 bytes"):
        backup.configuration_from_environment(
            _environment(POSTGRES_BACKUP_ENCRYPTION_KEY=base64.b64encode(b"short").decode())
        )
    with pytest.raises(backup.BackupConfigurationError, match="must not reuse"):
        backup.configuration_from_environment(_environment(EVIDENCE_SIGNING_KEY=KEY_B64))
    with pytest.raises(backup.BackupConfigurationError, match="must use HTTPS"):
        backup.configuration_from_environment(
            _environment(S3_ENDPOINT_URL="http://minio.example.invalid")
        )
    with pytest.raises(backup.BackupConfigurationError, match="configured together"):
        backup.configuration_from_environment(_environment(S3_SECRET_ACCESS_KEY=""))
    with pytest.raises(backup.BackupConfigurationError, match="PREFIX"):
        backup.configuration_from_environment(
            _environment(POSTGRES_BACKUP_S3_PREFIX="backups/../private")
        )


class _Body:
    def __init__(self, value: bytes):
        self.value = value

    def iter_chunks(self, *, chunk_size: int):
        for offset in range(0, len(self.value), chunk_size):
            yield self.value[offset : offset + chunk_size]


class _Paginator:
    def __init__(self, client: "_FakeS3"):
        self.client = client

    def paginate(self, *, Bucket: str, Prefix: str):
        assert Bucket == "backup-bucket"
        versions = [
            dict(item)
            for item in self.client.entries
            if item["Key"].startswith(Prefix) and item["kind"] == "version"
        ]
        markers = [
            dict(item)
            for item in self.client.entries
            if item["Key"].startswith(Prefix) and item["kind"] == "marker"
        ]
        for item in [*versions, *markers]:
            item.pop("kind", None)
            item.pop("body", None)
            item.pop("metadata", None)
        return [{"Versions": versions, "DeleteMarkers": markers}]


class _FakeS3:
    def __init__(self, *, versioning: str = "Enabled", corrupt_readback: bool = False):
        self.versioning = versioning
        self.corrupt_readback = corrupt_readback
        self.entries: list[dict] = []
        self.deleted: list[tuple[str, str]] = []
        self.upload_calls = 0

    def get_bucket_versioning(self, *, Bucket: str):
        assert Bucket == "backup-bucket"
        return {"Status": self.versioning}

    def upload_file(self, filename, bucket, key, *, ExtraArgs):
        assert bucket == "backup-bucket"
        self.upload_calls += 1
        self.entries.append(
            {
                "Key": key,
                "VersionId": f"new-v{self.upload_calls}",
                "LastModified": NOW,
                "kind": "version",
                "body": Path(filename).read_bytes(),
                "metadata": dict(ExtraArgs["Metadata"]),
            }
        )

    def _entry(self, key: str, version_id: str | None = None):
        matches = [
            item
            for item in self.entries
            if item["Key"] == key
            and item["kind"] == "version"
            and (version_id is None or item["VersionId"] == version_id)
        ]
        if not matches:
            raise RuntimeError("missing object")
        return matches[-1]

    def head_object(self, *, Bucket: str, Key: str):
        assert Bucket == "backup-bucket"
        item = self._entry(Key)
        return {
            "ContentLength": len(item["body"]),
            "Metadata": dict(item["metadata"]),
            "VersionId": item["VersionId"],
        }

    def get_object(self, *, Bucket: str, Key: str, VersionId: str):
        assert Bucket == "backup-bucket"
        body = self._entry(Key, VersionId)["body"]
        if self.corrupt_readback:
            body = body[:-1] + bytes([body[-1] ^ 1])
        return {"Body": _Body(body)}

    def get_paginator(self, name: str):
        assert name == "list_object_versions"
        return _Paginator(self)

    def delete_objects(self, *, Bucket: str, Delete: dict):
        assert Bucket == "backup-bucket"
        pairs = {(item["Key"], item["VersionId"]) for item in Delete["Objects"]}
        self.deleted.extend(sorted(pairs))
        self.entries = [
            item
            for item in self.entries
            if (item["Key"], item["VersionId"]) not in pairs
        ]
        return {"Deleted": list(Delete["Objects"])}


def _managed_key(day: str, suffix: str = "111111111111") -> str:
    return (
        f"backups/postgresql/{day}/"
        "standard-astro-20260601T031500Z-aaaaaaaaaaaa.tar.gz."
        f"{suffix}.aesgcm"
    )


def test_verified_upload_precedes_retention_and_only_managed_old_versions_are_deleted(
    tmp_path,
):
    configuration = backup.configuration_from_environment(_environment())
    client = _FakeS3()
    old = _managed_key("2026/06/01")
    recent = _managed_key("2026/07/15", "222222222222")
    unrelated = "backups/postgresql/operator-note.txt"
    client.entries.extend(
        [
            {
                "Key": old,
                "VersionId": "old-v1",
                "LastModified": NOW - timedelta(days=49),
                "kind": "version",
            },
            {
                "Key": recent,
                "VersionId": "recent-v1",
                "LastModified": NOW - timedelta(days=5),
                "kind": "version",
            },
            {
                "Key": unrelated,
                "VersionId": "note-v1",
                "LastModified": NOW - timedelta(days=100),
                "kind": "version",
            },
        ]
    )
    portable = tmp_path / _portable_name()
    portable.write_bytes(b"database-dump")
    envelope = tmp_path / f"{portable.name}.aesgcm"
    metadata = backup.encrypt_bundle(
        portable,
        envelope,
        key=KEY,
        key_id=configuration.encryption_key_id,
        now=NOW,
    )

    backup.require_bucket_versioning(client, configuration.bucket)
    uploaded = backup.upload_verified_envelope(
        client, configuration, envelope, metadata, now=NOW
    )
    deleted = backup.purge_expired_versions(
        client,
        configuration,
        now=NOW,
        protected=(uploaded["object_key"], uploaded["version_id"]),
    )

    assert deleted == 1
    assert (old, "old-v1") in client.deleted
    assert any(item["Key"] == recent for item in client.entries)
    assert any(item["Key"] == unrelated for item in client.entries)
    assert any(item["Key"] == uploaded["object_key"] for item in client.entries)


def test_versioning_or_remote_corruption_fails_closed_without_retention_deletion(
    tmp_path,
):
    configuration = backup.configuration_from_environment(_environment())
    suspended = _FakeS3(versioning="Suspended")
    with pytest.raises(backup.BackupConfigurationError, match="versioning Enabled"):
        backup.require_bucket_versioning(suspended, configuration.bucket)
    assert suspended.upload_calls == 0
    assert suspended.deleted == []

    portable = tmp_path / _portable_name()
    portable.write_bytes(b"database-dump")
    envelope = tmp_path / f"{portable.name}.aesgcm"
    metadata = backup.encrypt_bundle(
        portable,
        envelope,
        key=KEY,
        key_id=configuration.encryption_key_id,
        now=NOW,
    )
    corrupt = _FakeS3(corrupt_readback=True)
    old = _managed_key("2026/06/01")
    corrupt.entries.append(
        {
            "Key": old,
            "VersionId": "old-v1",
            "LastModified": NOW - timedelta(days=49),
            "kind": "version",
        }
    )
    with pytest.raises(backup.BackupIntegrityError, match="content verification"):
        backup.upload_verified_envelope(
            corrupt, configuration, envelope, metadata, now=NOW
        )
    assert any(item["Key"] == old for item in corrupt.entries)
    assert (old, "old-v1") not in corrupt.deleted
    assert not any(
        item["Key"] != old and item["kind"] == "version"
        for item in corrupt.entries
    )


def test_complete_runner_removes_plaintext_temporary_directory_after_upload(
    monkeypatch, tmp_path
):
    client = _FakeS3()
    seen_roots: list[Path] = []

    def fake_portable(configuration, working_root, environ):
        seen_roots.append(working_root)
        target = working_root / "portable" / _portable_name()
        target.parent.mkdir(mode=0o700)
        target.write_bytes(b"real-pg-dump-placeholder")
        return target

    monkeypatch.setattr(backup, "_run_portable_backup", fake_portable)
    receipt = backup.run_backup_from_environment(
        _environment(), now=NOW, client=client
    )

    assert receipt["status"] == "completed"
    assert receipt["encryption"] == "AES-256-GCM"
    assert receipt["retention_days"] == 30
    assert receipt["version_id"] == "new-v1"
    assert receipt["expired_versions_deleted"] == 0
    assert seen_roots and not seen_roots[0].exists()
    assert "secret" not in str(receipt)


def test_portable_pg_dump_process_does_not_inherit_unneeded_secrets(
    monkeypatch, tmp_path
):
    env = _environment(
        JWT_SECRET="jwt-secret",
        FERNET_KEY="fernet-secret",
        DELETION_TOMBSTONE_KEY="deletion-secret",
        EVIDENCE_SIGNING_KEY="evidence-secret",
        EVIDENCE_V2_SIGNING_PRIVATE_KEY="evidence-v2-secret",
        WORKER_TASK_SIGNING_PRIVATE_KEY="worker-signing-secret",
        AWS_SESSION_TOKEN="aws-session-secret",
    )
    configuration = backup.configuration_from_environment(env)
    captured: dict[str, str] = {}

    def fake_run(command, *, cwd, env, capture_output, text, timeout, check):
        captured.update(env)
        target = Path(env["BACKUP_ROOT"]) / _portable_name()
        target.write_bytes(b"pg-dump")
        return subprocess.CompletedProcess(command, 0, stdout=f"{target}\n", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    working = tmp_path / "private"
    working.mkdir()
    result = backup._run_portable_backup(configuration, working, env)

    assert result.read_bytes() == b"pg-dump"
    assert captured["DATABASE_URL"] == env["DATABASE_URL"]
    assert captured["FERNET_KEY_ID"] == env["FERNET_KEY_ID"]
    assert captured["EVIDENCE_SIGNING_KEY_ID"] == env["EVIDENCE_SIGNING_KEY_ID"]
    for name in (
        "POSTGRES_BACKUP_ENCRYPTION_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "JWT_SECRET",
        "FERNET_KEY",
        "DELETION_TOMBSTONE_KEY",
        "EVIDENCE_SIGNING_KEY",
        "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
        "WORKER_TASK_SIGNING_PRIVATE_KEY",
        "AWS_SESSION_TOKEN",
    ):
        assert name not in captured


def test_celery_task_and_schedule_are_opt_in_and_maintenance_only(monkeypatch):
    import celery_worker
    from app.tasks import postgres_backup_tasks

    monkeypatch.delenv("POSTGRES_BACKUP_ENABLED", raising=False)
    assert postgres_backup_tasks.postgres_backup_task.run() == {"status": "disabled"}
    assert "daily-encrypted-postgresql-backup" not in celery_worker._build_beat_schedule()
    assert celery_worker.celery_app.conf.task_routes["maintenance.*"] == {
        "queue": "maintenance"
    }

    monkeypatch.setenv("POSTGRES_BACKUP_ENABLED", "true")
    monkeypatch.setattr(
        backup,
        "run_backup_from_environment",
        lambda: {"status": "completed", "object_key": "backups/postgresql/test"},
    )
    assert postgres_backup_tasks.postgres_backup_task.run() == {
        "status": "completed",
        "object_key": "backups/postgresql/test",
    }
    schedule = celery_worker._build_beat_schedule()
    entry = schedule["daily-encrypted-postgresql-backup"]
    assert entry["task"] == "maintenance.postgres_backup"
    assert entry["options"] == {"queue": "maintenance"}
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {15}

    monkeypatch.setenv("POSTGRES_BACKUP_ENABLED", "tru")
    with pytest.raises(ValueError, match="must be a boolean"):
        celery_worker._build_beat_schedule()
    with pytest.raises(ValueError, match="must be a boolean"):
        postgres_backup_tasks.postgres_backup_task.run()


def test_render_and_compose_keep_backup_dark_and_secrets_out_of_beat():
    render = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    services = {item["name"]: item for item in render["services"]}
    worker_env = {
        item["key"]: item
        for item in services["standard-astro-celery-worker"]["envVars"]
    }
    beat_env = {
        item["key"]: item
        for item in services["standard-astro-celery-beat"]["envVars"]
    }
    assert worker_env["POSTGRES_BACKUP_ENABLED"]["value"] == "false"
    assert worker_env["POSTGRES_BACKUP_ENCRYPTION_KEY"] == {
        "key": "POSTGRES_BACKUP_ENCRYPTION_KEY",
        "sync": False,
    }
    assert worker_env["POSTGRES_BACKUP_RETENTION_DAYS"]["value"] == "30"
    assert beat_env["POSTGRES_BACKUP_ENABLED"]["value"] == "false"
    assert "POSTGRES_BACKUP_ENCRYPTION_KEY" not in beat_env
    assert "S3_SECRET_ACCESS_KEY" not in beat_env

    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )["services"]
    compose_worker = compose["celery-worker"]["environment"]
    compose_beat = compose["celery-beat"]["environment"]
    assert compose_worker["POSTGRES_BACKUP_ENABLED"] == (
        "${POSTGRES_BACKUP_ENABLED:-false}"
    )
    assert compose_worker["POSTGRES_BACKUP_RETENTION_DAYS"] == (
        "${POSTGRES_BACKUP_RETENTION_DAYS:-30}"
    )
    assert compose_beat["POSTGRES_BACKUP_ENABLED"] == (
        "${POSTGRES_BACKUP_ENABLED:-false}"
    )
    assert "POSTGRES_BACKUP_ENCRYPTION_KEY" not in compose_beat
