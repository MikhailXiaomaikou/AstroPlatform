"""Initial schema — all 13 tables.

UUID keys use the models' UUIDType (String(36) on SQLite, native UUID on
PostgreSQL) so a fresh PostgreSQL database is type-consistent with the
model-metadata tables that revision 0c9293030537 creates later; a varchar
users.id made chat_sessions' native-UUID FK fail with DatatypeMismatchError
on fresh installs. JSON payload columns (dag/results/metadata) use the
models' JSONType for the same reason. Existing databases never re-run
this revision.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users (no FK deps)
    op.create_table(
        "users",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("subscription_tier", sa.String(50), server_default="solo"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. data_files (FK -> users)
    op.create_table(
        "data_files",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("object_id", sa.String(255), nullable=False),
        sa.Column("fits_path", sa.Text, nullable=True),
        sa.Column("metadata", JSONType(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 3. pipeline_runs (FK -> users)
    op.create_table(
        "pipeline_runs",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("dag", JSONType(), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("results", JSONType(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 4. run_results (FK -> pipeline_runs)
    op.create_table(
        "run_results",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("run_id", UUIDType(), sa.ForeignKey("pipeline_runs.id"), nullable=False),
        sa.Column("node_id", sa.String(255), nullable=False),
        sa.Column("output_path", sa.Text, nullable=True),
        sa.Column("logs", sa.Text, nullable=True),
    )

    # 5. pipeline_templates (FK -> users, nullable)
    op.create_table(
        "pipeline_templates",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("dag", JSONType(), nullable=False),
        sa.Column("is_builtin", sa.Boolean, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 6. data_tags (FK -> users, data_files)
    op.create_table(
        "data_tags",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("data_file_id", UUIDType(), sa.ForeignKey("data_files.id"), nullable=False),
        sa.Column("tag", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 7. data_notes (FK -> users, data_files)
    op.create_table(
        "data_notes",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("data_file_id", UUIDType(), sa.ForeignKey("data_files.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 8. pipeline_versions (FK -> pipeline_templates)
    op.create_table(
        "pipeline_versions",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("template_id", UUIDType(), sa.ForeignKey("pipeline_templates.id"), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("dag", JSONType(), nullable=False),
        sa.Column("change_note", sa.Text, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 9. team_members (FK -> users x2)
    op.create_table(
        "team_members",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("owner_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("member_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(50), server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 10. shared_pipelines (FK -> pipeline_templates, users x2)
    op.create_table(
        "shared_pipelines",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("template_id", UUIDType(), sa.ForeignKey("pipeline_templates.id"), nullable=False),
        sa.Column("shared_by", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("shared_with", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("permission", sa.String(20), server_default="view"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 11. pipeline_comments (FK -> pipeline_templates, users)
    op.create_table(
        "pipeline_comments",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("template_id", UUIDType(), sa.ForeignKey("pipeline_templates.id"), nullable=False),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 12. shared_datasets (FK -> data_files, users x2)
    op.create_table(
        "shared_datasets",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("data_file_id", UUIDType(), sa.ForeignKey("data_files.id"), nullable=False),
        sa.Column("shared_by", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("shared_with", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 13. scheduled_runs (FK -> users)
    op.create_table(
        "scheduled_runs",
        sa.Column("id", UUIDType(), primary_key=True),
        sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("dag", JSONType(), nullable=False),
        sa.Column("input_data_id", sa.String(500), nullable=False),
        sa.Column("cron_expr", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean, server_default="1"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("scheduled_runs")
    op.drop_table("shared_datasets")
    op.drop_table("pipeline_comments")
    op.drop_table("shared_pipelines")
    op.drop_table("team_members")
    op.drop_table("pipeline_versions")
    op.drop_table("data_notes")
    op.drop_table("data_tags")
    op.drop_table("pipeline_templates")
    op.drop_table("run_results")
    op.drop_table("pipeline_runs")
    op.drop_table("data_files")
    op.drop_table("users")
