#!/usr/bin/env bash
# Daily blind-test driver — solar_system + exoplanet + cosmology in one shot.
#
# Each per-module runner reads its own cases.yaml, talks to an LLM via the
# real chat path, and dumps a results_<timestamp>/ directory next to its
# runner. This script just orchestrates the three and reports a one-line
# summary so the daily cron job has a single stdout to grep.
#
# Requirements:
#   - backend/venv/ exists with requirements.txt installed.
#   - One of ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / PLATFORM_DEEPSEEK_API_KEY
#     in the environment (the runner uses inference_router).
#   - Each runner accepts --provider local for the local Codex/OpenAI path
#     if no API key is available.
#
# Usage:
#   bash backend/scripts/daily_blind.sh
#   bash backend/scripts/daily_blind.sh --case A1,A2     # subset across all modules
#   bash backend/scripts/daily_blind.sh --provider local
set -uo pipefail

cd "$(dirname "$0")/.."  # cd to backend/
ROOT="$(pwd)"
PY="${PY:-./venv/bin/python}"

if [[ ! -x "$PY" ]]; then
  echo "ERROR: python not found at $PY (expected backend/venv/bin/python)." >&2
  exit 2
fi

# Default-provider policy (2026-05-28 update): always prefer DeepSeek
# when its key is present and the caller didn't pass --provider
# explicitly. The runner's argparse default is "anthropic"; the daily
# cron should NEVER silently bill Anthropic just because both keys are
# configured. Manual dispatch via daily.yml inputs.provider=anthropic
# still overrides this (it sets --provider anthropic in the args).
if [[ "$*" != *"--provider"* ]]; then
  if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
    set -- "$@" --provider deepseek
    echo "(daily_blind.sh) DEEPSEEK_API_KEY set → --provider deepseek (default cron path)"
  fi
fi

modules=(
  "blind_test_m0:solar_system"
  "blind_test_exoplanet_m0:exoplanet"
  "blind_test_cosmology_m0:cosmology"
)

# Pull a --module <name> filter out of "$@" so the caller can run a
# single focus instead of all three. Used by the daily.yml manual-
# dispatch input for tight iteration loops on a single module. The
# remaining arguments are forwarded to the per-module runner as-is.
filter=""
forwarded=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --module)
      filter="$2"
      shift 2
      ;;
    --module=*)
      filter="${1#--module=}"
      shift
      ;;
    *)
      forwarded+=("$1")
      shift
      ;;
  esac
done
set -- "${forwarded[@]}"

if [[ -n "$filter" && "$filter" != "all" ]]; then
  case "$filter" in
    solar_system|exoplanet|cosmology) ;;
    *)
      echo "ERROR: --module must be one of: all, solar_system, exoplanet, cosmology (got $filter)." >&2
      exit 2
      ;;
  esac
  echo "(daily_blind.sh) --module=$filter → running only that focus."
  new_modules=()
  for entry in "${modules[@]}"; do
    if [[ "${entry##*:}" == "$filter" ]]; then
      new_modules+=("$entry")
    fi
  done
  modules=("${new_modules[@]}")
fi

total_pass=0
total_fail=0
total_err=0
log=()

for entry in "${modules[@]}"; do
  dir="${entry%%:*}"
  focus="${entry##*:}"
  runner="$ROOT/scripts/$dir/runner.py"

  echo "──────────────── $focus ($dir) ────────────────"
  if [[ ! -f "$runner" ]]; then
    echo "  skip: $runner not found"
    log+=("$focus: SKIP (runner missing)")
    continue
  fi
  if ASTRO_RESEARCH_FOCUS="$focus" "$PY" "$runner" "$@"; then
    log+=("$focus: ok (exit 0)")
    total_pass=$((total_pass + 1))
  else
    rc=$?
    log+=("$focus: FAIL (exit $rc)")
    total_fail=$((total_fail + 1))
  fi
done

echo
echo "════════════ daily blind-test summary ════════════"
for line in "${log[@]}"; do echo "  $line"; done
echo "──────────────────────────────────────────────────"
echo "  modules ok: $total_pass / failing: $total_fail"
echo

# Exit non-zero if anything failed so the CI cron surfaces the regression.
if [[ "$total_fail" -gt 0 ]]; then
  exit 1
fi
exit 0
