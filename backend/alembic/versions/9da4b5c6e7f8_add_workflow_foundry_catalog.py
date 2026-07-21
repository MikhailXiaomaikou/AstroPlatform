"""Add candidate workflow foundry and formal registry ledgers.

Revision ID: 9da4b5c6e7f8
Revises: 8c93a4b5d6e7
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType


revision: str = "9da4b5c6e7f8"
down_revision: Union[str, None] = "8c93a4b5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "foundry_candidates",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column("gap_fingerprint", sa.String(64), nullable=False),
        sa.Column("gap_code", sa.String(128), nullable=False),
        sa.Column("gap_descriptor", JSONType(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("risk_level", sa.String(8), nullable=True),
        sa.Column("generation_route", sa.String(32), nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("gap_fingerprint", name="uq_foundry_candidates_gap_fingerprint"),
    )
    op.create_index("ix_foundry_candidates_gap_code", "foundry_candidates", ["gap_code"])
    op.create_index("ix_foundry_candidates_status", "foundry_candidates", ["status"])
    op.create_index("ix_foundry_candidates_risk_level", "foundry_candidates", ["risk_level"])
    op.create_index(
        "idx_foundry_candidate_status_updated", "foundry_candidates", ["status", "updated_at"]
    )

    op.create_table(
        "capability_requests",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "audit_id", UUIDType(), sa.ForeignKey("claim_audits.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "candidate_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidates.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gap_id", sa.String(68), nullable=False),
        sa.Column("gap_fingerprint", sa.String(64), nullable=False),
        sa.Column("gap_snapshot", JSONType(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="SUBMITTED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "user_id", "audit_id", "gap_id", name="uq_capability_request_owner_audit_gap"
        ),
    )
    for name, columns in (
        ("ix_capability_requests_user_id", ["user_id"]),
        ("ix_capability_requests_audit_id", ["audit_id"]),
        ("ix_capability_requests_candidate_id", ["candidate_id"]),
        ("ix_capability_requests_gap_fingerprint", ["gap_fingerprint"]),
        ("ix_capability_requests_status", ["status"]),
        ("idx_capability_request_owner_created", ["user_id", "created_at"]),
        ("idx_capability_request_candidate_created", ["candidate_id", "created_at"]),
    ):
        op.create_index(name, "capability_requests", columns)

    op.create_table(
        "foundry_candidate_versions",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.String(97), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("version_hash", sa.String(64), nullable=False),
        sa.Column("workflow_id", sa.String(255), nullable=False),
        sa.Column("workflow_version", sa.String(64), nullable=False),
        sa.Column("candidate_bundle", JSONType(), nullable=False),
        sa.Column("workflow_spec", JSONType(), nullable=False),
        sa.Column("workflow_spec_hash", sa.String(64), nullable=False),
        sa.Column("code_tree_hash", sa.String(64), nullable=False),
        sa.Column("patch_hash", sa.String(64), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(64), nullable=False),
        sa.Column("sbom_hash", sa.String(64), nullable=False),
        sa.Column("fixture_hashes", JSONType(), nullable=False, server_default="[]"),
        sa.Column("data_hashes", JSONType(), nullable=False, server_default="{}"),
        sa.Column("ai_model", sa.String(255), nullable=False),
        sa.Column("ai_generation_config", JSONType(), nullable=False, server_default="{}"),
        sa.Column("validation_runner_image_digest", sa.String(71), nullable=False),
        sa.Column("created_by_kind", sa.String(32), nullable=False),
        sa.Column(
            "created_by_user_id",
            UUIDType(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("version_hash", name="uq_foundry_candidate_versions_version_hash"),
        sa.UniqueConstraint(
            "candidate_id", "version_number", name="uq_foundry_candidate_version_number"
        ),
    )
    for name, columns in (
        ("ix_foundry_candidate_versions_candidate_id", ["candidate_id"]),
        ("ix_foundry_candidate_versions_candidate_key", ["candidate_key"]),
        ("ix_foundry_candidate_versions_workflow_id", ["workflow_id"]),
        ("ix_foundry_candidate_versions_created_by_user_id", ["created_by_user_id"]),
        ("idx_foundry_candidate_version_created", ["candidate_id", "created_at"]),
    ):
        op.create_index(name, "foundry_candidate_versions", columns)

    op.create_table(
        "foundry_validation_runs",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_version_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_version_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("requested_by_kind", sa.String(32), nullable=False),
        sa.Column(
            "requested_by_user_id", UUIDType(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("runner_image_digest", sa.String(71), nullable=True),
        sa.Column("validation_summary", JSONType(), nullable=False, server_default="{}"),
        sa.Column("failure_class", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    for name, columns in (
        ("ix_foundry_validation_runs_candidate_id", ["candidate_id"]),
        ("ix_foundry_validation_runs_candidate_version_id", ["candidate_version_id"]),
        ("ix_foundry_validation_runs_status", ["status"]),
        ("ix_foundry_validation_runs_requested_by_user_id", ["requested_by_user_id"]),
        ("idx_foundry_validation_candidate_created", ["candidate_id", "created_at"]),
    ):
        op.create_index(name, "foundry_validation_runs", columns)

    op.create_table(
        "foundry_demo_runs",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id", UUIDType(), sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "candidate_version_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.String(97), nullable=False),
        sa.Column(
            "validation_run_id",
            UUIDType(),
            sa.ForeignKey("foundry_validation_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("evidence_class", sa.String(32), nullable=False, server_default="NON_FORMAL_DEMO"),
        sa.Column("publication_ready", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claim_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_pack_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("candidate_version_hash", sa.String(64), nullable=False),
        sa.Column("candidate_bundle_hash", sa.String(64), nullable=False),
        sa.Column("workflow_spec_hash", sa.String(64), nullable=False),
        sa.Column("code_tree_hash", sa.String(64), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(64), nullable=False),
        sa.Column("runner_definition_hash", sa.String(64), nullable=False),
        sa.Column("sbom_hash", sa.String(64), nullable=False),
        sa.Column("fixture_hashes", JSONType(), nullable=False, server_default="[]"),
        sa.Column("data_hashes", JSONType(), nullable=False, server_default="{}"),
        sa.Column("ai_model", sa.String(255), nullable=False),
        sa.Column("ai_generation_config", JSONType(), nullable=False, server_default="{}"),
        sa.Column("generation", JSONType(), nullable=False, server_default="{}"),
        sa.Column("source_pins", JSONType(), nullable=False, server_default="[]"),
        sa.Column("runner_image_digest", sa.String(71), nullable=False),
        sa.Column("environment", JSONType(), nullable=False, server_default="{}"),
        sa.Column("environment_sha256", sa.String(64), nullable=False),
        sa.Column("stdout_sha256", sa.String(64), nullable=False),
        sa.Column("stderr_sha256", sa.String(64), nullable=False),
        sa.Column("stdout_bytes", sa.BigInteger(), nullable=False),
        sa.Column("stderr_bytes", sa.BigInteger(), nullable=False),
        sa.Column("structured_result", JSONType(), nullable=False, server_default="{}"),
        sa.Column("limitations", JSONType(), nullable=False, server_default="[]"),
        sa.Column("validation_summary", JSONType(), nullable=False, server_default="{}"),
        sa.Column("failure_class", sa.String(255), nullable=True),
        sa.Column("resource_usage", JSONType(), nullable=False, server_default="{}"),
        sa.Column("artifact_manifest", JSONType(), nullable=False, server_default="[]"),
        sa.Column("duration_ms", sa.BigInteger(), nullable=False),
        sa.Column("demo_report_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("evidence_class = 'NON_FORMAL_DEMO'", name="ck_demo_non_formal_evidence"),
        sa.CheckConstraint("publication_ready IS FALSE", name="ck_demo_not_publication_ready"),
        sa.CheckConstraint("claim_eligible IS FALSE", name="ck_demo_not_claim_eligible"),
        sa.CheckConstraint("evidence_pack_allowed IS FALSE", name="ck_demo_no_evidence_pack"),
        sa.UniqueConstraint("demo_report_hash", name="uq_foundry_demo_runs_report_hash"),
    )
    for name, columns in (
        ("ix_foundry_demo_runs_candidate_id", ["candidate_id"]),
        ("ix_foundry_demo_runs_candidate_version_id", ["candidate_version_id"]),
        ("ix_foundry_demo_runs_candidate_key", ["candidate_key"]),
        ("ix_foundry_demo_runs_validation_run_id", ["validation_run_id"]),
        ("ix_foundry_demo_runs_status", ["status"]),
        ("idx_foundry_demo_candidate_created", ["candidate_id", "created_at"]),
        ("idx_foundry_demo_version_created", ["candidate_version_id", "created_at"]),
    ):
        op.create_index(name, "foundry_demo_runs", columns)

    op.create_table(
        "foundry_candidate_events",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id", UUIDType(), sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "candidate_version_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidate_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_kind", sa.String(32), nullable=False),
        sa.Column("actor_user_id", UUIDType(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_payload", JSONType(), nullable=False, server_default="{}"),
        sa.Column("previous_event_hash", sa.String(64), nullable=True),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("event_hash", name="uq_foundry_candidate_events_event_hash"),
    )
    for name, columns in (
        ("ix_foundry_candidate_events_candidate_id", ["candidate_id"]),
        ("ix_foundry_candidate_events_candidate_version_id", ["candidate_version_id"]),
        ("ix_foundry_candidate_events_event_type", ["event_type"]),
        ("ix_foundry_candidate_events_actor_user_id", ["actor_user_id"]),
        ("idx_foundry_event_candidate_created", ["candidate_id", "created_at"]),
    ):
        op.create_index(name, "foundry_candidate_events", columns)

    op.create_table(
        "foundry_reviews",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id", UUIDType(), sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "candidate_version_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_version_hash", sa.String(64), nullable=False),
        sa.Column("reviewer_user_id", UUIDType(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewer_pseudonym", sa.String(71), nullable=False),
        sa.Column("review_scope", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for name, columns in (
        ("ix_foundry_reviews_candidate_id", ["candidate_id"]),
        ("ix_foundry_reviews_candidate_version_id", ["candidate_version_id"]),
        ("ix_foundry_reviews_reviewer_user_id", ["reviewer_user_id"]),
        ("ix_foundry_reviews_decision", ["decision"]),
        ("idx_foundry_review_version_created", ["candidate_version_id", "created_at"]),
        ("idx_foundry_review_reviewer_created", ["reviewer_user_id", "created_at"]),
    ):
        op.create_index(name, "foundry_reviews", columns)

    op.create_table(
        "foundry_formal_build_attestations",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "candidate_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_version_id",
            UUIDType(),
            sa.ForeignKey("foundry_candidate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_version_hash", sa.String(64), nullable=False),
        sa.Column("source_tree_hash", sa.String(64), nullable=False),
        sa.Column("git_commit", sa.String(40), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(64), nullable=False),
        sa.Column("formal_sbom_hash", sa.String(64), nullable=False),
        sa.Column("test_report_hash", sa.String(64), nullable=False),
        sa.Column("formal_worker_image_digest", sa.String(71), nullable=False),
        sa.Column("oidc_issuer", sa.String(255), nullable=False),
        sa.Column("oidc_subject", sa.Text(), nullable=False),
        sa.Column("sigstore_bundle_hash", sa.String(64), nullable=False),
        sa.Column("provenance_hash", sa.String(64), nullable=False),
        sa.Column("build_metadata", JSONType(), nullable=False, server_default="{}"),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("receipt_hash", name="uq_foundry_formal_build_receipt_hash"),
    )
    op.create_index(
        "ix_foundry_formal_build_attestations_candidate_id",
        "foundry_formal_build_attestations",
        ["candidate_id"],
    )
    op.create_index(
        "ix_foundry_formal_build_attestations_candidate_version_id",
        "foundry_formal_build_attestations",
        ["candidate_version_id"],
    )
    op.create_index(
        "idx_foundry_formal_build_version_created",
        "foundry_formal_build_attestations",
        ["candidate_version_id", "created_at"],
    )

    op.create_table(
        "workflow_registry_entries",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column("workflow_id", sa.String(255), nullable=False),
        sa.Column("workflow_version", sa.String(64), nullable=False),
        sa.Column("candidate_id", UUIDType(), sa.ForeignKey("foundry_candidates.id"), nullable=False),
        sa.Column(
            "candidate_version_id", UUIDType(), sa.ForeignKey("foundry_candidate_versions.id"), nullable=False
        ),
        sa.Column("candidate_version_hash", sa.String(64), nullable=False),
        sa.Column(
            "formal_build_attestation_id",
            UUIDType(),
            sa.ForeignKey("foundry_formal_build_attestations.id"),
            nullable=False,
        ),
        sa.Column("registry_entry_hash", sa.String(71), nullable=False),
        sa.Column("workflow_spec", JSONType(), nullable=False),
        sa.Column("release_entry", JSONType(), nullable=False),
        sa.Column("risk_level", sa.String(8), nullable=False),
        sa.Column("worker_image_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_RELEASE"),
        sa.Column(
            "registered_by_user_id", UUIDType(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.UniqueConstraint("candidate_version_id", name="uq_registry_candidate_version"),
        sa.UniqueConstraint(
            "formal_build_attestation_id", name="uq_registry_formal_build_attestation"
        ),
        sa.UniqueConstraint("registry_entry_hash", name="uq_registry_entry_hash"),
        sa.UniqueConstraint("workflow_id", "workflow_version", name="uq_registry_workflow_version"),
    )
    op.create_index("ix_workflow_registry_entries_candidate_id", "workflow_registry_entries", ["candidate_id"])
    op.create_index("ix_workflow_registry_entries_status", "workflow_registry_entries", ["status"])
    op.create_index(
        "idx_workflow_registry_status_workflow",
        "workflow_registry_entries",
        ["status", "workflow_id"],
    )

    op.create_table(
        "workflow_registry_releases",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column("epoch", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING_SIGNATURE"),
        sa.Column("manifest", JSONType(), nullable=False),
        sa.Column("manifest_hash", sa.String(71), nullable=False),
        sa.Column("signature", sa.Text(), nullable=True),
        sa.Column("key_id", sa.String(255), nullable=True),
        sa.Column("public_key_fingerprint", sa.String(71), nullable=True),
        sa.Column(
            "created_by_user_id", UUIDType(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("epoch", name="uq_workflow_registry_releases_epoch"),
        sa.UniqueConstraint("manifest_hash", name="uq_workflow_registry_releases_manifest_hash"),
    )
    op.create_index("ix_workflow_registry_releases_status", "workflow_registry_releases", ["status"])


def downgrade() -> None:
    for table in (
        "workflow_registry_releases",
        "workflow_registry_entries",
        "foundry_formal_build_attestations",
        "foundry_reviews",
        "foundry_candidate_events",
        "foundry_demo_runs",
        "foundry_validation_runs",
        "foundry_candidate_versions",
        "capability_requests",
        "foundry_candidates",
    ):
        op.drop_table(table)
