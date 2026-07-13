#!/usr/bin/env bash
# Create a portable, checksummed Standard Astro backup bundle.

set -Eeuo pipefail
umask 077

: "${DATABASE_URL:?DATABASE_URL is required}"

BACKUP_ROOT="${BACKUP_ROOT:-./backups}"
STORAGE_DIR="${STORAGE_DIR:-}"
FERNET_KEY_ID="${FERNET_KEY_ID:-}"
EVIDENCE_SIGNING_KEY_ID="${EVIDENCE_SIGNING_KEY_ID:-}"
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

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backend_root="$(cd -- "$script_dir/../.." && pwd)"
commit="${RENDER_GIT_COMMIT:-${GIT_COMMIT:-${TOOL_VERSION:-}}}"
if [[ -z "$commit" ]] && command -v git >/dev/null 2>&1; then
  commit="$(git -C "$backend_root" rev-parse --verify HEAD 2>/dev/null || true)"
fi
commit="$(printf '%s' "$commit" | tr '[:upper:]' '[:lower:]')"
if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "backup requires a full 40-hex git commit from RENDER_GIT_COMMIT, GIT_COMMIT, TOOL_VERSION, or git rev-parse" >&2
  exit 2
fi

validate_key_id() {
  local name="$1"
  local value="$2"
  local normalized
  normalized="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  if [[ ! "$value" =~ ^[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,127}$ || "$normalized" == "unrecorded" ]]; then
    echo "$name must be a recorded key identifier using safe identifier characters" >&2
    exit 2
  fi
}
validate_key_id "FERNET_KEY_ID" "$FERNET_KEY_ID"
validate_key_id "EVIDENCE_SIGNING_KEY_ID" "$EVIDENCE_SIGNING_KEY_ID"

alembic_bin="${ALEMBIC_BIN:-}"
if [[ -z "$alembic_bin" && -x "$backend_root/venv/bin/alembic" ]]; then
  alembic_bin="$backend_root/venv/bin/alembic"
elif [[ -z "$alembic_bin" ]]; then
  alembic_bin="$(command -v alembic || true)"
fi
if [[ -z "$alembic_bin" || ! -x "$alembic_bin" ]]; then
  echo "alembic command unavailable; set ALEMBIC_BIN" >&2
  exit 2
fi

shipped_heads_output="$(cd "$backend_root" && "$alembic_bin" heads)"
shipped_revision="$(printf '%s\n' "$shipped_heads_output" | python3 -c '
import sys
heads = sorted(line.split()[0] for line in sys.stdin if line.strip())
if not heads:
    raise SystemExit("no shipped Alembic heads")
print(",".join(heads))
')"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
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

read_database_revision() {
  psql "$database_url" -X -A -t -v ON_ERROR_STOP=1 \
    -c "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;"
}

alembic_revision="$(read_database_revision)"
if [[ -z "$alembic_revision" ]]; then
  echo "could not record an Alembic revision before pg_dump; refusing an unverifiable backup" >&2
  exit 2
fi
if [[ "$alembic_revision" != "$shipped_revision" ]]; then
  echo "database Alembic revision does not match this code revision: database=$alembic_revision shipped=$shipped_revision" >&2
  exit 2
fi

echo "creating PostgreSQL logical backup" >&2
pg_dump \
  --dbname "$database_url" \
  --format=custom \
  --no-owner \
  --no-acl \
  --file "$work/database.dump"

alembic_revision_after_dump="$(read_database_revision)"
if [[ -z "$alembic_revision_after_dump" ]]; then
  echo "could not record an Alembic revision after pg_dump; refusing an unverifiable backup" >&2
  exit 2
fi
if [[ "$alembic_revision_after_dump" != "$alembic_revision" ]]; then
  echo "database Alembic revision changed during pg_dump: before=$alembic_revision after=$alembic_revision_after_dump" >&2
  exit 2
fi

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

python3 - "$work" "$backup_id" "$commit" "$alembic_revision" "$storage_file" "$FERNET_KEY_ID" "$EVIDENCE_SIGNING_KEY_ID" <<'PY'
import datetime
import hashlib
import json
import pathlib
import re
import sys

work = pathlib.Path(sys.argv[1])
backup_id, commit, revision, storage_file, fernet_key_id, evidence_key_id = sys.argv[2:]

if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("git_commit must be a full 40-hex commit")
if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*", revision):
    raise SystemExit("invalid alembic_revision")
for name, value in (
    ("fernet_key_id", fernet_key_id),
    ("evidence_signing_key_id", evidence_key_id),
):
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,127}", value)
        or value.lower() == "unrecorded"
    ):
        raise SystemExit(f"invalid {name}")

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
    "fernet_key_id": fernet_key_id,
    "evidence_signing_key_id": evidence_key_id,
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
