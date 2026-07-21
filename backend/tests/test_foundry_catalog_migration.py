"""Real Alembic upgrade/downgrade drill for the Workflow Foundry schema."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_foundry_migration_up_and_down(tmp_path: Path) -> None:
    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "foundry-migration.sqlite"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
            "ENV": "dev",
            "APP_ROLE": "migration",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "foundry_formal_build_attestations" in tables
        demo_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(foundry_demo_runs)")
        }
        version_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(foundry_candidate_versions)"
            )
        }
        assert "candidate_key" in demo_columns
        assert "validation_runner_image_digest" in version_columns
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "downgrade",
            "8c93a4b5d6e7",
        ],
        cwd=backend,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "foundry_candidates" not in tables
    assert "foundry_formal_build_attestations" not in tables
