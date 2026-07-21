"""Durable records for the AI workflow foundry and formal workflow ledger."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.schemas import JSONType, UUIDType


class CapabilityRequest(Base):
    """One owner-scoped request derived from an existing structured gap."""

    __tablename__ = "capability_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("claim_audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    gap_id: Mapped[str] = mapped_column(String(68), nullable=False)
    gap_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    gap_snapshot: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBMITTED", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "audit_id", "gap_id", name="uq_capability_request_owner_audit_gap"
        ),
        Index("idx_capability_request_owner_created", "user_id", "created_at"),
        Index("idx_capability_request_candidate_created", "candidate_id", "created_at"),
    )


class FoundryCandidate(Base):
    """A globally de-duplicated, non-formal workflow candidate."""

    __tablename__ = "foundry_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    gap_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    gap_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    gap_descriptor: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    risk_level: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    generation_route: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_foundry_candidate_status_updated", "status", "updated_at"),
    )


class FoundryCandidateVersion(Base):
    """Immutable content-addressed version of a generated candidate."""

    __tablename__ = "foundry_candidate_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(97), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_bundle: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    workflow_spec: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    workflow_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    patch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_lock_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sbom_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fixture_hashes: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    data_hashes: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    ai_model: Mapped[str] = mapped_column(String(255), nullable=False)
    ai_generation_config: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    validation_runner_image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    created_by_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("candidate_id", "version_number", name="uq_foundry_candidate_version_number"),
        Index("idx_foundry_candidate_version_created", "candidate_id", "created_at"),
    )


class FoundryDemoRun(Base):
    """Immutable non-formal Demo result produced by an isolated runner."""

    __tablename__ = "foundry_demo_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_key: Mapped[str] = mapped_column(String(97), nullable=False, index=True)
    validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("foundry_validation_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    evidence_class: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NON_FORMAL_DEMO"
    )
    publication_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claim_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence_pack_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_spec_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    dependency_lock_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sbom_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fixture_hashes: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    data_hashes: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    ai_model: Mapped[str] = mapped_column(String(255), nullable=False)
    ai_generation_config: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    generation: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    source_pins: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    runner_image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    environment: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    environment_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stdout_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stderr_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    stdout_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stderr_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    structured_result: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    limitations: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    validation_summary: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    failure_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_usage: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    artifact_manifest: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    demo_report_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("evidence_class = 'NON_FORMAL_DEMO'", name="ck_demo_non_formal_evidence"),
        CheckConstraint("publication_ready IS FALSE", name="ck_demo_not_publication_ready"),
        CheckConstraint("claim_eligible IS FALSE", name="ck_demo_not_claim_eligible"),
        CheckConstraint("evidence_pack_allowed IS FALSE", name="ck_demo_no_evidence_pack"),
        Index("idx_foundry_demo_candidate_created", "candidate_id", "created_at"),
        Index("idx_foundry_demo_version_created", "candidate_version_id", "created_at"),
    )


class FoundryValidationRun(Base):
    """Durable trigger and result envelope for isolated candidate validation."""

    __tablename__ = "foundry_validation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED", index=True)
    requested_by_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    runner_image_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    validation_summary: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    failure_class: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_foundry_validation_candidate_created", "candidate_id", "created_at"),
    )


class FoundryCandidateEvent(Base):
    """Append-only hash-chained lifecycle event for one candidate."""

    __tablename__ = "foundry_candidate_events"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_payload: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_foundry_event_candidate_created", "candidate_id", "created_at"),
        # One candidate event may have at most one direct successor.  The
        # separate partial index for NULL also permits exactly one genesis
        # event.  Together these constraints make a fork impossible even if a
        # future caller bypasses the service-layer candidate row lock.
        Index(
            "uq_foundry_event_candidate_parent",
            "candidate_id",
            "previous_event_hash",
            unique=True,
            postgresql_where=text("previous_event_hash IS NOT NULL"),
            sqlite_where=text("previous_event_hash IS NOT NULL"),
        ),
        Index(
            "uq_foundry_event_candidate_genesis",
            "candidate_id",
            unique=True,
            postgresql_where=text("previous_event_hash IS NULL"),
            sqlite_where=text("previous_event_hash IS NULL"),
        ),
    )


class FoundryReview(Base):
    """Append-only human review bound to one exact candidate version hash."""

    __tablename__ = "foundry_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewer_pseudonym: Mapped[str] = mapped_column(String(71), nullable=False)
    review_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_foundry_review_version_created", "candidate_version_id", "created_at"),
        Index("idx_foundry_review_reviewer_created", "reviewer_user_id", "created_at"),
    )


class FoundryFormalBuildAttestation(Base):
    """Immutable receipt accepted only from the protected formal-build callback."""

    __tablename__ = "foundry_formal_build_attestations"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    git_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    dependency_lock_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    formal_sbom_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    test_report_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    formal_release_audit_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    formal_release_audit_receipts: Mapped[dict | None] = mapped_column(
        JSONType(), nullable=True
    )
    formal_worker_image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    github_repository: Mapped[str] = mapped_column(String(255), nullable=False)
    github_workflow_ref: Mapped[str] = mapped_column(Text, nullable=False)
    github_workflow_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    oidc_issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    oidc_subject: Mapped[str] = mapped_column(Text, nullable=False)
    attestation_signing_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sigstore_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sigstore_verification_record_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    provenance_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    build_metadata: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    receipt_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    attestation_artifact_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index(
            "idx_foundry_formal_build_version_created",
            "candidate_version_id",
            "created_at",
        ),
    )


class WorkflowRegistryEntry(Base):
    """Pending or imported formal entry bound to one approved version."""

    __tablename__ = "workflow_registry_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidates.id"), nullable=False, index=True
    )
    candidate_version_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("foundry_candidate_versions.id"), nullable=False, unique=True
    )
    candidate_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    formal_build_attestation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("foundry_formal_build_attestations.id"),
        nullable=False,
        unique=True,
    )
    registry_entry_hash: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    workflow_spec: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    release_entry: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)
    worker_image_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_RELEASE", index=True)
    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("workflow_id", "workflow_version", name="uq_registry_workflow_version"),
        Index("idx_workflow_registry_status_workflow", "status", "workflow_id"),
    )


class WorkflowRegistryRelease(Base):
    """Immutable unsigned request or verified signed registry receipt."""

    __tablename__ = "workflow_registry_releases"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    epoch: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_SIGNATURE", index=True)
    manifest: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    public_key_fingerprint: Mapped[str | None] = mapped_column(String(71), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowRegistryReleaseImport(Base):
    """Append-only receipt for a verified, deployment-ready signed release.

    Importing a signature never changes the in-process registry.  A deployment
    must still pin ``signed_snapshot`` as the configured release file and boot
    a fresh process that verifies it before the first registry lookup.
    """

    __tablename__ = "workflow_registry_release_imports"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)
    release_request_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("workflow_registry_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    release_request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    base_registry_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    base_registry_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    registry_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    registry_snapshot_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(255), nullable=False)
    signing_public_key_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    signed_snapshot: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="SIGNED_READY_FOR_DEPLOYMENT", index=True
    )
    runtime_registry_modified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "status = 'SIGNED_READY_FOR_DEPLOYMENT'",
            name="ck_registry_import_deployment_ready_only",
        ),
        CheckConstraint(
            "runtime_registry_modified IS FALSE",
            name="ck_registry_import_no_runtime_mutation",
        ),
        UniqueConstraint(
            "release_request_id", name="uq_registry_import_release_request"
        ),
        UniqueConstraint("registry_epoch", name="uq_registry_import_epoch"),
        UniqueConstraint(
            "registry_snapshot_hash", name="uq_registry_import_snapshot_hash"
        ),
        UniqueConstraint("receipt_hash", name="uq_registry_import_receipt_hash"),
    )


_IMMUTABLE_LEDGER_MODELS = (
    FoundryCandidateVersion,
    FoundryDemoRun,
    FoundryCandidateEvent,
    FoundryReview,
    FoundryFormalBuildAttestation,
    WorkflowRegistryRelease,
    WorkflowRegistryReleaseImport,
)


def _reject_immutable_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are append-only")


for _model in _IMMUTABLE_LEDGER_MODELS:
    event.listen(_model, "before_update", _reject_immutable_mutation)
    event.listen(_model, "before_delete", _reject_immutable_mutation)
