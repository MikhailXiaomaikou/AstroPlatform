"""Add append-only Workflow Registry activation receipts.

Revision ID: c1708293a4b5
Revises: bf60718293a4
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType


revision: str = "c1708293a4b5"
down_revision: Union[str, None] = "bf60718293a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_registry_activation_receipts",
        sa.Column("id", UUIDType(), primary_key=True, nullable=False),
        sa.Column(
            "release_import_id",
            UUIDType(),
            sa.ForeignKey("workflow_registry_release_imports.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "release_request_id",
            UUIDType(),
            sa.ForeignKey("workflow_registry_releases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("release_request_hash", sa.String(71), nullable=False),
        sa.Column("registry_epoch", sa.String(128), nullable=False),
        sa.Column("registry_snapshot_hash", sa.String(71), nullable=False),
        sa.Column("activation_manifest_hash", sa.String(71), nullable=False),
        sa.Column("signed_snapshot_file_hash", sa.String(71), nullable=False),
        sa.Column("trusted_keyring_file_hash", sa.String(71), nullable=False),
        sa.Column("target_git_commit", sa.String(40), nullable=False),
        sa.Column(
            "deployment_provider",
            sa.String(64),
            nullable=False,
            server_default="render_api_exact_commit",
        ),
        sa.Column("deployment_receipts", JSONType(), nullable=False),
        sa.Column("deployment_set_hash", sa.String(71), nullable=False),
        sa.Column("projection_summary", JSONType(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "runtime_hot_switched",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "projection_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("receipt_hash", sa.String(71), nullable=False),
        sa.Column(
            "activated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "deployment_provider = 'render_api_exact_commit'",
            name="ck_registry_activation_exact_commit_provider",
        ),
        sa.CheckConstraint(
            "status = 'ACTIVE'", name="ck_registry_activation_active_only"
        ),
        sa.CheckConstraint(
            "runtime_hot_switched IS FALSE",
            name="ck_registry_activation_no_hot_switch",
        ),
        sa.CheckConstraint(
            "projection_verified IS TRUE",
            name="ck_registry_activation_projection_verified",
        ),
        sa.UniqueConstraint(
            "release_import_id", name="uq_registry_activation_release_import"
        ),
        sa.UniqueConstraint(
            "release_request_id", name="uq_registry_activation_release_request"
        ),
        sa.UniqueConstraint(
            "registry_epoch", name="uq_registry_activation_epoch"
        ),
        sa.UniqueConstraint(
            "registry_snapshot_hash", name="uq_registry_activation_snapshot_hash"
        ),
        sa.UniqueConstraint(
            "activation_manifest_hash", name="uq_registry_activation_manifest_hash"
        ),
        sa.UniqueConstraint(
            "receipt_hash", name="uq_registry_activation_receipt_hash"
        ),
    )
def downgrade() -> None:
    op.drop_table("workflow_registry_activation_receipts")
