"""Protect historical migrations from future ORM metadata drift."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


def _load_sync_revision():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0c9293030537_sync_models_schema.py"
    )
    spec = importlib.util.spec_from_file_location("sync_models_revision_0c929", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chat_session_snapshot_does_not_reference_future_workspace(monkeypatch):
    revision = _load_sync_revision()
    captured: dict[str, object] = {}

    monkeypatch.setattr(revision, "_table_exists", lambda _name: False)
    monkeypatch.setattr(
        revision.op,
        "create_table",
        lambda name, *items, **_kwargs: captured.update(
            {"name": name, "items": items}
        ),
    )
    monkeypatch.setattr(revision.op, "create_index", lambda *_args, **_kwargs: None)

    revision._create_chat_sessions_at_revision()

    assert captured["name"] == "chat_sessions"
    items = captured["items"]
    columns = [item for item in items if isinstance(item, sa.Column)]
    assert "workspace_id" not in {column.name for column in columns}
    constraints = [
        item for item in items if isinstance(item, sa.ForeignKeyConstraint)
    ]
    targets = {
        element.target_fullname
        for constraint in constraints
        for element in constraint.elements
    }
    assert targets == {"users.id"}
