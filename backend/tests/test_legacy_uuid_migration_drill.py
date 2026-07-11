"""Unit coverage for the real-PostgreSQL legacy UUID drill fixture."""

import importlib.util
from pathlib import Path

import pytest


_BACKEND = Path(__file__).resolve().parents[1]
_FIXTURE = _BACKEND / "scripts" / "ops" / "legacy_uuid_migration_fixture.py"
_BRIDGE = _BACKEND / "alembic" / "versions" / "002a_uuid_type_bridge.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_fixture():
    return _load(_FIXTURE, "legacy_uuid_fixture")


def _snapshot(fixture, *, migrated: bool, dirty: bool = False):
    column_type = "uuid" if migrated else "varchar(36)"
    values = dict(fixture.EXPECTED_VALID_VALUES)
    if dirty:
        values[fixture.DIRTY_UUID_COLUMN] = tuple(
            sorted((fixture.ROW_IDS["users"], fixture.INVALID_USER_ID))
        )
    tables = fixture.BASE_TABLES | {"manual_records", "alembic_version"}
    if migrated:
        tables |= {"chat_sessions", "research_jobs"}
    return fixture.Snapshot(
        revisions=(fixture.declared_unique_head() if migrated else "002",),
        tables=frozenset(tables),
        column_types={
            column: column_type for column in fixture.EXPECTED_UUID_COLUMNS
        },
        values=values,
        foreign_keys=fixture.BASE_FOREIGN_KEYS | {fixture.MANUAL_FOREIGN_KEY},
        api_keys_type="jsonb" if migrated else "text",
        api_keys_values=(fixture.LEGACY_API_KEYS_JSON,),
    )


def test_inventory_matches_bridge_and_complete_revision_002_schema() -> None:
    fixture = _load_fixture()
    bridge = _load(_BRIDGE, "uuid_bridge_inventory")

    assert fixture.BASE_UUID_COLUMNS == bridge._BASE_UUID_COLUMNS
    assert len(fixture.BASE_TABLES) == 13
    assert {table for table, _column in fixture.BASE_UUID_COLUMNS} == fixture.BASE_TABLES
    assert len(fixture.BASE_FOREIGN_KEYS) == 20
    assert {table for table, _values in fixture.SEED_ROWS} == fixture.BASE_TABLES


def test_dirty_value_is_in_last_sorted_conversion_and_is_unreferenced() -> None:
    fixture = _load_fixture()

    assert sorted(fixture.EXPECTED_UUID_COLUMNS)[-1] == fixture.DIRTY_UUID_COLUMN
    assert fixture.DIRTY_UUID_COLUMN == ("users", "id")
    assert fixture.INVALID_USER_ID not in {
        value
        for column, values in fixture.EXPECTED_VALID_VALUES.items()
        if column != fixture.DIRTY_UUID_COLUMN
        for value in values
    }


def test_database_safety_guard_accepts_only_dedicated_postgresql_database() -> None:
    fixture = _load_fixture()

    normalized, database = fixture.safe_async_database_url(
        "postgresql://astro:secret@127.0.0.1:5432/astro_uuid_drill_ci"
    )
    assert normalized.startswith("postgresql+asyncpg://")
    assert database == "astro_uuid_drill_ci"

    with pytest.raises(ValueError, match="dedicated database"):
        fixture.safe_async_database_url(
            "postgresql://astro:secret@127.0.0.1:5432/astro"
        )
    with pytest.raises(ValueError, match="PostgreSQL asyncpg"):
        fixture.safe_async_database_url("sqlite+aiosqlite:///astro_uuid_drill_ci.db")


def test_fixture_pins_postgresql_16_and_one_declared_head() -> None:
    fixture = _load_fixture()

    fixture.assert_postgresql_16(160014)
    assert fixture.declared_unique_head()
    with pytest.raises(RuntimeError, match="requires PostgreSQL 16"):
        fixture.assert_postgresql_16(150014)
    with pytest.raises(RuntimeError, match="requires PostgreSQL 16"):
        fixture.assert_postgresql_16(170000)


def test_valid_snapshot_requires_all_native_values_and_restored_fks() -> None:
    fixture = _load_fixture()

    fixture.assert_snapshot(_snapshot(fixture, migrated=True), "valid", "migrated")


def test_dirty_snapshot_requires_complete_transactional_rollback() -> None:
    fixture = _load_fixture()

    fixture.assert_snapshot(
        _snapshot(fixture, migrated=False, dirty=True), "dirty", "rollback"
    )


@pytest.mark.parametrize(
    "damage", ["type", "value", "foreign_key", "revision", "inventory", "table"]
)
def test_verifier_fails_closed_on_partial_or_laundered_outcome(damage: str) -> None:
    fixture = _load_fixture()
    original = _snapshot(fixture, migrated=True)
    column_types = dict(original.column_types)
    values = dict(original.values)
    foreign_keys = set(original.foreign_keys)
    revisions = original.revisions
    tables = set(original.tables)

    if damage == "type":
        column_types[("data_files", "user_id")] = "varchar(36)"
    elif damage == "value":
        values[("data_files", "user_id")] = (fixture.ROW_IDS["data_files"],)
    elif damage == "foreign_key":
        foreign_keys.remove(fixture.MANUAL_FOREIGN_KEY)
    elif damage == "revision":
        revisions = ("002",)
    elif damage == "inventory":
        column_types.pop(("shared_datasets", "shared_with"))
    else:
        tables.remove("data_notes")

    damaged = fixture.Snapshot(
        revisions=revisions,
        tables=frozenset(tables),
        column_types=column_types,
        values=values,
        foreign_keys=frozenset(foreign_keys),
        api_keys_type=original.api_keys_type,
        api_keys_values=original.api_keys_values,
    )
    with pytest.raises(AssertionError):
        fixture.assert_snapshot(damaged, "valid", "migrated")
