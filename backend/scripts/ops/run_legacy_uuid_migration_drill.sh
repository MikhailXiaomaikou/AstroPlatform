#!/usr/bin/env bash
# Exercise a complete revision-002 legacy database against PostgreSQL 16.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend_dir="$(cd "$script_dir/../.." && pwd)"
cd "$backend_dir"

: "${DATABASE_URL:?Set DATABASE_URL to a disposable astro_uuid_drill_* database}"

python_bin="${PYTHON:-python3}"
fixture="scripts/ops/legacy_uuid_migration_fixture.py"
bridge_revision="002a_uuid_bridge"

prepare_complete_legacy_fixture() {
  local scenario="$1"
  "$python_bin" "$fixture" reset
  "$python_bin" -m alembic upgrade "$bridge_revision"
  "$python_bin" -m alembic downgrade 002
  "$python_bin" "$fixture" seed --scenario "$scenario"
}

prepare_complete_legacy_fixture valid
"$python_bin" -m alembic upgrade head
"$python_bin" "$fixture" verify --scenario valid --expect migrated

prepare_complete_legacy_fixture dirty

error_log="$(mktemp -t astro-uuid-drill.XXXXXX)"
trap 'rm -f "$error_log"' EXIT
if "$python_bin" -m alembic upgrade head >"$error_log" 2>&1; then
  echo "Dirty UUID migration unexpectedly succeeded" >&2
  exit 1
fi
if ! grep -Eiq '(invalid[^[:space:]]*.*uuid|uuid.*invalid)' "$error_log"; then
  echo "Dirty UUID migration failed for an unexpected reason:" >&2
  sed -n '1,200p' "$error_log" >&2
  exit 1
fi
if ! grep -Eiq '(ALTER TABLE users ALTER COLUMN id|users.*id.*uuid)' "$error_log"; then
  echo "Dirty UUID did not fail at the intentionally late users.id conversion:" >&2
  sed -n '1,200p' "$error_log" >&2
  exit 1
fi

"$python_bin" "$fixture" verify --scenario dirty --expect rollback
echo "PostgreSQL 16 complete legacy UUID drill passed (head upgrade + dirty rollback)"
