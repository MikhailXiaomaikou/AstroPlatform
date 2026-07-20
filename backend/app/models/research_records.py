"""Durable records for scientific provenance and long-running research jobs."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.schemas import JSONType, UUIDType


class ProvenanceRecord(Base):
    """One immutable activity in a user-owned scientific lineage graph."""

    __tablename__ = "provenance_records"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    activity: Mapped[str] = mapped_column(String(255), nullable=False)
    params: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    parent_ids: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    agent: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    environment: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    data_release: Mapped[str | None] = mapped_column(String(255), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_provenance_owner_entity", "user_id", "entity_id"),
        Index("idx_provenance_entity_created", "entity_id", "created_at"),
    )


class ResearchJob(Base):
    """Persistent owner-scoped lifecycle for a Celery-backed scientific job."""

    __tablename__ = "research_jobs"

    job_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id"), nullable=True, index=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("chat_sessions.id"), nullable=True, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    args: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    args_replayable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSONType(), nullable=True
    )
    # Server HMAC over the completed job identity, inputs, and persisted result.
    # A client-authored or manually inserted ``publication_ready`` flag is not
    # publication evidence without this binding.
    attestation: Mapped[dict | None] = mapped_column(JSONType(), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    background_backend: Mapped[str] = mapped_column(
        String(32), nullable=False, default="celery"
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("research_workspaces.id"), nullable=True, index=True
    )
    workflow_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    capability_requirements: Mapped[dict] = mapped_column(
        JSONType(), nullable=False, default=dict
    )
    current_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_research_job_owner_created", "user_id", "created_at"),
        Index("idx_research_job_owner_status", "user_id", "status"),
    )
