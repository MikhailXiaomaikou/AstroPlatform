"""Owner-scoped workspaces, immutable sources, and scientific reviews."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class ResearchWorkspace(Base):
    """A private container for one user's registered research workflow."""

    __tablename__ = "research_workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_research_workspace_owner_updated", "user_id", "updated_at"),
        Index("idx_research_workspace_owner_status", "user_id", "status"),
    )


class SourceDocument(Base):
    """One immutable, versioned source-document record."""

    __tablename__ = "source_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("research_workspaces.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    supersedes_source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("source_documents.id"), nullable=True
    )
    source_profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_identifier: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_document_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_artifacts: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    raw_artifact_hashes: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    lifecycle_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    coverage_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_metadata: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_profile_key",
            "canonical_identifier",
            "version",
            name="uq_source_document_registered_version",
        ),
        Index("idx_source_document_owner_created", "user_id", "created_at"),
        Index("idx_source_document_workspace_version", "workspace_id", "version"),
    )


class SourceExtraction(Base):
    """A content-addressed extraction; application services only append rows."""

    __tablename__ = "source_extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("source_documents.id"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reader_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_source_document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_payload: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    extraction_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    extraction_artifacts: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    extraction_artifact_hashes: Mapped[dict] = mapped_column(
        JSONType(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "reader_version",
            name="uq_source_extraction_document_reader",
        ),
        Index("idx_source_extraction_owner_created", "user_id", "created_at"),
    )


class ClaimAuditReview(Base):
    """Append-only scientific review bound to exact claim and source hashes."""

    __tablename__ = "claim_audit_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("claim_audits.id"), nullable=False, index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("research_workspaces.id"), nullable=False, index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("source_documents.id"), nullable=False, index=True
    )
    source_extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("source_extractions.id"), nullable=False, index=True
    )
    audit_owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=False, index=True
    )
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Historical column name retained for schema compatibility. New rows store
    # only an audit-scoped pseudonym here, never the account username.
    reviewer_username: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(71), nullable=False)
    claim_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    anchor_ids: Mapped[list] = mapped_column(JSONType(), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    review_scope: Mapped[str] = mapped_column(
        String(64), nullable=False, default="scientific_claim_review"
    )
    supports_finalization: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_claim_audit_review_audit_created", "audit_id", "created_at"),
        Index("idx_claim_audit_review_reviewer_created", "reviewer_user_id", "created_at"),
    )
