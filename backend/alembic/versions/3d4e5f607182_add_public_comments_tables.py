"""Create public comment and trending visibility tables.

Revision ID: 3d4e5f607182
Revises: 2c3d4e5f6071
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import UUIDType

revision: str = "3d4e5f607182"
down_revision: Union[str, None] = "2c3d4e5f6071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def upgrade() -> None:
    if not _table_exists("comments"):
        op.create_table(
            "comments",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("author_name", sa.String(length=40), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("is_visible", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("client_ip", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        )
    try:
        op.create_index("ix_comments_visible_created", "comments", ["is_visible", "created_at"], unique=False)
    except Exception:
        pass

    if not _table_exists("trending_visibility"):
        op.create_table(
            "trending_visibility",
            sa.Column("key", sa.String(length=32), primary_key=True, nullable=False),
            sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        )


def downgrade() -> None:
    if _table_exists("trending_visibility"):
        op.drop_table("trending_visibility")
    if _table_exists("comments"):
        try:
            op.drop_index("ix_comments_visible_created", table_name="comments")
        except Exception:
            pass
        op.drop_table("comments")
