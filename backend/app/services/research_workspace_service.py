"""Owner-isolated workspace, immutable source, and review operations."""

from __future__ import annotations

import hashlib
import os
import unicodedata
import uuid
from collections.abc import Collection
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim_audit_records import ClaimAudit
from app.models.workspace_records import (
    ClaimAuditReview,
    ResearchWorkspace,
    SourceDocument,
    SourceExtraction,
)
from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_ATOM_URL,
    UNION3_EXTRACTION_SCHEMA_VERSION,
    UNION3_PDF_SHA256,
    UNION3_READER_VERSION,
    UNION3_SOURCE_TAR_SHA256,
    UNION3_SOURCE_TAR_URL,
    UNION3_SOURCE_URL,
    Union3PdfTextSnapshot,
    Union3ReaderError,
    build_union3_source_document_hash,
    extract_union3_table9,
    load_registered_union3_snapshot,
    normalize_union3_identifier,
)


WORKSPACE_STATUSES = frozenset({"ACTIVE", "ARCHIVED"})
CLAIM_AUDIT_REVIEW_DECISIONS = frozenset({"APPROVED", "REJECTED", "CHANGES_REQUESTED"})
SCIENTIFIC_REVIEW_SCOPE = "scientific_claim_review"
_UNION3_ACQUISITION_ORDER = [
    "arxiv_atom_metadata",
    "version_pinned_source_tar",
    "authoritative_pdf",
]
_UNION3_SNAPSHOT_PREFIX = f"source-snapshots/union3/{UNION3_ARXIV_ID}"
_UNION3_ALLOWED_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
_MAX_SOURCE_RESPONSE_BYTES = 100 * 1024 * 1024
_MAX_SOURCE_TAR_UNCOMPRESSED_BYTES = 500 * 1024 * 1024
_MAX_SOURCE_TAR_ENTRIES = 10_000
_UNION3_SOURCE_MAX_AUTO_ATTEMPTS = 6
_UNION3_SOURCE_TAR_BYTE_SIZE = 5_414_745
_UNION3_PDF_BYTE_SIZE = 5_953_972
_UNION3_SOURCE_TAR_VALIDATION = {
    "schema_version": "union3_source_tar_validation_v1",
    "entry_count": 40,
    "regular_file_count": 40,
    "directory_count": 0,
    "uncompressed_bytes": 6_295_735,
    "member_manifest_sha256": (
        "40b6f1edf7e8f2df31896221446a884ea9308b97e8c3c5c493caa41f5877a40f"
    ),
    "required_root_files": ["00README.json", "merged.tex"],
    "extracted_to_disk": False,
}
_UNION3_METADATA_IDENTITY_KEYS = {
    "schema_version",
    "entry_id",
    "canonical_identifier",
    "base_identifier",
    "version",
    "title",
    "authors",
    "published",
    "updated",
    "primary_category",
    "categories",
    "pdf_link",
}


class WorkspaceServiceError(ValueError):
    """A classified workspace-service error."""

    def __init__(
        self,
        error_class: str,
        message: str,
        *,
        retryable: bool = False,
        source_document_id: uuid.UUID | None = None,
    ):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable
        self.source_document_id = source_document_id


class WorkspaceNotFoundError(WorkspaceServiceError):
    def __init__(self, message: str = "Research workspace resource not found"):
        super().__init__("workspace_resource_not_found", message)


class WorkspaceInputError(WorkspaceServiceError):
    pass


class WorkspaceConflictError(WorkspaceServiceError):
    pass


class WorkspacePermissionError(WorkspaceServiceError):
    pass


def _source_retry_metadata(
    source_metadata: dict[str, object],
    *,
    retryable: bool,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    previous = source_metadata.get("retry_state")
    previous_count = (
        int(previous.get("attempt_count") or 0) if isinstance(previous, dict) else 0
    )
    attempt_count = previous_count + 1
    auto_retry_exhausted = attempt_count >= _UNION3_SOURCE_MAX_AUTO_ATTEMPTS
    delay_seconds = min(900, 15 * (2 ** max(0, attempt_count - 1)))
    return {
        **source_metadata,
        "retry_state": {
            "attempt_count": attempt_count,
            "last_attempted_at": now.isoformat(),
            "next_retry_at": (
                (now + timedelta(seconds=delay_seconds)).isoformat()
                if retryable and not auto_retry_exhausted
                else None
            ),
            "auto_retry_exhausted": auto_retry_exhausted,
            "max_auto_attempts": _UNION3_SOURCE_MAX_AUTO_ATTEMPTS,
        },
    }


def canonical_claim_hash(claim_text: str) -> str:
    """Hash a claim after deterministic Unicode and whitespace normalization."""

    normalized = unicodedata.normalize("NFKC", " ".join(str(claim_text).split()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def reviewer_pseudonym(audit_id: uuid.UUID, reviewer_user_id: uuid.UUID) -> str:
    """Return an audit-scoped identity that does not expose an account."""

    material = f"standard-astro-reviewer-v1\0{audit_id}\0{reviewer_user_id}"
    return "reviewer:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _is_sha256(value: object) -> bool:
    normalized = str(value or "").strip().lower()
    return len(normalized) == 64 and all(
        character in "0123456789abcdef" for character in normalized
    )


def _require_allowed_resolved_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise Union3ReaderError(
            "source_provenance_invalid",
            "The trusted source receipt contains an invalid resolved URL",
        ) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _UNION3_ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or port not in {None, 443}
    ):
        raise Union3ReaderError(
            "source_provenance_invalid",
            "The trusted source receipt left the registered arXiv allowlist",
        )
    return value


def _metadata_identity_url_matches(value: object, *, expected_path: str) -> bool:
    try:
        parsed = urlparse(str(value or ""))
        port = parsed.port
    except ValueError:
        return False
    normalized_path = parsed.path.removesuffix(".pdf").rstrip("/")
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname == "arxiv.org"
        and port in {None, 80, 443}
        and not parsed.username
        and not parsed.password
        and normalized_path == expected_path
        and not parsed.query
        and not parsed.fragment
    )


def _validate_union3_metadata_identity(metadata_identity: dict[str, object]) -> None:
    if (
        set(metadata_identity) != _UNION3_METADATA_IDENTITY_KEYS
        or not _metadata_identity_url_matches(
            metadata_identity.get("entry_id"),
            expected_path=f"/abs/{UNION3_ARXIV_ID}",
        )
        or not _metadata_identity_url_matches(
            metadata_identity.get("pdf_link"),
            expected_path=f"/pdf/{UNION3_ARXIV_ID}",
        )
        or metadata_identity.get("schema_version") != "union3_arxiv_atom_identity_v1"
        or metadata_identity.get("canonical_identifier") != UNION3_ARXIV_ID
        or metadata_identity.get("base_identifier") != "2311.12098"
        or metadata_identity.get("version") != 4
        or not isinstance(metadata_identity.get("title"), str)
        or not str(metadata_identity["title"]).strip()
        or not isinstance(metadata_identity.get("authors"), list)
        or not metadata_identity["authors"]
        or any(
            not isinstance(author, str) or not author.strip()
            for author in metadata_identity["authors"]
        )
        or not isinstance(metadata_identity.get("published"), str)
        or not str(metadata_identity["published"]).strip()
        or not isinstance(metadata_identity.get("updated"), str)
        or not str(metadata_identity["updated"]).strip()
        or not isinstance(metadata_identity.get("categories"), list)
    ):
        raise Union3ReaderError(
            "source_metadata_identity_mismatch",
            "The trusted source receipt does not identify exactly arXiv v4",
        )


def validate_registered_union3_source_receipt(
    *,
    raw_artifacts: object,
    raw_artifact_hashes: object,
    source_metadata: object,
) -> None:
    """Validate the persisted Atom -> source tar -> PDF acquisition receipt."""

    if (
        not isinstance(raw_artifacts, list)
        or not isinstance(raw_artifact_hashes, dict)
        or not isinstance(source_metadata, dict)
        or set(raw_artifact_hashes) != {"atom_metadata", "source_tar", "pdf"}
    ):
        raise Union3ReaderError(
            "source_receipt_schema_invalid",
            "The persisted Union3 source receipt has an invalid shape",
        )

    metadata_sha256 = str(raw_artifact_hashes["atom_metadata"]).strip().lower()
    source_tar_sha256 = str(raw_artifact_hashes["source_tar"]).strip().lower()
    pdf_sha256 = str(raw_artifact_hashes["pdf"]).strip().lower()
    if (
        not _is_sha256(metadata_sha256)
        or source_tar_sha256 != UNION3_SOURCE_TAR_SHA256
        or pdf_sha256 != UNION3_PDF_SHA256
    ):
        raise Union3ReaderError(
            "source_receipt_checksum_invalid",
            "The persisted Union3 source receipt has an unregistered checksum",
        )

    metadata_identity = source_metadata.get("metadata_identity")
    source_tar_validation = source_metadata.get("source_tar_validation")
    if not isinstance(metadata_identity, dict):
        raise Union3ReaderError(
            "source_metadata_identity_mismatch",
            "The persisted source receipt has no registered metadata identity",
        )
    _validate_union3_metadata_identity(metadata_identity)
    if source_tar_validation != _UNION3_SOURCE_TAR_VALIDATION:
        raise Union3ReaderError(
            "source_tar_validation_invalid",
            "The persisted source tar validation receipt is invalid",
        )

    expected_metadata = {
        "authority": "arxiv_pdf",
        "canonical_source_version": "v4",
        "reader_version": UNION3_READER_VERSION,
        "extraction_schema_version": UNION3_EXTRACTION_SCHEMA_VERSION,
        "acquisition_profile": "arxiv_atom_source_pdf_v1",
        "acquisition_order": list(_UNION3_ACQUISITION_ORDER),
        "metadata_identity": metadata_identity,
        "source_tar_validation": _UNION3_SOURCE_TAR_VALIDATION,
        "authoritative_evidence_role": "authoritative_pdf",
        "html_policy": "auxiliary_display_only",
    }
    if source_metadata != expected_metadata:
        raise Union3ReaderError(
            "source_metadata_receipt_invalid",
            "The persisted Union3 acquisition metadata was modified",
        )

    expected_artifacts = [
        {
            "role": "arxiv_atom_metadata",
            "artifact_ref": (f"{_UNION3_SNAPSHOT_PREFIX}/{metadata_sha256}.atom.xml"),
            "source_url": UNION3_ATOM_URL,
            "content_type": "application/atom+xml",
            "sha256": metadata_sha256,
            "verification_status": "VERIFIED",
        },
        {
            "role": "version_pinned_source_tar",
            "artifact_ref": (
                f"{_UNION3_SNAPSHOT_PREFIX}/{UNION3_SOURCE_TAR_SHA256}.tar.gz"
            ),
            "source_url": UNION3_SOURCE_TAR_URL,
            "content_type": "application/gzip",
            "sha256": UNION3_SOURCE_TAR_SHA256,
            "byte_size": _UNION3_SOURCE_TAR_BYTE_SIZE,
            "verification_status": "VERIFIED",
        },
        {
            "role": "authoritative_pdf",
            "artifact_ref": f"{_UNION3_SNAPSHOT_PREFIX}/{UNION3_PDF_SHA256}.pdf",
            "source_url": UNION3_SOURCE_URL,
            "content_type": "application/pdf",
            "sha256": UNION3_PDF_SHA256,
            "byte_size": _UNION3_PDF_BYTE_SIZE,
            "verification_status": "VERIFIED",
        },
    ]
    if len(raw_artifacts) != len(expected_artifacts):
        raise Union3ReaderError(
            "source_artifact_receipt_invalid",
            "The persisted Union3 artifact chain is incomplete",
        )
    for index, (artifact, expected) in enumerate(
        zip(raw_artifacts, expected_artifacts, strict=True)
    ):
        if not isinstance(artifact, dict):
            raise Union3ReaderError(
                "source_artifact_receipt_invalid",
                "The persisted Union3 artifact chain has an invalid entry",
            )
        expected_keys = set(expected) | {"resolved_url"}
        if index == 0:
            expected_keys.add("byte_size")
        if set(artifact) != expected_keys:
            raise Union3ReaderError(
                "source_artifact_receipt_invalid",
                "The persisted Union3 artifact receipt shape was modified",
            )
        for key, expected_value in expected.items():
            if artifact.get(key) != expected_value:
                raise Union3ReaderError(
                    "source_artifact_receipt_invalid",
                    "The persisted Union3 artifact receipt was modified",
                )
        byte_size = artifact.get("byte_size")
        if (
            type(byte_size) is not int
            or not 0 < byte_size <= _MAX_SOURCE_RESPONSE_BYTES
        ):
            raise Union3ReaderError(
                "source_artifact_receipt_invalid",
                "The persisted Union3 artifact receipt has an invalid byte size",
            )
        _require_allowed_resolved_url(str(artifact.get("resolved_url") or ""))


def _registered_snapshot_records(
    snapshot: Union3PdfTextSnapshot,
) -> tuple[list[dict[str, object]], dict[str, str], dict[str, object]]:
    """Validate and serialize the complete immutable acquisition receipt."""

    metadata_sha256 = str(snapshot.metadata_sha256).strip().lower()
    source_tar_sha256 = str(snapshot.source_tar_sha256).strip().lower()
    pdf_sha256 = str(snapshot.pdf_sha256).strip().lower()
    if not _is_sha256(metadata_sha256):
        raise Union3ReaderError(
            "source_metadata_checksum_invalid",
            "The trusted Atom metadata receipt has no valid SHA-256",
        )
    if source_tar_sha256 != UNION3_SOURCE_TAR_SHA256:
        raise Union3ReaderError(
            "source_tar_checksum_mismatch",
            "The Union3 source tar checksum does not match the registered v4 artifact",
        )
    if pdf_sha256 != UNION3_PDF_SHA256:
        raise Union3ReaderError(
            "pdf_checksum_mismatch",
            "The Union3 PDF checksum does not match the registered v4 artifact",
        )

    expected_metadata_ref = f"{_UNION3_SNAPSHOT_PREFIX}/{metadata_sha256}.atom.xml"
    expected_source_tar_ref = (
        f"{_UNION3_SNAPSHOT_PREFIX}/{UNION3_SOURCE_TAR_SHA256}.tar.gz"
    )
    expected_pdf_ref = f"{_UNION3_SNAPSHOT_PREFIX}/{UNION3_PDF_SHA256}.pdf"
    if (
        snapshot.metadata_artifact_ref != expected_metadata_ref
        or snapshot.source_tar_artifact_ref != expected_source_tar_ref
        or snapshot.artifact_ref != expected_pdf_ref
    ):
        raise Union3ReaderError(
            "source_snapshot_store_binding_invalid",
            "The trusted source receipt is not bound to content-addressed storage",
        )
    if snapshot.source_url != UNION3_SOURCE_URL:
        raise Union3ReaderError(
            "source_url_mismatch",
            "The trusted snapshot URL does not match the registered PDF source",
        )
    metadata_resolved_url = _require_allowed_resolved_url(
        snapshot.metadata_resolved_url
    )
    source_tar_resolved_url = _require_allowed_resolved_url(
        snapshot.source_tar_resolved_url
    )
    pdf_resolved_url = _require_allowed_resolved_url(snapshot.pdf_resolved_url)

    metadata_identity = dict(snapshot.metadata_identity or {})
    _validate_union3_metadata_identity(metadata_identity)
    source_tar_validation = dict(snapshot.source_tar_validation or {})
    if (
        source_tar_validation != _UNION3_SOURCE_TAR_VALIDATION
        or source_tar_validation["entry_count"] > _MAX_SOURCE_TAR_ENTRIES
        or source_tar_validation["uncompressed_bytes"]
        > _MAX_SOURCE_TAR_UNCOMPRESSED_BYTES
    ):
        raise Union3ReaderError(
            "source_tar_validation_invalid",
            "The trusted source tar safety receipt is incomplete or invalid",
        )

    byte_sizes = (
        snapshot.metadata_byte_size,
        snapshot.source_tar_byte_size,
        snapshot.pdf_byte_size,
    )
    if (
        any(
            type(size) is not int or not 0 < size <= _MAX_SOURCE_RESPONSE_BYTES
            for size in byte_sizes
        )
        or snapshot.source_tar_byte_size != _UNION3_SOURCE_TAR_BYTE_SIZE
        or (snapshot.pdf_byte_size != _UNION3_PDF_BYTE_SIZE)
    ):
        raise Union3ReaderError(
            "source_response_size_invalid",
            "The trusted source receipt contains an invalid response size",
        )
    metadata_content_type = snapshot.metadata_content_type.split(";", 1)[0].strip()
    source_tar_content_type = snapshot.source_tar_content_type.split(";", 1)[0].strip()
    pdf_content_type = snapshot.content_type.split(";", 1)[0].strip()
    if (
        metadata_content_type
        not in {"application/atom+xml", "application/xml", "text/xml"}
        or source_tar_content_type
        not in {
            "application/gzip",
            "application/octet-stream",
            "application/x-eprint-tar",
            "application/x-gzip",
            "application/x-tar",
        }
        or pdf_content_type != "application/pdf"
    ):
        raise Union3ReaderError(
            "source_content_type_invalid",
            "The trusted source receipt contains an invalid content type",
        )

    artifacts: list[dict[str, object]] = [
        {
            "role": "arxiv_atom_metadata",
            "artifact_ref": expected_metadata_ref,
            "source_url": UNION3_ATOM_URL,
            "resolved_url": metadata_resolved_url,
            "content_type": metadata_content_type,
            "sha256": metadata_sha256,
            "byte_size": snapshot.metadata_byte_size,
            "verification_status": "VERIFIED",
        },
        {
            "role": "version_pinned_source_tar",
            "artifact_ref": expected_source_tar_ref,
            "source_url": UNION3_SOURCE_TAR_URL,
            "resolved_url": source_tar_resolved_url,
            "content_type": source_tar_content_type,
            "sha256": source_tar_sha256,
            "byte_size": snapshot.source_tar_byte_size,
            "verification_status": "VERIFIED",
        },
        {
            "role": "authoritative_pdf",
            "artifact_ref": expected_pdf_ref,
            "source_url": UNION3_SOURCE_URL,
            "resolved_url": pdf_resolved_url,
            "content_type": pdf_content_type,
            "sha256": pdf_sha256,
            "byte_size": snapshot.pdf_byte_size,
            "verification_status": "VERIFIED",
        },
    ]
    artifact_hashes = {
        "atom_metadata": metadata_sha256,
        "source_tar": source_tar_sha256,
        "pdf": pdf_sha256,
    }
    source_metadata: dict[str, object] = {
        "authority": "arxiv_pdf",
        "canonical_source_version": "v4",
        "reader_version": UNION3_READER_VERSION,
        "extraction_schema_version": UNION3_EXTRACTION_SCHEMA_VERSION,
        "acquisition_profile": "arxiv_atom_source_pdf_v1",
        "acquisition_order": list(_UNION3_ACQUISITION_ORDER),
        "metadata_identity": metadata_identity,
        "source_tar_validation": source_tar_validation,
        "authoritative_evidence_role": "authoritative_pdf",
        "html_policy": "auxiliary_display_only",
    }
    validate_registered_union3_source_receipt(
        raw_artifacts=artifacts,
        raw_artifact_hashes=artifact_hashes,
        source_metadata=source_metadata,
    )
    return artifacts, artifact_hashes, source_metadata


def _unacquired_source_records() -> tuple[
    list[dict[str, object]], dict[str, str], dict[str, object]
]:
    """Describe the registered chain without claiming unobserved acquisition."""

    artifacts: list[dict[str, object]] = [
        {
            "role": "arxiv_atom_metadata",
            "source_url": UNION3_ATOM_URL,
            "verification_status": "NOT_ACQUIRED",
        },
        {
            "role": "version_pinned_source_tar",
            "source_url": UNION3_SOURCE_TAR_URL,
            "expected_sha256": UNION3_SOURCE_TAR_SHA256,
            "verification_status": "NOT_ACQUIRED",
        },
        {
            "role": "authoritative_pdf",
            "source_url": UNION3_SOURCE_URL,
            "expected_sha256": UNION3_PDF_SHA256,
            "verification_status": "NOT_ACQUIRED",
        },
    ]
    return (
        artifacts,
        {"source_tar": UNION3_SOURCE_TAR_SHA256, "pdf": UNION3_PDF_SHA256},
        {
            "authority": "arxiv_pdf",
            "canonical_source_version": "v4",
            "acquisition_profile": "arxiv_atom_source_pdf_v1",
            "acquisition_order": list(_UNION3_ACQUISITION_ORDER),
            "authoritative_evidence_role": "authoritative_pdf",
            "html_policy": "auxiliary_display_only",
        },
    )


def configured_scientific_reviewer_usernames() -> frozenset[str]:
    """Return the fail-closed configured reviewer username allowlist."""

    return frozenset(
        value.strip().lower()
        for value in os.getenv("SCIENTIFIC_REVIEWER_USERNAMES", "").split(",")
        if value.strip()
    )


def _is_configured_reviewer(
    username: str,
    reviewer_usernames: Collection[str] | None,
) -> bool:
    allowed = (
        configured_scientific_reviewer_usernames()
        if reviewer_usernames is None
        else frozenset(str(value).strip().lower() for value in reviewer_usernames)
    )
    return bool(allowed) and str(username).strip().lower() in allowed


def _clean_title(title: str) -> str:
    clean = str(title).strip()
    if not clean:
        raise WorkspaceInputError(
            "workspace_title_empty", "Workspace title cannot be empty"
        )
    if len(clean) > 160:
        raise WorkspaceInputError(
            "workspace_title_too_long", "Workspace title cannot exceed 160 characters"
        )
    return clean


def _clean_description(description: str) -> str:
    clean = str(description).strip()
    if len(clean) > 20_000:
        raise WorkspaceInputError(
            "workspace_description_too_long",
            "Workspace description cannot exceed 20000 characters",
        )
    return clean


async def create_workspace(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    description: str = "",
) -> ResearchWorkspace:
    workspace = ResearchWorkspace(
        user_id=user_id,
        title=_clean_title(title),
        description=_clean_description(description),
        status="ACTIVE",
    )
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


async def list_workspaces(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[ResearchWorkspace]:
    return list(
        (
            await db.execute(
                select(ResearchWorkspace)
                .where(ResearchWorkspace.user_id == user_id)
                .order_by(
                    ResearchWorkspace.updated_at.desc(), ResearchWorkspace.id.desc()
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )


async def get_owned_workspace(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> ResearchWorkspace:
    workspace = (
        await db.execute(
            select(ResearchWorkspace).where(
                ResearchWorkspace.id == workspace_id,
                ResearchWorkspace.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFoundError()
    return workspace


async def update_workspace(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
) -> ResearchWorkspace:
    workspace = await get_owned_workspace(
        db, user_id=user_id, workspace_id=workspace_id
    )
    if title is not None:
        workspace.title = _clean_title(title)
    if description is not None:
        workspace.description = _clean_description(description)
    if status is not None:
        normalized_status = str(status).strip().upper()
        if normalized_status not in WORKSPACE_STATUSES:
            raise WorkspaceInputError(
                "workspace_status_invalid",
                "Workspace status must be ACTIVE or ARCHIVED",
            )
        workspace.status = normalized_status
    await db.commit()
    await db.refresh(workspace)
    return workspace


async def _latest_source_document(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    source_profile_key: str,
    canonical_identifier: str,
) -> SourceDocument | None:
    return (
        await db.execute(
            select(SourceDocument)
            .where(
                SourceDocument.workspace_id == workspace_id,
                SourceDocument.user_id == user_id,
                SourceDocument.source_profile_key == source_profile_key,
                SourceDocument.canonical_identifier == canonical_identifier,
            )
            .order_by(SourceDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _source_extraction(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    source_document_id: uuid.UUID,
) -> SourceExtraction | None:
    return (
        await db.execute(
            select(SourceExtraction).where(
                SourceExtraction.source_document_id == source_document_id,
                SourceExtraction.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def queue_union3_source(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    source_profile_key: str,
    identifier: str,
    create_new_version: bool = False,
) -> tuple[SourceDocument, SourceExtraction | None]:
    """Persist a durable source request without performing network I/O.

    PostgreSQL is the source of truth for source acquisition.  Celery only
    wakes the control worker, so a broker restart cannot make the request
    disappear.  A queued record claims only the registered expected hashes;
    it does not claim that any artifact has been downloaded or verified.
    """

    workspace = await get_owned_workspace(
        db, user_id=user_id, workspace_id=workspace_id
    )
    if workspace.status != "ACTIVE":
        raise WorkspaceConflictError(
            "workspace_archived",
            "Archived Workspaces are read-only; restore the Workspace before adding a source",
        )
    try:
        canonical_identifier = normalize_union3_identifier(
            source_profile_key, identifier
        )
    except Union3ReaderError as exc:
        raise WorkspaceInputError(exc.error_class, str(exc)) from exc

    latest = await _latest_source_document(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
        source_profile_key=source_profile_key,
        canonical_identifier=canonical_identifier,
    )
    if latest is not None and not create_new_version:
        return latest, await _source_extraction(
            db, user_id=user_id, source_document_id=latest.id
        )

    version = (latest.version + 1) if latest is not None else 1
    raw_artifacts, raw_artifact_hashes, source_metadata = _unacquired_source_records()
    document = SourceDocument(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        supersedes_source_document_id=latest.id if latest else None,
        source_profile_key=source_profile_key,
        requested_identifier=str(identifier).strip(),
        canonical_identifier=canonical_identifier,
        version=version,
        source_url=UNION3_SOURCE_URL,
        source_document_hash=build_union3_source_document_hash(
            pdf_sha256=UNION3_PDF_SHA256,
            version=version,
        ),
        raw_artifacts=raw_artifacts,
        raw_artifact_hashes=raw_artifact_hashes,
        lifecycle_status="QUEUED",
        coverage_status="PENDING",
        source_metadata=source_metadata,
    )
    db.add(document)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        concurrent = await _latest_source_document(
            db,
            user_id=user_id,
            workspace_id=workspace_id,
            source_profile_key=source_profile_key,
            canonical_identifier=canonical_identifier,
        )
        if concurrent is None:
            raise WorkspaceConflictError(
                "source_document_conflict",
                "Could not queue the immutable source document",
            ) from exc
        return concurrent, await _source_extraction(
            db, user_id=user_id, source_document_id=concurrent.id
        )
    await db.refresh(document)
    return document, None


async def process_queued_union3_source(
    db: AsyncSession,
    *,
    source_document_id: uuid.UUID,
    trusted_snapshot: Union3PdfTextSnapshot | None = None,
) -> tuple[SourceDocument, SourceExtraction | None]:
    """Acquire and extract one durable queued source request.

    The expensive download happens before the row lock.  Duplicate Celery
    delivery may therefore repeat a download, but only one transaction can
    finalize the immutable source/extraction pair.  This is preferable to
    holding a database lock across external network and ``pdftotext`` work.
    """

    document = await db.get(SourceDocument, source_document_id)
    if document is None:
        raise WorkspaceNotFoundError("Source document not found")
    if document.lifecycle_status == "COMPLETED":
        return document, await _source_extraction(
            db, user_id=document.user_id, source_document_id=document.id
        )
    if document.lifecycle_status not in {"QUEUED", "FAILED_RETRYABLE"}:
        raise WorkspaceConflictError(
            "source_document_not_processable",
            "The source document is not eligible for acquisition",
        )

    snapshot: Union3PdfTextSnapshot | None = None
    try:
        if trusted_snapshot is None:
            from app.services.union3_snapshot_loader import (
                fetch_registered_union3_snapshot,
            )

            snapshot = await fetch_registered_union3_snapshot(
                document.canonical_identifier
            )
        else:
            snapshot = trusted_snapshot
        raw_artifacts, raw_artifact_hashes, source_metadata = (
            _registered_snapshot_records(snapshot)
        )
        payload = extract_union3_table9(
            snapshot.text,
            pdf_sha256=snapshot.pdf_sha256,
            source_profile_key=document.source_profile_key,
            identifier=document.canonical_identifier,
            source_document_version=document.version,
            artifact_ref=snapshot.artifact_ref,
        )
    except Union3ReaderError as exc:
        locked = await db.scalar(
            select(SourceDocument)
            .where(SourceDocument.id == source_document_id)
            .with_for_update()
        )
        if locked is None:
            raise WorkspaceNotFoundError("Source document not found") from exc
        if locked.lifecycle_status != "COMPLETED":
            reported_sha = str(snapshot.pdf_sha256).strip().lower() if snapshot else ""
            locked.lifecycle_status = (
                "FAILED_RETRYABLE" if exc.retryable else "FAILED_FINAL"
            )
            locked.coverage_status = (
                "CAPABILITY_GAP" if exc.retryable else "SOURCE_REJECTED"
            )
            locked.source_metadata = _source_retry_metadata(
                {
                    **dict(locked.source_metadata or {}),
                    "reported_pdf_sha256": reported_sha or None,
                },
                retryable=exc.retryable,
            )
            locked.error_class = exc.error_class
            locked.error = str(exc)
            await db.commit()
        raise WorkspaceServiceError(
            exc.error_class,
            str(exc),
            retryable=exc.retryable,
            source_document_id=source_document_id,
        ) from exc

    locked = await db.scalar(
        select(SourceDocument)
        .where(SourceDocument.id == source_document_id)
        .with_for_update()
    )
    if locked is None:
        raise WorkspaceNotFoundError("Source document not found")
    if locked.lifecycle_status == "COMPLETED":
        return locked, await _source_extraction(
            db, user_id=locked.user_id, source_document_id=locked.id
        )
    if locked.lifecycle_status not in {"QUEUED", "FAILED_RETRYABLE"}:
        raise WorkspaceConflictError(
            "source_document_not_processable",
            "The source document is no longer eligible for acquisition",
        )

    source_payload = payload["source"]
    locked.source_document_hash = source_payload["source_document_hash"]
    locked.raw_artifacts = raw_artifacts
    locked.raw_artifact_hashes = raw_artifact_hashes
    locked.lifecycle_status = "COMPLETED"
    locked.coverage_status = payload["coverage_status"]
    locked.source_metadata = source_metadata
    locked.error = None
    locked.error_class = None
    extraction = SourceExtraction(
        source_document_id=locked.id,
        user_id=locked.user_id,
        schema_version=UNION3_EXTRACTION_SCHEMA_VERSION,
        reader_version=UNION3_READER_VERSION,
        input_source_document_hash=locked.source_document_hash,
        extraction_payload=payload,
        extraction_payload_hash=payload["extraction_hash"],
        extraction_artifacts=[],
        extraction_artifact_hashes={},
    )
    db.add(extraction)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        completed = await db.get(SourceDocument, source_document_id)
        if completed is None:
            raise WorkspaceNotFoundError("Source document not found")
        concurrent_extraction = await _source_extraction(
            db,
            user_id=completed.user_id,
            source_document_id=completed.id,
        )
        if completed.lifecycle_status != "COMPLETED" or concurrent_extraction is None:
            raise WorkspaceConflictError(
                "source_extraction_conflict",
                "Could not finalize the immutable source extraction",
            )
        return completed, concurrent_extraction
    await db.refresh(locked)
    await db.refresh(extraction)
    return locked, extraction


async def ingest_union3_source(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    source_profile_key: str,
    identifier: str,
    trusted_snapshot: Union3PdfTextSnapshot | None = None,
    create_new_version: bool = False,
) -> tuple[SourceDocument, SourceExtraction | None]:
    """Create an immutable Union3 document and immutable extraction."""

    await get_owned_workspace(db, user_id=user_id, workspace_id=workspace_id)
    try:
        canonical_identifier = normalize_union3_identifier(
            source_profile_key, identifier
        )
    except Union3ReaderError as exc:
        raise WorkspaceInputError(exc.error_class, str(exc)) from exc

    latest = await _latest_source_document(
        db,
        user_id=user_id,
        workspace_id=workspace_id,
        source_profile_key=source_profile_key,
        canonical_identifier=canonical_identifier,
    )
    if latest is not None and not create_new_version:
        if latest.lifecycle_status != "COMPLETED":
            raise WorkspaceServiceError(
                latest.error_class or "source_document_not_ready",
                latest.error or "The existing source document is not ready",
                retryable=latest.lifecycle_status == "FAILED_RETRYABLE",
                source_document_id=latest.id,
            )
        return latest, await _source_extraction(
            db, user_id=user_id, source_document_id=latest.id
        )
    version = (latest.version + 1) if latest is not None else 1

    snapshot: Union3PdfTextSnapshot | None = None
    raw_artifacts, raw_artifact_hashes, source_metadata = _unacquired_source_records()
    try:
        snapshot = trusted_snapshot or await load_registered_union3_snapshot(
            canonical_identifier
        )
        raw_artifacts, raw_artifact_hashes, source_metadata = (
            _registered_snapshot_records(snapshot)
        )
        payload = extract_union3_table9(
            snapshot.text,
            pdf_sha256=snapshot.pdf_sha256,
            source_profile_key=source_profile_key,
            identifier=canonical_identifier,
            source_document_version=version,
            artifact_ref=snapshot.artifact_ref,
        )
    except Union3ReaderError as exc:
        reported_sha = str(snapshot.pdf_sha256).strip().lower() if snapshot else ""
        artifact_sha = (
            reported_sha
            if len(reported_sha) == 64
            and all(character in "0123456789abcdef" for character in reported_sha)
            else UNION3_PDF_SHA256
        )
        failed_document = SourceDocument(
            workspace_id=workspace_id,
            user_id=user_id,
            supersedes_source_document_id=latest.id if latest else None,
            source_profile_key=source_profile_key,
            requested_identifier=str(identifier).strip(),
            canonical_identifier=canonical_identifier,
            version=version,
            source_url=UNION3_SOURCE_URL,
            source_document_hash=build_union3_source_document_hash(
                pdf_sha256=artifact_sha,
                version=version,
            ),
            raw_artifacts=raw_artifacts,
            raw_artifact_hashes=raw_artifact_hashes,
            lifecycle_status="FAILED_RETRYABLE" if exc.retryable else "FAILED_FINAL",
            coverage_status="CAPABILITY_GAP" if exc.retryable else "SOURCE_REJECTED",
            source_metadata=_source_retry_metadata(
                {
                    **source_metadata,
                    "reported_pdf_sha256": reported_sha or None,
                },
                retryable=exc.retryable,
            ),
            error_class=exc.error_class,
            error=str(exc),
        )
        db.add(failed_document)
        await db.commit()
        await db.refresh(failed_document)
        raise WorkspaceServiceError(
            exc.error_class,
            str(exc),
            retryable=exc.retryable,
            source_document_id=failed_document.id,
        ) from exc

    source_payload = payload["source"]
    document = SourceDocument(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        user_id=user_id,
        supersedes_source_document_id=latest.id if latest else None,
        source_profile_key=source_profile_key,
        requested_identifier=str(identifier).strip(),
        canonical_identifier=canonical_identifier,
        version=version,
        source_url=UNION3_SOURCE_URL,
        source_document_hash=source_payload["source_document_hash"],
        raw_artifacts=raw_artifacts,
        raw_artifact_hashes=raw_artifact_hashes,
        lifecycle_status="COMPLETED",
        coverage_status=payload["coverage_status"],
        source_metadata=source_metadata,
    )
    extraction = SourceExtraction(
        source_document_id=document.id,
        user_id=user_id,
        schema_version=UNION3_EXTRACTION_SCHEMA_VERSION,
        reader_version=UNION3_READER_VERSION,
        input_source_document_hash=document.source_document_hash,
        extraction_payload=payload,
        extraction_payload_hash=payload["extraction_hash"],
        extraction_artifacts=[],
        extraction_artifact_hashes={},
    )
    db.add_all([document, extraction])
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        concurrent = await _latest_source_document(
            db,
            user_id=user_id,
            workspace_id=workspace_id,
            source_profile_key=source_profile_key,
            canonical_identifier=canonical_identifier,
        )
        if concurrent is None:
            raise WorkspaceConflictError(
                "source_document_conflict",
                "Could not create the immutable source document",
            ) from exc
        return concurrent, await _source_extraction(
            db, user_id=user_id, source_document_id=concurrent.id
        )
    await db.refresh(document)
    await db.refresh(extraction)
    return document, extraction


async def list_source_documents(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[tuple[SourceDocument, SourceExtraction | None]]:
    await get_owned_workspace(db, user_id=user_id, workspace_id=workspace_id)
    documents = list(
        (
            await db.execute(
                select(SourceDocument)
                .where(
                    SourceDocument.workspace_id == workspace_id,
                    SourceDocument.user_id == user_id,
                )
                .order_by(SourceDocument.created_at.desc(), SourceDocument.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if not documents:
        return []
    extractions = list(
        (
            await db.execute(
                select(SourceExtraction)
                .where(
                    SourceExtraction.user_id == user_id,
                    SourceExtraction.source_document_id.in_(
                        [document.id for document in documents]
                    ),
                )
                .order_by(
                    SourceExtraction.created_at.desc(),
                    SourceExtraction.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    extraction_by_document: dict[uuid.UUID, SourceExtraction] = {}
    for extraction in extractions:
        extraction_by_document.setdefault(extraction.source_document_id, extraction)
    return [
        (document, extraction_by_document.get(document.id)) for document in documents
    ]


async def get_owned_source_document(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    source_document_id: uuid.UUID,
) -> tuple[SourceDocument, SourceExtraction | None]:
    await get_owned_workspace(db, user_id=user_id, workspace_id=workspace_id)
    document = (
        await db.execute(
            select(SourceDocument).where(
                SourceDocument.id == source_document_id,
                SourceDocument.workspace_id == workspace_id,
                SourceDocument.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if document is None:
        raise WorkspaceNotFoundError("Source document not found")
    extraction = await _source_extraction(
        db, user_id=user_id, source_document_id=document.id
    )
    return document, extraction


async def _review_binding(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    audit_id: uuid.UUID,
    source_document_id: uuid.UUID,
    source_extraction_id: uuid.UUID,
) -> tuple[ClaimAudit, SourceDocument, SourceExtraction]:
    audit = await db.scalar(
        select(ClaimAudit).where(ClaimAudit.id == audit_id).with_for_update()
    )
    document = await db.scalar(
        select(SourceDocument).where(
            SourceDocument.id == source_document_id,
            SourceDocument.workspace_id == workspace_id,
        )
    )
    extraction = await db.scalar(
        select(SourceExtraction).where(
            SourceExtraction.id == source_extraction_id,
            SourceExtraction.source_document_id == source_document_id,
        )
    )
    if audit is None or document is None or extraction is None:
        raise WorkspaceNotFoundError("Claim Audit review target not found")
    if audit.user_id != document.user_id:
        raise WorkspaceNotFoundError("Claim Audit review target not found")
    audit_workspace_id = getattr(audit, "workspace_id", None)
    if audit_workspace_id is not None and audit_workspace_id != workspace_id:
        raise WorkspaceNotFoundError("Claim Audit review target not found")
    return audit, document, extraction


async def create_claim_audit_review(
    db: AsyncSession,
    *,
    reviewer_user_id: uuid.UUID,
    reviewer_username: str,
    workspace_id: uuid.UUID,
    audit_id: uuid.UUID,
    source_document_id: uuid.UUID,
    source_extraction_id: uuid.UUID,
    candidate_id: str,
    claim_hash: str,
    source_hash: str,
    anchor_ids: list[str],
    decision: str,
    comment: str = "",
    reviewer_usernames: Collection[str] | None = None,
) -> ClaimAuditReview:
    """Append a configured, independent review bound to immutable hashes."""

    if not _is_configured_reviewer(reviewer_username, reviewer_usernames):
        raise WorkspacePermissionError(
            "scientific_reviewer_not_configured",
            "This account is not configured as a scientific reviewer",
        )
    audit, document, extraction = await _review_binding(
        db,
        workspace_id=workspace_id,
        audit_id=audit_id,
        source_document_id=source_document_id,
        source_extraction_id=source_extraction_id,
    )
    if audit.lifecycle_status != "COMPLETED":
        raise WorkspaceConflictError(
            "claim_audit_not_reviewable",
            "Only a completed Claim Audit can receive scientific review",
        )
    if audit.scientific_verdict not in {None, "WITHHELD"}:
        raise WorkspaceConflictError(
            "claim_audit_not_withheld",
            "Scientific review can only be appended while the Audit is WITHHELD",
        )
    if not audit.machine_support_eligible or audit.review_status != "PENDING":
        raise WorkspaceConflictError(
            "claim_audit_machine_gate_not_ready",
            "Scientific review requires a passed machine gate and PENDING review state",
        )
    if document.lifecycle_status != "COMPLETED":
        raise WorkspaceConflictError(
            "source_document_not_reviewable",
            "Only a completed source document can receive scientific review",
        )
    if audit.user_id == reviewer_user_id:
        raise WorkspacePermissionError(
            "independent_reviewer_required",
            "The scientific reviewer must be different from the Claim Audit owner",
        )
    normalized_decision = str(decision).strip().upper()
    if normalized_decision not in CLAIM_AUDIT_REVIEW_DECISIONS:
        raise WorkspaceInputError(
            "claim_audit_review_decision_invalid",
            "Decision must be APPROVED, REJECTED, or CHANGES_REQUESTED",
        )
    clean_comment = str(comment).strip()
    if len(clean_comment) > 4000:
        raise WorkspaceInputError(
            "claim_audit_review_comment_too_long",
            "Review comment cannot exceed 4000 characters",
        )
    payload = extraction.extraction_payload
    candidate = next(
        (
            item
            for item in payload.get("candidates", [])
            if item.get("candidate_id") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise WorkspaceNotFoundError("Source candidate not found")
    expected_claim_hash = candidate.get("claim_hash")
    audit_claim_hash = getattr(audit, "claim_hash", None) or canonical_claim_hash(
        audit.claim_text
    )
    if claim_hash != expected_claim_hash or claim_hash != audit_claim_hash:
        raise WorkspaceConflictError(
            "claim_hash_mismatch",
            "The review claim hash does not match both Audit and source candidate",
        )
    if source_hash != document.source_document_hash:
        raise WorkspaceConflictError(
            "source_hash_mismatch",
            "The review source hash does not match the immutable source document",
        )
    expected_anchor_ids = list(candidate.get("source_anchor_ids", []))
    if list(anchor_ids) != expected_anchor_ids or not expected_anchor_ids:
        raise WorkspaceConflictError(
            "anchor_ids_mismatch",
            "The review anchors do not exactly match the source candidate",
        )
    review = ClaimAuditReview(
        audit_id=audit.id,
        workspace_id=workspace_id,
        source_document_id=document.id,
        source_extraction_id=extraction.id,
        audit_owner_user_id=audit.user_id,
        reviewer_user_id=reviewer_user_id,
        reviewer_username=reviewer_pseudonym(audit.id, reviewer_user_id),
        candidate_id=candidate_id,
        claim_hash=claim_hash,
        source_hash=source_hash,
        anchor_ids=expected_anchor_ids,
        decision=normalized_decision,
        review_scope=SCIENTIFIC_REVIEW_SCOPE,
        supports_finalization=normalized_decision == "APPROVED",
        comment=clean_comment,
    )
    db.add(review)
    audit.review_status = normalized_decision
    await db.commit()
    await db.refresh(review)
    return review


async def list_claim_audit_reviews(
    db: AsyncSession,
    *,
    requester_user_id: uuid.UUID,
    requester_username: str,
    workspace_id: uuid.UUID,
    audit_id: uuid.UUID,
    reviewer_usernames: Collection[str] | None = None,
) -> list[ClaimAuditReview]:
    audit = await db.scalar(select(ClaimAudit).where(ClaimAudit.id == audit_id))
    if audit is None:
        raise WorkspaceNotFoundError("Claim Audit not found")
    audit_workspace_id = getattr(audit, "workspace_id", None)
    if audit_workspace_id is not None and audit_workspace_id != workspace_id:
        raise WorkspaceNotFoundError("Claim Audit not found")
    is_owner = audit.user_id == requester_user_id
    is_reviewer = _is_configured_reviewer(requester_username, reviewer_usernames)
    if not is_owner and not is_reviewer:
        raise WorkspaceNotFoundError("Claim Audit not found")
    return list(
        (
            await db.execute(
                select(ClaimAuditReview)
                .where(
                    ClaimAuditReview.audit_id == audit_id,
                    ClaimAuditReview.workspace_id == workspace_id,
                )
                .order_by(
                    ClaimAuditReview.created_at.desc(), ClaimAuditReview.id.desc()
                )
            )
        )
        .scalars()
        .all()
    )


def serialize_workspace(workspace: ResearchWorkspace) -> dict:
    return {
        "workspace_id": str(workspace.id),
        "title": workspace.title,
        "description": workspace.description,
        "status": workspace.status,
        "created_at": workspace.created_at.isoformat()
        if workspace.created_at
        else None,
        "updated_at": workspace.updated_at.isoformat()
        if workspace.updated_at
        else None,
    }


def serialize_extraction(extraction: SourceExtraction | None) -> dict | None:
    if extraction is None:
        return None
    return {
        "source_extraction_id": str(extraction.id),
        "schema_version": extraction.schema_version,
        "reader_version": extraction.reader_version,
        "input_source_document_hash": extraction.input_source_document_hash,
        "extraction_payload": extraction.extraction_payload,
        "extraction_payload_hash": extraction.extraction_payload_hash,
        "extraction_artifacts": list(extraction.extraction_artifacts or []),
        "extraction_artifact_hashes": dict(extraction.extraction_artifact_hashes or {}),
        "created_at": extraction.created_at.isoformat()
        if extraction.created_at
        else None,
    }


def serialize_source_document(
    document: SourceDocument,
    extraction: SourceExtraction | None = None,
) -> dict:
    return {
        "source_document_id": str(document.id),
        "workspace_id": str(document.workspace_id),
        "supersedes_source_document_id": (
            str(document.supersedes_source_document_id)
            if document.supersedes_source_document_id
            else None
        ),
        "source_profile_key": document.source_profile_key,
        "requested_identifier": document.requested_identifier,
        "canonical_identifier": document.canonical_identifier,
        "version": document.version,
        "source_url": document.source_url,
        "source_document_hash": document.source_document_hash,
        "raw_artifacts": list(document.raw_artifacts or []),
        "raw_artifact_hashes": dict(document.raw_artifact_hashes or {}),
        "lifecycle_status": document.lifecycle_status,
        "coverage_status": document.coverage_status,
        "source_metadata": dict(document.source_metadata or {}),
        "error": document.error,
        "error_class": document.error_class,
        "extraction": serialize_extraction(extraction),
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


def serialize_claim_audit_review(review: ClaimAuditReview) -> dict:
    return {
        "review_id": str(review.id),
        "audit_id": str(review.audit_id),
        "workspace_id": str(review.workspace_id),
        "source_document_id": str(review.source_document_id),
        "source_extraction_id": str(review.source_extraction_id),
        "reviewer_pseudonym": review.reviewer_username,
        "candidate_id": review.candidate_id,
        "claim_hash": review.claim_hash,
        "source_hash": review.source_hash,
        "anchor_ids": list(review.anchor_ids or []),
        "decision": review.decision,
        "review_scope": review.review_scope,
        "supports_finalization": review.supports_finalization,
        "comment": review.comment,
        "created_at": review.created_at.isoformat() if review.created_at else None,
    }


__all__ = [
    "CLAIM_AUDIT_REVIEW_DECISIONS",
    "SCIENTIFIC_REVIEW_SCOPE",
    "WORKSPACE_STATUSES",
    "WorkspaceConflictError",
    "WorkspaceInputError",
    "WorkspaceNotFoundError",
    "WorkspacePermissionError",
    "WorkspaceServiceError",
    "canonical_claim_hash",
    "configured_scientific_reviewer_usernames",
    "create_claim_audit_review",
    "create_workspace",
    "get_owned_source_document",
    "get_owned_workspace",
    "ingest_union3_source",
    "list_claim_audit_reviews",
    "list_source_documents",
    "list_workspaces",
    "process_queued_union3_source",
    "queue_union3_source",
    "reviewer_pseudonym",
    "serialize_claim_audit_review",
    "serialize_source_document",
    "serialize_workspace",
    "update_workspace",
    "validate_registered_union3_source_receipt",
]
