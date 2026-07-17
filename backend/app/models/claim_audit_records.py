"""Durable records for claim audits, evidence packs, and privacy controls."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.schemas import JSONType, UUIDType


class ClaimAudit(Base):
    """One owner-scoped scientific-claim audit and its terminal verdict."""

    __tablename__ = "claim_audits"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="QUEUED", index=True
    )
    scientific_verdict: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_value: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_input_refs: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    dataset_hints: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    normalized_claims: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    capability_gaps: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    evidence_record_ids: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    child_job_ids: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    evidence_graph: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    fact_check_report: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "request_hash", name="uq_claim_audit_owner_request"),
        Index("idx_claim_audit_owner_created", "user_id", "created_at"),
        Index("idx_claim_audit_owner_status", "user_id", "lifecycle_status"),
    )


class EvidencePack(Base):
    """Immutable private artifact produced from one completed claim audit."""

    __tablename__ = "evidence_packs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("claim_audits.id"), nullable=False, unique=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="FINALIZED")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    artifact_ref: Mapped[str] = mapped_column(Text, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    signature: Mapped[str] = mapped_column(String(96), nullable=False)
    key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    upload_lease_id: Mapped[str] = mapped_column(String(64), nullable=False)
    upload_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrivacyPreference(Base):
    """Explicit analytics choice for one account."""

    __tablename__ = "privacy_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), primary_key=True
    )
    analytics_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AccountDeletionTombstone(Base):
    """Restore marker plus a transient queue for late artifact cleanup."""

    __tablename__ = "account_deletion_tombstones"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # A worker can finish after the main row/object scan. These fields remain
    # only until its late artifacts are permanently erased, then are cleared
    # so the long-lived restore marker contains no account identifier.
    pending_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), nullable=True
    )
    pending_artifact_refs: Mapped[list] = mapped_column(
        JSONType(), nullable=False, default=list
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backup_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtifactCleanupQueue(Base):
    """Durable discovery record for uploaded but not-yet-ledgered objects."""

    __tablename__ = "artifact_cleanup_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    artifact_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    reason_class: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    not_before: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Invitation(Base):
    """One-time invite; only a one-way digest is stored."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="alpha")
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=True
    )
    used_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
