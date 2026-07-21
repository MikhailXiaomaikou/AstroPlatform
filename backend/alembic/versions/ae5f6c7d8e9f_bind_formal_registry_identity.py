"""Bind audits, jobs, and attempts to an exact formal registry release.

Revision ID: ae5f6c7d8e9f
Revises: 9da4b5c6e7f8
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ae5f6c7d8e9f"
down_revision: Union[str, None] = "9da4b5c6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BINDING_COLUMNS = (
    ("workflow_id", sa.String(length=255)),
    ("workflow_version", sa.String(length=64)),
    ("registry_epoch", sa.String(length=128)),
    ("registry_entry_hash", sa.String(length=71)),
    ("entrypoint_id", sa.String(length=128)),
    ("runner_image_digest", sa.String(length=71)),
)


def upgrade() -> None:
    for table_name in (
        "claim_audits",
        "research_jobs",
        "science_execution_attempts",
    ):
        for column_name, column_type in _BINDING_COLUMNS:
            op.add_column(
                table_name,
                sa.Column(column_name, column_type, nullable=True),
            )

    op.create_index(
        "ix_claim_audits_workflow_id", "claim_audits", ["workflow_id"]
    )
    op.create_index(
        "ix_research_jobs_workflow_id", "research_jobs", ["workflow_id"]
    )
    op.create_index(
        "ix_science_execution_attempts_workflow_id",
        "science_execution_attempts",
        ["workflow_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_science_execution_attempts_workflow_id",
        table_name="science_execution_attempts",
    )
    op.drop_index("ix_research_jobs_workflow_id", table_name="research_jobs")
    op.drop_index("ix_claim_audits_workflow_id", table_name="claim_audits")
    for table_name in (
        "science_execution_attempts",
        "research_jobs",
        "claim_audits",
    ):
        for column_name, _column_type in reversed(_BINDING_COLUMNS):
            op.drop_column(table_name, column_name)
