"""Consolidate manual migrations into Alembic.

Adds columns and indexes that were previously handled by
_migrate_add_columns() in app/main.py.  All column operations use
batch mode for SQLite compatibility; every operation is guarded by an
inspector existence check so the migration is fully idempotent (safe to run
on a fresh database *or* one that already had the manual migration applied)
without ever issuing a colliding DDL statement — on PostgreSQL a failed DDL
aborts the whole transaction even when the Python exception is swallowed.

Revision ID: 002
Revises: 001_initial
Create Date: 2026-04-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001_initial"
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
    # ── Users table: new profile / OAuth columns ─────────────────────
    user_columns = [
        ("username", sa.String(255)),
        ("google_id", sa.String(255)),
        ("avatar_url", sa.Text()),
        ("display_name", sa.String(255)),
    ]
    missing_user_columns = [
        (col_name, col_type)
        for col_name, col_type in user_columns
        if not _column_exists("users", col_name)
    ]
    if missing_user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            for col_name, col_type in missing_user_columns:
                batch_op.add_column(sa.Column(col_name, col_type, nullable=True))

    # ── Users table: unique indexes ──────────────────────────────────
    for idx_name, table, cols, unique in [
        ("ix_users_username", "users", ["username"], True),
        ("ix_users_google_id", "users", ["google_id"], True),
    ]:
        if not _index_exists(table, idx_name):
            op.create_index(idx_name, table, cols, unique=unique)

    # ── RunResult: reproducibility metadata columns ──────────────────
    run_result_columns = [
        ("input_hash", sa.String(64)),
        ("output_checksum", sa.String(64)),
        ("execution_time_ms", sa.Integer()),
    ]
    missing_run_result_columns = [
        (col_name, col_type)
        for col_name, col_type in run_result_columns
        if not _column_exists("run_results", col_name)
    ]
    if missing_run_result_columns:
        with op.batch_alter_table("run_results", schema=None) as batch_op:
            for col_name, col_type in missing_run_result_columns:
                batch_op.add_column(sa.Column(col_name, col_type, nullable=True))

    # ── Performance indexes on data_files and pipeline_runs ──────────
    for idx_name, table, cols in [
        ("idx_datafile_source", "data_files", ["source"]),
        ("idx_datafile_object_id", "data_files", ["object_id"]),
        ("idx_datafile_user_source", "data_files", ["user_id", "source"]),
        ("idx_pipelinerun_status", "pipeline_runs", ["status"]),
        ("idx_pipelinerun_user_status", "pipeline_runs", ["user_id", "status"]),
    ]:
        if not _index_exists(table, idx_name):
            op.create_index(idx_name, table, cols)


def downgrade() -> None:
    # ── Remove performance indexes ───────────────────────────────────
    for idx_name, table in [
        ("idx_pipelinerun_user_status", "pipeline_runs"),
        ("idx_pipelinerun_status", "pipeline_runs"),
        ("idx_datafile_user_source", "data_files"),
        ("idx_datafile_object_id", "data_files"),
        ("idx_datafile_source", "data_files"),
    ]:
        if _index_exists(table, idx_name):
            op.drop_index(idx_name, table_name=table)

    # ── Remove RunResult metadata columns ────────────────────────────
    run_result_drops = [
        col_name
        for col_name in ["execution_time_ms", "output_checksum", "input_hash"]
        if _column_exists("run_results", col_name)
    ]
    if run_result_drops:
        with op.batch_alter_table("run_results", schema=None) as batch_op:
            for col_name in run_result_drops:
                batch_op.drop_column(col_name)

    # ── Remove user unique indexes ───────────────────────────────────
    for idx_name in ["ix_users_google_id", "ix_users_username"]:
        if _index_exists("users", idx_name):
            op.drop_index(idx_name, table_name="users")

    # ── Remove user profile / OAuth columns ──────────────────────────
    user_drops = [
        col_name
        for col_name in ["display_name", "avatar_url", "google_id", "username"]
        if _column_exists("users", col_name)
    ]
    if user_drops:
        with op.batch_alter_table("users", schema=None) as batch_op:
            for col_name in user_drops:
                batch_op.drop_column(col_name)
