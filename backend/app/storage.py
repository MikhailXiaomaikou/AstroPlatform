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

import base64
import hashlib
import hmac
import os
import tempfile
import uuid
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings


class StorageIntegrityError(IOError):
    """Raised when stored bytes do not match their recorded SHA-256."""


class StorageDeletionError(IOError):
    """Raised when durable deletion cannot be proved."""


class StorageOwnershipError(FileNotFoundError):
    """Raised when an object key is not registered to the requested owner.

    This intentionally has the same public semantics as a missing object so
    callers cannot use storage APIs to enumerate another user's files.
    """


class StorageOwnerRequired(PermissionError):
    """Raised when a user-file operation has no authenticated owner context."""


class StorageOwnerInactive(PermissionError):
    """Raised when account deletion revoked new object persistence."""


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


async def lock_active_storage_owner(db: Any, owner_id: str | uuid.UUID) -> uuid.UUID:
    """Lock and verify an upload owner in the caller's SQL transaction.

    Holding this lock from immediately before object upload through DataFile
    commit serializes uploads with account deletion: whichever transaction
    wins first leaves a ledger that the deletion scan can see.
    """

    try:
        owner_uuid = owner_id if isinstance(owner_id, uuid.UUID) else uuid.UUID(str(owner_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise StorageOwnerInactive("Storage owner is invalid") from exc

    from sqlalchemy import select

    from app.models.schemas import User

    owner = (
        await db.execute(
            select(User)
            .where(User.id == owner_uuid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if owner is None or str(owner.account_status or "").upper() != "ACTIVE":
        raise StorageOwnerInactive("Account deletion revoked new object storage")
    return owner_uuid


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


def create_presigned_upload_url(
    path: str,
    *,
    sha256: str,
    size_bytes: int,
    content_type: str = "application/octet-stream",
    expires_seconds: int = 15 * 60,
) -> dict[str, Any]:
    """Create a short-lived integrity-bound direct-upload URL for a worker.

    Local storage intentionally has no equivalent: a science worker must not
    gain an API-process filesystem write primitive. Hosted worker execution
    therefore requires the configured S3/R2 authority for large artifacts.
    """

    key = normalize_storage_key(path)
    digest = str(sha256 or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("Artifact SHA-256 must contain 64 hexadecimal characters")
    if _backend() != "s3":
        raise RuntimeError("Direct worker artifact upload requires STORAGE_BACKEND=s3")
    declared_size = int(size_bytes)
    if declared_size <= 0 or declared_size > 2 * 1024 * 1024 * 1024:
        raise ValueError("Artifact size is outside the supported range")
    lifetime = max(60, min(int(expires_seconds), 60 * 60))
    media_type = str(content_type or "application/octet-stream").strip()[:255]
    checksum_sha256 = base64.b64encode(bytes.fromhex(digest)).decode("ascii")
    params = {
        "Bucket": settings.s3_bucket,
        "Key": key,
        "ContentType": media_type,
        "ContentLength": declared_size,
        "Metadata": {"sha256": digest},
        # Bind the presigned capability to the expected bytes.  Metadata is
        # still written for provenance, but is never accepted as proof of
        # integrity because the uploading Worker controls it.
        "ChecksumSHA256": checksum_sha256,
    }
    url = _s3_client().generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=lifetime,
        HttpMethod="PUT",
    )
    return {
        "method": "PUT",
        "url": url,
        "artifact_ref": key,
        "expires_in": lifetime,
        "headers": {
            "Content-Type": media_type,
            "Content-Length": str(declared_size),
            "x-amz-meta-sha256": digest,
            "x-amz-checksum-sha256": checksum_sha256,
        },
    }


def _s3_missing(exc: Exception) -> bool:
    code = str(
        getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    ).lower()
    return code in {"404", "nosuchkey", "notfound", "no_such_key"}


def _stream_sha256(body: Any, *, expected_size_bytes: int) -> tuple[int, str]:
    """Hash one bounded streaming body without buffering it in API memory."""

    observed_size = 0
    digest = hashlib.sha256()
    iterator = (
        body.iter_chunks(chunk_size=1024 * 1024)
        if hasattr(body, "iter_chunks")
        else iter(lambda: body.read(1024 * 1024), b"")
    )
    try:
        for chunk in iterator:
            if not chunk:
                continue
            observed_size += len(chunk)
            if observed_size > expected_size_bytes:
                raise StorageIntegrityError(
                    "Stored object exceeds its signed size receipt"
                )
            digest.update(chunk)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    return observed_size, digest.hexdigest()


def verify_storage_object(
    path: str,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
) -> dict[str, Any]:
    """Verify an object's actual bytes against a server-held receipt.

    A native full-object SHA-256 returned by S3 is preferred.  Compatible
    stores that do not expose that checksum are read through a bounded stream
    and hashed by the control plane.  User-supplied ``x-amz-meta-sha256`` is
    deliberately ignored.
    """

    key = normalize_storage_key(path)
    digest = str(expected_sha256 or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("Expected artifact SHA-256 is invalid")
    size = int(expected_size_bytes)
    if size <= 0 or size > 2 * 1024 * 1024 * 1024:
        raise ValueError("Expected artifact size is outside the supported range")

    if _backend() == "s3":
        client = _s3_client()
        try:
            try:
                head = client.head_object(
                    Bucket=settings.s3_bucket,
                    Key=key,
                    ChecksumMode="ENABLED",
                )
            except Exception as checksum_exc:
                # R2/MinIO versions without ChecksumMode can still be verified
                # safely by streaming the object.  A second plain HEAD keeps
                # actual permission/outage errors visible instead of treating
                # them as a missing checksum.
                if _s3_missing(checksum_exc):
                    raise FileNotFoundError(
                        f"Research object not found: {key}"
                    ) from checksum_exc
                head = client.head_object(Bucket=settings.s3_bucket, Key=key)
        except FileNotFoundError:
            raise
        except Exception as exc:
            if _s3_missing(exc):
                raise FileNotFoundError(f"Research object not found: {key}") from exc
            raise

        observed_size = int(head.get("ContentLength", -1))
        if observed_size != size:
            raise StorageIntegrityError(
                f"Stored object size mismatch for {key}: {observed_size} != {size}"
            )
        native_checksum = str(head.get("ChecksumSHA256") or "").strip()
        if native_checksum:
            try:
                native_digest = base64.b64decode(
                    native_checksum, validate=True
                ).hex()
            except (TypeError, ValueError) as exc:
                raise StorageIntegrityError(
                    f"Stored object has an invalid native SHA-256 for {key}"
                ) from exc
            if not hmac.compare_digest(native_digest, digest):
                raise StorageIntegrityError(
                    f"Stored object native SHA-256 mismatch for {key}"
                )
            return {
                "backend": "s3",
                "key": key,
                "size_bytes": observed_size,
                "sha256": digest,
                "verification_method": "s3_checksum_sha256",
                "version_id": head.get("VersionId"),
                "etag": head.get("ETag"),
            }

        try:
            response = client.get_object(Bucket=settings.s3_bucket, Key=key)
        except Exception as exc:
            if _s3_missing(exc):
                raise FileNotFoundError(f"Research object not found: {key}") from exc
            raise
        streamed_size, streamed_digest = _stream_sha256(
            response["Body"], expected_size_bytes=size
        )
        if streamed_size != size or not hmac.compare_digest(streamed_digest, digest):
            raise StorageIntegrityError(
                f"Stored object byte-level SHA-256 mismatch for {key}"
            )
        return {
            "backend": "s3",
            "key": key,
            "size_bytes": streamed_size,
            "sha256": streamed_digest,
            "verification_method": "streamed_sha256",
            "version_id": response.get("VersionId") or head.get("VersionId"),
            "etag": response.get("ETag") or head.get("ETag"),
        }

    full = _validate_path(key)
    if not full.is_file():
        raise FileNotFoundError(f"Research object not found: {key}")
    with full.open("rb") as body:
        streamed_size, streamed_digest = _stream_sha256(
            body, expected_size_bytes=size
        )
    if streamed_size != size or not hmac.compare_digest(streamed_digest, digest):
        raise StorageIntegrityError(
            f"Stored object byte-level SHA-256 mismatch for {key}"
        )
    return {
        "backend": "local",
        "key": key,
        "size_bytes": streamed_size,
        "sha256": streamed_digest,
        "verification_method": "streamed_sha256",
        "version_id": None,
        "etag": None,
    }


def promote_verified_storage_object(
    source_path: str,
    destination_path: str,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
    content_type: str = "application/octet-stream",
    source_version_id: str | None = None,
    source_etag: str | None = None,
) -> dict[str, Any]:
    """Server-copy verified staging bytes into a Worker-unwritable key.

    The source version (or its ETag on stores without version IDs) is pinned so
    the copy cannot silently select a different staging object after the
    caller's byte-level verification.  The destination is verified again and
    only that destination may become authoritative scientific evidence.
    """

    source_key = normalize_storage_key(source_path)
    destination_key = normalize_storage_key(destination_path)
    if source_key == destination_key:
        raise ValueError("Authoritative artifact key must differ from staging key")
    digest = str(expected_sha256 or "").strip().lower().removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("Expected artifact SHA-256 is invalid")
    size = int(expected_size_bytes)
    if size <= 0 or size > 2 * 1024 * 1024 * 1024:
        raise ValueError("Expected artifact size is outside the supported range")

    if _backend() == "s3":
        client = _s3_client()
        copy_source: dict[str, str] = {
            "Bucket": settings.s3_bucket,
            "Key": source_key,
        }
        if source_version_id:
            copy_source["VersionId"] = str(source_version_id)
        params: dict[str, Any] = {
            "Bucket": settings.s3_bucket,
            "Key": destination_key,
            "CopySource": copy_source,
            "MetadataDirective": "REPLACE",
            "Metadata": {"sha256": digest},
            "ContentType": str(content_type or "application/octet-stream")[:255],
            "ChecksumAlgorithm": "SHA256",
        }
        if not source_version_id:
            if not source_etag:
                raise StorageIntegrityError(
                    "Storage did not expose a source version or ETag for safe promotion"
                )
            params["CopySourceIfMatch"] = str(source_etag)
        client.copy_object(**params)
        promoted = verify_storage_object(
            destination_key,
            expected_sha256=digest,
            expected_size_bytes=size,
        )
        return {**promoted, "source_key": source_key}

    source = _validate_path(source_key)
    destination = _validate_path(destination_key)
    if not source.is_file():
        raise FileNotFoundError(f"Research object not found: {source_key}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as body:
        payload = body.read(size + 1)
    if len(payload) != size or not hmac.compare_digest(_digest(payload), digest):
        raise StorageIntegrityError(
            f"Staging object changed before promotion for {source_key}"
        )
    _atomic_write(destination, payload)
    _atomic_write(_sidecar_path(destination), f"{digest}\n".encode("ascii"))
    promoted = verify_storage_object(
        destination_key,
        expected_sha256=digest,
        expected_size_bytes=size,
    )
    return {**promoted, "source_key": source_key}


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


def delete_fits_all_versions(path: str) -> None:
    """Permanently delete one object, including version-store history.

    Normal scientific replacement and cleanup use :func:`delete_fits`. Account
    erasure uses this stronger operation so a versioned S3/R2 bucket cannot
    retain recoverable user data behind a delete marker. Failure to enumerate,
    delete, or verify versions is surfaced to the caller and must be retried.
    """

    key = normalize_storage_key(path)
    if _backend() != "s3":
        delete_fits(key)
        return

    client = _s3_client()
    versioning = client.get_bucket_versioning(Bucket=settings.s3_bucket)
    status = str((versioning or {}).get("Status") or "")
    if status not in {"Enabled", "Suspended"}:
        client.delete_object(Bucket=settings.s3_bucket, Key=key)
        return

    def _exact_versions() -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=key):
            for item in [
                *(page.get("Versions") or []),
                *(page.get("DeleteMarkers") or []),
            ]:
                if str(item.get("Key") or "") != key:
                    continue
                version_id = item.get("VersionId")
                if version_id is None:
                    raise StorageDeletionError(
                        f"Object version has no VersionId: {key}"
                    )
                found.append({"Key": key, "VersionId": str(version_id)})
        return found

    versions = _exact_versions()
    for offset in range(0, len(versions), 1000):
        response = client.delete_objects(
            Bucket=settings.s3_bucket,
            Delete={"Objects": versions[offset : offset + 1000], "Quiet": True},
        )
        errors = list((response or {}).get("Errors") or [])
        if errors:
            error_codes = sorted({str(item.get("Code") or "unknown") for item in errors})
            raise StorageDeletionError(
                f"Object version deletion failed for {key}: {','.join(error_codes)}"
            )

    if _exact_versions():
        raise StorageDeletionError(
            f"Object versions remain after permanent deletion: {key}"
        )


def list_storage_keys(prefix: str) -> set[str]:
    """List every object key below an owner-scoped prefix.

    Account erasure uses this as a recovery fallback when a database restore
    predates an upload-issuance ledger row.  On versioned S3/R2 stores the
    version listing is used so keys hidden behind delete markers are still
    discovered.  The function returns keys only; callers must delete and then
    verify every version with :func:`delete_fits_all_versions`.
    """

    normalized_prefix = normalize_storage_key(prefix).rstrip("/") + "/"
    if _backend() == "s3":
        client = _s3_client()
        versioning = client.get_bucket_versioning(Bucket=settings.s3_bucket)
        status = str((versioning or {}).get("Status") or "")
        keys: set[str] = set()
        if status in {"Enabled", "Suspended"}:
            paginator = client.get_paginator("list_object_versions")
            for page in paginator.paginate(
                Bucket=settings.s3_bucket,
                Prefix=normalized_prefix,
            ):
                for item in [
                    *(page.get("Versions") or []),
                    *(page.get("DeleteMarkers") or []),
                ]:
                    key = str(item.get("Key") or "")
                    if key.startswith(normalized_prefix):
                        keys.add(normalize_storage_key(key))
            return keys

        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=settings.s3_bucket,
            Prefix=normalized_prefix,
        ):
            for item in page.get("Contents") or []:
                key = str(item.get("Key") or "")
                if key.startswith(normalized_prefix):
                    keys.add(normalize_storage_key(key))
        return keys

    root = _validate_path(normalized_prefix)
    if not root.exists():
        return set()
    storage_root_source = _storage_root
    storage_root = Path(
        storage_root_source() if callable(storage_root_source) else storage_root_source
    ).resolve()
    return {
        normalize_storage_key(path.resolve().relative_to(storage_root).as_posix())
        for path in root.rglob("*")
        if path.is_file() and not path.name.endswith(".sha256")
    }


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
