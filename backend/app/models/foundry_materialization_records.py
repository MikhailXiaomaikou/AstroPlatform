"""Append-only receipts for reviewed AI source materialization.

These records are deliberately separate from both Candidate validation and the
formal Registry.  Opening a pull request proves only that the reviewed inert
patch was materialized; finalization after merge creates a fresh Candidate
version which must repeat Demo and human review.
"""

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
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.schemas import UUIDType


class FoundryMaterializationAttestation(Base):
    """Signed protected-workflow receipt for one exact pull request."""

    __tablename__ = "foundry_materialization_attestations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    origin_candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="RESTRICT"), nullable=False,
    )
    origin_candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    materialization_request_event_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_events.id", ondelete="RESTRICT"), nullable=False,
        unique=True,
    )
    draft_run_id: Mapped[uuid.UUID] = mapped_column(UUIDType(), nullable=False)
    artifact_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_workflow_run_id: Mapped[str] = mapped_column(String(20), nullable=False)
    artifact_id: Mapped[str] = mapped_column(String(20), nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    base_source_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_source_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_module_path: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_module_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pull_request_state: Mapped[str] = mapped_column(String(16), nullable=False)
    pull_request_url: Mapped[str] = mapped_column(Text, nullable=False)
    pull_request_head_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    pull_request_head_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    github_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    github_workflow_ref: Mapped[str] = mapped_column(Text, nullable=False)
    github_workflow_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    github_run_id: Mapped[str] = mapped_column(String(20), nullable=False)
    github_run_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "origin_candidate_version_id",
            name="uq_foundry_materialization_origin_version",
        ),
        Index("idx_foundry_materialization_pr", "github_repository", "pull_request_number"),
    )


class FoundryMaterializationReceipt(Base):
    """Signed receipt binding a merged PR to a fresh Candidate version."""

    __tablename__ = "foundry_materialization_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    materialization_attestation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_materialization_attestations.id", ondelete="RESTRICT"),
        nullable=False, unique=True,
    )
    origin_candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="RESTRICT"), nullable=False,
    )
    origin_candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    materialized_candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="RESTRICT"), nullable=False,
    )
    pull_request_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pull_request_head_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    pull_request_base_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    pull_request_head_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    merge_commit: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    origin_main_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    merge_commit_is_ancestor_of_origin_main: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    merge_source_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_module_path: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_module_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_lock_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_sbom_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_runner_image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    github_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    github_workflow_ref: Mapped[str] = mapped_column(Text, nullable=False)
    github_workflow_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    github_run_id: Mapped[str] = mapped_column(String(20), nullable=False)
    github_run_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "materialized_candidate_version_id",
            name="uq_foundry_materialization_final_version",
        ),
        Index("idx_fmr_origin_version", "origin_candidate_version_id"),
    )


def _reject_immutable_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are append-only")


for _model in (FoundryMaterializationAttestation, FoundryMaterializationReceipt):
    event.listen(_model, "before_update", _reject_immutable_mutation)
    event.listen(_model, "before_delete", _reject_immutable_mutation)


__all__ = ["FoundryMaterializationAttestation", "FoundryMaterializationReceipt"]
