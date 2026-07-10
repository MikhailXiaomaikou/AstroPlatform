#!/usr/bin/env bash
# Verify and restore a Standard Astro backup bundle into an empty database.

set -Eeuo pipefail
umask 077

bundle="${1:-}"
if [[ -z "$bundle" || ! -f "$bundle" ]]; then
  echo "usage: DATABASE_URL=... RESTORE_CONFIRM=restore:<backup-id> $0 <bundle.tar.gz>" >&2
  exit 2
fi
: "${DATABASE_URL:?DATABASE_URL is required}"

for command_name in pg_restore psql python3 tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "missing required command: $command_name" >&2
    exit 2
  }
done

database_url="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
database_url="${database_url/postgres+asyncpg:/postgresql:}"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/standard-astro-restore.XXXXXX")"
cleanup() { rm -rf "$tmp"; }
trap cleanup EXIT

validate_tar_paths() {
  local archive="$1"
  python3 - "$archive" <<'PY'
import pathlib
import sys
import tarfile

with tarfile.open(sys.argv[1], "r:*") as archive:
    for member in archive.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe path in backup archive: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported archive entry type: {member.name}")
PY
}

validate_tar_paths "$bundle"
tar -xzf "$bundle" -C "$tmp"
manifest="$(find "$tmp" -mindepth 2 -maxdepth 2 -name manifest.json -print -quit)"
if [[ -z "$manifest" ]]; then
  echo "manifest.json not found in backup bundle" >&2
  exit 2
fi
work="$(dirname "$manifest")"

backup_id="$(python3 - "$manifest" "$work" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
work = pathlib.Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("format_version") != 1:
    raise SystemExit("unsupported backup format_version")

def verify(section_name: str) -> None:
    section = manifest.get(section_name)
    if not section:
        return
    path = work / section["file"]
    if not path.is_file() or path.parent.resolve() != work.resolve():
        raise SystemExit(f"invalid {section_name} file path")
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != section["sha256"]:
        raise SystemExit(f"{section_name} checksum mismatch")

verify("database")
verify("storage")
backup_id = str(manifest.get("backup_id") or "")
if not backup_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in backup_id):
    raise SystemExit("invalid backup_id")
print(backup_id)
PY
)"

expected_confirmation="restore:$backup_id"
if [[ "${RESTORE_CONFIRM:-}" != "$expected_confirmation" ]]; then
  echo "restore is destructive; set RESTORE_CONFIRM=$expected_confirmation" >&2
  exit 2
fi

storage_archive="$work/storage.tar.gz"
if [[ -f "$storage_archive" && -n "${RESTORE_STORAGE_DIR:-}" ]]; then
  validate_tar_paths "$storage_archive"
  mkdir -p "$RESTORE_STORAGE_DIR"
  if find "$RESTORE_STORAGE_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q . \
    && [[ "${RESTORE_ALLOW_STORAGE_OVERLAY:-0}" != "1" ]]; then
    echo "RESTORE_STORAGE_DIR is not empty; refusing to overlay" >&2
    exit 2
  fi
fi

table_count="$(psql "$database_url" -X -A -t -v ON_ERROR_STOP=1 \
  -c "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';")"
restore_flags=(--dbname "$database_url" --no-owner --no-acl --exit-on-error --single-transaction)
if [[ "$table_count" != "0" ]]; then
  if [[ "${RESTORE_ALLOW_NONEMPTY_DB:-0}" != "1" ]]; then
    echo "target database is not empty; restore into a new database or set RESTORE_ALLOW_NONEMPTY_DB=1" >&2
    exit 2
  fi
  restore_flags+=(--clean --if-exists)
fi

echo "restoring PostgreSQL backup" >&2
pg_restore "${restore_flags[@]}" "$work/database.dump"

if [[ -f "$storage_archive" ]]; then
  if [[ -z "${RESTORE_STORAGE_DIR:-}" ]]; then
    echo "storage archive verified but skipped; set RESTORE_STORAGE_DIR to restore it" >&2
  else
    tar -xzf "$storage_archive" -C "$RESTORE_STORAGE_DIR"
  fi
fi

restored_revision="$({
  psql "$database_url" -X -A -t -v ON_ERROR_STOP=1 \
    -c "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;"
} 2>/dev/null || true)"
echo "restore complete: $backup_id (schema ${restored_revision:-unversioned})" >&2
