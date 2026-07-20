#!/usr/bin/env python3
"""Create, encrypt, upload, and retain portable PostgreSQL backups.

The existing ``backup.sh`` remains the authority for producing a portable,
schema-bound pg_dump bundle.  This module adds a streaming AES-256-GCM
envelope and an independently verified upload to a versioned S3-compatible
bucket.  Secret key bytes are never written to the envelope or object
metadata; only the operator-managed key identifier is recorded.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Mapping
from urllib.parse import urlparse

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


_MAGIC = b"STANDARD-ASTRO-PG-BACKUP\x00\x01"
_HEADER_LENGTH = struct.Struct(">I")
_TAG_BYTES = 16
_NONCE_BYTES = 12
_CHUNK_BYTES = 4 * 1024 * 1024
_MAX_HEADER_BYTES = 16 * 1024
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,127}")
_MANAGED_FILENAME = re.compile(
    r"standard-astro-\d{8}T\d{6}Z-[0-9a-f]{12}\.tar\.gz\."
    r"[0-9a-f]{12}\.aesgcm"
)
_REUSED_SECRET_NAMES = (
    "JWT_SECRET",
    "FERNET_KEY",
    "DELETION_TOMBSTONE_KEY",
    "EVIDENCE_SIGNING_KEY",
    "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
    "WORKER_TASK_SIGNING_PRIVATE_KEY",
)


class BackupConfigurationError(ValueError):
    """Raised before any dump or upload when backup configuration is unsafe."""


class BackupIntegrityError(RuntimeError):
    """Raised when local or remote backup bytes cannot be authenticated."""


@dataclass(frozen=True)
class BackupConfiguration:
    database_url: str = field(repr=False)
    encryption_key: bytes = field(repr=False)
    encryption_key_id: str
    bucket: str
    prefix: str
    retention_days: int
    endpoint_url: str
    region: str
    addressing_style: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    backup_script: Path
    timeout_seconds: int


def _required(environ: Mapping[str, str], name: str) -> str:
    value = str(environ.get(name) or "").strip()
    if not value:
        raise BackupConfigurationError(f"{name} is required")
    return value


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = str(environ.get(name, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise BackupConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise BackupConfigurationError(f"{name} must be a positive integer")
    return value


def _normalize_prefix(raw: str) -> str:
    value = str(raw or "").strip().strip("/")
    if not value or "\\" in value or any(ord(char) < 32 for char in value):
        raise BackupConfigurationError("POSTGRES_BACKUP_S3_PREFIX is invalid")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise BackupConfigurationError("POSTGRES_BACKUP_S3_PREFIX is invalid")
    normalized = PurePosixPath(*parts).as_posix()
    if normalized != value:
        raise BackupConfigurationError("POSTGRES_BACKUP_S3_PREFIX is not canonical")
    return normalized


def _decode_encryption_key(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BackupConfigurationError(
            "POSTGRES_BACKUP_ENCRYPTION_KEY must be canonical base64"
        ) from exc
    if len(key) != 32:
        raise BackupConfigurationError(
            "POSTGRES_BACKUP_ENCRYPTION_KEY must encode exactly 32 bytes"
        )
    if base64.b64encode(key).decode("ascii") != raw:
        raise BackupConfigurationError(
            "POSTGRES_BACKUP_ENCRYPTION_KEY must be canonical base64"
        )
    return key


def configuration_from_environment(
    environ: Mapping[str, str] | None = None,
) -> BackupConfiguration:
    """Parse the complete backup contract without logging secret values."""

    env = os.environ if environ is None else environ
    raw_key = _required(env, "POSTGRES_BACKUP_ENCRYPTION_KEY")
    key = _decode_encryption_key(raw_key)
    for name in _REUSED_SECRET_NAMES:
        candidate = str(env.get(name) or "")
        if candidate and hmac.compare_digest(raw_key, candidate):
            raise BackupConfigurationError(
                f"POSTGRES_BACKUP_ENCRYPTION_KEY must not reuse {name}"
            )

    key_id = _required(env, "POSTGRES_BACKUP_ENCRYPTION_KEY_ID")
    if _KEY_ID.fullmatch(key_id) is None or key_id.lower() == "unrecorded":
        raise BackupConfigurationError(
            "POSTGRES_BACKUP_ENCRYPTION_KEY_ID is not a safe recorded identifier"
        )

    endpoint = str(env.get("S3_ENDPOINT_URL") or "").strip()
    allow_insecure = str(
        env.get("POSTGRES_BACKUP_ALLOW_INSECURE_S3") or "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    if endpoint:
        parsed = urlparse(endpoint)
        if parsed.scheme not in ({"http", "https"} if allow_insecure else {"https"}):
            raise BackupConfigurationError(
                "S3_ENDPOINT_URL must use HTTPS unless insecure S3 is explicitly allowed"
            )
        if not parsed.netloc or parsed.username or parsed.password:
            raise BackupConfigurationError("S3_ENDPOINT_URL is invalid")

    access_key_id = str(env.get("S3_ACCESS_KEY_ID") or "").strip()
    secret_access_key = str(env.get("S3_SECRET_ACCESS_KEY") or "").strip()
    if bool(access_key_id) != bool(secret_access_key):
        raise BackupConfigurationError(
            "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together"
        )

    addressing_style = str(env.get("S3_ADDRESSING_STYLE") or "path").strip()
    if addressing_style not in {"auto", "path", "virtual"}:
        raise BackupConfigurationError(
            "S3_ADDRESSING_STYLE must be auto, path, or virtual"
        )

    script = Path(
        str(
            env.get("POSTGRES_BACKUP_SCRIPT")
            or Path(__file__).with_name("backup.sh")
        )
    ).expanduser()
    if not script.is_file() or not os.access(script, os.X_OK):
        raise BackupConfigurationError(
            "POSTGRES_BACKUP_SCRIPT must be an executable regular file"
        )

    retention_days = _positive_int(env, "POSTGRES_BACKUP_RETENTION_DAYS", 30)
    if retention_days > 3650:
        raise BackupConfigurationError(
            "POSTGRES_BACKUP_RETENTION_DAYS must not exceed 3650"
        )

    return BackupConfiguration(
        database_url=_required(env, "DATABASE_URL"),
        encryption_key=key,
        encryption_key_id=key_id,
        bucket=_required(env, "S3_BUCKET"),
        prefix=_normalize_prefix(
            str(env.get("POSTGRES_BACKUP_S3_PREFIX") or "backups/postgresql")
        ),
        retention_days=retention_days,
        endpoint_url=endpoint,
        region=str(env.get("S3_REGION") or "us-east-1").strip(),
        addressing_style=addressing_style,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        backup_script=script.resolve(),
        timeout_seconds=_positive_int(
            env, "POSTGRES_BACKUP_TIMEOUT_SECONDS", 4 * 60 * 60
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_header(header: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(header),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def encrypt_bundle(
    source: Path,
    destination: Path,
    *,
    key: bytes,
    key_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Stream one portable bundle into an authenticated AES-GCM envelope."""

    if len(key) != 32:
        raise BackupConfigurationError("AES-256-GCM requires a 32-byte key")
    if _KEY_ID.fullmatch(key_id) is None:
        raise BackupConfigurationError("Invalid backup encryption key identifier")
    if not source.is_file() or source.is_symlink():
        raise BackupIntegrityError("Portable backup is not a regular file")
    source_size = source.stat().st_size
    if source_size <= 0:
        raise BackupIntegrityError("Portable backup is empty")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace existing envelope: {destination}")

    created_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    nonce = os.urandom(_NONCE_BYTES)
    plaintext_sha256 = _sha256_file(source)
    header = {
        "algorithm": "AES-256-GCM",
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "format_version": 1,
        "key_id": key_id,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "plaintext_sha256": plaintext_sha256,
        "plaintext_size": source_size,
        "source_format": "standard_astro_portable_backup_v1",
    }
    header_bytes = _canonical_header(header)
    if len(header_bytes) > _MAX_HEADER_BYTES:
        raise BackupIntegrityError("Backup envelope header is too large")
    aad = _MAGIC + _HEADER_LENGTH.pack(len(header_bytes)) + header_bytes

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=str(destination.parent)
    )
    temporary = Path(temporary_name)
    try:
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(aad)
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            writer.write(aad)
            for chunk in iter(lambda: reader.read(_CHUNK_BYTES), b""):
                writer.write(encryptor.update(chunk))
            writer.write(encryptor.finalize())
            writer.write(encryptor.tag)
            writer.flush()
            os.fsync(writer.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise

    return {
        **header,
        "ciphertext_sha256": _sha256_file(destination),
        "ciphertext_size": destination.stat().st_size,
    }


def _read_envelope_header(handle: BinaryIO) -> tuple[dict[str, Any], bytes]:
    magic = handle.read(len(_MAGIC))
    if magic != _MAGIC:
        raise BackupIntegrityError("Unsupported encrypted backup format")
    raw_length = handle.read(_HEADER_LENGTH.size)
    if len(raw_length) != _HEADER_LENGTH.size:
        raise BackupIntegrityError("Truncated encrypted backup header")
    header_length = _HEADER_LENGTH.unpack(raw_length)[0]
    if header_length <= 0 or header_length > _MAX_HEADER_BYTES:
        raise BackupIntegrityError("Invalid encrypted backup header length")
    header_bytes = handle.read(header_length)
    if len(header_bytes) != header_length:
        raise BackupIntegrityError("Truncated encrypted backup header")
    try:
        header = json.loads(header_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupIntegrityError("Invalid encrypted backup header") from exc
    expected_keys = {
        "algorithm",
        "created_at",
        "format_version",
        "key_id",
        "nonce",
        "plaintext_sha256",
        "plaintext_size",
        "source_format",
    }
    if not isinstance(header, dict) or set(header) != expected_keys:
        raise BackupIntegrityError("Encrypted backup header schema mismatch")
    if _canonical_header(header) != header_bytes:
        raise BackupIntegrityError("Encrypted backup header is not canonical")
    if (
        header["algorithm"] != "AES-256-GCM"
        or header["format_version"] != 1
        or header["source_format"] != "standard_astro_portable_backup_v1"
        or _KEY_ID.fullmatch(str(header["key_id"])) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(header["plaintext_sha256"])) is None
        or not isinstance(header["plaintext_size"], int)
        or header["plaintext_size"] <= 0
    ):
        raise BackupIntegrityError("Encrypted backup header values are invalid")
    return header, _MAGIC + raw_length + header_bytes


def decrypt_envelope(
    source: Path,
    destination: Path,
    *,
    key: bytes,
    expected_key_id: str | None = None,
) -> dict[str, Any]:
    """Authenticate and decrypt an envelope atomically for restore tooling."""

    if len(key) != 32:
        raise BackupConfigurationError("AES-256-GCM requires a 32-byte key")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace existing output: {destination}")
    total_size = source.stat().st_size
    with source.open("rb") as reader:
        header, aad = _read_envelope_header(reader)
        if expected_key_id and not hmac.compare_digest(
            str(header["key_id"]), expected_key_id
        ):
            raise BackupIntegrityError("Backup encryption key identifier mismatch")
        ciphertext_offset = reader.tell()
        ciphertext_size = total_size - ciphertext_offset - _TAG_BYTES
        if ciphertext_size <= 0 or ciphertext_size != header["plaintext_size"]:
            raise BackupIntegrityError("Encrypted backup payload length mismatch")
        reader.seek(total_size - _TAG_BYTES)
        tag = reader.read(_TAG_BYTES)
        try:
            nonce = base64.b64decode(str(header["nonce"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise BackupIntegrityError("Invalid backup nonce") from exc
        if len(nonce) != _NONCE_BYTES or len(tag) != _TAG_BYTES:
            raise BackupIntegrityError("Invalid backup nonce or tag")
        reader.seek(ciphertext_offset)

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=str(destination.parent)
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        written = 0
        remaining = ciphertext_size
        try:
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(aad)
            with os.fdopen(fd, "wb") as writer:
                while remaining:
                    chunk = reader.read(min(_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise BackupIntegrityError("Truncated encrypted backup payload")
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    writer.write(plaintext)
                    digest.update(plaintext)
                    written += len(plaintext)
                final = decryptor.finalize()
                writer.write(final)
                digest.update(final)
                written += len(final)
                writer.flush()
                os.fsync(writer.fileno())
            if (
                written != header["plaintext_size"]
                or digest.hexdigest() != header["plaintext_sha256"]
            ):
                raise BackupIntegrityError("Decrypted backup checksum mismatch")
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        except InvalidTag as exc:
            temporary.unlink(missing_ok=True)
            raise BackupIntegrityError("Encrypted backup authentication failed") from exc
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise
    return header


def create_s3_client(configuration: BackupConfiguration):
    """Create a bounded S3 client without importing application Settings."""

    import boto3
    from botocore.config import Config

    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": configuration.region,
        "config": Config(
            connect_timeout=10,
            read_timeout=120,
            signature_version="s3v4",
            s3={"addressing_style": configuration.addressing_style},
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    }
    if configuration.endpoint_url:
        kwargs["endpoint_url"] = configuration.endpoint_url
    if configuration.access_key_id:
        kwargs["aws_access_key_id"] = configuration.access_key_id
        kwargs["aws_secret_access_key"] = configuration.secret_access_key
    return boto3.client(**kwargs)


def require_bucket_versioning(client: Any, bucket: str) -> None:
    status = str((client.get_bucket_versioning(Bucket=bucket) or {}).get("Status") or "")
    if status != "Enabled":
        raise BackupConfigurationError(
            "The PostgreSQL backup bucket must have versioning Enabled"
        )


def _remote_sha256(body: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        iterator = body.iter_chunks(chunk_size=_CHUNK_BYTES)
    except AttributeError:
        iterator = iter(lambda: body.read(_CHUNK_BYTES), b"")
    for chunk in iterator:
        if not chunk:
            continue
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def upload_verified_envelope(
    client: Any,
    configuration: BackupConfiguration,
    envelope: Path,
    envelope_metadata: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, str]:
    """Upload a unique object and read its exact version back in full."""

    random_suffix = uuid.uuid4().hex[:12]
    key = (
        f"{configuration.prefix}/{now:%Y/%m/%d}/"
        f"{envelope.name.removesuffix('.aesgcm')}.{random_suffix}.aesgcm"
    )
    expected_hash = str(envelope_metadata["ciphertext_sha256"])
    expected_size = int(envelope_metadata["ciphertext_size"])
    metadata = {
        "sha256": expected_hash,
        "plaintext-sha256": str(envelope_metadata["plaintext_sha256"]),
        "encryption": "aes-256-gcm",
        "format-version": "1",
        "key-id": configuration.encryption_key_id,
    }
    client.upload_file(
        str(envelope),
        configuration.bucket,
        key,
        ExtraArgs={
            "ContentType": "application/vnd.standard-astro.pg-backup+aesgcm",
            "Metadata": metadata,
        },
    )

    version_id = ""
    try:
        head = client.head_object(Bucket=configuration.bucket, Key=key)
        version_id = str(head.get("VersionId") or "")
        remote_metadata = {
            str(name).lower(): str(value)
            for name, value in (head.get("Metadata") or {}).items()
        }
        if (
            not version_id
            or version_id.lower() == "null"
            or int(head.get("ContentLength", -1)) != expected_size
            or remote_metadata != metadata
        ):
            raise BackupIntegrityError("S3 backup metadata verification failed")
        response = client.get_object(
            Bucket=configuration.bucket,
            Key=key,
            VersionId=version_id,
        )
        remote_hash, remote_size = _remote_sha256(response["Body"])
        if remote_hash != expected_hash or remote_size != expected_size:
            raise BackupIntegrityError("S3 backup content verification failed")
    except Exception:
        _purge_exact_key(client, configuration.bucket, key)
        raise
    return {"object_key": key, "version_id": version_id}


def _managed_object(prefix: str, key: str) -> bool:
    prefix_parts = prefix.split("/")
    parts = key.split("/")
    if parts[: len(prefix_parts)] != prefix_parts:
        return False
    remainder = parts[len(prefix_parts) :]
    return (
        len(remainder) == 4
        and re.fullmatch(r"\d{4}", remainder[0]) is not None
        and re.fullmatch(r"\d{2}", remainder[1]) is not None
        and re.fullmatch(r"\d{2}", remainder[2]) is not None
        and _MANAGED_FILENAME.fullmatch(remainder[3]) is not None
    )


def _version_entries(client: Any, bucket: str, prefix: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        entries.extend(page.get("Versions") or [])
        entries.extend(page.get("DeleteMarkers") or [])
    return entries


def _delete_versions(client: Any, bucket: str, entries: list[dict[str, str]]) -> None:
    for offset in range(0, len(entries), 1000):
        batch = entries[offset : offset + 1000]
        response = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": batch, "Quiet": True},
        )
        errors = list((response or {}).get("Errors") or [])
        if errors:
            codes = sorted({str(item.get("Code") or "unknown") for item in errors})
            raise BackupIntegrityError(
                "S3 backup version deletion failed: " + ",".join(codes)
            )


def _purge_exact_key(client: Any, bucket: str, key: str) -> None:
    entries = [
        {"Key": key, "VersionId": str(item["VersionId"])}
        for item in _version_entries(client, bucket, key)
        if str(item.get("Key") or "") == key and item.get("VersionId") is not None
    ]
    if entries:
        _delete_versions(client, bucket, entries)


def purge_expired_versions(
    client: Any,
    configuration: BackupConfiguration,
    *,
    now: datetime,
    protected: tuple[str, str],
) -> int:
    """Permanently remove only managed backup versions older than retention."""

    cutoff = now - timedelta(days=configuration.retention_days)
    prefix = configuration.prefix + "/"
    expired: list[dict[str, str]] = []
    for item in _version_entries(client, configuration.bucket, prefix):
        key = str(item.get("Key") or "")
        version_id = str(item.get("VersionId") or "")
        modified = item.get("LastModified")
        if not key or not version_id or not isinstance(modified, datetime):
            raise BackupIntegrityError("S3 version listing omitted required fields")
        if modified.tzinfo is None:
            raise BackupIntegrityError("S3 version timestamp must include a timezone")
        if (
            (key, version_id) != protected
            and _managed_object(configuration.prefix, key)
            and modified.astimezone(timezone.utc) < cutoff
        ):
            expired.append({"Key": key, "VersionId": version_id})
    _delete_versions(client, configuration.bucket, expired)

    remaining = {
        (str(item.get("Key") or ""), str(item.get("VersionId") or ""))
        for item in _version_entries(client, configuration.bucket, prefix)
        if _managed_object(configuration.prefix, str(item.get("Key") or ""))
        and isinstance(item.get("LastModified"), datetime)
        and item["LastModified"].astimezone(timezone.utc) < cutoff
        and (
            str(item.get("Key") or ""),
            str(item.get("VersionId") or ""),
        )
        != protected
    }
    if remaining:
        raise BackupIntegrityError("Expired S3 backup versions remain after cleanup")
    return len(expired)


def _run_portable_backup(
    configuration: BackupConfiguration,
    working_root: Path,
    environ: Mapping[str, str],
) -> Path:
    backup_root = working_root / "portable"
    backup_root.mkdir(mode=0o700)
    child_env = dict(environ)
    child_env.update(
        {
            "DATABASE_URL": configuration.database_url,
            "BACKUP_ROOT": str(backup_root),
            "BACKUP_RETENTION_DAYS": "0",
        }
    )
    child_env.pop("STORAGE_DIR", None)
    # pg_dump and the portable packager do not need object-store, encryption,
    # login, deletion, or signing secrets. Keep those values out of every
    # descendant process even though the parent control worker owns them.
    for name in (
        "POSTGRES_BACKUP_ENCRYPTION_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "JWT_SECRET",
        "FERNET_KEY",
        "DELETION_TOMBSTONE_KEY",
        "EVIDENCE_SIGNING_KEY",
        "EVIDENCE_V2_SIGNING_PRIVATE_KEY",
        "WORKER_TASK_SIGNING_PRIVATE_KEY",
    ):
        child_env.pop(name, None)
    result = subprocess.run(
        [str(configuration.backup_script)],
        cwd=str(configuration.backup_script.parents[2]),
        env=child_env,
        capture_output=True,
        text=True,
        timeout=configuration.timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Portable PostgreSQL backup failed with exit status {result.returncode}"
        )
    output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not output_lines:
        raise BackupIntegrityError("Portable backup command returned no artifact path")
    candidate = Path(output_lines[-1]).resolve()
    try:
        candidate.relative_to(backup_root.resolve())
    except ValueError as exc:
        raise BackupIntegrityError(
            "Portable backup command returned a path outside its private directory"
        ) from exc
    if not candidate.is_file() or candidate.is_symlink() or candidate.stat().st_size <= 0:
        raise BackupIntegrityError("Portable backup artifact is missing or empty")
    if not re.fullmatch(
        r"standard-astro-\d{8}T\d{6}Z-[0-9a-f]{12}\.tar\.gz",
        candidate.name,
    ):
        raise BackupIntegrityError("Portable backup artifact name is invalid")
    return candidate


def run_backup_from_environment(
    environ: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Run the complete daily backup transaction and return non-secret receipt."""

    env = os.environ if environ is None else environ
    configuration = configuration_from_environment(env)
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    s3 = client or create_s3_client(configuration)
    require_bucket_versioning(s3, configuration.bucket)

    working_root = Path(tempfile.mkdtemp(prefix="standard-astro-pg-backup-"))
    os.chmod(working_root, 0o700)
    try:
        portable = _run_portable_backup(configuration, working_root, env)
        envelope = working_root / f"{portable.name}.aesgcm"
        metadata = encrypt_bundle(
            portable,
            envelope,
            key=configuration.encryption_key,
            key_id=configuration.encryption_key_id,
            now=current_time,
        )
        uploaded = upload_verified_envelope(
            s3,
            configuration,
            envelope,
            metadata,
            now=current_time,
        )
        deleted = purge_expired_versions(
            s3,
            configuration,
            now=current_time,
            protected=(uploaded["object_key"], uploaded["version_id"]),
        )
    finally:
        # The directory held a plaintext pg_dump bundle. A cleanup failure is
        # therefore an operational failure, not a best-effort cache cleanup.
        shutil.rmtree(working_root)

    return {
        "status": "completed",
        "bucket": configuration.bucket,
        "object_key": uploaded["object_key"],
        "version_id": uploaded["version_id"],
        "ciphertext_sha256": metadata["ciphertext_sha256"],
        "plaintext_sha256": metadata["plaintext_sha256"],
        "encryption": "AES-256-GCM",
        "encryption_key_id": configuration.encryption_key_id,
        "retention_days": configuration.retention_days,
        "expired_versions_deleted": deleted,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("backup", help="create and upload one encrypted backup")
    decrypt = subcommands.add_parser("decrypt", help="decrypt a downloaded backup")
    decrypt.add_argument("source", type=Path)
    decrypt.add_argument("destination", type=Path)
    args = parser.parse_args()

    if args.command == "backup":
        receipt = run_backup_from_environment()
        print(json.dumps(receipt, sort_keys=True))
        return 0

    raw_key = _required(os.environ, "POSTGRES_BACKUP_ENCRYPTION_KEY")
    key_id = _required(os.environ, "POSTGRES_BACKUP_ENCRYPTION_KEY_ID")
    header = decrypt_envelope(
        args.source,
        args.destination,
        key=_decode_encryption_key(raw_key),
        expected_key_id=key_id,
    )
    print(
        json.dumps(
            {
                "status": "decrypted",
                "destination": str(args.destination),
                "key_id": header["key_id"],
                "plaintext_sha256": header["plaintext_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
