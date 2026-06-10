#!/bin/bash
# 项目实时统计 — 用 grep / wc / Python import 动态计算, 防文档数字漂移.
# 每次 CLAUDE.md / ARCHITECTURE.md 改了项目体量描述, 跑一遍这个对一下.
#
# 用法: ./scripts/stats.sh
#
# 替代: 任何手写在文档里的 "N tools" / "M routers" / "K-token system prompt".

set -e
cd "$(dirname "$0")/.."

# Live-import probes need the backend deps (yaml etc.) — prefer the venv python.
PY=python3
[ -x backend/venv/bin/python3 ] && PY=backend/venv/bin/python3

echo "=== Backend code ==="
echo "API routers:    $(ls backend/app/api/*.py 2>/dev/null | grep -v __init__ | wc -l | tr -d ' ')"
echo "Services:       $(ls backend/app/services/*.py 2>/dev/null | wc -l | tr -d ' ')"
echo "Pipeline nodes: $(ls backend/app/pipeline/nodes/*.py 2>/dev/null | grep -v __init__ | wc -l | tr -d ' ')"
echo "Connector keys (registry CONNECTORS_KEYS): $(grep -A 5 '^CONNECTORS_KEYS' backend/app/connectors/registry.py | tr ',' '\n' | grep -c '\".*\"' || true)"
echo "Total Py LOC:   $(find backend/app -name '*.py' | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}')"
echo

echo "=== AI tools (live import) ==="
"$PY" -c "
import sys
sys.path.insert(0, 'backend')
try:
    from app.services.ai_tools import TOOLS
    print(f'TOOLS array: {len(TOOLS)} tools')
except Exception as e:
    print(f'(import failed: {e})')
"
echo

echo "=== SYSTEM_PROMPT (live build via prompt_loader, cosmology focus) ==="
"$PY" -c "
import re, sys
sys.path.insert(0, 'backend')
try:
    from app.services.prompt_loader import build_system_prompt
    body = build_system_prompt('cosmology')
    chars = len(body)
    sects = len(re.findall(r'^## ', body, re.MULTILINE))
    subs = len(re.findall(r'^### ', body, re.MULTILINE))
    print(f'Chars: {chars} (~{chars / 1024:.0f} KB, ~{chars // 4 // 1000} K tokens)')
    print(f'## sections: {sects}, ### subsections: {subs}')
except Exception as e:
    print(f'(live build failed: {e})')
"
echo

echo "=== Frontend ==="
echo "Pages: $(ls frontend/src/pages/ 2>/dev/null | wc -l | tr -d ' ')"
echo "TS/TSX LOC: $(find frontend/src -name '*.ts' -o -name '*.tsx' 2>/dev/null | xargs wc -l 2>/dev/null | tail -1 | awk '{print $1}')"
echo

echo "=== Tests ==="
echo "Backend tests: $(ls backend/tests/test_*.py 2>/dev/null | wc -l | tr -d ' ')"
echo "Frontend tests: $(find frontend/src/__tests__ -name '*.test.*' 2>/dev/null | wc -l | tr -d ' ')"
