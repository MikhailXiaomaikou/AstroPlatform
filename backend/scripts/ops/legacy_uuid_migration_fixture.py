#!/usr/bin/env python3
"""Seed and verify the PostgreSQL 16 legacy UUID migration drill.

The wrapper builds the legacy database through Alembic itself: reset the
dedicated database's ``public`` schema, upgrade a fresh database to
``002a_uuid_bridge``, then downgrade to ``002``.  That produces all 13 original
tables with their UUID columns represented as ``VARCHAR(36)``.

Two populated scenarios are then exercised:

* ``valid`` upgrades from revision 002 to the repository's sole current head
  and proves every bridge UUID column, including a recursively discovered
  manual child FK, is native UUID with values and FK semantics preserved.
* ``dirty`` puts an invalid UUID in an unreferenced ``users.id`` row.  This is
  the final column in the bridge's deterministic conversion order, so earlier
  conversions are attempted before PostgreSQL rejects it.  The verifier proves
  the whole migration transaction rolled back to revision 002, VARCHAR(36),
  original values, and original FKs.

All destructive operations refuse databases whose names do not start with
``astro_uuid_drill_`` and refuse PostgreSQL majors other than 16.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine


DATABASE_PREFIX = "astro_uuid_drill_"
INVALID_USER_ID = "not-a-uuid"

BASE_TABLES = frozenset(
    {
        "users",
        "data_files",
        "pipeline_runs",
        "run_results",
        "pipeline_templates",
        "data_tags",
        "data_notes",
        "pipeline_versions",
        "team_members",
        "shared_pipelines",
        "pipeline_comments",
        "shared_datasets",
        "scheduled_runs",
    }
)

# This independent inventory is intentionally pinned to
# 002a_uuid_type_bridge._BASE_UUID_COLUMNS.  Its unit test fails if the bridge
# grows without the real-PostgreSQL drill being expanded as well.
BASE_UUID_COLUMNS = frozenset(
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
MANUAL_UUID_COLUMN = ("manual_records", "owner_id")
EXPECTED_UUID_COLUMNS = BASE_UUID_COLUMNS | {MANUAL_UUID_COLUMN}
DIRTY_UUID_COLUMN = ("users", "id")

ROW_IDS = {
    "users": "00000000-0000-4000-8000-000000000001",
    "data_files": "00000000-0000-4000-8000-000000000002",
    "pipeline_runs": "00000000-0000-4000-8000-000000000003",
    "run_results": "00000000-0000-4000-8000-000000000004",
    "pipeline_templates": "00000000-0000-4000-8000-000000000005",
    "data_tags": "00000000-0000-4000-8000-000000000006",
    "data_notes": "00000000-0000-4000-8000-000000000007",
    "pipeline_versions": "00000000-0000-4000-8000-000000000008",
    "team_members": "00000000-0000-4000-8000-000000000009",
    "shared_pipelines": "00000000-0000-4000-8000-00000000000a",
    "pipeline_comments": "00000000-0000-4000-8000-00000000000b",
    "shared_datasets": "00000000-0000-4000-8000-00000000000c",
    "scheduled_runs": "00000000-0000-4000-8000-00000000000d",
}

EXPECTED_VALID_VALUES = {
    ("users", "id"): (ROW_IDS["users"],),
    ("data_files", "id"): (ROW_IDS["data_files"],),
    ("data_files", "user_id"): (ROW_IDS["users"],),
    ("pipeline_runs", "id"): (ROW_IDS["pipeline_runs"],),
    ("pipeline_runs", "user_id"): (ROW_IDS["users"],),
    ("run_results", "id"): (ROW_IDS["run_results"],),
    ("run_results", "run_id"): (ROW_IDS["pipeline_runs"],),
    ("pipeline_templates", "id"): (ROW_IDS["pipeline_templates"],),
    ("pipeline_templates", "user_id"): (ROW_IDS["users"],),
    ("data_tags", "id"): (ROW_IDS["data_tags"],),
    ("data_tags", "user_id"): (ROW_IDS["users"],),
    ("data_tags", "data_file_id"): (ROW_IDS["data_files"],),
    ("data_notes", "id"): (ROW_IDS["data_notes"],),
    ("data_notes", "user_id"): (ROW_IDS["users"],),
    ("data_notes", "data_file_id"): (ROW_IDS["data_files"],),
    ("pipeline_versions", "id"): (ROW_IDS["pipeline_versions"],),
    ("pipeline_versions", "template_id"): (ROW_IDS["pipeline_templates"],),
    ("team_members", "id"): (ROW_IDS["team_members"],),
    ("team_members", "owner_id"): (ROW_IDS["users"],),
    ("team_members", "member_id"): (ROW_IDS["users"],),
    ("shared_pipelines", "id"): (ROW_IDS["shared_pipelines"],),
    ("shared_pipelines", "template_id"): (ROW_IDS["pipeline_templates"],),
    ("shared_pipelines", "shared_by"): (ROW_IDS["users"],),
    ("shared_pipelines", "shared_with"): (ROW_IDS["users"],),
    ("pipeline_comments", "id"): (ROW_IDS["pipeline_comments"],),
    ("pipeline_comments", "template_id"): (ROW_IDS["pipeline_templates"],),
    ("pipeline_comments", "user_id"): (ROW_IDS["users"],),
    ("shared_datasets", "id"): (ROW_IDS["shared_datasets"],),
    ("shared_datasets", "data_file_id"): (ROW_IDS["data_files"],),
    ("shared_datasets", "shared_by"): (ROW_IDS["users"],),
    ("shared_datasets", "shared_with"): (ROW_IDS["users"],),
    ("scheduled_runs", "id"): (ROW_IDS["scheduled_runs"],),
    ("scheduled_runs", "user_id"): (ROW_IDS["users"],),
    MANUAL_UUID_COLUMN: (ROW_IDS["users"],),
}

# name, source table/column, target table/column, delete rule, validated
BASE_FOREIGN_KEYS = frozenset(
    {
        ("data_files_user_id_fkey", "data_files", "user_id", "users", "id", "NO ACTION", True),
        ("pipeline_runs_user_id_fkey", "pipeline_runs", "user_id", "users", "id", "NO ACTION", True),
        ("run_results_run_id_fkey", "run_results", "run_id", "pipeline_runs", "id", "NO ACTION", True),
        ("pipeline_templates_user_id_fkey", "pipeline_templates", "user_id", "users", "id", "NO ACTION", True),
        ("data_tags_user_id_fkey", "data_tags", "user_id", "users", "id", "NO ACTION", True),
        ("data_tags_data_file_id_fkey", "data_tags", "data_file_id", "data_files", "id", "NO ACTION", True),
        ("data_notes_user_id_fkey", "data_notes", "user_id", "users", "id", "NO ACTION", True),
        ("data_notes_data_file_id_fkey", "data_notes", "data_file_id", "data_files", "id", "NO ACTION", True),
        ("pipeline_versions_template_id_fkey", "pipeline_versions", "template_id", "pipeline_templates", "id", "NO ACTION", True),
        ("team_members_owner_id_fkey", "team_members", "owner_id", "users", "id", "NO ACTION", True),
        ("team_members_member_id_fkey", "team_members", "member_id", "users", "id", "NO ACTION", True),
        ("shared_pipelines_template_id_fkey", "shared_pipelines", "template_id", "pipeline_templates", "id", "NO ACTION", True),
        ("shared_pipelines_shared_by_fkey", "shared_pipelines", "shared_by", "users", "id", "NO ACTION", True),
        ("shared_pipelines_shared_with_fkey", "shared_pipelines", "shared_with", "users", "id", "NO ACTION", True),
        ("pipeline_comments_template_id_fkey", "pipeline_comments", "template_id", "pipeline_templates", "id", "NO ACTION", True),
        ("pipeline_comments_user_id_fkey", "pipeline_comments", "user_id", "users", "id", "NO ACTION", True),
        ("shared_datasets_data_file_id_fkey", "shared_datasets", "data_file_id", "data_files", "id", "NO ACTION", True),
        ("shared_datasets_shared_by_fkey", "shared_datasets", "shared_by", "users", "id", "NO ACTION", True),
        ("shared_datasets_shared_with_fkey", "shared_datasets", "shared_with", "users", "id", "NO ACTION", True),
        ("scheduled_runs_user_id_fkey", "scheduled_runs", "user_id", "users", "id", "NO ACTION", True),
    }
)
MANUAL_FOREIGN_KEY = (
    "manual_records_owner_id_fkey",
    "manual_records",
    "owner_id",
    "users",
    "id",
    "CASCADE",
    True,
)

SEED_ROWS = (
    (
        "users",
        {
            "id": ROW_IDS["users"],
            "email": "uuid-drill@example.test",
            "password_hash": "not-a-real-password-hash",
        },
    ),
    (
        "data_files",
        {
            "id": ROW_IDS["data_files"],
            "user_id": ROW_IDS["users"],
            "source": "uuid-drill",
            "object_id": "legacy-fixture",
        },
    ),
    (
        "pipeline_runs",
        {
            "id": ROW_IDS["pipeline_runs"],
            "user_id": ROW_IDS["users"],
            "dag": "{}",
        },
    ),
    (
        "run_results",
        {
            "id": ROW_IDS["run_results"],
            "run_id": ROW_IDS["pipeline_runs"],
            "node_id": "uuid-drill-node",
        },
    ),
    (
        "pipeline_templates",
        {
            "id": ROW_IDS["pipeline_templates"],
            "user_id": ROW_IDS["users"],
            "name": "UUID drill template",
            "dag": "{}",
        },
    ),
    (
        "data_tags",
        {
            "id": ROW_IDS["data_tags"],
            "user_id": ROW_IDS["users"],
            "data_file_id": ROW_IDS["data_files"],
            "tag": "uuid-drill",
        },
    ),
    (
        "data_notes",
        {
            "id": ROW_IDS["data_notes"],
            "user_id": ROW_IDS["users"],
            "data_file_id": ROW_IDS["data_files"],
            "content": "UUID drill note",
        },
    ),
    (
        "pipeline_versions",
        {
            "id": ROW_IDS["pipeline_versions"],
            "template_id": ROW_IDS["pipeline_templates"],
            "dag": "{}",
        },
    ),
    (
        "team_members",
        {
            "id": ROW_IDS["team_members"],
            "owner_id": ROW_IDS["users"],
            "member_id": ROW_IDS["users"],
        },
    ),
    (
        "shared_pipelines",
        {
            "id": ROW_IDS["shared_pipelines"],
            "template_id": ROW_IDS["pipeline_templates"],
            "shared_by": ROW_IDS["users"],
            "shared_with": ROW_IDS["users"],
        },
    ),
    (
        "pipeline_comments",
        {
            "id": ROW_IDS["pipeline_comments"],
            "template_id": ROW_IDS["pipeline_templates"],
            "user_id": ROW_IDS["users"],
            "content": "UUID drill comment",
        },
    ),
    (
        "shared_datasets",
        {
            "id": ROW_IDS["shared_datasets"],
            "data_file_id": ROW_IDS["data_files"],
            "shared_by": ROW_IDS["users"],
            "shared_with": ROW_IDS["users"],
        },
    ),
    (
        "scheduled_runs",
        {
            "id": ROW_IDS["scheduled_runs"],
            "user_id": ROW_IDS["users"],
            "name": "UUID drill schedule",
            "dag": "{}",
            "input_data_id": "uuid-drill-input",
            "cron_expr": "0 0 * * *",
        },
    ),
)


@dataclass(frozen=True)
class Snapshot:
    revisions: tuple[str, ...]
    tables: frozenset[str]
    column_types: dict[tuple[str, str], str]
    values: dict[tuple[str, str], tuple[str, ...]]
    foreign_keys: frozenset[tuple[str, str, str, str, str, str, bool]]


def safe_async_database_url(raw_url: str) -> tuple[str, str]:
    if raw_url.startswith("postgresql://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)

    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("UUID drill requires a PostgreSQL asyncpg DATABASE_URL")
    database = url.database or ""
    if not database.startswith(DATABASE_PREFIX):
        raise ValueError(
            "Refusing destructive UUID drill outside a dedicated database "
            f"named {DATABASE_PREFIX}*"
        )
    return url.render_as_string(hide_password=False), database


def assert_postgresql_16(server_version_num: int) -> None:
    if server_version_num // 10_000 != 16:
        raise RuntimeError(
            "Legacy UUID drill requires PostgreSQL 16; "
            f"server_version_num={server_version_num}"
        )


def declared_unique_head() -> str:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"UUID drill requires exactly one Alembic head; found {heads}")
    return heads[0]


async def _assert_connected_database(
    connection: AsyncConnection, expected_database: str
) -> None:
    connected_database = await connection.scalar(text("SELECT current_database()"))
    if connected_database != expected_database:
        raise RuntimeError(
            "Connected database does not match the validated DATABASE_URL"
        )
    server_version_num = await connection.scalar(text("SHOW server_version_num"))
    assert_postgresql_16(int(str(server_version_num)))


async def reset_schema(raw_url: str) -> None:
    database_url, database = safe_async_database_url(raw_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await _assert_connected_database(connection, database)
            await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


async def _read_revisions(connection: AsyncConnection) -> tuple[str, ...]:
    rows = await connection.execute(
        text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
    )
    return tuple(str(row[0]) for row in rows)


async def _read_tables(connection: AsyncConnection) -> frozenset[str]:
    rows = await connection.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    )
    return frozenset(str(row[0]) for row in rows)


async def _read_column_types(
    connection: AsyncConnection,
) -> dict[tuple[str, str], str]:
    rows = await connection.execute(
        text(
            """
            SELECT table_name,
                   column_name,
                   CASE
                       WHEN udt_name = 'varchar'
                           THEN format('varchar(%s)', character_maximum_length)
                       ELSE udt_name
                   END AS type_signature
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
    )
    return {
        (str(row["table_name"]), str(row["column_name"])): str(
            row["type_signature"]
        )
        for row in rows.mappings()
        if (str(row["table_name"]), str(row["column_name"]))
        in EXPECTED_UUID_COLUMNS
    }


async def _read_foreign_keys(
    connection: AsyncConnection,
) -> frozenset[tuple[str, str, str, str, str, str, bool]]:
    rows = await connection.execute(
        text(
            """
            SELECT con.conname AS constraint_name,
                   child.relname AS source_table,
                   child_column.attname AS source_column,
                   parent.relname AS target_table,
                   parent_column.attname AS target_column,
                   CASE con.confdeltype
                       WHEN 'a' THEN 'NO ACTION'
                       WHEN 'r' THEN 'RESTRICT'
                       WHEN 'c' THEN 'CASCADE'
                       WHEN 'n' THEN 'SET NULL'
                       WHEN 'd' THEN 'SET DEFAULT'
                   END AS delete_rule,
                   con.convalidated
            FROM pg_constraint AS con
            JOIN pg_class AS child ON child.oid = con.conrelid
            JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
            JOIN pg_class AS parent ON parent.oid = con.confrelid
            JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
            CROSS JOIN LATERAL generate_subscripts(con.conkey, 1) AS position
            JOIN pg_attribute AS child_column
              ON child_column.attrelid = child.oid
             AND child_column.attnum = con.conkey[position]
            JOIN pg_attribute AS parent_column
              ON parent_column.attrelid = parent.oid
             AND parent_column.attnum = con.confkey[position]
            WHERE con.contype = 'f'
              AND child_ns.nspname = 'public'
              AND parent_ns.nspname = 'public'
            """
        )
    )
    return frozenset(
        (
            str(row["constraint_name"]),
            str(row["source_table"]),
            str(row["source_column"]),
            str(row["target_table"]),
            str(row["target_column"]),
            str(row["delete_rule"]),
            bool(row["convalidated"]),
        )
        for row in rows.mappings()
    )


async def _assert_complete_legacy_baseline(connection: AsyncConnection) -> None:
    revisions = await _read_revisions(connection)
    if revisions != ("002",):
        raise AssertionError(f"Legacy baseline revision is not 002: {revisions}")

    tables = await _read_tables(connection)
    expected_tables = BASE_TABLES | {"alembic_version"}
    if tables != expected_tables:
        raise AssertionError(
            f"Legacy baseline is not the complete 13-table schema: {tables}"
        )

    column_types = await _read_column_types(connection)
    expected_types = {column: "varchar(36)" for column in BASE_UUID_COLUMNS}
    if column_types != expected_types:
        raise AssertionError(
            "Legacy baseline UUID inventory is incomplete or not VARCHAR(36)"
        )

    foreign_keys = await _read_foreign_keys(connection)
    if foreign_keys != BASE_FOREIGN_KEYS:
        raise AssertionError("Legacy baseline FK graph differs from revision 002")


async def _insert_row(
    connection: AsyncConnection, table_name: str, values: dict[str, object]
) -> None:
    columns = ", ".join(f'"{column}"' for column in values)
    bindings = ", ".join(f":{column}" for column in values)
    await connection.execute(
        text(f'INSERT INTO "{table_name}" ({columns}) VALUES ({bindings})'),
        values,
    )


async def seed_fixture(raw_url: str, scenario: str) -> None:
    if scenario not in {"valid", "dirty"}:
        raise ValueError(f"Unsupported scenario: {scenario}")
    database_url, database = safe_async_database_url(raw_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await _assert_connected_database(connection, database)
            await _assert_complete_legacy_baseline(connection)
            await connection.execute(
                text(
                    """
                    CREATE TABLE public.manual_records (
                        id BIGSERIAL PRIMARY KEY,
                        owner_id VARCHAR(36) NOT NULL,
                        note TEXT NOT NULL,
                        CONSTRAINT manual_records_owner_id_fkey
                            FOREIGN KEY (owner_id) REFERENCES public.users(id)
                            ON DELETE CASCADE
                    )
                    """
                )
            )
            for table_name, values in SEED_ROWS:
                await _insert_row(connection, table_name, dict(values))
            await _insert_row(
                connection,
                "manual_records",
                {
                    "owner_id": ROW_IDS["users"],
                    "note": "recursive FK discovery",
                },
            )
            if scenario == "dirty":
                await _insert_row(
                    connection,
                    "users",
                    {
                        "id": INVALID_USER_ID,
                        "email": "dirty-uuid-drill@example.test",
                        "password_hash": "not-a-real-password-hash",
                    },
                )
    finally:
        await engine.dispose()


async def _read_values(
    connection: AsyncConnection,
) -> dict[tuple[str, str], tuple[str, ...]]:
    values: dict[tuple[str, str], tuple[str, ...]] = {}
    for table_name, column_name in sorted(EXPECTED_UUID_COLUMNS):
        rows = await connection.execute(
            text(
                f'SELECT "{column_name}"::text FROM "{table_name}" '
                f'ORDER BY "{column_name}"::text'
            )
        )
        values[(table_name, column_name)] = tuple(str(row[0]) for row in rows)
    return values


async def _read_snapshot(connection: AsyncConnection) -> Snapshot:
    return Snapshot(
        revisions=await _read_revisions(connection),
        tables=await _read_tables(connection),
        column_types=await _read_column_types(connection),
        values=await _read_values(connection),
        foreign_keys=await _read_foreign_keys(connection),
    )


def _assert_expected_foreign_keys(
    actual: frozenset[tuple[str, str, str, str, str, str, bool]],
    *,
    allow_unrelated: bool,
) -> None:
    expected = BASE_FOREIGN_KEYS | {MANUAL_FOREIGN_KEY}
    expected_names = {fact[0] for fact in expected}
    relevant_actual = frozenset(fact for fact in actual if fact[0] in expected_names)
    if relevant_actual != expected:
        raise AssertionError("Legacy UUID foreign-key names or semantics changed")
    if not allow_unrelated and actual != expected:
        raise AssertionError("Revision 002 rollback retained unexpected foreign keys")


def assert_snapshot(snapshot: Snapshot, scenario: str, expectation: str) -> None:
    supported = {("valid", "migrated"), ("dirty", "rollback")}
    if (scenario, expectation) not in supported:
        raise ValueError(
            f"Unsupported scenario/expectation: {scenario}/{expectation}"
        )

    expected_revision = declared_unique_head() if expectation == "migrated" else "002"
    if snapshot.revisions != (expected_revision,):
        raise AssertionError(
            f"Alembic revisions are {snapshot.revisions!r}, "
            f"expected only {expected_revision!r}"
        )

    if not (BASE_TABLES | {"manual_records", "alembic_version"}).issubset(
        snapshot.tables
    ):
        raise AssertionError("Expected legacy tables are missing after migration drill")

    if set(snapshot.column_types) != set(EXPECTED_UUID_COLUMNS):
        raise AssertionError("UUID drill did not inspect the complete bridge inventory")
    expected_type = "uuid" if expectation == "migrated" else "varchar(36)"
    wrong_types = {
        column: column_type
        for column, column_type in snapshot.column_types.items()
        if column_type != expected_type
    }
    if wrong_types:
        raise AssertionError(f"UUID columns have unexpected types: {wrong_types}")

    expected_values = dict(EXPECTED_VALID_VALUES)
    if scenario == "dirty":
        expected_values[DIRTY_UUID_COLUMN] = tuple(
            sorted((ROW_IDS["users"], INVALID_USER_ID))
        )
    if snapshot.values != expected_values:
        raise AssertionError("UUID values or parent/child relationships changed")

    _assert_expected_foreign_keys(
        snapshot.foreign_keys,
        allow_unrelated=expectation == "migrated",
    )

    if expectation == "rollback":
        expected_tables = BASE_TABLES | {"manual_records", "alembic_version"}
        if snapshot.tables != expected_tables:
            raise AssertionError("Failed head upgrade left partial later-revision tables")


async def verify_fixture(raw_url: str, scenario: str, expectation: str) -> None:
    database_url, database = safe_async_database_url(raw_url)
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            await _assert_connected_database(connection, database)
            snapshot = await _read_snapshot(connection)
    finally:
        await engine.dispose()
    assert_snapshot(snapshot, scenario, expectation)


def _database_url_from_environment() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the UUID migration drill")
    return database_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("reset")

    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--scenario", choices=("valid", "dirty"), required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument(
        "--scenario", choices=("valid", "dirty"), required=True
    )
    verify_parser.add_argument(
        "--expect", choices=("migrated", "rollback"), required=True
    )

    args = parser.parse_args()
    database_url = _database_url_from_environment()
    if args.command == "reset":
        asyncio.run(reset_schema(database_url))
        print("Reset dedicated UUID drill public schema")
        return
    if args.command == "seed":
        asyncio.run(seed_fixture(database_url, args.scenario))
        print(f"Seeded complete legacy UUID {args.scenario} fixture")
        return

    asyncio.run(verify_fixture(database_url, args.scenario, args.expect))
    print(f"Verified legacy UUID {args.scenario}/{args.expect} fixture")


if __name__ == "__main__":
    main()
