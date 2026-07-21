"""Keep model and migration identifiers valid on production PostgreSQL."""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  # Load every durable model into Base.metadata.
from app.models.database import Base


BACKEND_ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IDENTIFIER_LIMIT = postgresql.dialect().max_identifier_length


def test_model_identifiers_fit_postgresql_limit() -> None:
    identifiers: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        identifiers.append((f"table {table.name}", table.name))
        identifiers.extend(
            (f"column {table.name}.{column.name}", column.name)
            for column in table.columns
        )
        identifiers.extend(
            (f"index on {table.name}", index.name)
            for index in table.indexes
            if index.name is not None
        )
        identifiers.extend(
            (f"constraint on {table.name}", constraint.name)
            for constraint in table.constraints
            if constraint.name is not None
        )

    too_long = [
        f"{context}: {name!r} ({len(name)} characters)"
        for context, name in identifiers
        if len(name) > POSTGRES_IDENTIFIER_LIMIT
    ]
    assert not too_long, "PostgreSQL identifiers exceed 63 characters:\n" + "\n".join(
        sorted(too_long)
    )


def test_literal_migration_identifiers_fit_postgresql_limit() -> None:
    """Audit names passed explicitly to Alembic and SQLAlchemy operations."""

    first_argument_calls = {
        "Column",
        "Index",
        "create_check_constraint",
        "create_foreign_key",
        "create_index",
        "create_table",
        "create_unique_constraint",
        "drop_constraint",
        "drop_index",
    }
    named_constraint_calls = {
        "CheckConstraint",
        "ForeignKeyConstraint",
        "PrimaryKeyConstraint",
        "UniqueConstraint",
    }
    too_long: list[str] = []

    for path in sorted((BACKEND_ROOT / "alembic" / "versions").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                function_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                function_name = node.func.id
            else:
                continue

            names: list[str] = []
            if (
                function_name in first_argument_calls
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names.append(node.args[0].value)
            if function_name in named_constraint_calls:
                names.extend(
                    keyword.value.value
                    for keyword in node.keywords
                    if keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                )

            too_long.extend(
                f"{path.name}:{node.lineno}: {name!r} ({len(name)} characters)"
                for name in names
                if len(name) > POSTGRES_IDENTIFIER_LIMIT
            )

    assert not too_long, "Migration identifiers exceed 63 characters:\n" + "\n".join(
        too_long
    )
