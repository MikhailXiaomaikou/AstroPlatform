#!/usr/bin/env bash
# Create a portable, checksummed Standard Astro backup bundle.

set -Eeuo pipefail
umask 077

: "${DATABASE_URL:?DATABASE_URL is required}"

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
STORAGE_DIR="${STORAGE_DIR:-}"
FERNET_KEY_ID="${FERNET_KEY_ID:-unrecorded}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-0}"

for command_name in pg_dump psql python3 tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "missing required command: $command_name" >&2
    exit 2
  }
done

case "$BACKUP_RETENTION_DAYS" in
  ''|*[!0-9]*)
    echo "BACKUP_RETENTION_DAYS must be a non-negative integer" >&2
    exit 2
    ;;
esac

# PostgreSQL CLI tools do not understand SQLAlchemy's async driver suffix.
database_url="${DATABASE_URL/postgresql+asyncpg:/postgresql:}"
database_url="${database_url/postgres+asyncpg:/postgresql:}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
commit="${RENDER_GIT_COMMIT:-${GIT_COMMIT:-unknown}}"
backup_id="standard-astro-${timestamp}-${commit:0:12}"

mkdir -p "$BACKUP_ROOT"
stage="$(mktemp -d "$BACKUP_ROOT/.backup.XXXXXX")"
work="$stage/$backup_id"
tmp_bundle="$BACKUP_ROOT/.${backup_id}.tar.gz.tmp"
bundle="$BACKUP_ROOT/${backup_id}.tar.gz"
cleanup() {
  rm -rf "$stage"
  rm -f "$tmp_bundle"
}
trap cleanup EXIT
mkdir -p "$work"

echo "creating PostgreSQL logical backup" >&2
pg_dump \
  --dbname "$database_url" \
  --format=custom \
  --no-owner \
  --no-acl \
  --file "$work/database.dump"

alembic_revision="$({
  psql "$database_url" -X -A -t \
    -c "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;"
} 2>/dev/null || true)"
alembic_revision="${alembic_revision:-unversioned}"

storage_file=""
if [[ -n "$STORAGE_DIR" ]]; then
  if [[ ! -d "$STORAGE_DIR" ]]; then
    echo "STORAGE_DIR does not exist: $STORAGE_DIR" >&2
    exit 2
  fi
  if python3 - "$BACKUP_ROOT" "$STORAGE_DIR" <<'PY'
import pathlib
import sys

backup_root = pathlib.Path(sys.argv[1]).resolve()
storage_root = pathlib.Path(sys.argv[2]).resolve()
try:
    backup_root.relative_to(storage_root)
except ValueError:
    raise SystemExit(1)
PY
  then
    echo "BACKUP_ROOT must not be inside STORAGE_DIR (recursive archive risk)" >&2
    exit 2
  fi
  unsafe_entry="$(find "$STORAGE_DIR" -mindepth 1 ! -type f ! -type d -print -quit)"
  if [[ -n "$unsafe_entry" ]]; then
    echo "STORAGE_DIR contains a symlink or special file; refusing unsafe archive: $unsafe_entry" >&2
    exit 2
  fi
  echo "creating storage archive" >&2
  storage_file="storage.tar.gz"
  tar \
    --exclude='.health_probe_*' \
    -C "$STORAGE_DIR" \
    -czf "$work/$storage_file" \
    .
fi

python3 - "$work" "$backup_id" "$commit" "$alembic_revision" "$storage_file" "$FERNET_KEY_ID" <<'PY'
import datetime
import hashlib
import json
import pathlib
import sys

work = pathlib.Path(sys.argv[1])
backup_id, commit, revision, storage_file, key_id = sys.argv[2:]

def digest(name: str) -> str:
    hasher = hashlib.sha256()
    with (work / name).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

manifest = {
    "format_version": 1,
    "backup_id": backup_id,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "git_commit": commit,
    "alembic_revision": revision,
    "fernet_key_id": key_id,
    "database": {
        "file": "database.dump",
        "sha256": digest("database.dump"),
        "format": "pg_dump_custom",
    },
    "storage": None,
}
if storage_file:
    manifest["storage"] = {
        "file": storage_file,
        "sha256": digest(storage_file),
        "format": "tar_gzip",
    }
(work / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

tar -C "$stage" -czf "$tmp_bundle" "$backup_id"
mv "$tmp_bundle" "$bundle"

if (( BACKUP_RETENTION_DAYS > 0 )); then
  find "$BACKUP_ROOT" -maxdepth 1 -type f \
    -name 'standard-astro-*.tar.gz' \
    -mtime "+$BACKUP_RETENTION_DAYS" -delete
fi

echo "backup complete: $bundle" >&2
# The final stdout line is machine-readable for CI and automation.
printf '%s\n' "$bundle"
