#!/usr/bin/env bash
# One-command natural-phrasing matrix rerun for the current HEAD.
#
# Run this from a CLEAN terminal (Terminal.app), NOT from inside a Claude
# Code session: an active session leaks CLAUDE*/ANTHROPIC* variables and
# contends for ~/.claude state, which makes the claude CLI bridge exit 1
# intermittently (observed 2026-08-11). The scrub below is defense in
# depth, not a substitute for a clean terminal.
#
# Model set: claude-fable-5 by default. kimi-k3 is excluded until issue #53
# (166 KB prompt > 120 KiB argv cap) is resolved. The gpt-5.6 trio needs
# the codex CLI on PATH; pass them explicitly once codex is available:
#   OPENAI_CLI_ENABLED=1 OPENAI_CLI_COMMAND=codex \
#   bash scripts/rerun_natural_matrix.sh gpt-5.6-sol gpt-5.6-terra gpt-5.6-luna
# Resume is automatic: re-running fills only the missing samples.

set -euo pipefail
cd "$(dirname "$0")/.."

MODELS=("${@:-claude-fable-5}")
OUT_DIR="$PWD/../.local/standard-astro-v02-natural"
# Isolate results per revision: resuming an old samples file after moving
# HEAD would silently mix revisions (Codex review P1, PR #54).
REV=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)
SAMPLES="$OUT_DIR/rerun_${REV}_samples.jsonl"
mkdir -p "$OUT_DIR"

PY="$PWD/venv/bin/python"
[ -x "$PY" ] || PY="$HOME/Projects/astro-platform/backend/venv/bin/python"

# A clean terminal may have no CLAUDE*/ANTHROPIC* vars at all; grep then
# exits 1 and set -e would kill the script (Codex review P2, PR #54).
SCRUB=$(env | grep -oE '^(CLAUDE|ANTHROPIC)[A-Za-z_]*' | sort -u | sed 's/^/-u /' | tr '\n' ' ' || true)

# shellcheck disable=SC2086
env $SCRUB \
  LIGHTWEIGHT_VERIFICATION_ENABLED=1 \
  CLAUDE_CLI_ENABLED=1 CLAUDE_CLI_COMMAND=claude \
  ${OPENAI_CLI_ENABLED:+OPENAI_CLI_ENABLED=$OPENAI_CLI_ENABLED} \
  ${OPENAI_CLI_COMMAND:+OPENAI_CLI_COMMAND=$OPENAI_CLI_COMMAND} \
  "$PY" -m scripts.evaluate_standard_astro_v02 \
  --tasks-path "$PWD/../docs/research/standard_astro_v02_natural_preregistered_tasks.json" \
  --conditions standard_astro \
  --models "${MODELS[@]}" \
  --output "$SAMPLES" \
  --evaluation-id standard-astro-v02-natural-rerun-head

PYTHONPATH="$PWD" "$PY" scripts/score_standard_astro_v02_natural.py \
  --samples "$SAMPLES" \
  --scores "$OUT_DIR/rerun_${REV}_scores.csv" \
  --summary "$OUT_DIR/rerun_${REV}_summary.json" \
  --allow-partial

"$PY" - "$OUT_DIR/rerun_${REV}_summary.json" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"\nsamples={d['samples']} transport_failures={d['transport_failures']}")
for name, s in d["strata"].items():
    if s.get("samples"):
        print(f"{name}: {s['score']}/{s['maximum']} = {s['percentage']}%")
EOF
