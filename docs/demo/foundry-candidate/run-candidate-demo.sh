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
  python_selection="explicit"
elif [[ -x "$backend/venv/bin/python" ]]; then
  python_bin="$backend/venv/bin/python"
  python_selection="canonical_venv"
else
  cat >&2 <<'EOF'
ERROR: Foundry replay environment rejected: replay_python_required
Set PYTHON to the executable in a supported, dependency-complete virtual
environment, or install the canonical environment at backend/venv. The retired
backend/.venv and an ambient python3 are intentionally never used.
EOF
  exit 78
fi

if [[ "$python_bin" != /* || ! -x "$python_bin" ]]; then
  cat >&2 <<EOF
ERROR: Foundry replay environment rejected: replay_python_not_executable
PYTHON must name an absolute executable path, not a shell command: $python_bin
EOF
  exit 78
fi

# Ignore ambient import hooks for both the preflight and the actual replay.
# The candidate child inherits this sanitized environment from the replay.
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1

# Fail before the replay creates an output directory.  The preflight uses only
# the standard library until it can turn missing/incompatible dependencies into
# a stable error code; a raw ModuleNotFoundError must never become a receipt.
"$python_bin" -I - "$backend" "$python_selection" <<'PY'
from __future__ import annotations

import importlib
import importlib.metadata
import platform
import re
import sys
from pathlib import Path


def reject(reason: str) -> "NoReturn":
    print(
        "ERROR: Foundry replay environment rejected: " + reason,
        file=sys.stderr,
    )
    raise SystemExit(78)


backend = Path(sys.argv[1]).resolve()
python_selection = sys.argv[2]
if platform.python_implementation() != "CPython":
    reject("replay_python_implementation_unsupported")
if not ((3, 11) <= sys.version_info[:2] < (3, 15)):
    reject("replay_python_version_unsupported")
if not sys.flags.isolated or not sys.flags.no_user_site:
    reject("replay_python_isolation_required")

prefix = Path(sys.prefix).resolve()
base_prefix = Path(sys.base_prefix).resolve()
if python_selection == "canonical_venv" and (
    prefix == base_prefix or not (prefix / "pyvenv.cfg").is_file()
):
    reject("replay_virtual_environment_required")


def numeric_version(value: str) -> tuple[int, ...]:
    match = re.match(r"^(\d+(?:\.\d+)*)", value)
    if match is None:
        reject("replay_dependency_version_invalid")
    return tuple(int(part) for part in match.group(1).split("."))


dependency_contract = (
    ("numpy", "numpy", (1, 26, 4), (3, 0, 0)),
    ("PyYAML", "yaml", (6, 0, 0), (7, 0, 0)),
)
for distribution_name, module_name, minimum, maximum in dependency_contract:
    try:
        distribution = importlib.metadata.distribution(distribution_name)
        installed = numeric_version(distribution.version)
    except importlib.metadata.PackageNotFoundError:
        reject(f"replay_dependency_missing:{distribution_name}")
    except (TypeError, ValueError):
        reject(f"replay_dependency_metadata_invalid:{distribution_name}")
    if installed < minimum or installed >= maximum:
        reject(f"replay_dependency_version_unsupported:{distribution_name}")
    distribution_root = Path(distribution.locate_file("")).resolve()
    try:
        distribution_root.relative_to(prefix)
    except ValueError:
        reject(f"replay_dependency_outside_virtualenv:{distribution_name}")
    try:
        dependency_module = importlib.import_module(module_name)
    except Exception as exc:  # normalize binary/import failures before replay
        reject(
            f"replay_dependency_import_failed:{distribution_name}:"
            f"{type(exc).__name__}"
        )
    module_file = getattr(dependency_module, "__file__", None)
    if not module_file:
        reject(f"replay_dependency_origin_missing:{distribution_name}")
    try:
        Path(module_file).resolve().relative_to(prefix)
    except ValueError:
        reject(f"replay_dependency_outside_virtualenv:{distribution_name}")

project_contract = (
    (
        "app.services.foundry_source_tree",
        backend / "app/services/foundry_source_tree.py",
    ),
    (
        "app.services.foundry_demo_runner",
        backend / "app/services/foundry_demo_runner.py",
    ),
    (
        "app.services.cosmology_likelihoods.dark_energy_matrix",
        backend / "app/services/cosmology_likelihoods/dark_energy_matrix.py",
    ),
)
for module_name, expected_file in project_contract:
    if not expected_file.is_file():
        reject(f"replay_project_module_missing:{module_name}")

sys.path.insert(0, str(backend))
for module_name, expected_file in project_contract:
    try:
        project_module = importlib.import_module(module_name)
    except Exception as exc:  # never expose a ModuleNotFoundError traceback
        reject(f"replay_project_import_failed:{module_name}:{type(exc).__name__}")
    module_file = getattr(project_module, "__file__", None)
    if not module_file or Path(module_file).resolve() != expected_file.resolve():
        reject(f"replay_project_module_origin_mismatch:{module_name}")
PY

if [[ "${1:-}" == "--verify-recorded" ]]; then
  exec "$python_bin" -I \
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

exec "$python_bin" -I "${args[@]}"
