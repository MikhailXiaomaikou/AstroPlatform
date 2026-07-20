"""Add local-compute, workspace, source, and scientific-review records.

Revision ID: 8c93a4b5d6e7
Revises: 7b8293a4c5d6
Create Date: 2026-07-20
"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType


revision: str = "8c93a4b5d6e7"
down_revision: Union[str, None] = "7b8293a4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table_name: str) -> set[str]:
    if not _table_exists(table_name):
        return set()
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _create_index(
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    existing = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)
    }
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def _drop_index_if_present(name: str, table: str) -> None:
    if not _table_exists(table):
        return
    existing = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)
    }
    if name in existing:
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    if not _table_exists("research_workspaces"):
        op.create_table(
            "research_workspaces",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    for name, columns in (
        ("ix_research_workspaces_user_id", ["user_id"]),
        ("ix_research_workspaces_status", ["status"]),
        ("idx_research_workspace_owner_updated", ["user_id", "updated_at"]),
        ("idx_research_workspace_owner_status", ["user_id", "status"]),
    ):
        _create_index(name, "research_workspaces", columns)

    if not _table_exists("source_documents"):
        op.create_table(
            "source_documents",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column(
                "workspace_id",
                UUIDType(),
                sa.ForeignKey("research_workspaces.id"),
                nullable=False,
            ),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "supersedes_source_document_id",
                UUIDType(),
                sa.ForeignKey("source_documents.id"),
                nullable=True,
            ),
            sa.Column("source_profile_key", sa.String(length=64), nullable=False),
            sa.Column("requested_identifier", sa.Text(), nullable=False),
            sa.Column("canonical_identifier", sa.String(length=255), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False),
            sa.Column("source_document_hash", sa.String(length=64), nullable=False),
            sa.Column("raw_artifacts", JSONType(), nullable=False, server_default="[]"),
            sa.Column(
                "raw_artifact_hashes", JSONType(), nullable=False, server_default="{}"
            ),
            sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
            sa.Column("coverage_status", sa.String(length=64), nullable=False),
            sa.Column("source_metadata", JSONType(), nullable=False, server_default="{}"),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("error_class", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "workspace_id",
                "source_profile_key",
                "canonical_identifier",
                "version",
                name="uq_source_document_registered_version",
            ),
        )
    for name, columns in (
        ("ix_source_documents_workspace_id", ["workspace_id"]),
        ("ix_source_documents_user_id", ["user_id"]),
        ("ix_source_documents_source_document_hash", ["source_document_hash"]),
        ("ix_source_documents_lifecycle_status", ["lifecycle_status"]),
        ("ix_source_documents_coverage_status", ["coverage_status"]),
        ("idx_source_document_owner_created", ["user_id", "created_at"]),
        ("idx_source_document_workspace_version", ["workspace_id", "version"]),
    ):
        _create_index(name, "source_documents", columns)

    if not _table_exists("source_extractions"):
        op.create_table(
            "source_extractions",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column(
                "source_document_id",
                UUIDType(),
                sa.ForeignKey("source_documents.id"),
                nullable=False,
            ),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("schema_version", sa.String(length=64), nullable=False),
            sa.Column("reader_version", sa.String(length=64), nullable=False),
            sa.Column("input_source_document_hash", sa.String(length=64), nullable=False),
            sa.Column("extraction_payload", JSONType(), nullable=False),
            sa.Column("extraction_payload_hash", sa.String(length=64), nullable=False),
            sa.Column(
                "extraction_artifacts", JSONType(), nullable=False, server_default="[]"
            ),
            sa.Column(
                "extraction_artifact_hashes",
                JSONType(),
                nullable=False,
                server_default="{}",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "source_document_id",
                "reader_version",
                name="uq_source_extraction_document_reader",
            ),
        )
    for name, columns in (
        ("ix_source_extractions_source_document_id", ["source_document_id"]),
        ("ix_source_extractions_user_id", ["user_id"]),
        ("ix_source_extractions_extraction_payload_hash", ["extraction_payload_hash"]),
        ("idx_source_extraction_owner_created", ["user_id", "created_at"]),
    ):
        _create_index(name, "source_extractions", columns)

    if "workspace_id" not in _columns("chat_sessions"):
        with op.batch_alter_table("chat_sessions") as batch_op:
            batch_op.add_column(sa.Column("workspace_id", UUIDType(), nullable=True))
            batch_op.create_foreign_key(
                "fk_chat_sessions_workspace_id_research_workspaces",
                "research_workspaces",
                ["workspace_id"],
                ["id"],
            )
    _create_index("ix_chat_sessions_workspace_id", "chat_sessions", ["workspace_id"])

    research_job_columns = _columns("research_jobs")
    with op.batch_alter_table("research_jobs") as batch_op:
        if "workspace_id" not in research_job_columns:
            batch_op.add_column(sa.Column("workspace_id", UUIDType(), nullable=True))
            batch_op.create_foreign_key(
                "fk_research_jobs_workspace_id_research_workspaces",
                "research_workspaces",
                ["workspace_id"],
                ["id"],
            )
        if "workflow_key" not in research_job_columns:
            batch_op.add_column(
                sa.Column("workflow_key", sa.String(length=255), nullable=True)
            )
        if "capability_requirements" not in research_job_columns:
            batch_op.add_column(
                sa.Column(
                    "capability_requirements",
                    JSONType(),
                    nullable=False,
                    server_default="{}",
                )
            )
        if "current_attempt_id" not in research_job_columns:
            batch_op.add_column(sa.Column("current_attempt_id", UUIDType(), nullable=True))
    for name, columns in (
        ("ix_research_jobs_workspace_id", ["workspace_id"]),
        ("ix_research_jobs_workflow_key", ["workflow_key"]),
        ("ix_research_jobs_current_attempt_id", ["current_attempt_id"]),
    ):
        _create_index(name, "research_jobs", columns)

    claim_columns = _columns("claim_audits")
    with op.batch_alter_table("claim_audits") as batch_op:
        for column_name, target in (
            ("workspace_id", "research_workspaces"),
            ("source_document_id", "source_documents"),
            ("source_extraction_id", "source_extractions"),
            ("supersedes_audit_id", "claim_audits"),
        ):
            if column_name not in claim_columns:
                batch_op.add_column(sa.Column(column_name, UUIDType(), nullable=True))
                batch_op.create_foreign_key(
                    f"fk_claim_audits_{column_name}_{target}",
                    target,
                    [column_name],
                    ["id"],
                )
        if "claim_schema_version" not in claim_columns:
            batch_op.add_column(
                sa.Column("claim_schema_version", sa.String(length=64), nullable=True)
            )
        if "atomic_claim" not in claim_columns:
            batch_op.add_column(sa.Column("atomic_claim", JSONType(), nullable=True))
        if "claim_hash" not in claim_columns:
            batch_op.add_column(sa.Column("claim_hash", sa.String(length=64), nullable=True))
        if "risk_level" not in claim_columns:
            batch_op.add_column(sa.Column("risk_level", sa.String(length=16), nullable=True))
        if "review_status" not in claim_columns:
            batch_op.add_column(
                sa.Column(
                    "review_status",
                    sa.String(length=32),
                    nullable=False,
                    server_default="NOT_SUBMITTED",
                )
            )
        if "machine_support_eligible" not in claim_columns:
            batch_op.add_column(
                sa.Column(
                    "machine_support_eligible",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "reproduction_ready" not in claim_columns:
            batch_op.add_column(
                sa.Column(
                    "reproduction_ready",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "publication_ready" not in claim_columns:
            batch_op.add_column(
                sa.Column(
                    "publication_ready",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
        if "progress" not in claim_columns:
            batch_op.add_column(sa.Column("progress", sa.Float(), nullable=True))
        if "progress_stage" not in claim_columns:
            batch_op.add_column(
                sa.Column("progress_stage", sa.String(length=64), nullable=True)
            )
        if "independent_verification_job_id" not in claim_columns:
            batch_op.add_column(
                sa.Column(
                    "independent_verification_job_id",
                    sa.String(length=255),
                    nullable=True,
                )
            )
            batch_op.create_foreign_key(
                "fk_claim_audits_independent_verification_job_id_research_jobs",
                "research_jobs",
                ["independent_verification_job_id"],
                ["job_id"],
            )
    for name, columns in (
        ("ix_claim_audits_workspace_id", ["workspace_id"]),
        ("ix_claim_audits_source_document_id", ["source_document_id"]),
        ("ix_claim_audits_source_extraction_id", ["source_extraction_id"]),
        ("ix_claim_audits_supersedes_audit_id", ["supersedes_audit_id"]),
        ("ix_claim_audits_claim_hash", ["claim_hash"]),
        ("ix_claim_audits_review_status", ["review_status"]),
        (
            "ix_claim_audits_independent_verification_job_id",
            ["independent_verification_job_id"],
        ),
    ):
        _create_index(name, "claim_audits", columns)

    evidence_columns = _columns("evidence_packs")
    with op.batch_alter_table("evidence_packs") as batch_op:
        if "pack_hash" not in evidence_columns:
            batch_op.add_column(
                sa.Column("pack_hash", sa.String(length=71), nullable=True)
            )
        if "signature_algorithm" not in evidence_columns:
            batch_op.add_column(
                sa.Column(
                    "signature_algorithm",
                    sa.String(length=32),
                    nullable=False,
                    server_default="hmac-sha256",
                )
            )
        if "public_key_fingerprint" not in evidence_columns:
            batch_op.add_column(
                sa.Column("public_key_fingerprint", sa.String(length=71), nullable=True)
            )

    if not _table_exists("worker_enrollment_tokens"):
        op.create_table(
            "worker_enrollment_tokens",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("token_prefix", sa.String(length=16), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    for name, columns in (
        ("ix_worker_enrollment_tokens_user_id", ["user_id"]),
        ("ix_worker_enrollment_tokens_token_prefix", ["token_prefix"]),
        ("ix_worker_enrollment_tokens_expires_at", ["expires_at"]),
        ("idx_worker_enrollment_owner_expiry", ["user_id", "expires_at"]),
    ):
        _create_index(name, "worker_enrollment_tokens", columns)

    if not _table_exists("worker_nodes"):
        op.create_table(
            "worker_nodes",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("public_key", sa.Text(), nullable=False),
            sa.Column(
                "public_key_fingerprint", sa.String(length=71), nullable=False, unique=True
            ),
            sa.Column("protocol_version", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("capabilities", JSONType(), nullable=False, server_default="{}"),
            sa.Column("release_manifest", JSONType(), nullable=False, server_default="{}"),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    for name, columns in (
        ("ix_worker_nodes_user_id", ["user_id"]),
        ("ix_worker_nodes_status", ["status"]),
        ("ix_worker_nodes_last_seen_at", ["last_seen_at"]),
        ("idx_worker_node_owner_status", ["user_id", "status"]),
    ):
        _create_index(name, "worker_nodes", columns)

    if not _table_exists("science_execution_attempts"):
        op.create_table(
            "science_execution_attempts",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column(
                "job_id",
                sa.String(length=255),
                sa.ForeignKey("research_jobs.job_id"),
                nullable=False,
            ),
            sa.Column("audit_id", UUIDType(), sa.ForeignKey("claim_audits.id"), nullable=True),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "worker_node_id",
                UUIDType(),
                sa.ForeignKey("worker_nodes.id"),
                nullable=False,
            ),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("lease_id", sa.String(length=64), nullable=False, unique=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("input_hash", sa.String(length=64), nullable=False),
            sa.Column("task_envelope", JSONType(), nullable=False),
            sa.Column("result_hash", sa.String(length=64), nullable=True),
            sa.Column("result", JSONType(), nullable=True),
            sa.Column("diagnostics", JSONType(), nullable=True),
            sa.Column("artifact_manifest", JSONType(), nullable=False, server_default="[]"),
            sa.Column("error_class", sa.String(length=255), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "job_id", "attempt_number", name="uq_science_attempt_job_number"
            ),
        )
    for name, columns in (
        ("ix_science_execution_attempts_job_id", ["job_id"]),
        ("ix_science_execution_attempts_audit_id", ["audit_id"]),
        ("ix_science_execution_attempts_user_id", ["user_id"]),
        ("ix_science_execution_attempts_worker_node_id", ["worker_node_id"]),
        ("ix_science_execution_attempts_status", ["status"]),
        ("ix_science_execution_attempts_lease_expires_at", ["lease_expires_at"]),
        ("idx_science_attempt_owner_status", ["user_id", "status"]),
        ("idx_science_attempt_worker_lease", ["worker_node_id", "lease_expires_at"]),
    ):
        _create_index(name, "science_execution_attempts", columns)

    if not _table_exists("worker_artifact_issuances"):
        op.create_table(
            "worker_artifact_issuances",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("batch_id", UUIDType(), nullable=False),
            sa.Column(
                "attempt_id",
                UUIDType(),
                sa.ForeignKey("science_execution_attempts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                UUIDType(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "worker_node_id",
                UUIDType(),
                sa.ForeignKey("worker_nodes.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("artifact_name", sa.String(length=255), nullable=False),
            sa.Column("artifact_ref", sa.Text(), nullable=False, unique=True),
            sa.Column("authoritative_ref", sa.Text(), nullable=False, unique=True),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("content_type", sa.String(length=255), nullable=False),
            sa.Column(
                "authoritative_version_id", sa.String(length=1024), nullable=True
            ),
            sa.Column("verification_method", sa.String(length=64), nullable=True),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("staging_cleaned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "authoritative_cleaned_at", sa.DateTime(timezone=True), nullable=True
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    issuance_columns = _columns("worker_artifact_issuances")
    with op.batch_alter_table("worker_artifact_issuances") as batch_op:
        if "staging_cleaned_at" not in issuance_columns:
            batch_op.add_column(
                sa.Column("staging_cleaned_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "authoritative_cleaned_at" not in issuance_columns:
            batch_op.add_column(
                sa.Column(
                    "authoritative_cleaned_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                )
            )
    for name, columns in (
        ("ix_worker_artifact_issuances_batch_id", ["batch_id"]),
        ("ix_worker_artifact_issuances_attempt_id", ["attempt_id"]),
        ("ix_worker_artifact_issuances_user_id", ["user_id"]),
        ("ix_worker_artifact_issuances_worker_node_id", ["worker_node_id"]),
        ("ix_worker_artifact_issuances_verified_at", ["verified_at"]),
        ("ix_worker_artifact_issuances_staging_cleaned_at", ["staging_cleaned_at"]),
        (
            "ix_worker_artifact_issuances_authoritative_cleaned_at",
            ["authoritative_cleaned_at"],
        ),
        ("ix_worker_artifact_issuances_expires_at", ["expires_at"]),
        ("idx_worker_artifact_attempt_created", ["attempt_id", "created_at"]),
        ("idx_worker_artifact_owner_created", ["user_id", "created_at"]),
    ):
        _create_index(name, "worker_artifact_issuances", columns)

    if not _table_exists("claim_audit_reviews"):
        op.create_table(
            "claim_audit_reviews",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("audit_id", UUIDType(), sa.ForeignKey("claim_audits.id"), nullable=False),
            sa.Column(
                "workspace_id",
                UUIDType(),
                sa.ForeignKey("research_workspaces.id"),
                nullable=False,
            ),
            sa.Column(
                "source_document_id",
                UUIDType(),
                sa.ForeignKey("source_documents.id"),
                nullable=False,
            ),
            sa.Column(
                "source_extraction_id",
                UUIDType(),
                sa.ForeignKey("source_extractions.id"),
                nullable=False,
            ),
            sa.Column(
                "audit_owner_user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False
            ),
            sa.Column(
                "reviewer_user_id",
                UUIDType(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("reviewer_username", sa.String(length=255), nullable=False),
            sa.Column("candidate_id", sa.String(length=71), nullable=False),
            sa.Column("claim_hash", sa.String(length=64), nullable=False),
            sa.Column("source_hash", sa.String(length=64), nullable=False),
            sa.Column("anchor_ids", JSONType(), nullable=False),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column(
                "review_scope",
                sa.String(length=64),
                nullable=False,
                server_default="scientific_claim_review",
            ),
            sa.Column(
                "supports_finalization",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("comment", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    for name, columns in (
        ("ix_claim_audit_reviews_audit_id", ["audit_id"]),
        ("ix_claim_audit_reviews_workspace_id", ["workspace_id"]),
        ("ix_claim_audit_reviews_source_document_id", ["source_document_id"]),
        ("ix_claim_audit_reviews_source_extraction_id", ["source_extraction_id"]),
        ("ix_claim_audit_reviews_audit_owner_user_id", ["audit_owner_user_id"]),
        ("ix_claim_audit_reviews_reviewer_user_id", ["reviewer_user_id"]),
        ("idx_claim_audit_review_audit_created", ["audit_id", "created_at"]),
        ("idx_claim_audit_review_reviewer_created", ["reviewer_user_id", "created_at"]),
    ):
        _create_index(name, "claim_audit_reviews", columns)

    # Give every pre-existing user's records one private compatibility home.
    bind = op.get_bind()
    if _table_exists("users"):
        user_ids = [str(row[0]) for row in bind.execute(sa.text("SELECT id FROM users"))]
        for user_id in user_ids:
            workspace_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"https://standard-astro.example/imported-research/{user_id}",
                )
            )
            exists = bind.execute(
                sa.text(
                    "SELECT 1 FROM research_workspaces WHERE id = :workspace_id"
                ),
                {"workspace_id": workspace_id},
            ).first()
            if exists is None:
                bind.execute(
                    sa.text(
                        "INSERT INTO research_workspaces "
                        "(id, user_id, title, description, status) "
                        "VALUES (:id, :user_id, :title, :description, :status)"
                    ),
                    {
                        "id": workspace_id,
                        "user_id": user_id,
                        "title": "Imported research",
                        "description": "Imported from records created before Research Workspaces.",
                        "status": "ACTIVE",
                    },
                )
            for table in ("chat_sessions", "research_jobs", "claim_audits"):
                if _table_exists(table) and "workspace_id" in _columns(table):
                    bind.execute(
                        sa.text(
                            f"UPDATE {table} SET workspace_id = :workspace_id "  # noqa: S608
                            "WHERE user_id = :user_id AND workspace_id IS NULL"
                        ),
                        {"workspace_id": workspace_id, "user_id": user_id},
                    )


def downgrade() -> None:
    for table in (
        "claim_audit_reviews",
        "worker_artifact_issuances",
        "science_execution_attempts",
        "worker_nodes",
        "worker_enrollment_tokens",
    ):
        if _table_exists(table):
            op.drop_table(table)

    if _table_exists("evidence_packs"):
        evidence_columns = _columns("evidence_packs")
        with op.batch_alter_table("evidence_packs") as batch_op:
            for column in (
                "public_key_fingerprint",
                "signature_algorithm",
                "pack_hash",
            ):
                if column in evidence_columns:
                    batch_op.drop_column(column)

    if _table_exists("claim_audits"):
        for index in (
            "ix_claim_audits_workspace_id",
            "ix_claim_audits_source_document_id",
            "ix_claim_audits_source_extraction_id",
            "ix_claim_audits_supersedes_audit_id",
            "ix_claim_audits_claim_hash",
            "ix_claim_audits_review_status",
            "ix_claim_audits_independent_verification_job_id",
        ):
            _drop_index_if_present(index, "claim_audits")
        claim_columns = _columns("claim_audits")
        with op.batch_alter_table("claim_audits") as batch_op:
            for column in (
                "independent_verification_job_id",
                "progress_stage",
                "progress",
                "publication_ready",
                "reproduction_ready",
                "machine_support_eligible",
                "review_status",
                "risk_level",
                "claim_hash",
                "atomic_claim",
                "claim_schema_version",
                "supersedes_audit_id",
                "source_extraction_id",
                "source_document_id",
                "workspace_id",
            ):
                if column in claim_columns:
                    batch_op.drop_column(column)

    if _table_exists("research_jobs"):
        for index in (
            "ix_research_jobs_workspace_id",
            "ix_research_jobs_workflow_key",
            "ix_research_jobs_current_attempt_id",
        ):
            _drop_index_if_present(index, "research_jobs")
        research_columns = _columns("research_jobs")
        with op.batch_alter_table("research_jobs") as batch_op:
            for column in (
                "current_attempt_id",
                "capability_requirements",
                "workflow_key",
                "workspace_id",
            ):
                if column in research_columns:
                    batch_op.drop_column(column)

    if _table_exists("chat_sessions") and "workspace_id" in _columns("chat_sessions"):
        _drop_index_if_present("ix_chat_sessions_workspace_id", "chat_sessions")
        with op.batch_alter_table("chat_sessions") as batch_op:
            batch_op.drop_column("workspace_id")

    for table in ("source_extractions", "source_documents", "research_workspaces"):
        if _table_exists(table):
            op.drop_table(table)
