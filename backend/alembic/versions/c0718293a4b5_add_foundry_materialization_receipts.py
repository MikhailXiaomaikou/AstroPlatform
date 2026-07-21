"""Add protected Candidate source-materialization receipts.

Revision ID: c0718293a4b5
Revises: bf60718293a4
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import UUIDType


revision: str = "c0718293a4b5"
down_revision: Union[str, None] = "bf60718293a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "foundry_materialization_attestations",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column("candidate_id", UUIDType(), sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin_candidate_version_id", UUIDType(), sa.ForeignKey("foundry_candidate_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_candidate_version_hash", sa.String(64), nullable=False),
        sa.Column("materialization_request_event_id", UUIDType(), sa.ForeignKey("foundry_candidate_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("draft_run_id", UUIDType(), nullable=False),
        sa.Column("artifact_repository", sa.String(255), nullable=False),
        sa.Column("artifact_workflow_run_id", sa.String(20), nullable=False),
        sa.Column("artifact_id", sa.String(20), nullable=False),
        sa.Column("artifact_name", sa.String(128), nullable=False),
        sa.Column("artifact_hash", sa.String(64), nullable=False),
        sa.Column("base_commit", sa.String(40), nullable=False),
        sa.Column("base_source_tree_hash", sa.String(64), nullable=False),
        sa.Column("draft_source_tree_hash", sa.String(64), nullable=False),
        sa.Column("patch_hash", sa.String(64), nullable=False),
        sa.Column("candidate_module_path", sa.String(255), nullable=False),
        sa.Column("candidate_module_hash", sa.String(64), nullable=False),
        sa.Column("branch_name", sa.String(255), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("pull_request_state", sa.String(16), nullable=False),
        sa.Column("pull_request_url", sa.Text(), nullable=False),
        sa.Column("pull_request_head_commit", sa.String(40), nullable=False),
        sa.Column("pull_request_head_tree_hash", sa.String(64), nullable=False),
        sa.Column("github_repository", sa.String(255), nullable=False),
        sa.Column("github_workflow_ref", sa.Text(), nullable=False),
        sa.Column("github_workflow_sha", sa.String(40), nullable=False),
        sa.Column("github_run_id", sa.String(20), nullable=False),
        sa.Column("github_run_attempt", sa.Integer(), nullable=False),
        sa.Column("signing_key_id", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("origin_candidate_version_id", name="uq_foundry_materialization_origin_version"),
        sa.UniqueConstraint("materialization_request_event_id", name="uq_foundry_materialization_request_event"),
        sa.UniqueConstraint("payload_hash", name="uq_foundry_materialization_payload_hash"),
        sa.UniqueConstraint("receipt_hash", name="uq_foundry_materialization_receipt_hash"),
    )
    op.create_index("ix_foundry_materialization_attestations_candidate_id", "foundry_materialization_attestations", ["candidate_id"])
    op.create_index("idx_foundry_materialization_pr", "foundry_materialization_attestations", ["github_repository", "pull_request_number"])

    op.create_table(
        "foundry_materialization_receipts",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column("candidate_id", UUIDType(), sa.ForeignKey("foundry_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("materialization_attestation_id", UUIDType(), sa.ForeignKey("foundry_materialization_attestations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_candidate_version_id", UUIDType(), sa.ForeignKey("foundry_candidate_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_candidate_version_hash", sa.String(64), nullable=False),
        sa.Column("materialized_candidate_version_id", UUIDType(), sa.ForeignKey("foundry_candidate_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("pull_request_number", sa.Integer(), nullable=False),
        sa.Column("pull_request_head_commit", sa.String(40), nullable=False),
        sa.Column("pull_request_base_ref", sa.String(255), nullable=False),
        sa.Column("pull_request_head_repository", sa.String(255), nullable=False),
        sa.Column("merge_commit", sa.String(40), nullable=False),
        sa.Column("origin_main_commit", sa.String(40), nullable=False),
        sa.Column("merge_commit_is_ancestor_of_origin_main", sa.Boolean(), nullable=False),
        sa.Column("merge_source_tree_hash", sa.String(64), nullable=False),
        sa.Column("candidate_module_path", sa.String(255), nullable=False),
        sa.Column("candidate_module_hash", sa.String(64), nullable=False),
        sa.Column("patch_hash", sa.String(64), nullable=False),
        sa.Column("dependency_lock_hash", sa.String(64), nullable=False),
        sa.Column("runner_definition_hash", sa.String(64), nullable=False),
        sa.Column("validation_sbom_hash", sa.String(64), nullable=False),
        sa.Column("validation_runner_image_digest", sa.String(71), nullable=False),
        sa.Column("github_repository", sa.String(255), nullable=False),
        sa.Column("github_workflow_ref", sa.Text(), nullable=False),
        sa.Column("github_workflow_sha", sa.String(40), nullable=False),
        sa.Column("github_run_id", sa.String(20), nullable=False),
        sa.Column("github_run_attempt", sa.Integer(), nullable=False),
        sa.Column("signing_key_id", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("materialization_attestation_id", name="uq_foundry_materialization_final_attestation"),
        sa.UniqueConstraint("materialized_candidate_version_id", name="uq_foundry_materialization_final_version"),
        sa.UniqueConstraint("merge_commit", name="uq_foundry_materialization_merge_commit"),
        sa.UniqueConstraint("payload_hash", name="uq_foundry_materialization_final_payload_hash"),
        sa.UniqueConstraint("receipt_hash", name="uq_foundry_materialization_final_receipt_hash"),
    )
    op.create_index("ix_foundry_materialization_receipts_candidate_id", "foundry_materialization_receipts", ["candidate_id"])
    op.create_index("idx_fmr_origin_version", "foundry_materialization_receipts", ["origin_candidate_version_id"])


def downgrade() -> None:
    op.drop_table("foundry_materialization_receipts")
    op.drop_table("foundry_materialization_attestations")
