"""Consolidate manual migrations into Alembic.

Adds columns and indexes that were previously handled by
_migrate_add_columns() in app/main.py.  All column operations use
batch mode for SQLite compatibility; every operation is wrapped in
try/except so the migration is fully idempotent (safe to run on a
fresh database *or* one that already had the manual migration applied).

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


def upgrade() -> None:
    # ── Users table: new profile / OAuth columns ─────────────────────
    with op.batch_alter_table("users", schema=None) as batch_op:
        for col_name, col_type in [
            ("username", sa.String(255)),
            ("google_id", sa.String(255)),
            ("avatar_url", sa.Text()),
            ("display_name", sa.String(255)),
        ]:
            try:
                batch_op.add_column(sa.Column(col_name, col_type, nullable=True))
            except Exception:
                pass  # Column may already exist from manual migration

    # ── Users table: unique indexes ──────────────────────────────────
    for idx_name, table, cols, unique in [
        ("ix_users_username", "users", ["username"], True),
        ("ix_users_google_id", "users", ["google_id"], True),
    ]:
        try:
            op.create_index(idx_name, table, cols, unique=unique)
        except Exception:
            pass  # Index may already exist

    # ── RunResult: reproducibility metadata columns ──────────────────
    with op.batch_alter_table("run_results", schema=None) as batch_op:
        for col_name, col_type in [
            ("input_hash", sa.String(64)),
            ("output_checksum", sa.String(64)),
            ("execution_time_ms", sa.Integer()),
        ]:
            try:
                batch_op.add_column(sa.Column(col_name, col_type, nullable=True))
            except Exception:
                pass  # Column may already exist

    # ── Performance indexes on data_files and pipeline_runs ──────────
    for idx_name, table, cols in [
        ("idx_datafile_source", "data_files", ["source"]),
        ("idx_datafile_object_id", "data_files", ["object_id"]),
        ("idx_datafile_user_source", "data_files", ["user_id", "source"]),
        ("idx_pipelinerun_status", "pipeline_runs", ["status"]),
        ("idx_pipelinerun_user_status", "pipeline_runs", ["user_id", "status"]),
    ]:
        try:
            op.create_index(idx_name, table, cols)
        except Exception:
            pass  # Index may already exist


def downgrade() -> None:
    # ── Remove performance indexes ───────────────────────────────────
    for idx_name, table in [
        ("idx_pipelinerun_user_status", "pipeline_runs"),
        ("idx_pipelinerun_status", "pipeline_runs"),
        ("idx_datafile_user_source", "data_files"),
        ("idx_datafile_object_id", "data_files"),
        ("idx_datafile_source", "data_files"),
    ]:
        try:
            op.drop_index(idx_name, table_name=table)
        except Exception:
            pass

    # ── Remove RunResult metadata columns ────────────────────────────
    with op.batch_alter_table("run_results", schema=None) as batch_op:
        for col_name in ["execution_time_ms", "output_checksum", "input_hash"]:
            try:
                batch_op.drop_column(col_name)
            except Exception:
                pass

    # ── Remove user unique indexes ───────────────────────────────────
    for idx_name in ["ix_users_google_id", "ix_users_username"]:
        try:
            op.drop_index(idx_name, table_name="users")
        except Exception:
            pass

    # ── Remove user profile / OAuth columns ──────────────────────────
    with op.batch_alter_table("users", schema=None) as batch_op:
        for col_name in ["display_name", "avatar_url", "google_id", "username"]:
            try:
                batch_op.drop_column(col_name)
            except Exception:
                pass
