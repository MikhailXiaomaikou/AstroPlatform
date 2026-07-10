"""Regression coverage for the PostgreSQL legacy UUID bridge migration."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import String
from sqlalchemy.dialects import postgresql


_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, _VERSIONS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Inspector:
    def __init__(self, *, native: bool):
        uuid_type = postgresql.UUID(as_uuid=True) if native else String(36)
        self._columns = {
            "users": [
                {"name": "id", "type": uuid_type},
                {"name": "email", "type": String(255)},
            ],
            "data_files": [
                {"name": "id", "type": uuid_type},
                {"name": "user_id", "type": uuid_type},
            ],
            # Simulate a legacy installation with an extra manually-created
            # child table. The bridge must discover its FK column recursively.
            "manual_records": [
                {"name": "id", "type": String(64)},
                {"name": "owner_id", "type": uuid_type},
            ],
        }
        self._foreign_keys = {
            "users": [],
            "data_files": [
                {
                    "name": "data_files_user_id_fkey",
                    "constrained_columns": ["user_id"],
                    "referred_schema": None,
                    "referred_table": "users",
                    "referred_columns": ["id"],
                    "options": {"ondelete": "CASCADE"},
                }
            ],
            "manual_records": [
                {
                    "name": "manual_records_owner_id_fkey",
                    "constrained_columns": ["owner_id"],
                    "referred_schema": None,
                    "referred_table": "users",
                    "referred_columns": ["id"],
                    "options": {},
                }
            ],
        }

    def get_table_names(self):
        return list(self._columns)

    def get_columns(self, table_name: str):
        return list(self._columns[table_name])

    def get_foreign_keys(self, table_name: str):
        return list(self._foreign_keys[table_name])


def _capture_operations(monkeypatch, migration, *, native: bool):
    operations: list[tuple[str, tuple, dict]] = []
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    inspector = _Inspector(native=native)

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(
        migration.op,
        "drop_constraint",
        lambda *args, **kwargs: operations.append(("drop", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda *args, **kwargs: operations.append(("alter", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_foreign_key",
        lambda *args, **kwargs: operations.append(("create", args, kwargs)),
    )
    return operations


def test_revision_runs_before_model_metadata_sync() -> None:
    bridge = _load_module("002a_uuid_type_bridge.py", "uuid_bridge_revision")
    sync = _load_module("0c9293030537_sync_models_schema.py", "sync_schema_revision")

    assert bridge.down_revision == "002"
    assert sync.down_revision == bridge.revision


def test_sqlite_upgrade_and_downgrade_are_noops(monkeypatch) -> None:
    migration = _load_module("002a_uuid_type_bridge.py", "uuid_bridge_sqlite")
    bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

    def fail_if_inspected(_bind):
        raise AssertionError("SQLite must not inspect or rewrite UUID columns")

    monkeypatch.setattr(migration.sa, "inspect", fail_if_inspected)

    migration.upgrade()
    migration.downgrade()


def test_legacy_postgresql_columns_convert_before_fks_are_restored(
    monkeypatch,
) -> None:
    migration = _load_module("002a_uuid_type_bridge.py", "uuid_bridge_upgrade")
    operations = _capture_operations(monkeypatch, migration, native=False)

    migration.upgrade()

    operation_names = [operation[0] for operation in operations]
    assert operation_names[:2] == ["drop", "drop"]
    assert operation_names[-2:] == ["create", "create"]
    assert operation_names[2:-2] == ["alter", "alter", "alter", "alter"]

    altered = {
        (args[0], args[1]): kwargs
        for operation, args, kwargs in operations
        if operation == "alter"
    }
    assert set(altered) == {
        ("users", "id"),
        ("data_files", "id"),
        ("data_files", "user_id"),
        ("manual_records", "owner_id"),
    }
    for kwargs in altered.values():
        assert isinstance(kwargs["type_"], postgresql.UUID)
        assert kwargs["postgresql_using"].endswith("::uuid")

    recreated = {
        args[0]: kwargs
        for operation, args, kwargs in operations
        if operation == "create"
    }
    assert recreated["data_files_user_id_fkey"]["ondelete"] == "CASCADE"


def test_fresh_native_postgresql_schema_is_a_noop(monkeypatch) -> None:
    migration = _load_module("002a_uuid_type_bridge.py", "uuid_bridge_native")
    operations = _capture_operations(monkeypatch, migration, native=True)

    migration.upgrade()

    assert operations == []


def test_downgrade_casts_native_uuid_columns_back_to_strings(monkeypatch) -> None:
    migration = _load_module("002a_uuid_type_bridge.py", "uuid_bridge_downgrade")
    operations = _capture_operations(monkeypatch, migration, native=True)

    migration.downgrade()

    altered = [
        kwargs
        for operation, _args, kwargs in operations
        if operation == "alter"
    ]
    assert len(altered) == 4
    for kwargs in altered:
        assert isinstance(kwargs["type_"], String)
        assert kwargs["type_"].length == 36
        assert kwargs["postgresql_using"].endswith("::text")
