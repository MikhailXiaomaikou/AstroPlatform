"""Convert legacy PostgreSQL UUID strings before model-metadata tables.

Revision ID: 002a_uuid_bridge
Revises: 002
Create Date: 2026-07-10

The original ``001_initial`` revision used ``VARCHAR(36)`` for UUID primary
and foreign keys.  Fresh installs now create native PostgreSQL UUID columns,
but databases or restored snapshots that already recorded revision 001/002 do
not replay that historical change.  Convert those legacy columns before
``0c9293030537`` creates tables whose native-UUID foreign keys reference them.

SQLite deliberately remains a no-op: its portable representation is still
``String(36)``.  PostgreSQL casts are transactional and fail closed if a legacy
value is not a valid UUID; foreign keys are restored only after every affected
column has converted successfully.
"""

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "002a_uuid_bridge"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# UUID-bearing columns created by the legacy 001 baseline.  Foreign-key
# discovery below also pulls in any extra/manual child columns that reference
# one of these keys, which keeps old installations with manually-created tables
# consistent without guessing their table names.
_BASE_UUID_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("users", "id"),
        ("data_files", "id"),
        ("data_files", "user_id"),
        ("pipeline_runs", "id"),
        ("pipeline_runs", "user_id"),
        ("run_results", "id"),
        ("run_results", "run_id"),
        ("pipeline_templates", "id"),
        ("pipeline_templates", "user_id"),
        ("data_tags", "id"),
        ("data_tags", "user_id"),
        ("data_tags", "data_file_id"),
        ("data_notes", "id"),
        ("data_notes", "user_id"),
        ("data_notes", "data_file_id"),
        ("pipeline_versions", "id"),
        ("pipeline_versions", "template_id"),
        ("team_members", "id"),
        ("team_members", "owner_id"),
        ("team_members", "member_id"),
        ("shared_pipelines", "id"),
        ("shared_pipelines", "template_id"),
        ("shared_pipelines", "shared_by"),
        ("shared_pipelines", "shared_with"),
        ("pipeline_comments", "id"),
        ("pipeline_comments", "template_id"),
        ("pipeline_comments", "user_id"),
        ("shared_datasets", "id"),
        ("shared_datasets", "data_file_id"),
        ("shared_datasets", "shared_by"),
        ("shared_datasets", "shared_with"),
        ("scheduled_runs", "id"),
        ("scheduled_runs", "user_id"),
    }
)


def _foreign_keys(inspector: Any, table_names: set[str]) -> list[dict[str, Any]]:
    discovered: list[dict[str, Any]] = []
    for table_name in sorted(table_names):
        for raw in inspector.get_foreign_keys(table_name):
            item = dict(raw)
            item["source_table"] = table_name
            discovered.append(item)
    return discovered


def _expanded_uuid_columns(
    table_names: set[str], foreign_keys: list[dict[str, Any]]
) -> set[tuple[str, str]]:
    targets = {
        item for item in _BASE_UUID_COLUMNS if item[0] in table_names
    }
    changed = True
    while changed:
        changed = False
        for foreign_key in foreign_keys:
            referred_table = str(foreign_key.get("referred_table") or "")
            referred_columns = list(foreign_key.get("referred_columns") or [])
            if not any(
                (referred_table, str(column)) in targets
                for column in referred_columns
            ):
                continue
            source_table = str(foreign_key["source_table"])
            for column in foreign_key.get("constrained_columns") or []:
                target = (source_table, str(column))
                if target not in targets:
                    targets.add(target)
                    changed = True
    return targets


def _column_types(
    inspector: Any,
    table_names: set[str],
) -> dict[tuple[str, str], sa.types.TypeEngine]:
    result: dict[tuple[str, str], sa.types.TypeEngine] = {}
    for table_name in sorted(table_names):
        for column in inspector.get_columns(table_name):
            result[(table_name, str(column["name"]))] = column["type"]
    return result


def _foreign_key_columns(
    foreign_key: dict[str, Any],
) -> set[tuple[str, str]]:
    source_table = str(foreign_key["source_table"])
    referred_table = str(foreign_key.get("referred_table") or "")
    columns = {
        (source_table, str(column))
        for column in foreign_key.get("constrained_columns") or []
    }
    columns.update(
        (referred_table, str(column))
        for column in foreign_key.get("referred_columns") or []
    )
    return columns


def _drop_foreign_key(foreign_key: dict[str, Any]) -> None:
    name = foreign_key.get("name")
    if not name:
        raise RuntimeError(
            "Cannot safely convert UUID columns with an unnamed foreign key on "
            f"{foreign_key['source_table']}"
        )
    op.drop_constraint(
        str(name),
        str(foreign_key["source_table"]),
        type_="foreignkey",
    )


def _restore_foreign_key(foreign_key: dict[str, Any]) -> None:
    options = dict(foreign_key.get("options") or {})
    supported_options = {
        key: options[key]
        for key in (
            "onupdate",
            "ondelete",
            "deferrable",
            "initially",
            "match",
        )
        if key in options
    }
    op.create_foreign_key(
        str(foreign_key["name"]),
        str(foreign_key["source_table"]),
        str(foreign_key["referred_table"]),
        list(foreign_key.get("constrained_columns") or []),
        list(foreign_key.get("referred_columns") or []),
        source_schema=None,
        referent_schema=foreign_key.get("referred_schema"),
        **supported_options,
    )


def _is_native_uuid(column_type: sa.types.TypeEngine) -> bool:
    return isinstance(column_type, sa.Uuid) or (
        column_type.__class__.__name__.lower() == "uuid"
    )


def _convert(*, to_native_uuid: bool) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())
    foreign_keys = _foreign_keys(inspector, table_names)
    targets = _expanded_uuid_columns(table_names, foreign_keys)
    column_types = _column_types(inspector, table_names)

    if to_native_uuid:
        conversions = {
            target
            for target in targets
            if isinstance(column_types.get(target), sa.String)
        }
    else:
        conversions = {
            target
            for target in targets
            if target in column_types and _is_native_uuid(column_types[target])
        }
    if not conversions:
        return

    affected_foreign_keys = [
        foreign_key
        for foreign_key in foreign_keys
        if _foreign_key_columns(foreign_key) & conversions
    ]
    for foreign_key in affected_foreign_keys:
        _drop_foreign_key(foreign_key)

    for table_name, column_name in sorted(conversions):
        quoted_column = '"' + column_name.replace('"', '""') + '"'
        if to_native_uuid:
            op.alter_column(
                table_name,
                column_name,
                existing_type=column_types[(table_name, column_name)],
                type_=postgresql.UUID(as_uuid=True),
                postgresql_using=f"{quoted_column}::uuid",
            )
        else:
            op.alter_column(
                table_name,
                column_name,
                existing_type=column_types[(table_name, column_name)],
                type_=sa.String(length=36),
                postgresql_using=f"{quoted_column}::text",
            )

    for foreign_key in affected_foreign_keys:
        _restore_foreign_key(foreign_key)


def upgrade() -> None:
    _convert(to_native_uuid=True)


def downgrade() -> None:
    _convert(to_native_uuid=False)
