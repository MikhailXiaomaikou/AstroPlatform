"""Solar-system M0 盲测 runner.

用法:
    cd backend
    source .venv/bin/activate
    export ASTRO_RESEARCH_FOCUS=exoplanet
    export ANTHROPIC_API_KEY=sk-ant-...    # 或 export CLAUDE_MODEL=claude-sonnet-4-6
    python scripts/blind_test_m0/runner.py              # 全跑 20 case
    python scripts/blind_test_m0/runner.py --case A1    # 单跑一个 dry run
    python scripts/blind_test_m0/runner.py --case A1,B2 # 跑指定子集

本地 Codex CLI 路径:
    export OPENAI_CLI_ENABLED=1
    export OPENAI_CLI_COMMAND=codex
    python scripts/blind_test_m0/runner.py --provider local

每个 case 把完整 trace dump 到 scripts/blind_test_m0/results_<timestamp>/case_<id>.json。
全跑结束后还会输出 summary.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

# ── 必须在 import chat 之前设 focus, SYSTEM_PROMPT 是 module-level 常量 ──
os.environ.setdefault("ASTRO_RESEARCH_FOCUS", "exoplanet")

# 让 backend/app 进 import path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import yaml  # type: ignore

from app.ai.model_profiles import resolve_model_profile
from app.api.chat import (
    SYSTEM_PROMPT,
    _filter_tools_by_research_focus,
    _run_agent_loop,
)
from app.services.ai_tools import TOOLS


SCRIPT_DIR = Path(__file__).resolve().parent
CASES_FILE = SCRIPT_DIR / "cases.yaml"


def _check_env(provider: str) -> str | None:
    focus = os.environ.get("ASTRO_RESEARCH_FOCUS", "")
    if focus != "exoplanet":
        raise SystemExit(f"ASTRO_RESEARCH_FOCUS 必须是 'exoplanet', 现在是 {focus!r}")
    if provider == "local":
        if not os.environ.get("OPENAI_CLI_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise SystemExit(
                "local provider 需要 OPENAI_CLI_ENABLED=1。\n"
                "    export OPENAI_CLI_ENABLED=1\n"
                "    export OPENAI_CLI_COMMAND=codex"
            )
        return None
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise SystemExit(
            "缺 ANTHROPIC_API_KEY。\n"
            "    export ANTHROPIC_API_KEY=sk-ant-...\n"
            "或在 backend/.env 里设。"
        )
    return key


def load_cases() -> list[dict]:
    with CASES_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def run_one_case(case: dict, api_key: str | None, out_dir: Path, *, provider: str) -> dict:
    """跑一个 case, 返回 result summary dict."""
    case_id = case["id"]
    print(f"[{case_id}] starting...", flush=True)

    events: list[dict] = []

    async def collect(evt: dict) -> None:
        # 浅拷贝 + ts; 不写入磁盘期间的全部 mutable state
        rec = dict(evt)
        rec["_ts"] = time.time()
        events.append(rec)

    tools = _filter_tools_by_research_focus(TOOLS)
    if provider == "local":
        profile = resolve_model_profile("local", "local:openai-cli")
    else:
        profile = resolve_model_profile("anthropic", "anthropic:default")

    messages = [{"role": "user", "content": case["prompt"]}]
    python_session_id = f"blindtest-{uuid.uuid4().hex[:12]}"

    t0 = time.time()
    error: str | None = None
    loop_result: dict | None = None
    try:
        loop_result = await _run_agent_loop(
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
            provider_api_keys={"anthropic": api_key} if api_key else {},
            agent_name="blind_test",
            python_session_id=python_session_id,
            preferred_backend=provider,
            model_profile=profile,
            user_id=None,
            chat_session_id=None,
            on_event=collect,
            workflow_budget=None,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"

    elapsed = time.time() - t0

    record = {
        "case_id": case_id,
        "group": case.get("group"),
        "prompt": case["prompt"],
        "expect_tools_called": case.get("expect_tools_called", []),
        "expect_pass": case.get("expect_pass", []),
        "elapsed_seconds": round(elapsed, 1),
        "model": profile.resolved_model_id,
        "n_events": len(events),
        "n_tool_calls": sum(1 for e in events if e.get("type") == "tool_call"),
        "tools_called": [e.get("tool") for e in events if e.get("type") == "tool_call"],
        "hit_iteration_cap": (loop_result or {}).get("hit_iteration_cap"),
        "hit_deadline": (loop_result or {}).get("hit_deadline"),
        "reply": (loop_result or {}).get("reply"),
        "error": error,
        "events": events,
    }

    out_file = out_dir / f"case_{case_id}.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)

    status = "ERROR" if error else "DONE"
    print(
        f"[{case_id}] {status} in {elapsed:.1f}s, "
        f"n_tools={record['n_tool_calls']}, tools={record['tools_called']}",
        flush=True,
    )
    return record


def write_summary(records: list[dict], out_dir: Path) -> None:
    lines = ["# Solar-system M0 盲测结果\n"]
    lines.append(f"- 跑完时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Case 总数: {len(records)}")
    n_err = sum(1 for r in records if r.get("error"))
    lines.append(f"- 异常 (LLM/系统挂掉): {n_err}")
    avg_time = sum(r["elapsed_seconds"] for r in records) / max(len(records), 1)
    lines.append(f"- 平均耗时: {avg_time:.1f}s/case\n")

    lines.append("## 按 case 一览\n")
    lines.append("| ID | group | tools called | n_tools | time | error |")
    lines.append("|---|---|---|---|---|---|")
    for r in records:
        tools = ",".join(r.get("tools_called") or []) or "—"
        err = "✗" if r.get("error") else ""
        lines.append(
            f"| {r['case_id']} | {r.get('group')} | {tools} | {r['n_tool_calls']} | "
            f"{r['elapsed_seconds']}s | {err} |"
        )

    lines.append("\n## 期望工具 vs 实际工具\n")
    for r in records:
        expect = set(r.get("expect_tools_called") or [])
        actual = set(r.get("tools_called") or [])
        match = "✓" if expect.issubset(actual) or (not expect and not actual) else "△"
        missing = expect - actual
        extra = actual - expect
        lines.append(f"- **{r['case_id']}** {match} expect={sorted(expect)} actual={sorted(actual)}")
        if missing:
            lines.append(f"    - 缺工具: {sorted(missing)}")
        if extra:
            lines.append(f"    - 多工具: {sorted(extra)}")

    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nsummary 写到: {out_dir / 'summary.md'}", flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="只跑指定 case (逗号分隔, 如 A1 或 A1,B2)")
    parser.add_argument("--group", help="只跑指定 group (A/B/C/D/E)")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "local"],
        default="anthropic",
        help="LLM backend: anthropic requires ANTHROPIC_API_KEY; local uses Codex/OpenAI CLI bridge.",
    )
    args = parser.parse_args()

    api_key = _check_env(args.provider)
    all_cases = load_cases()

    selected = all_cases
    if args.case:
        ids = {x.strip() for x in args.case.split(",")}
        # 支持前缀匹配: A1 → A1_phaethon_golden
        selected = [
            c for c in all_cases
            if c["id"] in ids or any(c["id"].startswith(x + "_") for x in ids)
        ]
    if args.group:
        groups = {g.strip().upper() for g in args.group.split(",")}
        selected = [c for c in selected if c.get("group") in groups]

    if not selected:
        raise SystemExit("没选到任何 case")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = SCRIPT_DIR / f"results_{timestamp}"
    out_dir.mkdir(exist_ok=True)
    print(f"输出目录: {out_dir}", flush=True)
    print(f"将跑 {len(selected)} 个 case: {[c['id'] for c in selected]}", flush=True)
    print(f"Provider: {args.provider}", flush=True)
    print(f"工具数 (focus=exoplanet): {len(_filter_tools_by_research_focus(TOOLS))}", flush=True)
    print(f"SYSTEM_PROMPT 长度: {len(SYSTEM_PROMPT):,} chars\n", flush=True)

    records: list[dict] = []
    for i, case in enumerate(selected, 1):
        print(f"\n=== [{i}/{len(selected)}] {case['id']} ===")
        rec = await run_one_case(case, api_key, out_dir, provider=args.provider)
        records.append(rec)

    write_summary(records, out_dir)
    print(f"\n全部完成。共 {len(records)} 个 case。")


if __name__ == "__main__":
    asyncio.run(main())
