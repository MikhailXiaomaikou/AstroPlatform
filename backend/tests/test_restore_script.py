"""Fail-closed tests for the portable backup restore command."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

import scripts.ops.atomic_publish_dir as atomic_publish


BACKEND = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = BACKEND / "scripts" / "ops" / "restore.sh"
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
FERNET_KEY_ID = "fernet-test-v1"
EVIDENCE_KEY_ID = "evidence-test-v1"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _storage_payload() -> bytes:
    payload = b"recovered storage\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        member = tarfile.TarInfo("sentinel.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def _bundle(
    tmp_path: Path,
    *,
    database_file: str = "database.dump",
    database_payload: bytes = b"verified database artifact",
    extra_database_dump: bytes | None = None,
    revision: str = "head123",
    commit: str = COMMIT,
    fernet_key_id: str = FERNET_KEY_ID,
    evidence_key_id: str = EVIDENCE_KEY_ID,
    with_storage: bool = False,
) -> tuple[Path, str]:
    backup_id = "standard-astro-test-head123"
    root = tmp_path / "bundle-root" / backup_id
    root.mkdir(parents=True)
    (root / database_file).write_bytes(database_payload)
    if extra_database_dump is not None:
        (root / "database.dump").write_bytes(extra_database_dump)
    storage_payload = _storage_payload() if with_storage else None
    if storage_payload is not None:
        (root / "storage.tar.gz").write_bytes(storage_payload)
    manifest = {
        "format_version": 1,
        "backup_id": backup_id,
        "git_commit": commit,
        "alembic_revision": revision,
        "fernet_key_id": fernet_key_id,
        "evidence_signing_key_id": evidence_key_id,
        "database": {
            "file": database_file,
            "sha256": _sha256(database_payload),
            "format": "pg_dump_custom",
        },
        "storage": (
            {
                "file": "storage.tar.gz",
                "sha256": _sha256(storage_payload),
                "format": "tar_gzip",
            }
            if storage_payload is not None
            else None
        ),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    bundle = tmp_path / "backup.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(root, arcname=backup_id)
    return bundle, backup_id


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_commands(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    restore_log = tmp_path / "pg_restore.log"
    alembic_log = tmp_path / "alembic.log"
    psql_log = tmp_path / "psql.log"

    _write_executable(
        fake_bin / "pg_restore",
        """#!/bin/sh
printf '%s\n' "$*" >"$PG_RESTORE_LOG"
exit "${FAKE_PG_RESTORE_EXIT:-0}"
""",
    )
    _write_executable(
        fake_bin / "psql",
        """#!/bin/sh
printf '%s\n' "$*" >>"$PSQL_LOG"
case "$*" in
  *"WITH user_namespaces AS"*)
    if [ "${FAKE_FRESHNESS_EXIT:-0}" != "0" ]; then
      printf 'permission denied for freshness catalogs\n' >&2
      exit "$FAKE_FRESHNESS_EXIT"
    fi
    printf '%s\n' "$FAKE_OBJECT_COUNT"
    ;;
  *"to_regclass"*) printf '%s\n' "$FAKE_VERSION_TABLE_PRESENT" ;;
  *"string_agg(version_num"*) printf '%s\n' "$FAKE_RESTORED_REVISION" ;;
  *) printf 'unexpected psql command: %s\n' "$*" >&2; exit 3 ;;
esac
""",
    )
    _write_executable(
        fake_bin / "alembic",
        """#!/bin/sh
case "$*" in
  *"heads"*) printf '%s (head)\n' "$FAKE_ALEMBIC_HEAD" ;;
  *"check"*)
    if [ -n "${RESTORE_STORAGE_DIR:-}" ] && [ -e "$RESTORE_STORAGE_DIR" ]; then
      printf 'storage target existed before alembic validation\n' >&2
      exit 88
    fi
    printf 'check\n' >>"$ALEMBIC_LOG"
    exit "${FAKE_ALEMBIC_CHECK_EXIT:-0}"
    ;;
  *) printf 'unexpected alembic command: %s\n' "$*" >&2; exit 3 ;;
esac
""",
    )
    return fake_bin, restore_log, alembic_log, psql_log


def _run_restore(
    tmp_path: Path,
    bundle: Path,
    backup_id: str,
    *,
    restored_revision: str = "head123",
    shipped_head: str = "head123",
    alembic_check_exit: int = 0,
    pg_restore_exit: int = 0,
    object_count: int | str = 0,
    freshness_exit: int = 0,
    version_table_present: str = "t",
    runtime_commit: str | None = COMMIT,
    storage_dir: Path | None = None,
    race_storage_target: Path | None = None,
    env_overrides: dict[str, str | None] | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    fake_bin, restore_log, alembic_log, psql_log = _fake_commands(tmp_path)
    if race_storage_target is not None:
        real_tar = shutil.which("tar")
        assert real_tar is not None
        _write_executable(
            fake_bin / "tar",
            """#!/bin/sh
"$REAL_TAR" "$@"
status="$?"
case "$*" in
  *"storage.tar.gz"*) mkdir -p "$RACE_STORAGE_TARGET" ;;
esac
exit "$status"
""",
        )
    env = os.environ.copy()
    for name in (
        "RENDER_GIT_COMMIT",
        "GIT_COMMIT",
        "TOOL_VERSION",
        "RESTORE_ALEMBIC_CHECK",
        "RESTORE_STORAGE_DIR",
        "RESTORE_ALLOW_STORAGE_OVERLAY",
        "RESTORE_ALLOW_NONEMPTY_DB",
        "RESTORE_EXPECTED_COMMIT",
        "RESTORE_FERNET_KEY_ID",
        "RESTORE_EVIDENCE_SIGNING_KEY_ID",
    ):
        env.pop(name, None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "DATABASE_URL": "postgresql://astro:test@localhost/restore_test",
            "RESTORE_CONFIRM": f"restore:{backup_id}",
            "RESTORE_EXPECTED_COMMIT": COMMIT,
            "RESTORE_FERNET_KEY_ID": FERNET_KEY_ID,
            "RESTORE_EVIDENCE_SIGNING_KEY_ID": EVIDENCE_KEY_ID,
            "FAKE_RESTORED_REVISION": restored_revision,
            "FAKE_ALEMBIC_HEAD": shipped_head,
            "FAKE_ALEMBIC_CHECK_EXIT": str(alembic_check_exit),
            "FAKE_PG_RESTORE_EXIT": str(pg_restore_exit),
            "FAKE_OBJECT_COUNT": str(object_count),
            "FAKE_FRESHNESS_EXIT": str(freshness_exit),
            "FAKE_VERSION_TABLE_PRESENT": version_table_present,
            "PG_RESTORE_LOG": str(restore_log),
            "ALEMBIC_LOG": str(alembic_log),
            "PSQL_LOG": str(psql_log),
            "ALEMBIC_BIN": str(fake_bin / "alembic"),
        }
    )
    if runtime_commit is not None:
        env["GIT_COMMIT"] = runtime_commit
    if storage_dir is not None:
        env["RESTORE_STORAGE_DIR"] = str(storage_dir)
    if race_storage_target is not None:
        env["REAL_TAR"] = real_tar
        env["RACE_STORAGE_TARGET"] = str(race_storage_target)
    for name, value in (env_overrides or {}).items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    result = subprocess.run(
        ["bash", str(RESTORE_SCRIPT), str(bundle)],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, restore_log, alembic_log, psql_log


def test_restore_rejects_manifest_database_artifact_mismatch(tmp_path):
    bundle, backup_id = _bundle(
        tmp_path,
        database_file="verified.dump",
        extra_database_dump=b"unverified artifact",
    )

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path, bundle, backup_id
    )

    assert result.returncode != 0
    assert "database.dump" in result.stderr
    assert not restore_log.exists()


def test_restore_binds_verified_database_and_runs_all_validation(tmp_path):
    bundle, backup_id = _bundle(tmp_path)

    result, restore_log, alembic_log, psql_log = _run_restore(
        tmp_path, bundle, backup_id
    )

    assert result.returncode == 0, result.stderr
    restore_args = restore_log.read_text(encoding="utf-8")
    assert restore_args.rstrip().endswith(f"/{backup_id}/database.dump")
    assert "--single-transaction" in restore_args
    assert "--clean" not in restore_args
    assert alembic_log.read_text(encoding="utf-8") == "check\n"
    freshness_query = psql_log.read_text(encoding="utf-8")
    assert "pg_namespace" in freshness_query
    assert "pg_class" in freshness_query
    assert "pg_proc" in freshness_query
    assert "pg_type" in freshness_query
    assert "schema head123, commit" in result.stderr


@pytest.mark.parametrize(
    ("bundle_overrides", "error"),
    [
        ({"commit": "abc123"}, "git_commit must be a full 40-hex commit"),
        ({"fernet_key_id": "unrecorded"}, "invalid fernet_key_id"),
        ({"evidence_key_id": "UNRECORDED"}, "invalid evidence_signing_key_id"),
    ],
)
def test_restore_rejects_unverifiable_manifest_material(
    tmp_path, bundle_overrides, error
):
    bundle, backup_id = _bundle(tmp_path, **bundle_overrides)

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path, bundle, backup_id
    )

    assert result.returncode != 0
    assert error in result.stderr
    assert not restore_log.exists()


@pytest.mark.parametrize(
    ("env_overrides", "runtime_commit", "error"),
    [
        (
            {"RESTORE_EXPECTED_COMMIT": None},
            COMMIT,
            "RESTORE_EXPECTED_COMMIT must be a full 40-hex git commit",
        ),
        (
            {"RESTORE_EXPECTED_COMMIT": OTHER_COMMIT},
            COMMIT,
            "RESTORE_EXPECTED_COMMIT does not match backup manifest",
        ),
        (
            {"RESTORE_FERNET_KEY_ID": "fernet-other-v1"},
            COMMIT,
            "RESTORE_FERNET_KEY_ID does not match backup manifest",
        ),
        (
            {"RESTORE_EVIDENCE_SIGNING_KEY_ID": "evidence-other-v1"},
            COMMIT,
            "RESTORE_EVIDENCE_SIGNING_KEY_ID does not match backup manifest",
        ),
        ({}, OTHER_COMMIT, "runtime git commit does not match backup manifest"),
    ],
)
def test_restore_requires_exact_operator_and_runtime_material(
    tmp_path, env_overrides, runtime_commit, error
):
    bundle, backup_id = _bundle(tmp_path)

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        runtime_commit=runtime_commit,
        env_overrides=env_overrides,
    )

    assert result.returncode != 0
    assert error in result.stderr
    assert not restore_log.exists()


def test_restore_can_bind_to_the_current_checkout_commit(tmp_path):
    checkout_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    bundle, backup_id = _bundle(tmp_path, commit=checkout_commit)

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        runtime_commit=None,
        env_overrides={"RESTORE_EXPECTED_COMMIT": checkout_commit},
    )

    assert result.returncode == 0, result.stderr
    assert restore_log.exists()


def test_restore_rejects_code_schema_revision_mismatch_before_database_change(
    tmp_path,
):
    bundle, backup_id = _bundle(tmp_path, revision="old123")

    result, restore_log, alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        restored_revision="old123",
        shipped_head="head123",
    )

    assert result.returncode != 0
    assert "does not match this code revision" in result.stderr
    assert not restore_log.exists()
    assert not alembic_log.exists()


def test_restore_rejects_any_non_system_database_object(tmp_path):
    bundle, backup_id = _bundle(tmp_path)

    result, restore_log, _alembic_log, psql_log = _run_restore(
        tmp_path, bundle, backup_id, object_count=1
    )

    assert result.returncode != 0
    assert "newly created database" in result.stderr
    freshness_query = psql_log.read_text(encoding="utf-8")
    for catalog in (
        "pg_namespace",
        "pg_class",
        "pg_proc",
        "pg_type",
        "pg_publication",
        "pg_subscription",
        "pg_event_trigger",
        "pg_extension",
        "pg_foreign_data_wrapper",
        "pg_foreign_server",
        "pg_user_mapping",
        "pg_largeobject_metadata",
        "pg_default_acl",
        "pg_language",
        "pg_db_role_setting",
        "pg_cast",
        "pg_transform",
        "pg_am",
        "pg_operator",
        "pg_opclass",
        "pg_opfamily",
        "pg_collation",
        "pg_conversion",
        "pg_ts_config",
        "pg_ts_dict",
        "pg_ts_parser",
        "pg_ts_template",
    ):
        assert catalog in freshness_query
    assert not restore_log.exists()


@pytest.mark.parametrize(
    ("object_count", "freshness_exit", "error"),
    [
        ("not-a-number", 0, "could not verify that the target database is fresh"),
        (0, 13, "permission denied for freshness catalogs"),
    ],
)
def test_restore_fails_closed_when_catalog_freshness_cannot_be_proven(
    tmp_path, object_count, freshness_exit, error
):
    bundle, backup_id = _bundle(tmp_path)

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        object_count=object_count,
        freshness_exit=freshness_exit,
    )

    assert result.returncode != 0
    assert error in result.stderr
    assert not restore_log.exists()


@pytest.mark.parametrize(
    "override_name",
    [
        "RESTORE_ALLOW_NONEMPTY_DB",
        "RESTORE_ALLOW_STORAGE_OVERLAY",
        "RESTORE_ALEMBIC_CHECK",
    ],
)
def test_restore_rejects_legacy_bypass_controls(tmp_path, override_name):
    bundle, backup_id = _bundle(tmp_path)

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        env_overrides={override_name: "1"},
    )

    assert result.returncode != 0
    assert f"{override_name} is no longer supported" in result.stderr
    assert not restore_log.exists()


def test_restore_rejects_revision_mismatch_before_alembic_check(tmp_path):
    bundle, backup_id = _bundle(tmp_path, revision="head123")

    result, _restore_log, alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        restored_revision="different",
    )

    assert result.returncode != 0
    assert "revision mismatch" in result.stderr
    assert not alembic_log.exists()


def test_restore_propagates_alembic_check_failure(tmp_path):
    bundle, backup_id = _bundle(tmp_path)

    result, _restore_log, alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        alembic_check_exit=7,
    )

    assert result.returncode == 7
    assert alembic_log.read_text(encoding="utf-8") == "check\n"


def test_restore_requires_a_new_storage_target_and_atomically_publishes_it(
    tmp_path,
):
    bundle, backup_id = _bundle(tmp_path, with_storage=True)
    storage_target = tmp_path / "recovered" / "storage"

    result, _restore_log, alembic_log, _psql_log = _run_restore(
        tmp_path, bundle, backup_id, storage_dir=storage_target
    )

    assert result.returncode == 0, result.stderr
    assert alembic_log.read_text(encoding="utf-8") == "check\n"
    assert (storage_target / "sentinel.txt").read_text(encoding="utf-8") == (
        "recovered storage\n"
    )
    assert not list(storage_target.parent.glob(".storage.restore.*"))


def test_restore_rejects_even_an_empty_preexisting_storage_target(tmp_path):
    bundle, backup_id = _bundle(tmp_path, with_storage=True)
    storage_target = tmp_path / "storage"
    storage_target.mkdir()

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path, bundle, backup_id, storage_dir=storage_target
    )

    assert result.returncode != 0
    assert "must not exist" in result.stderr
    assert not restore_log.exists()


def test_restore_refuses_partial_storage_recovery_without_a_target(tmp_path):
    bundle, backup_id = _bundle(tmp_path, with_storage=True)

    result, restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path, bundle, backup_id
    )

    assert result.returncode != 0
    assert "RESTORE_STORAGE_DIR is required" in result.stderr
    assert not restore_log.exists()


def test_restore_does_not_extract_storage_when_database_validation_fails(tmp_path):
    bundle, backup_id = _bundle(tmp_path, with_storage=True)
    storage_target = tmp_path / "recovered" / "storage"

    result, _restore_log, alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        storage_dir=storage_target,
        alembic_check_exit=7,
    )

    assert result.returncode == 7
    assert alembic_log.read_text(encoding="utf-8") == "check\n"
    assert not storage_target.exists()
    assert not list(storage_target.parent.glob(".storage.restore.*"))


def test_restore_never_moves_staged_storage_under_a_racing_target(tmp_path):
    bundle, backup_id = _bundle(tmp_path, with_storage=True)
    storage_target = tmp_path / "recovered" / "storage"

    result, _restore_log, _alembic_log, _psql_log = _run_restore(
        tmp_path,
        bundle,
        backup_id,
        storage_dir=storage_target,
        race_storage_target=storage_target,
    )

    assert result.returncode != 0
    assert "atomic storage publication failed" in result.stderr
    assert storage_target.is_dir()
    assert list(storage_target.iterdir()) == []
    assert not list(storage_target.parent.glob(".storage.restore.*"))


@pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"},
    reason="no supported no-replace rename primitive on this host",
)
def test_atomic_publish_syscall_refuses_target_created_at_final_boundary(
    tmp_path, monkeypatch
):
    source = tmp_path / "stage"
    target = tmp_path / "storage"
    source.mkdir()
    (source / "sentinel.txt").write_text("staged\n", encoding="utf-8")
    primitive_name = (
        "_linux_rename_noreplace"
        if sys.platform == "linux"
        else "_macos_rename_noreplace"
    )
    real_primitive = getattr(atomic_publish, primitive_name)

    def race_at_syscall(source_path, target_path):
        target_path.mkdir()
        real_primitive(source_path, target_path)

    monkeypatch.setattr(atomic_publish, primitive_name, race_at_syscall)

    with pytest.raises(FileExistsError):
        atomic_publish.publish_directory_no_replace(source, target)

    assert (source / "sentinel.txt").read_text(encoding="utf-8") == "staged\n"
    assert target.is_dir()
    assert list(target.iterdir()) == []
