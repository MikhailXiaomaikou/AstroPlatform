#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${STANDARD_ASTRO_REPO:-}" ]]; then
  repo_root="$STANDARD_ASTRO_REPO"
else
  repo_root="$(git rev-parse --show-toplevel)"
fi

backend="$repo_root/backend"
output_dir="${1:-$repo_root/.local/foundry-candidate-demo}"
if [[ -n "${PYTHON:-}" ]]; then
  python_bin="$PYTHON"
elif [[ -x "$backend/venv/bin/python" ]]; then
  python_bin="$backend/venv/bin/python"
elif [[ -x "$backend/.venv/bin/python" ]]; then
  python_bin="$backend/.venv/bin/python"
else
  python_bin="python3"
fi

mkdir -p "$output_dir"
if [[ -e "$output_dir/demo-report.json" ]]; then
  echo "Refusing to overwrite immutable Demo output: $output_dir/demo-report.json" >&2
  exit 2
fi

commit="$(git -C "$repo_root" rev-parse HEAD)"
export TOOL_VERSION="$commit"

args=(
  "$backend/scripts/run_foundry_candidate_demo.py"
  --candidate desi_dr2_official_chain_summary_v1
  --output "$output_dir/demo-report.json"
  --runner-image-digest local-descriptor-not-signed-oci
)

if [[ -n "${DESI_DR2_OFFICIAL_CHAIN_ROOT:-}" ]]; then
  args+=(--chain-root "$DESI_DR2_OFFICIAL_CHAIN_ROOT")
fi

"$python_bin" "${args[@]}"

"$python_bin" - "$output_dir/demo-report.json" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert report["evidence_class"] == "NON_FORMAL_DEMO"
assert report["publication_ready"] is False
assert report["claim_eligible"] is False
assert report["evidence_pack_allowed"] is False
print(json.dumps({
    "status": report["status"],
    "failure_class": report.get("failure_class"),
    "demo_report_sha256": report["demo_report_sha256"],
    "formal_claim_escape_blocked": True,
}, sort_keys=True))
PY
