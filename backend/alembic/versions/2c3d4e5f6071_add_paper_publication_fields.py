"""Add explicit publication fields to paper drafts.

Revision ID: 2c3d4e5f6071
Revises: 1b2c3d4e5f60
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "2c3d4e5f6071"
down_revision: Union[str, None] = "1b2c3d4e5f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {col["name"] for col in _inspector().get_columns(table_name)}


def upgrade() -> None:
    if not _table_exists("paper_drafts"):
        return
    with op.batch_alter_table("paper_drafts", schema=None) as batch_op:
        if not _column_exists("paper_drafts", "is_public"):
            batch_op.add_column(sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()))
        if not _column_exists("paper_drafts", "public_token"):
            batch_op.add_column(sa.Column("public_token", sa.String(length=64), nullable=True))
        if not _column_exists("paper_drafts", "published_at"):
            batch_op.add_column(sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))

    for idx_name, columns, unique in [
        ("ix_paper_drafts_is_public", ["is_public"], False),
        ("ix_paper_drafts_public_token", ["public_token"], True),
    ]:
        try:
            op.create_index(idx_name, "paper_drafts", columns, unique=unique)
        except Exception:
            pass


def downgrade() -> None:
    if not _table_exists("paper_drafts"):
        return
    for idx_name in ["ix_paper_drafts_public_token", "ix_paper_drafts_is_public"]:
        try:
            op.drop_index(idx_name, table_name="paper_drafts")
        except Exception:
            pass
    with op.batch_alter_table("paper_drafts", schema=None) as batch_op:
        for column in ["published_at", "public_token", "is_public"]:
            if _column_exists("paper_drafts", column):
                batch_op.drop_column(column)
