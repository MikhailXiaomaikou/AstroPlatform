"""Release-material regression tests for the portable backup command."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest


BACKEND = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = BACKEND / "scripts" / "ops" / "backup.sh"
COMMIT = "a" * 40
FERNET_KEY_ID = "fernet-test-v1"
EVIDENCE_KEY_ID = "evidence-test-v1"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_backup_commands(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "pg_dump",
        """#!/bin/sh
output=''
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--file" ]; then
    shift
    output="$1"
  fi
  shift
done
if [ -z "$output" ]; then
  printf 'missing --file\n' >&2
  exit 3
fi
printf 'portable database dump\n' >"$output"
if [ -n "${FAKE_PG_DUMP_MARKER:-}" ]; then
  : >"$FAKE_PG_DUMP_MARKER"
fi
""",
    )
    _write_executable(
        fake_bin / "psql",
        """#!/bin/sh
index=0
if [ -f "$FAKE_PSQL_STATE" ]; then
  index="$(cat "$FAKE_PSQL_STATE")"
fi
index=$((index + 1))
printf '%s\n' "$index" >"$FAKE_PSQL_STATE"
printf '%s' "$FAKE_ALEMBIC_REVISIONS" | cut -d '|' -f "$index"
printf '\n'
""",
    )
    _write_executable(
        fake_bin / "alembic",
        """#!/bin/sh
if [ "${1:-}" != "heads" ]; then
  printf 'unsupported alembic command\n' >&2
  exit 3
fi
printf '%s\n' "$FAKE_SHIPPED_HEADS"
""",
    )
    return fake_bin


def _run_backup(
    tmp_path: Path,
    *,
    commit: str | None = COMMIT,
    fernet_key_id: str | None = FERNET_KEY_ID,
    evidence_key_id: str | None = EVIDENCE_KEY_ID,
    revision: str = "head123",
    post_revision: str | None = None,
    shipped_heads: tuple[str, ...] = ("head123",),
) -> subprocess.CompletedProcess[str]:
    fake_bin = _fake_backup_commands(tmp_path)
    if post_revision is None:
        post_revision = revision
    env = os.environ.copy()
    for name in (
        "RENDER_GIT_COMMIT",
        "GIT_COMMIT",
        "TOOL_VERSION",
        "FERNET_KEY_ID",
        "EVIDENCE_SIGNING_KEY_ID",
        "STORAGE_DIR",
        "ALEMBIC_BIN",
    ):
        env.pop(name, None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "DATABASE_URL": "postgresql://astro:test@localhost/backup_test",
            "BACKUP_ROOT": str(tmp_path / "backups"),
            "ALEMBIC_BIN": str(fake_bin / "alembic"),
            "FAKE_ALEMBIC_REVISIONS": f"{revision}|{post_revision}",
            "FAKE_PSQL_STATE": str(tmp_path / "psql-state"),
            "FAKE_PG_DUMP_MARKER": str(tmp_path / "pg-dump-ran"),
            "FAKE_SHIPPED_HEADS": "\n".join(
                f"{head} (head)" for head in shipped_heads
            ),
        }
    )
    if commit is not None:
        env["GIT_COMMIT"] = commit
    if fernet_key_id is not None:
        env["FERNET_KEY_ID"] = fernet_key_id
    if evidence_key_id is not None:
        env["EVIDENCE_SIGNING_KEY_ID"] = evidence_key_id
    return subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        cwd=BACKEND,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _manifest(bundle: Path) -> dict[str, object]:
    with tarfile.open(bundle, "r:gz") as archive:
        member = next(
            item for item in archive.getmembers() if item.name.endswith("/manifest.json")
        )
        extracted = archive.extractfile(member)
        assert extracted is not None
        return json.loads(extracted.read().decode("utf-8"))


def test_backup_records_full_commit_and_required_key_identifiers(tmp_path):
    result = _run_backup(tmp_path, commit=COMMIT.upper())

    assert result.returncode == 0, result.stderr
    bundle = Path(result.stdout.strip())
    manifest = _manifest(bundle)
    assert manifest["git_commit"] == COMMIT
    assert manifest["fernet_key_id"] == FERNET_KEY_ID
    assert manifest["evidence_signing_key_id"] == EVIDENCE_KEY_ID
    assert manifest["alembic_revision"] == "head123"
    assert COMMIT[:12] in bundle.name


def test_backup_uses_checkout_commit_when_release_env_is_absent(tmp_path):
    result = _run_backup(tmp_path, commit=None)

    assert result.returncode == 0, result.stderr
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert _manifest(Path(result.stdout.strip()))["git_commit"] == expected


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"commit": "abc123"}, "full 40-hex git commit"),
        ({"fernet_key_id": None}, "FERNET_KEY_ID"),
        ({"fernet_key_id": "unrecorded"}, "FERNET_KEY_ID"),
        ({"evidence_key_id": None}, "EVIDENCE_SIGNING_KEY_ID"),
        ({"evidence_key_id": "UNRECORDED"}, "EVIDENCE_SIGNING_KEY_ID"),
    ],
)
def test_backup_rejects_unverifiable_release_material(tmp_path, overrides, error):
    result = _run_backup(tmp_path, **overrides)

    assert result.returncode != 0
    assert error in result.stderr
    assert not list((tmp_path / "backups").glob("standard-astro-*.tar.gz"))


def test_backup_rejects_missing_alembic_revision(tmp_path):
    result = _run_backup(tmp_path, revision="")

    assert result.returncode != 0
    assert "could not record an Alembic revision before pg_dump" in result.stderr
    assert not (tmp_path / "pg-dump-ran").exists()
    assert not list((tmp_path / "backups").glob("standard-astro-*.tar.gz"))


def test_backup_rejects_database_revision_that_is_not_a_shipped_head(tmp_path):
    result = _run_backup(
        tmp_path,
        revision="old123",
        shipped_heads=("head123",),
    )

    assert result.returncode != 0
    assert "database=old123 shipped=head123" in result.stderr
    assert not (tmp_path / "pg-dump-ran").exists()
    assert not list((tmp_path / "backups").glob("standard-astro-*.tar.gz"))


def test_backup_rejects_missing_shipped_alembic_heads(tmp_path):
    result = _run_backup(
        tmp_path,
        shipped_heads=(),
    )

    assert result.returncode != 0
    assert "no shipped Alembic heads" in result.stderr
    assert not (tmp_path / "pg-dump-ran").exists()
    assert not list((tmp_path / "backups").glob("standard-astro-*.tar.gz"))


def test_backup_records_sorted_multiple_shipped_heads(tmp_path):
    result = _run_backup(
        tmp_path,
        revision="alpha123,zeta456",
        shipped_heads=("zeta456", "alpha123"),
    )

    assert result.returncode == 0, result.stderr
    manifest = _manifest(Path(result.stdout.strip()))
    assert manifest["alembic_revision"] == "alpha123,zeta456"


def test_backup_rejects_revision_change_during_pg_dump(tmp_path):
    result = _run_backup(
        tmp_path,
        revision="head123",
        post_revision="next456",
    )

    assert result.returncode != 0
    assert "changed during pg_dump: before=head123 after=next456" in result.stderr
    assert (tmp_path / "pg-dump-ran").exists()
    assert not list((tmp_path / "backups").glob("standard-astro-*.tar.gz"))


def test_backup_rejects_missing_revision_after_pg_dump(tmp_path):
    result = _run_backup(
        tmp_path,
        revision="head123",
        post_revision="",
    )

    assert result.returncode != 0
    assert "could not record an Alembic revision after pg_dump" in result.stderr
    assert (tmp_path / "pg-dump-ran").exists()
    assert not list((tmp_path / "backups").glob("standard-astro-*.tar.gz"))
