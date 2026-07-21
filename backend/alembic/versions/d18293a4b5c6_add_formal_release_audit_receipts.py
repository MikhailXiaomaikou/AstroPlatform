"""Bind formal release supply-chain audit receipts.

Revision ID: d18293a4b5c6
Revises: c0718293a4b5
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType


revision: str = "d18293a4b5c6"
down_revision: Union[str, None] = "c0718293a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These identity fields were added after the original Foundry migration
    # had already shipped on development/dark databases.  Add them here rather
    # than rewriting that migration.  Legacy rows receive conspicuous,
    # deterministic placeholders and remain unusable for registration because
    # their release-audit fields below are NULL; every new callback supplies
    # verified values.
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("github_repository", sa.String(255), nullable=True),
    )
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("github_workflow_ref", sa.Text(), nullable=True),
    )
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("github_workflow_sha", sa.String(40), nullable=True),
    )
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("attestation_signing_key_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column(
            "sigstore_verification_record_hash",
            sa.String(64),
            nullable=True,
        ),
    )
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("attestation_artifact_hash", sa.String(64), nullable=True),
    )
    attestations = sa.table(
        "foundry_formal_build_attestations",
        sa.column("receipt_hash", sa.String(64)),
        sa.column("github_repository", sa.String(255)),
        sa.column("github_workflow_ref", sa.Text()),
        sa.column("github_workflow_sha", sa.String(40)),
        sa.column("attestation_signing_key_id", sa.String(128)),
        sa.column("sigstore_verification_record_hash", sa.String(64)),
        sa.column("attestation_artifact_hash", sa.String(64)),
    )
    op.execute(
        attestations.update().values(
            github_repository="legacy/unverified",
            github_workflow_ref="legacy-unverified",
            github_workflow_sha="0" * 40,
            attestation_signing_key_id="legacy-unverified",
            sigstore_verification_record_hash=attestations.c.receipt_hash,
            attestation_artifact_hash=attestations.c.receipt_hash,
        )
    )
    with op.batch_alter_table("foundry_formal_build_attestations") as batch:
        batch.alter_column(
            "github_repository", existing_type=sa.String(255), nullable=False
        )
        batch.alter_column(
            "github_workflow_ref", existing_type=sa.Text(), nullable=False
        )
        batch.alter_column(
            "github_workflow_sha", existing_type=sa.String(40), nullable=False
        )
        batch.alter_column(
            "attestation_signing_key_id",
            existing_type=sa.String(128),
            nullable=False,
        )
        batch.alter_column(
            "sigstore_verification_record_hash",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.alter_column(
            "attestation_artifact_hash",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_foundry_formal_build_attestation_artifact_hash",
            ["attestation_artifact_hash"],
        )

    # Nullable only for a safe upgrade over the pre-release rows above.  New
    # callbacks and registration fail closed and require both fields.
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("formal_release_audit_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "foundry_formal_build_attestations",
        sa.Column("formal_release_audit_receipts", JSONType(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "foundry_formal_build_attestations", "formal_release_audit_receipts"
    )
    op.drop_column(
        "foundry_formal_build_attestations", "formal_release_audit_hash"
    )
    with op.batch_alter_table("foundry_formal_build_attestations") as batch:
        batch.drop_constraint(
            "uq_foundry_formal_build_attestation_artifact_hash",
            type_="unique",
        )
        batch.drop_column("attestation_artifact_hash")
        batch.drop_column("sigstore_verification_record_hash")
        batch.drop_column("attestation_signing_key_id")
        batch.drop_column("github_workflow_sha")
        batch.drop_column("github_workflow_ref")
        batch.drop_column("github_repository")
