"""Durable records for owner-scoped HTTPS science workers."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.schemas import JSONType, UUIDType


class WorkerEnrollmentToken(Base):
    """One short-lived, single-use worker enrollment secret digest."""

    __tablename__ = "worker_enrollment_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_worker_enrollment_owner_expiry", "user_id", "expires_at"),
    )


class WorkerNode(Base):
    """A user-owned science node authenticated with an Ed25519 public key."""

    __tablename__ = "worker_nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(
        String(71), nullable=False, unique=True
    )
    protocol_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )
    capabilities: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    release_manifest: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_worker_node_owner_status", "user_id", "status"),
    )


class ScienceExecutionAttempt(Base):
    """One immutable-numbered lease attempt for a registered science job."""

    __tablename__ = "science_execution_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("research_jobs.job_id"), nullable=False, index=True
    )
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("claim_audits.id"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    worker_node_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("worker_nodes.id"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="LEASED", index=True
    )
    lease_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Copied from the server-signed task envelope at lease time.  Keeping the
    # binding in columns makes stale/revoked executions queryable even when an
    # envelope schema is retired later.
    workflow_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    workflow_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    registry_epoch: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registry_entry_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    entrypoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    runner_image_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    task_envelope: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    artifact_manifest: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_number", name="uq_science_attempt_job_number"),
        Index("idx_science_attempt_owner_status", "user_id", "status"),
        Index("idx_science_attempt_worker_lease", "worker_node_id", "lease_expires_at"),
    )


class WorkerArtifactIssuance(Base):
    """Append-only receipt for every direct-upload URL issued to a worker.

    ``ScienceExecutionAttempt.artifact_manifest`` remains the compact attempt
    summary.  This table is the durable discovery ledger: reissuing an upload
    URL creates another row and never erases the object reference associated
    with an earlier URL.
    """

    __tablename__ = "worker_artifact_issuances"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), nullable=False, index=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("science_execution_attempts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    worker_node_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("worker_nodes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # ``artifact_ref`` is the Worker-writable staging key.  The authoritative
    # key is reserved before the upload URL leaves the API and is never exposed
    # as a write capability to the Worker.
    artifact_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    authoritative_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    authoritative_version_id: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    verification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    staging_cleaned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    authoritative_cleaned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_worker_artifact_attempt_created", "attempt_id", "created_at"),
        Index("idx_worker_artifact_owner_created", "user_id", "created_at"),
    )
