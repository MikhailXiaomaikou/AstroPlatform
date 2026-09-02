#!/usr/bin/env bash
# One-command Standard Astro v0.3 exploration-depth matrix for the current HEAD.
#
# Usage:
#   bash scripts/run_exploration_matrix.sh ARM [MODEL ...] [-- extra runner flags]
#   bash scripts/run_exploration_matrix.sh C1 claude-fable-5 --repeats 2 --budget production --lightweight both
#   bash scripts/run_exploration_matrix.sh C0 claude-fable-5 --repeats 2
#   bash scripts/run_exploration_matrix.sh C1 claude-fable-5 --task-ids V03_03_h0_anchor_clustering --repeats 1 --lightweight off
#
# ARM is one of C0 C1 C2a C2b C2c C2d C2_exploration (presets live in
# scripts/evaluate_standard_astro_v02.py; explicit flags override the preset).
# Every argument from the first one starting with "--" onwards is passed
# through to the runner unchanged (--repeats, --budget, --lightweight,
# --steering, --task-ids, --system-appendix, --lane-override,
# --record-pregate-drafts, ...).
#
# Run this from a CLEAN terminal (Terminal.app), NOT from inside a Claude
# Code session: an active session leaks CLAUDE*/ANTHROPIC* variables and
# contends for ~/.claude state, which makes the claude CLI bridge exit 1
# intermittently (observed 2026-08-11). The scrub below is defense in
# depth, not a substitute for a clean terminal.
#
# Pre-registration rules (docs/research/standard_astro_v03_exploration_tasks.json):
# the task file is frozen (its sha256 is committed and written into every
# sample), strata are never blended by the scorer, and zero-event cells are
# reported with rule-of-three upper bounds. Do not edit the task file to make
# a run pass; a changed prompt is a new file plus a new commitment.
#
# Offline pre-gate drafts: --record-pregate-drafts writes
# <out dir>/offline_drafts_<rev>.jsonl beside the samples. Those files are
# evaluation-only working data: they are never served, never published, and
# never copied under docs/research (assets included).
#
# Model set: claude-fable-5 by default. kimi-k3 is excluded until issue #53
# (166 KB prompt > 120 KiB argv cap) is resolved. The gpt-5.6 trio needs
# the codex CLI on PATH; pass them explicitly once codex is available:
#   OPENAI_CLI_ENABLED=1 OPENAI_CLI_COMMAND=codex \
#   bash scripts/run_exploration_matrix.sh C1 gpt-5.6-sol gpt-5.6-terra gpt-5.6-luna
# Resume is automatic per arm and revision: re-running fills only the
# missing samples.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$#" -lt 1 ] || [[ "$1" == --* ]]; then
  echo "usage: $0 ARM [MODEL ...] [runner flags...]" >&2
  exit 2
fi
ARM="$1"
shift

MODELS=()
while [ "$#" -gt 0 ] && [[ "$1" != --* ]]; do
  MODELS+=("$1")
  shift
done
[ "${#MODELS[@]}" -gt 0 ] || MODELS=(claude-fable-5)
EXTRA=("$@")

OUT_DIR="$PWD/../.local/standard-astro-v03-exploration"
# Isolate results per arm and revision: resuming an old samples file after
# moving HEAD would silently mix revisions (Codex review P1, PR #54).
REV=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
SAMPLES="$OUT_DIR/${ARM}_${REV}_samples.jsonl"
TASKS="$PWD/../docs/research/standard_astro_v03_exploration_tasks.json"
mkdir -p "$OUT_DIR"

PY="$PWD/venv/bin/python"
[ -x "$PY" ] || PY="$HOME/Projects/astro-platform/backend/venv/bin/python"

# A clean terminal may have no CLAUDE*/ANTHROPIC* vars at all; grep then
# exits 1 and set -e would kill the script (Codex review P2, PR #54).
SCRUB=$(env | grep -oE '^(CLAUDE|ANTHROPIC)[A-Za-z_]*' | sort -u | sed 's/^/-u /' | tr '\n' ' ' || true)

# No LIGHTWEIGHT_VERIFICATION_ENABLED here: the runner sets the switch per
# sample from --lightweight (default on; C1 runs both states).
# shellcheck disable=SC2086
env $SCRUB \
  CLAUDE_CLI_ENABLED=1 CLAUDE_CLI_COMMAND=claude \
  ${OPENAI_CLI_ENABLED:+OPENAI_CLI_ENABLED=$OPENAI_CLI_ENABLED} \
  ${OPENAI_CLI_COMMAND:+OPENAI_CLI_COMMAND=$OPENAI_CLI_COMMAND} \
  "$PY" -m scripts.evaluate_standard_astro_v02 \
  --tasks-path "$TASKS" \
  --arm "$ARM" \
  --models "${MODELS[@]}" \
  --output "$SAMPLES" \
  --evaluation-id standard-astro-v03-exploration-depth \
  ${EXTRA[@]+"${EXTRA[@]}"}  # empty-array-safe under set -u on macOS bash 3.2

SCORER="scripts/score_standard_astro_v03_exploration.py"
if [ -f "$SCORER" ]; then
  PYTHONPATH="$PWD" "$PY" "$SCORER" \
    --samples "$SAMPLES" \
    --scores "$OUT_DIR/${ARM}_${REV}_scores.csv" \
    --summary "$OUT_DIR/${ARM}_${REV}_summary.json"
else
  echo "samples written to $SAMPLES; scorer $SCORER not present, skipping scoring" >&2
fi
