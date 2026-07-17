"""Add claim-audit, evidence-pack, invitation, and privacy records.

Revision ID: 7b8293a4c5d6
Revises: 6a718293b4c5
Create Date: 2026-07-17
"""

from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType


revision: str = "7b8293a4c5d6"
down_revision: Union[str, None] = "6a718293b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _create_index(
    name: str,
    table: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=unique)


def upgrade() -> None:
    if _table_exists("research_jobs"):
        research_job_columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns("research_jobs")
        }
        if "attestation" not in research_job_columns:
            op.add_column(
                "research_jobs",
                sa.Column("attestation", JSONType(), nullable=True),
            )

    users_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "account_status" not in users_columns:
        op.add_column(
            "users",
            sa.Column(
                "account_status",
                sa.String(length=32),
                nullable=False,
                server_default="ACTIVE",
            ),
        )
    if "deletion_requested_at" not in users_columns:
        op.add_column(
            "users",
            sa.Column(
                "deletion_requested_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
    _create_index("ix_users_account_status", "users", ["account_status"])

    if not _table_exists("claim_audits"):
        op.create_table(
            "claim_audits",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("session_id", UUIDType(), sa.ForeignKey("chat_sessions.id"), nullable=True),
            sa.Column("request_hash", sa.String(length=64), nullable=False),
            sa.Column("lifecycle_status", sa.String(length=32), nullable=False),
            sa.Column("scientific_verdict", sa.String(length=32), nullable=True),
            sa.Column("mode", sa.String(length=32), nullable=False),
            sa.Column("claim_text", sa.Text(), nullable=False),
            sa.Column("source_kind", sa.String(length=32), nullable=False),
            sa.Column("source_value", sa.Text(), nullable=False),
            sa.Column("evidence_input_refs", JSONType(), nullable=False),
            sa.Column("dataset_hints", JSONType(), nullable=False),
            sa.Column("normalized_claims", JSONType(), nullable=False),
            sa.Column("capability_gaps", JSONType(), nullable=False),
            sa.Column("evidence_record_ids", JSONType(), nullable=False),
            sa.Column("child_job_ids", JSONType(), nullable=False),
            sa.Column("evidence_graph", JSONType(), nullable=True),
            sa.Column("fact_check_report", JSONType(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("error_class", sa.String(length=255), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("worker_lease_id", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "request_hash", name="uq_claim_audit_owner_request"),
        )
    claim_audit_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("claim_audits")
    }
    if "attempt_count" not in claim_audit_columns:
        op.add_column(
            "claim_audits",
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "worker_lease_id" not in claim_audit_columns:
        op.add_column(
            "claim_audits",
            sa.Column("worker_lease_id", sa.String(length=64), nullable=True),
        )
    if "lease_expires_at" not in claim_audit_columns:
        op.add_column(
            "claim_audits",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    for name, columns in (
        ("ix_claim_audits_user_id", ["user_id"]),
        ("ix_claim_audits_session_id", ["session_id"]),
        ("ix_claim_audits_lifecycle_status", ["lifecycle_status"]),
        ("ix_claim_audits_scientific_verdict", ["scientific_verdict"]),
        ("ix_claim_audits_lease_expires_at", ["lease_expires_at"]),
        ("idx_claim_audit_owner_created", ["user_id", "created_at"]),
        ("idx_claim_audit_owner_status", ["user_id", "lifecycle_status"]),
    ):
        _create_index(name, "claim_audits", columns)

    if not _table_exists("evidence_packs"):
        op.create_table(
            "evidence_packs",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("audit_id", UUIDType(), sa.ForeignKey("claim_audits.id"), nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("artifact_ref", sa.Text(), nullable=False),
            sa.Column("manifest", JSONType(), nullable=False),
            sa.Column("manifest_hash", sa.String(length=71), nullable=False),
            sa.Column("signature", sa.String(length=96), nullable=False),
            sa.Column("key_id", sa.String(length=255), nullable=False),
            sa.Column("upload_lease_id", sa.String(length=64), nullable=False),
            sa.Column("upload_started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("audit_id", name="uq_evidence_pack_audit"),
        )
    evidence_pack_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("evidence_packs")
    }
    if "upload_lease_id" not in evidence_pack_columns:
        op.add_column(
            "evidence_packs",
            sa.Column(
                "upload_lease_id",
                sa.String(length=64),
                nullable=False,
                server_default="legacy-upload",
            ),
        )
    _create_index("ix_evidence_packs_audit_id", "evidence_packs", ["audit_id"], unique=True)
    _create_index("ix_evidence_packs_user_id", "evidence_packs", ["user_id"])

    if not _table_exists("privacy_preferences"):
        op.create_table(
            "privacy_preferences",
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), primary_key=True, nullable=False),
            sa.Column("analytics_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    # Pre-P1 analytics had neither explicit consent records nor the strict
    # metadata scrubber. There is no defensible way to infer consent or prove
    # that legacy event_data contains no research content, so the cutover
    # deletes it once instead of retaining/exporting it for another 30 days.
    if _table_exists("user_events"):
        op.execute(sa.text("DELETE FROM user_events"))

    # Legacy inference failures stored arbitrary provider exception strings.
    # Redact them irreversibly, then enforce the 30-day metrics ceiling before
    # the new hourly purge task takes over.
    if _table_exists("inference_logs"):
        op.execute(
            sa.text(
                "UPDATE inference_logs SET error = 'legacy_error_redacted' "
                "WHERE error IS NOT NULL"
            )
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        op.execute(
            sa.text("DELETE FROM inference_logs WHERE timestamp < :cutoff")
            .bindparams(cutoff=cutoff)
        )

    if not _table_exists("account_deletion_tombstones"):
        op.create_table(
            "account_deletion_tombstones",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_fingerprint", sa.String(length=64), nullable=False, unique=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("receipt_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_id", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error_class", sa.String(length=255), nullable=True),
            sa.Column("pending_user_id", UUIDType(), nullable=True),
            sa.Column(
                "pending_artifact_refs",
                JSONType(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("backup_expires_at", sa.DateTime(timezone=True), nullable=False),
        )
    tombstone_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "account_deletion_tombstones"
        )
    }
    if "attempt_count" not in tombstone_columns:
        op.add_column(
            "account_deletion_tombstones",
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if "last_error_class" not in tombstone_columns:
        op.add_column(
            "account_deletion_tombstones",
            sa.Column("last_error_class", sa.String(length=255), nullable=True),
        )
    if "lease_id" not in tombstone_columns:
        op.add_column(
            "account_deletion_tombstones",
            sa.Column("lease_id", sa.String(length=64), nullable=True),
        )
    if "lease_expires_at" not in tombstone_columns:
        op.add_column(
            "account_deletion_tombstones",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "pending_user_id" not in tombstone_columns:
        op.add_column(
            "account_deletion_tombstones",
            sa.Column("pending_user_id", UUIDType(), nullable=True),
        )
    if "pending_artifact_refs" not in tombstone_columns:
        op.add_column(
            "account_deletion_tombstones",
            sa.Column(
                "pending_artifact_refs",
                JSONType(),
                nullable=False,
                server_default="[]",
            ),
        )
    _create_index(
        "ix_account_deletion_tombstones_lease_expires_at",
        "account_deletion_tombstones",
        ["lease_expires_at"],
    )

    if not _table_exists("artifact_cleanup_queue"):
        op.create_table(
            "artifact_cleanup_queue",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_fingerprint", sa.String(length=64), nullable=True),
            sa.Column("artifact_ref", sa.Text(), nullable=False, unique=True),
            sa.Column("reason_class", sa.String(length=64), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error_class", sa.String(length=255), nullable=True),
            sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    cleanup_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(
            "artifact_cleanup_queue"
        )
    }
    if "not_before" not in cleanup_columns:
        op.add_column(
            "artifact_cleanup_queue",
            sa.Column(
                "not_before",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        cleanup_deadline = datetime.now(timezone.utc) + timedelta(days=1)
        op.execute(
            sa.text(
                "UPDATE artifact_cleanup_queue SET not_before = :deadline "
                "WHERE not_before IS NULL"
            ).bindparams(deadline=cleanup_deadline)
        )
        with op.batch_alter_table("artifact_cleanup_queue") as batch_op:
            batch_op.alter_column(
                "not_before",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )
    _create_index(
        "ix_artifact_cleanup_queue_user_fingerprint",
        "artifact_cleanup_queue",
        ["user_fingerprint"],
    )
    _create_index(
        "ix_artifact_cleanup_queue_not_before",
        "artifact_cleanup_queue",
        ["not_before"],
    )

    if not _table_exists("invitations"):
        op.create_table(
            "invitations",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("key_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("key_prefix", sa.String(length=16), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("target_user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("used_by", UUIDType(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    invitation_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("invitations")
    }
    if "target_user_id" not in invitation_columns:
        op.add_column(
            "invitations",
            sa.Column(
                "target_user_id",
                UUIDType(),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
        )
    _create_index("ix_invitations_key_prefix", "invitations", ["key_prefix"])


def downgrade() -> None:
    for table in (
        "invitations",
        "artifact_cleanup_queue",
        "account_deletion_tombstones",
        "privacy_preferences",
        "evidence_packs",
        "claim_audits",
    ):
        if _table_exists(table):
            op.drop_table(table)
    users_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    users_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("users")
    }
    if "ix_users_account_status" in users_indexes:
        op.drop_index("ix_users_account_status", table_name="users")
    if "deletion_requested_at" in users_columns:
        op.drop_column("users", "deletion_requested_at")
    if "account_status" in users_columns:
        op.drop_column("users", "account_status")
    if _table_exists("research_jobs"):
        research_job_columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns("research_jobs")
        }
        if "attestation" in research_job_columns:
            op.drop_column("research_jobs", "attestation")
