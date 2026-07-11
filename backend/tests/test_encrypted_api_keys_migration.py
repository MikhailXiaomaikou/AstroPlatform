"""Regression coverage for the PostgreSQL encrypted API-key migration."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "6a718293b4c5_normalize_encrypted_api_keys_jsonb.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("api_keys_jsonb_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Inspector:
    def __init__(self, column_type):
        self.column_type = column_type

    def get_table_names(self):
        return ["users"]

    def get_columns(self, table_name):
        assert table_name == "users"
        return [{"name": "api_keys", "type": self.column_type}]


def _capture(monkeypatch, migration, column_type):
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _Inspector(column_type))
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(str(statement)))
    return statements


def test_upgrade_converts_text_to_jsonb_without_parsing_or_losing_ciphertext(monkeypatch):
    migration = _load()
    statements = _capture(monkeypatch, migration, sa.Text())

    migration.upgrade()

    assert len(statements) == 1
    assert "ALTER COLUMN api_keys TYPE JSONB" in statements[0]
    assert "to_jsonb(api_keys)" in statements[0]


def test_upgrade_is_idempotent_for_existing_jsonb(monkeypatch):
    migration = _load()
    statements = _capture(monkeypatch, migration, postgresql.JSONB())

    migration.upgrade()

    assert statements == []


def test_downgrade_unwraps_jsonb_scalar_strings(monkeypatch):
    migration = _load()
    statements = _capture(monkeypatch, migration, postgresql.JSONB())

    migration.downgrade()

    assert len(statements) == 1
    assert "ALTER COLUMN api_keys TYPE TEXT" in statements[0]
    assert "api_keys #>> '{}'" in statements[0]


def test_unsupported_postgresql_type_fails_closed(monkeypatch):
    migration = _load()
    _capture(monkeypatch, migration, sa.Integer())

    with pytest.raises(RuntimeError, match="unsupported type"):
        migration.upgrade()
