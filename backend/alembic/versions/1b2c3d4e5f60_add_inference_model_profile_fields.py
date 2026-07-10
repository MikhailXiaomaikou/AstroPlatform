"""Add model selection metadata to inference logs.

Revision ID: 1b2c3d4e5f60
Revises: 0c9293030537
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "1b2c3d4e5f60"
down_revision: Union[str, None] = "0c9293030537"
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


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table_name)}


def upgrade() -> None:
    if not _table_exists("inference_logs"):
        return
    with op.batch_alter_table("inference_logs", schema=None) as batch_op:
        if not _column_exists("inference_logs", "model_name"):
            batch_op.add_column(sa.Column("model_name", sa.String(length=255), nullable=True))
        if not _column_exists("inference_logs", "model_profile"):
            batch_op.add_column(sa.Column("model_profile", sa.String(length=255), nullable=True))
        if not _column_exists("inference_logs", "fallback_from"):
            batch_op.add_column(sa.Column("fallback_from", sa.String(length=255), nullable=True))

    # Existence-guarded, not try/except: 0c9293030537 creates inference_logs
    # from current model metadata on fresh databases (indexes included), and
    # on PostgreSQL a failed CREATE INDEX aborts the whole transaction even
    # when the Python exception is swallowed.
    for idx_name, column in [
        ("ix_inference_logs_model_name", "model_name"),
        ("ix_inference_logs_model_profile", "model_profile"),
    ]:
        if not _index_exists("inference_logs", idx_name):
            op.create_index(idx_name, "inference_logs", [column], unique=False)


def downgrade() -> None:
    if not _table_exists("inference_logs"):
        return
    for idx_name in ["ix_inference_logs_model_profile", "ix_inference_logs_model_name"]:
        if _index_exists("inference_logs", idx_name):
            op.drop_index(idx_name, table_name="inference_logs")
    with op.batch_alter_table("inference_logs", schema=None) as batch_op:
        for column in ["fallback_from", "model_profile", "model_name"]:
            if _column_exists("inference_logs", column):
                batch_op.drop_column(column)
