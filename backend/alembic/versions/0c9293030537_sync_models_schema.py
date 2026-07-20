"""Sync Alembic schema with current SQLAlchemy models.

Revision ID: 0c9293030537
Revises: 002a_uuid_bridge
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import app.models.schemas  # noqa: F401 - load all model tables into metadata
from app.models.database import Base
from app.models.schemas import EncryptedJSONType, JSONType, UUIDType


revision: str = "0c9293030537"
down_revision: Union[str, None] = "002a_uuid_bridge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_TABLES_IN_DEPENDENCY_ORDER = [
    "inference_logs",
    "isochrone_cache",
    "transient_alerts",
    "chat_sessions",
    "friendships",
    "saved_objects",
    "search_history",
    "setup_keys",
    "shared_notebooks",
    "shared_results",
    "team_activities",
    "user_events",
    "user_research_profiles",
    "paper_drafts",
    "session_comments",
    "session_embeddings",
    "session_forks",
    "session_snapshots",
    "shared_sessions",
]


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {col["name"] for col in _inspector().get_columns(table_name)}


def _create_table_if_missing(table_name: str) -> None:
    if _table_exists(table_name):
        return
    Base.metadata.tables[table_name].create(op.get_bind(), checkfirst=True)


def _create_chat_sessions_at_revision() -> None:
    """Create the 0c929 chat table without columns introduced later.

    This historical migration predates ``research_workspaces``. Building the
    table from today's mutable ORM metadata would include ``workspace_id`` and
    make a fresh PostgreSQL install reference a table that revision 8c93 has
    not created yet. Keep this one table as an explicit revision snapshot;
    revision 8c93 adds the workspace foreign key in dependency order.
    """

    if _table_exists("chat_sessions"):
        return
    op.create_table(
        "chat_sessions",
        sa.Column("id", UUIDType(), nullable=False),
        sa.Column("user_id", UUIDType(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("messages", JSONType(), nullable=False),
        sa.Column("audit_log", JSONType(), nullable=True),
        sa.Column("agent_status", sa.String(length=32), nullable=True),
        sa.Column("current_run_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_chatsession_user", "chat_sessions", ["user_id"])
    op.create_index(
        "idx_chatsession_user_created",
        "chat_sessions",
        ["user_id", "created_at"],
    )


def _drop_table_if_exists(table_name: str) -> None:
    if _table_exists(table_name):
        op.drop_table(table_name)


def upgrade() -> None:
    for table_name in NEW_TABLES_IN_DEPENDENCY_ORDER:
        if table_name == "chat_sessions":
            _create_chat_sessions_at_revision()
        else:
            _create_table_if_missing(table_name)

    if _table_exists("pipeline_comments") and not _column_exists("pipeline_comments", "parent_comment_id"):
        with op.batch_alter_table("pipeline_comments", schema=None) as batch_op:
            batch_op.add_column(sa.Column("parent_comment_id", UUIDType(), nullable=True))
            batch_op.create_foreign_key(
                "fk_pipeline_comments_parent_comment_id",
                "pipeline_comments",
                ["parent_comment_id"],
                ["id"],
            )

    if _table_exists("pipeline_runs") and not _column_exists("pipeline_runs", "environment"):
        with op.batch_alter_table("pipeline_runs", schema=None) as batch_op:
            batch_op.add_column(sa.Column("environment", JSONType(), nullable=True))

    if _table_exists("chat_sessions") and not _column_exists("chat_sessions", "audit_log"):
        with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
            batch_op.add_column(sa.Column("audit_log", JSONType(), nullable=True))

    if _table_exists("users") and not _column_exists("users", "anthropic_api_key"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("anthropic_api_key", sa.Text(), nullable=True))

    if _table_exists("users") and not _column_exists("users", "api_keys"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(sa.Column("api_keys", EncryptedJSONType(), nullable=True))


def downgrade() -> None:
    if _table_exists("users") and _column_exists("users", "api_keys"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("api_keys")

    if _table_exists("users") and _column_exists("users", "anthropic_api_key"):
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.drop_column("anthropic_api_key")

    if _table_exists("pipeline_runs") and _column_exists("pipeline_runs", "environment"):
        with op.batch_alter_table("pipeline_runs", schema=None) as batch_op:
            batch_op.drop_column("environment")

    if _table_exists("chat_sessions") and _column_exists("chat_sessions", "audit_log"):
        with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
            batch_op.drop_column("audit_log")

    if _table_exists("pipeline_comments") and _column_exists("pipeline_comments", "parent_comment_id"):
        with op.batch_alter_table("pipeline_comments", schema=None) as batch_op:
            batch_op.drop_constraint("fk_pipeline_comments_parent_comment_id", type_="foreignkey")
            batch_op.drop_column("parent_comment_id")

    for table_name in reversed(NEW_TABLES_IN_DEPENDENCY_ORDER):
        _drop_table_if_exists(table_name)
