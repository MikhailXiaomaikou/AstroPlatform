"""Durable research-object storage with end-to-end integrity checks.

The public functions keep the historical ``upload_fits`` / ``download_fits``
names because they are used for every research artifact, not only FITS files.
Local storage remains the development default.  Production can select an
S3-compatible object store (AWS S3, Cloudflare R2, MinIO, …) through
``STORAGE_BACKEND=s3``.

Every newly written object carries a SHA-256 digest.  Reads verify that digest
before returning bytes so a truncated upload, damaged volume, or wrong object
version cannot silently enter a scientific analysis.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings


class StorageIntegrityError(IOError):
    """Raised when stored bytes do not match their recorded SHA-256."""


class StorageOwnershipError(FileNotFoundError):
    """Raised when an object key is not registered to the requested owner.

    This intentionally has the same public semantics as a missing object so
    callers cannot use storage APIs to enumerate another user's files.
    """


class StorageOwnerRequired(PermissionError):
    """Raised when a user-file operation has no authenticated owner context."""


def normalize_storage_key(path: str) -> str:
    """Return a safe, portable object key and reject traversal attempts."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Storage path must be a non-empty string")
    if "\\" in path or any(ord(char) < 32 for char in path):
        # Object keys are POSIX paths on every backend.  Treating a backslash
        # as an ordinary S3 character would make local and remote validation
        # disagree and can become traversal on Windows restores.
        raise ValueError(f"Invalid storage path: {path}")
    # Check raw components before PurePosixPath canonicalises ``.`` away.
    # A filename such as ``spectrum..final.fits`` remains legal: only complete
    # path components have traversal meaning.
    raw_parts = path.split("/")
    if path.startswith("/") or any(part in {".", ".."} for part in raw_parts):
        raise ValueError(f"Path traversal detected: {path}")
    key = PurePosixPath(path)
    normalized = key.as_posix()
    if key.is_absolute() or normalized in {"", ".", ".."}:
        raise ValueError(f"Path traversal detected: {path}")
    return normalized


# Backwards-compatible private spelling for administrative scripts that used
# it before the normalizer became part of the public storage contract.
_normalise_key = normalize_storage_key


async def resolve_owned_storage_key(
    path: str,
    *,
    owner_id: str | uuid.UUID | None,
    db: Any | None = None,
) -> str:
    """Normalize ``path`` and require a matching ``DataFile`` owner record.

    ``DataFile`` is the authorization boundary; an object merely existing in
    local/S3 storage is never enough to make it readable.  Supplying an
    existing async session lets API handlers keep one transaction scope, while
    tool execution can omit it and use a short-lived session.
    """
    key = normalize_storage_key(path)
    if owner_id in (None, ""):
        raise StorageOwnerRequired("Authenticated owner context is required")
    try:
        owner_uuid = owner_id if isinstance(owner_id, uuid.UUID) else uuid.UUID(str(owner_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise StorageOwnerRequired("Authenticated owner context is invalid") from exc

    from sqlalchemy import select

    from app.models.schemas import DataFile

    async def _resolve(session: Any) -> str:
        result = await session.execute(
            select(DataFile.fits_path)
            .where(DataFile.fits_path == key, DataFile.user_id == owner_uuid)
            .limit(1)
        )
        stored_key = result.scalar_one_or_none()
        if not stored_key:
            raise StorageOwnershipError(f"Research object not found: {key}")
        # Refuse a malformed legacy database value even after the lookup.
        return normalize_storage_key(str(stored_key))

    if db is not None:
        return await _resolve(db)

    from app.models.database import async_session

    async with async_session() as session:
        return await _resolve(session)


def _storage_root() -> Path:
    # Resolve dynamically so tests and administrative restore tools can safely
    # override LOCAL_STORAGE_DIR after module import.
    return Path(settings.local_storage_dir)


def _validate_path(path: str) -> Path:
    """Validate that a local path stays within the configured storage root."""
    key = normalize_storage_key(path)
    # ``_storage_root`` used to be a module-level ``Path`` and a few callers
    # still replace it directly (not with a function) in isolated tests and
    # administrative tooling.  Accept that legacy override while keeping the
    # production default dynamic so settings changes are observed at runtime.
    root_source = _storage_root
    root = Path(root_source() if callable(root_source) else root_source).resolve()
    full = (root / key).resolve()
    try:
        full.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path traversal detected: {path}") from exc
    return full


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sidecar_path(full: Path) -> Path:
    return full.with_name(full.name + ".sha256")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


@lru_cache(maxsize=1)
def _s3_client():
    """Build the S3 client lazily so local development needs no AWS setup."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - production packaging guard
        raise RuntimeError(
            "STORAGE_BACKEND=s3 requires boto3; install backend requirements"
        ) from exc

    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": settings.s3_region or "us-east-1",
        "config": Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.s3_addressing_style},
            retries={"max_attempts": 4, "mode": "standard"},
        ),
    }
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
    if settings.s3_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client(**kwargs)


def reset_storage_clients() -> None:
    """Clear cached remote clients (test/config reload helper)."""
    _s3_client.cache_clear()


def _backend() -> str:
    backend = str(settings.storage_backend or "local").strip().lower()
    if backend not in {"local", "s3"}:
        raise RuntimeError(f"Unsupported STORAGE_BACKEND={backend!r}")
    return backend


def upload_fits(path: str, data: bytes) -> str:
    """Store research bytes atomically and return their stable object key."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("Storage payload must be bytes-like")
    payload = bytes(data)
    key = normalize_storage_key(path)
    sha256 = _digest(payload)

    if _backend() == "s3":
        client = _s3_client()
        client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=payload,
            Metadata={"sha256": sha256},
        )
        # Do not claim durability until the object can be observed with the
        # exact size and digest metadata written above.
        head = client.head_object(Bucket=settings.s3_bucket, Key=key)
        remote_size = int(head.get("ContentLength", -1))
        remote_hash = str((head.get("Metadata") or {}).get("sha256") or "")
        if remote_size != len(payload) or remote_hash != sha256:
            try:
                client.delete_object(Bucket=settings.s3_bucket, Key=key)
            finally:
                raise StorageIntegrityError(
                    f"Object-store write verification failed for {key}"
                )
        return key

    full = _validate_path(key)
    _atomic_write(full, payload)
    _atomic_write(_sidecar_path(full), f"{sha256}\n".encode("ascii"))
    return key


def download_fits(path: str) -> bytes:
    """Read research bytes and verify their recorded SHA-256 when present."""
    key = normalize_storage_key(path)
    expected = ""

    if _backend() == "s3":
        client = _s3_client()
        try:
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        except Exception as exc:
            code = str(
                getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            ).lower()
            no_such_key = getattr(getattr(client, "exceptions", None), "NoSuchKey", None)
            if code in {"404", "nosuchkey", "notfound"} or (
                no_such_key is not None and isinstance(exc, no_such_key)
            ):
                raise FileNotFoundError(f"Research object not found: {key}") from exc
            raise
        payload = response["Body"].read()
        expected = str((response.get("Metadata") or {}).get("sha256") or "")
    else:
        full = _validate_path(key)
        if not full.is_file():
            raise FileNotFoundError(f"Research object not found: {key}")
        payload = full.read_bytes()
        sidecar = _sidecar_path(full)
        if sidecar.is_file():
            expected = sidecar.read_text(encoding="ascii").strip().lower()

    if not expected and settings.storage_require_integrity:
        raise StorageIntegrityError(
            f"No SHA-256 metadata for {key}; refusing unverified research bytes"
        )
    if expected and _digest(payload) != expected:
        raise StorageIntegrityError(
            f"SHA-256 mismatch for {key}; refusing scientifically unsafe bytes"
        )
    return payload


def delete_fits(path: str) -> None:
    """Delete an object and its integrity metadata; missing objects are fine."""
    key = normalize_storage_key(path)
    if _backend() == "s3":
        _s3_client().delete_object(Bucket=settings.s3_bucket, Key=key)
        return

    full = _validate_path(key)
    full.unlink(missing_ok=True)
    _sidecar_path(full).unlink(missing_ok=True)


def get_storage_metadata(path: str) -> dict[str, Any]:
    """Return non-secret object metadata used by provenance and health APIs."""
    key = normalize_storage_key(path)
    if _backend() == "s3":
        try:
            head = _s3_client().head_object(Bucket=settings.s3_bucket, Key=key)
        except Exception as exc:
            raise FileNotFoundError(f"Research object not found: {key}") from exc
        return {
            "backend": "s3",
            "key": key,
            "size_bytes": int(head.get("ContentLength", 0)),
            "sha256": str((head.get("Metadata") or {}).get("sha256") or "") or None,
            "version_id": head.get("VersionId"),
        }

    full = _validate_path(key)
    if not full.is_file():
        raise FileNotFoundError(f"Research object not found: {key}")
    sidecar = _sidecar_path(full)
    return {
        "backend": "local",
        "key": key,
        "size_bytes": full.stat().st_size,
        "sha256": sidecar.read_text(encoding="ascii").strip() if sidecar.is_file() else None,
        "version_id": None,
    }


def storage_healthcheck() -> dict[str, Any]:
    """Round-trip a small object through the configured storage backend."""
    key = f".health/{uuid.uuid4().hex}.probe"
    payload = b"standard-astro-storage-health-v1"
    try:
        upload_fits(key, payload)
        observed = download_fits(key)
        if observed != payload:
            raise StorageIntegrityError("Health probe returned different bytes")
        return {"ok": True, "backend": _backend()}
    finally:
        try:
            delete_fits(key)
        except Exception:
            # Preserve the original failure.  Orphan health probes live under
            # a dedicated prefix and can be lifecycle-expired safely.
            pass
