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
        assert "workflow_registry_release_imports" in tables
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
        formal_build_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(foundry_formal_build_attestations)"
            )
        }
        materialization_receipt_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(foundry_materialization_receipts)"
            )
        }
        event_indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(foundry_candidate_events)"
            )
        }
        assert "candidate_key" in demo_columns
        assert "validation_runner_image_digest" in version_columns
        assert {
            "attestation_artifact_hash",
            "attestation_signing_key_id",
            "github_repository",
            "github_workflow_ref",
            "github_workflow_sha",
            "sigstore_verification_record_hash",
            "formal_release_audit_hash",
            "formal_release_audit_receipts",
        }.issubset(formal_build_columns)
        assert {
            "pull_request_base_ref",
            "pull_request_head_repository",
            "origin_main_commit",
            "merge_commit_is_ancestor_of_origin_main",
        }.issubset(materialization_receipt_columns)
        assert {
            "uq_foundry_event_candidate_genesis",
            "uq_foundry_event_candidate_parent",
        }.issubset(event_indexes)
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
    assert "workflow_registry_release_imports" not in tables


def test_formal_build_identity_upgrade_preserves_legacy_rows(tmp_path: Path) -> None:
    """A database that already applied the original Foundry migration upgrades."""

    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "foundry-legacy-attestation.sqlite"
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{database}",
            "ENV": "dev",
            "APP_ROLE": "migration",
        }
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "c0718293a4b5"],
        cwd=backend,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    receipt_hash = "a" * 64
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO foundry_formal_build_attestations (
                id, candidate_id, candidate_version_id,
                candidate_version_hash, source_tree_hash, git_commit,
                dependency_lock_hash, formal_sbom_hash, test_report_hash,
                formal_worker_image_digest, oidc_issuer, oidc_subject,
                sigstore_bundle_hash, provenance_hash, build_metadata,
                receipt_hash, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
                "1" * 64,
                "2" * 64,
                "3" * 40,
                "4" * 64,
                "5" * 64,
                "6" * 64,
                "sha256:" + "7" * 64,
                "https://token.actions.githubusercontent.com",
                "legacy-subject",
                "8" * 64,
                "9" * 64,
                "{}",
                receipt_hash,
                "2026-07-21T00:00:00+00:00",
            ),
        )
        connection.commit()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT github_repository, github_workflow_ref,
                   github_workflow_sha, attestation_signing_key_id,
                   sigstore_verification_record_hash,
                   attestation_artifact_hash, formal_release_audit_hash
            FROM foundry_formal_build_attestations
            """
        ).fetchone()

    assert row == (
        "legacy/unverified",
        "legacy-unverified",
        "0" * 40,
        "legacy-unverified",
        receipt_hash,
        receipt_hash,
        None,
    )
