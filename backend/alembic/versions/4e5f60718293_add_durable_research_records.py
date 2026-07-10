"""Add durable provenance and research-job lifecycle tables.

Revision ID: 4e5f60718293
Revises: 3d4e5f607182
Create Date: 2026-07-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.models.schemas import JSONType, UUIDType

revision: str = "4e5f60718293"
down_revision: Union[str, None] = "3d4e5f607182"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _create_index(name: str, table: str, columns: list[str]) -> None:
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    if not _table_exists("provenance_records"):
        op.create_table(
            "provenance_records",
            sa.Column("id", UUIDType(), primary_key=True, nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("session_id", UUIDType(), sa.ForeignKey("chat_sessions.id"), nullable=True),
            sa.Column("entity_type", sa.String(length=100), nullable=False),
            sa.Column("entity_id", sa.String(length=255), nullable=False),
            sa.Column("activity", sa.String(length=255), nullable=False),
            sa.Column("params", JSONType(), nullable=False),
            sa.Column("parent_ids", JSONType(), nullable=False),
            sa.Column("agent", sa.String(length=100), nullable=False),
            sa.Column("environment", JSONType(), nullable=False),
            sa.Column("data_release", sa.String(length=255), nullable=True),
            sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    for name, columns in (
        ("ix_provenance_records_user_id", ["user_id"]),
        ("ix_provenance_records_session_id", ["session_id"]),
        ("ix_provenance_records_entity_id", ["entity_id"]),
        ("idx_provenance_owner_entity", ["user_id", "entity_id"]),
        ("idx_provenance_entity_created", ["entity_id", "created_at"]),
    ):
        _create_index(name, "provenance_records", columns)

    if not _table_exists("research_jobs"):
        op.create_table(
            "research_jobs",
            sa.Column("job_id", sa.String(length=255), primary_key=True, nullable=False),
            sa.Column("user_id", UUIDType(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("session_id", UUIDType(), sa.ForeignKey("chat_sessions.id"), nullable=True),
            sa.Column("tool_name", sa.String(length=255), nullable=False),
            sa.Column("inputs_hash", sa.String(length=64), nullable=False),
            sa.Column("args", JSONType(), nullable=True),
            sa.Column("args_replayable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("progress", sa.Float(), nullable=True),
            sa.Column("progress_message", sa.Text(), nullable=True),
            sa.Column("result", JSONType(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("error_class", sa.String(length=255), nullable=True),
            sa.Column("background_backend", sa.String(length=32), nullable=False, server_default="celery"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    for name, columns in (
        ("ix_research_jobs_user_id", ["user_id"]),
        ("ix_research_jobs_session_id", ["session_id"]),
        ("ix_research_jobs_tool_name", ["tool_name"]),
        ("ix_research_jobs_inputs_hash", ["inputs_hash"]),
        ("ix_research_jobs_status", ["status"]),
        ("idx_research_job_owner_created", ["user_id", "created_at"]),
        ("idx_research_job_owner_status", ["user_id", "status"]),
    ):
        _create_index(name, "research_jobs", columns)


def downgrade() -> None:
    if _table_exists("research_jobs"):
        op.drop_table("research_jobs")
    if _table_exists("provenance_records"):
        op.drop_table("provenance_records")
