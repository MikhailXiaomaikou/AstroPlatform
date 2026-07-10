"""Align legacy Alembic tables with current SQLAlchemy constraints.

Revision ID: 5f60718293a4
Revises: 4e5f60718293
Create Date: 2026-07-10

The original baseline migrations left Python-non-null model columns nullable in
the database and represented two user uniqueness rules as indexes instead of
constraints. Backfill before tightening so this remains safe for populated
legacy databases as well as fresh SQLite/PostgreSQL upgrades.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5f60718293a4"
down_revision: Union[str, None] = "4e5f60718293"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMN_FIXES: dict[str, tuple[tuple[str, sa.types.TypeEngine, str], ...]] = {
    "comments": (
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "data_files": (
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "data_notes": (
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "data_tags": (
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "pipeline_comments": (
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "pipeline_runs": (
        ("status", sa.String(length=50), "'pending'"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "pipeline_templates": (
        ("description", sa.Text(), "''"),
        ("is_builtin", sa.Boolean(), "false"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "pipeline_versions": (
        ("version", sa.Integer(), "1"),
        ("change_note", sa.Text(), "''"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "scheduled_runs": (
        ("enabled", sa.Boolean(), "true"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "shared_datasets": (
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "shared_pipelines": (
        ("permission", sa.String(length=20), "'view'"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "team_members": (
        ("role", sa.String(length=50), "'member'"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "trending_visibility": (
        ("updated_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
    "users": (
        ("username", sa.String(length=255), "NULL"),
        ("subscription_tier", sa.String(length=50), "'solo'"),
        ("created_at", sa.DateTime(timezone=True), "CURRENT_TIMESTAMP"),
    ),
}


def _backfill() -> None:
    # Usernames need a unique deterministic value; UUID primary keys make this
    # collision-free on both PostgreSQL and SQLite.
    op.execute(
        sa.text(
            "UPDATE users "
            "SET username = 'user_' || replace(CAST(id AS VARCHAR), '-', '') "
            "WHERE username IS NULL OR trim(username) = ''"
        )
    )
    for table_name, columns in _COLUMN_FIXES.items():
        for column_name, _column_type, fallback_sql in columns:
            if table_name == "users" and column_name == "username":
                continue
            op.execute(
                sa.text(
                    f'UPDATE "{table_name}" SET "{column_name}" = {fallback_sql} '
                    f'WHERE "{column_name}" IS NULL'
                )
            )


def _set_nullable(nullable: bool) -> None:
    for table_name, columns in _COLUMN_FIXES.items():
        with op.batch_alter_table(table_name, schema=None) as batch_op:
            for column_name, column_type, _fallback_sql in columns:
                batch_op.alter_column(
                    column_name,
                    existing_type=column_type,
                    nullable=nullable,
                )


def _index_names(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _unique_constraint_names(table_name: str) -> set[str]:
    return {
        str(constraint["name"])
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
        if constraint.get("name")
    }


def upgrade() -> None:
    _backfill()
    _set_nullable(False)

    indexes = _index_names("users")
    constraints = _unique_constraint_names("users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        if "ix_users_username" in indexes:
            batch_op.drop_index("ix_users_username")
        if "ix_users_google_id" in indexes:
            batch_op.drop_index("ix_users_google_id")
        if "uq_users_username" not in constraints:
            batch_op.create_unique_constraint("uq_users_username", ["username"])
        if "uq_users_google_id" not in constraints:
            batch_op.create_unique_constraint("uq_users_google_id", ["google_id"])


def downgrade() -> None:
    constraints = _unique_constraint_names("users")
    indexes = _index_names("users")
    with op.batch_alter_table("users", schema=None) as batch_op:
        if "uq_users_google_id" in constraints:
            batch_op.drop_constraint("uq_users_google_id", type_="unique")
        if "uq_users_username" in constraints:
            batch_op.drop_constraint("uq_users_username", type_="unique")
        if "ix_users_google_id" not in indexes:
            batch_op.create_index("ix_users_google_id", ["google_id"], unique=True)
        if "ix_users_username" not in indexes:
            batch_op.create_index("ix_users_username", ["username"], unique=True)

    _set_nullable(True)
