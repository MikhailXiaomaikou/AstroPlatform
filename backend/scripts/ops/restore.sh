#!/usr/bin/env bash
# Verify and restore a Standard Astro backup bundle into a fresh database.

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
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backend_root="$(cd -- "$script_dir/../.." && pwd)"
tmp="$(mktemp -d "${TMPDIR:-/tmp}/standard-astro-restore.XXXXXX")"
storage_stage=""
cleanup() {
  rm -rf "$tmp"
  if [[ -n "$storage_stage" ]]; then
    rm -rf "$storage_stage"
  fi
}
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

manifests=()
while IFS= read -r candidate; do
  manifests+=("$candidate")
done < <(find "$tmp" -mindepth 2 -maxdepth 2 -name manifest.json -print)
if (( ${#manifests[@]} != 1 )); then
  echo "backup bundle must contain exactly one top-level manifest.json" >&2
  exit 2
fi
manifest="${manifests[0]}"
work="$(dirname "$manifest")"

manifest_info="$(python3 - "$manifest" "$work" "$tmp" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

manifest_path = pathlib.Path(sys.argv[1])
work = pathlib.Path(sys.argv[2])
extract_root = pathlib.Path(sys.argv[3])
top_entries = list(extract_root.iterdir())
if len(top_entries) != 1 or top_entries[0].resolve() != work.resolve():
    raise SystemExit("backup bundle must contain exactly one top-level directory")

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("format_version") != 1:
    raise SystemExit("unsupported backup format_version")


def verify(section_name: str, expected_file: str, *, required: bool) -> bool:
    section = manifest.get(section_name)
    if section is None:
        if required:
            raise SystemExit(f"missing {section_name} manifest section")
        if (work / expected_file).exists():
            raise SystemExit(f"unmanifested {section_name} artifact")
        return False
    if not isinstance(section, dict):
        raise SystemExit(f"invalid {section_name} manifest section")
    declared_file = str(section.get("file") or "")
    if declared_file != expected_file:
        raise SystemExit(
            f"{section_name} file must be {expected_file}, got {declared_file or 'missing'}"
        )
    expected_format = "pg_dump_custom" if section_name == "database" else "tar_gzip"
    if section.get("format") != expected_format:
        raise SystemExit(f"invalid {section_name} format")
    path = work / declared_file
    if not path.is_file() or path.parent.resolve() != work.resolve():
        raise SystemExit(f"invalid {section_name} file path")
    expected_sha256 = str(section.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise SystemExit(f"invalid {section_name} sha256")
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    if hasher.hexdigest() != expected_sha256:
        raise SystemExit(f"{section_name} checksum mismatch")
    return True


verify("database", "database.dump", required=True)
has_storage = verify("storage", "storage.tar.gz", required=False)
expected_entries = {"manifest.json", "database.dump"}
if has_storage:
    expected_entries.add("storage.tar.gz")
actual_entries = {path.name for path in work.iterdir()}
if actual_entries != expected_entries or any(not path.is_file() for path in work.iterdir()):
    raise SystemExit("backup directory contains an unexpected artifact")

backup_id = str(manifest.get("backup_id") or "")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}", backup_id):
    raise SystemExit("invalid backup_id")
revision = str(manifest.get("alembic_revision") or "").strip()
if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*", revision):
    raise SystemExit("invalid alembic_revision")
commit = str(manifest.get("git_commit") or "").strip().lower()
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("git_commit must be a full 40-hex commit")


def key_id(name: str) -> str:
    value = str(manifest.get(name) or "")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,127}", value)
        or value.lower() == "unrecorded"
    ):
        raise SystemExit(f"invalid {name}")
    return value


fernet_key_id = key_id("fernet_key_id")
evidence_key_id = key_id("evidence_signing_key_id")
print(
    "\t".join(
        (
            backup_id,
            revision,
            str(int(has_storage)),
            commit,
            fernet_key_id,
            evidence_key_id,
        )
    )
)
PY
)"
IFS=$'\t' read -r backup_id manifest_revision manifest_has_storage manifest_commit manifest_fernet_key_id manifest_evidence_key_id <<<"$manifest_info"

runtime_commit="${RENDER_GIT_COMMIT:-${GIT_COMMIT:-${TOOL_VERSION:-}}}"
if [[ -z "$runtime_commit" ]] && command -v git >/dev/null 2>&1; then
  runtime_commit="$(git -C "$backend_root" rev-parse --verify HEAD 2>/dev/null || true)"
fi
python3 - \
  "$manifest_commit" \
  "$manifest_fernet_key_id" \
  "$manifest_evidence_key_id" \
  "${RESTORE_EXPECTED_COMMIT:-}" \
  "${RESTORE_FERNET_KEY_ID:-}" \
  "${RESTORE_EVIDENCE_SIGNING_KEY_ID:-}" \
  "$runtime_commit" <<'PY'
import re
import sys

(
    manifest_commit,
    manifest_fernet_key_id,
    manifest_evidence_key_id,
    expected_commit,
    expected_fernet_key_id,
    expected_evidence_key_id,
    runtime_commit,
) = sys.argv[1:]


def normalized_commit(name: str, value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise SystemExit(f"{name} must be a full 40-hex git commit")
    return value


def checked_key_id(name: str, value: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,127}", value)
        or value.lower() == "unrecorded"
    ):
        raise SystemExit(f"{name} must be a recorded key identifier")
    return value


expected_commit = normalized_commit("RESTORE_EXPECTED_COMMIT", expected_commit)
runtime_commit = normalized_commit("runtime git commit", runtime_commit)
expected_fernet_key_id = checked_key_id("RESTORE_FERNET_KEY_ID", expected_fernet_key_id)
expected_evidence_key_id = checked_key_id(
    "RESTORE_EVIDENCE_SIGNING_KEY_ID", expected_evidence_key_id
)
if expected_commit != manifest_commit:
    raise SystemExit("RESTORE_EXPECTED_COMMIT does not match backup manifest")
if runtime_commit != manifest_commit:
    raise SystemExit("runtime git commit does not match backup manifest")
if expected_fernet_key_id != manifest_fernet_key_id:
    raise SystemExit("RESTORE_FERNET_KEY_ID does not match backup manifest")
if expected_evidence_key_id != manifest_evidence_key_id:
    raise SystemExit("RESTORE_EVIDENCE_SIGNING_KEY_ID does not match backup manifest")
PY

expected_confirmation="restore:$backup_id"
if [[ "${RESTORE_CONFIRM:-}" != "$expected_confirmation" ]]; then
  echo "restore is destructive; set RESTORE_CONFIRM=$expected_confirmation" >&2
  exit 2
fi

for obsolete_override in \
  RESTORE_ALLOW_NONEMPTY_DB \
  RESTORE_ALLOW_STORAGE_OVERLAY \
  RESTORE_ALEMBIC_CHECK; do
  if [[ -n "${!obsolete_override:-}" ]]; then
    echo "$obsolete_override is no longer supported by the fail-closed restore path" >&2
    exit 2
  fi
done

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
if [[ "$manifest_revision" != "$shipped_revision" ]]; then
  echo "backup Alembic revision does not match this code revision: manifest=$manifest_revision shipped=$shipped_revision" >&2
  exit 2
fi

storage_archive=""
storage_target=""
storage_parent=""
storage_name=""
if [[ "$manifest_has_storage" == "1" ]]; then
  storage_archive="$work/storage.tar.gz"
  if [[ -z "${RESTORE_STORAGE_DIR:-}" ]]; then
    echo "backup includes storage; RESTORE_STORAGE_DIR is required for a complete restore" >&2
    exit 2
  fi
  if [[ -e "$RESTORE_STORAGE_DIR" || -L "$RESTORE_STORAGE_DIR" ]]; then
    echo "RESTORE_STORAGE_DIR must not exist; refusing a non-atomic overlay" >&2
    exit 2
  fi
  storage_parent="$(dirname -- "$RESTORE_STORAGE_DIR")"
  storage_name="$(basename -- "$RESTORE_STORAGE_DIR")"
  if [[ -z "$storage_name" || "$storage_name" == "." || "$storage_name" == ".." || "$storage_name" == "/" ]]; then
    echo "RESTORE_STORAGE_DIR must name a new child directory" >&2
    exit 2
  fi
  mkdir -p "$storage_parent"
  storage_parent="$(cd -- "$storage_parent" && pwd -P)"
  storage_target="$storage_parent/$storage_name"
  if [[ -e "$storage_target" || -L "$storage_target" ]]; then
    echo "RESTORE_STORAGE_DIR must not exist; refusing a non-atomic overlay" >&2
    exit 2
  fi
  validate_tar_paths "$storage_archive"
elif [[ -n "${RESTORE_STORAGE_DIR:-}" ]]; then
  echo "RESTORE_STORAGE_DIR was provided but this backup has no storage archive" >&2
  exit 2
fi

freshness_sql="
WITH user_namespaces AS (
  SELECT oid, nspname
  FROM pg_namespace
  WHERE nspname <> 'information_schema'
    AND left(nspname, 3) <> 'pg_'
), user_objects AS (
  SELECT 1
  FROM user_namespaces
  WHERE nspname <> 'public'
  UNION ALL
  SELECT 1
  FROM pg_class AS relation
  JOIN user_namespaces AS namespace ON namespace.oid = relation.relnamespace
  UNION ALL
  SELECT 1
  FROM pg_proc AS routine
  JOIN user_namespaces AS namespace ON namespace.oid = routine.pronamespace
  UNION ALL
  SELECT 1
  FROM pg_type AS standalone_type
  JOIN user_namespaces AS namespace ON namespace.oid = standalone_type.typnamespace
  WHERE standalone_type.typrelid = 0
  UNION ALL
  SELECT 1 FROM pg_publication
  UNION ALL
  SELECT 1 FROM pg_subscription WHERE subdbid = (SELECT oid FROM pg_database WHERE datname = current_database())
  UNION ALL
  SELECT 1 FROM pg_event_trigger
  UNION ALL
  SELECT 1 FROM pg_extension WHERE extname <> 'plpgsql'
  UNION ALL
  SELECT 1 FROM pg_foreign_data_wrapper
  UNION ALL
  SELECT 1 FROM pg_foreign_server
  UNION ALL
  SELECT 1 FROM pg_user_mapping
  UNION ALL
  SELECT 1 FROM pg_largeobject_metadata
  UNION ALL
  SELECT 1 FROM pg_default_acl
  UNION ALL
  SELECT 1 FROM pg_language WHERE lanname NOT IN ('internal', 'c', 'sql', 'plpgsql')
  UNION ALL
  SELECT 1
  FROM pg_db_role_setting
  WHERE setdatabase = (SELECT oid FROM pg_database WHERE datname = current_database())
  UNION ALL
  SELECT 1 FROM pg_cast WHERE oid >= 16384
  UNION ALL
  SELECT 1 FROM pg_transform WHERE oid >= 16384
  UNION ALL
  SELECT 1 FROM pg_am WHERE oid >= 16384
  UNION ALL
  SELECT 1
  FROM pg_operator AS user_operator
  JOIN user_namespaces AS namespace ON namespace.oid = user_operator.oprnamespace
  UNION ALL
  SELECT 1
  FROM pg_opclass AS user_opclass
  JOIN user_namespaces AS namespace ON namespace.oid = user_opclass.opcnamespace
  UNION ALL
  SELECT 1
  FROM pg_opfamily AS user_opfamily
  JOIN user_namespaces AS namespace ON namespace.oid = user_opfamily.opfnamespace
  UNION ALL
  SELECT 1
  FROM pg_collation AS user_collation
  JOIN user_namespaces AS namespace ON namespace.oid = user_collation.collnamespace
  UNION ALL
  SELECT 1
  FROM pg_conversion AS user_conversion
  JOIN user_namespaces AS namespace ON namespace.oid = user_conversion.connamespace
  UNION ALL
  SELECT 1
  FROM pg_ts_config AS user_ts_config
  JOIN user_namespaces AS namespace ON namespace.oid = user_ts_config.cfgnamespace
  UNION ALL
  SELECT 1
  FROM pg_ts_dict AS user_ts_dictionary
  JOIN user_namespaces AS namespace ON namespace.oid = user_ts_dictionary.dictnamespace
  UNION ALL
  SELECT 1
  FROM pg_ts_parser AS user_ts_parser
  JOIN user_namespaces AS namespace ON namespace.oid = user_ts_parser.prsnamespace
  UNION ALL
  SELECT 1
  FROM pg_ts_template AS user_ts_template
  JOIN user_namespaces AS namespace ON namespace.oid = user_ts_template.tmplnamespace
)
SELECT count(*) FROM user_objects;
"
object_count="$(psql "$database_url" -X -A -t -v ON_ERROR_STOP=1 -c "$freshness_sql")"
if [[ ! "$object_count" =~ ^[0-9]+$ ]]; then
  echo "could not verify that the target database is fresh" >&2
  exit 2
fi
if [[ "$object_count" != "0" ]]; then
  echo "target database contains non-system schemas or objects; restore requires a newly created database" >&2
  exit 2
fi

echo "restoring PostgreSQL backup" >&2
pg_restore \
  --dbname "$database_url" \
  --no-owner \
  --no-acl \
  --exit-on-error \
  --single-transaction \
  "$work/database.dump"

version_table_present="$(psql "$database_url" -X -A -t -v ON_ERROR_STOP=1 \
  -c "SELECT CASE WHEN to_regclass('public.alembic_version') IS NULL THEN 'f' ELSE 't' END;")"
if [[ "$version_table_present" != "t" ]]; then
  echo "restored database does not contain public.alembic_version" >&2
  exit 1
fi
restored_revision="$(psql "$database_url" -X -A -t -v ON_ERROR_STOP=1 \
  -c "SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version;")"
if [[ "$restored_revision" != "$manifest_revision" ]]; then
  echo "restored Alembic revision mismatch: manifest=$manifest_revision actual=${restored_revision:-missing}" >&2
  exit 1
fi

echo "checking restored schema against shipped SQLAlchemy metadata" >&2
(cd "$backend_root" && DATABASE_URL="$database_url" "$alembic_bin" check)

if [[ -n "$storage_archive" ]]; then
  if [[ -e "$storage_target" || -L "$storage_target" ]]; then
    echo "RESTORE_STORAGE_DIR appeared during database restore; refusing to overwrite it" >&2
    exit 2
  fi
  storage_stage="$(mktemp -d "$storage_parent/.${storage_name}.restore.XXXXXX")"
  tar -xzf "$storage_archive" -C "$storage_stage"
  python3 "$script_dir/atomic_publish_dir.py" "$storage_stage" "$storage_target"
  storage_stage=""
fi

echo "restore complete: $backup_id (schema $restored_revision, commit $manifest_commit)" >&2
