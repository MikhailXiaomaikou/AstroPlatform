"""Add append-only signed registry release import receipts.

Revision ID: bf60718293a4
Revises: ae5f6c7d8e9f
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType


revision: str = "bf60718293a4"
down_revision: Union[str, None] = "ae5f6c7d8e9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_registry_release_imports",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "release_request_id",
            UUIDType(),
            sa.ForeignKey("workflow_registry_releases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("release_request_hash", sa.String(71), nullable=False),
        sa.Column("base_registry_epoch", sa.String(128), nullable=False),
        sa.Column("base_registry_hash", sa.String(71), nullable=False),
        sa.Column("registry_epoch", sa.String(128), nullable=False),
        sa.Column("registry_snapshot_hash", sa.String(71), nullable=False),
        sa.Column("signing_key_id", sa.String(255), nullable=False),
        sa.Column("signing_public_key_fingerprint", sa.String(71), nullable=False),
        sa.Column("signed_snapshot", JSONType(), nullable=False),
        sa.Column("receipt_hash", sa.String(71), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="SIGNED_READY_FOR_DEPLOYMENT",
        ),
        sa.Column(
            "runtime_registry_modified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status = 'SIGNED_READY_FOR_DEPLOYMENT'",
            name="ck_registry_import_deployment_ready_only",
        ),
        sa.CheckConstraint(
            "runtime_registry_modified IS FALSE",
            name="ck_registry_import_no_runtime_mutation",
        ),
        sa.UniqueConstraint(
            "release_request_id", name="uq_registry_import_release_request"
        ),
        sa.UniqueConstraint("registry_epoch", name="uq_registry_import_epoch"),
        sa.UniqueConstraint(
            "registry_snapshot_hash", name="uq_registry_import_snapshot_hash"
        ),
        sa.UniqueConstraint("receipt_hash", name="uq_registry_import_receipt_hash"),
    )
    op.create_index(
        "ix_workflow_registry_release_imports_status",
        "workflow_registry_release_imports",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_registry_release_imports_status",
        table_name="workflow_registry_release_imports",
    )
    op.drop_table("workflow_registry_release_imports")
