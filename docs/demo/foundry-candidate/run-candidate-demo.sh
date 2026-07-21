#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${STANDARD_ASTRO_REPO:-}" ]]; then
  repo_root="$STANDARD_ASTRO_REPO"
else
  repo_root="$(git rev-parse --show-toplevel)"
fi

backend="$repo_root/backend"
if [[ -n "${PYTHON:-}" ]]; then
  python_bin="$PYTHON"
elif [[ -x "$backend/venv/bin/python" ]]; then
  python_bin="$backend/venv/bin/python"
elif [[ -x "$backend/.venv/bin/python" ]]; then
  python_bin="$backend/.venv/bin/python"
else
  python_bin="python3"
fi

if [[ "${1:-}" == "--verify-recorded" ]]; then
  exec "$python_bin" \
    "$backend/scripts/run_public_foundry_candidate_replay.py" \
    verify-recorded \
    --kit-dir "$repo_root/docs/demo/foundry-candidate"
fi

output_dir="${1:-$repo_root/.local/foundry-candidate-demo}"
args=(
  "$backend/scripts/run_public_foundry_candidate_replay.py"
  run
  --repo-root "$repo_root"
  --candidate desi_dr2_official_chain_summary_v1
  --output-dir "$output_dir"
)

if [[ -n "${DESI_DR2_OFFICIAL_CHAIN_ROOT:-}" ]]; then
  args+=(--chain-root "$DESI_DR2_OFFICIAL_CHAIN_ROOT")
fi

exec "$python_bin" "${args[@]}"
