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

# The report binds TOOL_VERSION to the checked-out commit.  Refuse any tracked
# or untracked source change that could make the executed bytes differ from
# that commit.  Ignored caches and the default .local output remain harmless.
dirty_state="$(git -C "$repo_root" status --porcelain --untracked-files=all)"
if [[ -n "$dirty_state" ]]; then
  echo "Refusing to bind a dirty checkout to a clean TOOL_VERSION commit." >&2
  echo "Commit, stash, or remove these changes before replaying:" >&2
  printf '%s\n' "$dirty_state" >&2
  exit 3
fi

commit="$(git -C "$repo_root" rev-parse HEAD)"
export TOOL_VERSION="$commit"

published_candidate_bundle_sha256="1206466dc33c8c9f043ada93e7d800783549741089f48e3baa62b896014440eb"
published_candidate_version_sha256="f4e8fa65deeb0b8662770fe436035596a89085ac33ef53cfb8e974d191268868"
published_workflow_spec_sha256="65581b0d72af19313b066a0e7fdaf8f6f569a526e9d086b256424fe016a69869"
published_runner_descriptor_digest="sha256:12a937d0522207449e2f3016142e1e7c82ac8abcd6fba6a19a3be372c7ed2f88"

args=(
  "$backend/scripts/run_foundry_candidate_demo.py"
  --candidate desi_dr2_official_chain_summary_v1
  --output "$output_dir/demo-report.json"
  --candidate-version-sha256 "$published_candidate_version_sha256"
  --runner-image-digest "$published_runner_descriptor_digest"
)

if [[ -n "${DESI_DR2_OFFICIAL_CHAIN_ROOT:-}" ]]; then
  args+=(--chain-root "$DESI_DR2_OFFICIAL_CHAIN_ROOT")
fi

"$python_bin" "${args[@]}"

"$python_bin" - \
  "$output_dir/demo-report.json" \
  "$published_candidate_bundle_sha256" \
  "$published_candidate_version_sha256" \
  "$published_workflow_spec_sha256" \
  "$published_runner_descriptor_digest" <<'PY'
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected_candidate_bundle = sys.argv[2]
expected_candidate_version = sys.argv[3]
expected_workflow_spec = sys.argv[4]
expected_runner_descriptor = sys.argv[5]


def require(condition, message):
    if not condition:
        raise SystemExit(f"Replay validation failed: {message}")


require(report.get("evidence_class") == "NON_FORMAL_DEMO", "evidence class")
require(report.get("publication_ready") is False, "publication gate")
require(report.get("claim_eligible") is False, "claim gate")
require(report.get("evidence_pack_allowed") is False, "Evidence Pack gate")
require(
    report.get("candidate_bundle_sha256") == expected_candidate_bundle,
    "candidate bundle binding",
)
require(
    report.get("candidate_version_sha256") == expected_candidate_version,
    "candidate version binding",
)
require(
    report.get("workflow_spec_sha256") == expected_workflow_spec,
    "workflow specification binding",
)
require(
    report.get("runner_image_digest") == expected_runner_descriptor,
    "runner descriptor binding",
)
require(
    isinstance(report.get("limitations"), list) and report["limitations"],
    "limitations missing",
)
summary = report.get("validation_summary")
require(isinstance(summary, dict), "validation summary missing")
require(summary.get("registry_integrity") is True, "registry integrity")
require(summary.get("numeric_claim_gate") == "NON_FORMAL_DEMO", "numeric claim gate")

status = report.get("status")
if status == "PARTIAL":
    require(
        report.get("failure_class") == "official_chain_mirror_unavailable",
        "unexpected partial failure class",
    )
    require(summary.get("official_mirror_verified") is False, "partial mirror state")
    require(summary.get("ready_cells") == 0, "partial ready-cell count")
    require(
        isinstance(summary.get("withheld_cells"), int)
        and summary["withheld_cells"] > 0,
        "partial withheld-cell count",
    )
elif status == "PASSED":
    require(report.get("failure_class") is None, "passed failure class")
    require(summary.get("official_mirror_verified") is True, "passed mirror state")
    require(
        isinstance(summary.get("ready_cells"), int) and summary["ready_cells"] > 0,
        "passed ready-cell count",
    )
    require(summary.get("withheld_cells") == 0, "passed withheld-cell count")
else:
    raise SystemExit(f"Replay validation failed: unexpected status {status!r}")

print(json.dumps({
    "status": status,
    "failure_class": report.get("failure_class"),
    "demo_report_sha256": report["demo_report_sha256"],
    "candidate_version_sha256": report["candidate_version_sha256"],
    "runner_image_digest": report["runner_image_digest"],
    "formal_claim_escape_blocked": True,
}, sort_keys=True))
PY
