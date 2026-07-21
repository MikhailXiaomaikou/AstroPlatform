"""Prevent concurrent Foundry candidate event-chain forks.

Revision ID: f3a4b5c6d7e8
Revises: e293a4b5c6d7
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e293a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The service serializes appends by locking foundry_candidates, while these
    # indexes provide a database-level invariant: one genesis and at most one
    # child for every parent hash in a candidate's append-only event chain.
    op.create_index(
        "uq_foundry_event_candidate_parent",
        "foundry_candidate_events",
        ["candidate_id", "previous_event_hash"],
        unique=True,
        postgresql_where=sa.text("previous_event_hash IS NOT NULL"),
        sqlite_where=sa.text("previous_event_hash IS NOT NULL"),
    )
    op.create_index(
        "uq_foundry_event_candidate_genesis",
        "foundry_candidate_events",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text("previous_event_hash IS NULL"),
        sqlite_where=sa.text("previous_event_hash IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_foundry_event_candidate_genesis",
        table_name="foundry_candidate_events",
    )
    op.drop_index(
        "uq_foundry_event_candidate_parent",
        table_name="foundry_candidate_events",
    )
