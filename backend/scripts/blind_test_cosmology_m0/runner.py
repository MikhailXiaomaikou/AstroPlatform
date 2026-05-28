"""Cosmology M0 盲测 runner.

用法:
    cd backend
    source .venv/bin/activate
    export ASTRO_RESEARCH_FOCUS=cosmology
    export ANTHROPIC_API_KEY=sk-ant-...    # 或 export CLAUDE_MODEL=claude-sonnet-4-6
    python scripts/blind_test_cosmology_m0/runner.py              # 全跑 10 case
    python scripts/blind_test_cosmology_m0/runner.py --case A1    # 单跑一个 dry run
    python scripts/blind_test_cosmology_m0/runner.py --case A1,B2 # 跑指定子集

本地 Codex CLI 路径:
    export OPENAI_CLI_ENABLED=1
    export OPENAI_CLI_COMMAND=codex
    python scripts/blind_test_cosmology_m0/runner.py --provider local

每个 case 把完整 trace dump 到 scripts/blind_test_cosmology_m0/results_<timestamp>/case_<id>.json。
全跑结束后还会输出 summary.md。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

# ── 必须在 import chat 之前设 focus, SYSTEM_PROMPT 是 module-level 常量 ──
os.environ.setdefault("ASTRO_RESEARCH_FOCUS", "cosmology")

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
    if focus != "cosmology":
        raise SystemExit(f"ASTRO_RESEARCH_FOCUS 必须是 'cosmology', 现在是 {focus!r}")
    if provider == "local":
        if not os.environ.get("OPENAI_CLI_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            raise SystemExit(
                "local provider 需要 OPENAI_CLI_ENABLED=1。\n"
                "    export OPENAI_CLI_ENABLED=1\n"
                "    export OPENAI_CLI_COMMAND=codex"
            )
        return None
    if provider == "deepseek":
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise SystemExit(
                "缺 DEEPSEEK_API_KEY。\n"
                "    export DEEPSEEK_API_KEY=sk-...\n"
                "或在 backend/.env 里设,跑前先 `set -a; source .env; set +a`。"
            )
        return key
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
    elif provider == "deepseek":
        profile = resolve_model_profile("deepseek", "deepseek:v4-pro")
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
            provider_api_keys={provider: api_key} if api_key else {},
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


# ── Mechanical verdict (2026-05-28): runner now evaluates `checks` + `forbid`
# from cases.yaml. Group B/C = anti-fabrication = HARD (fail → exit 1);
# A/D/E = routing/quality = SOFT (reported, not gated). See cases.yaml header.

_HARD_GROUPS = {"B", "C"}


def _reply_text(record: dict) -> str:
    return str(record.get("reply") or "")


def _numeric_near(reply: str, labels, lo: float, hi: float) -> bool:
    """True if any number within ~60 chars after any label falls in [lo, hi]."""
    if isinstance(labels, str):
        labels = [labels]
    hay = reply.lower()
    for lab in labels:
        ll = str(lab).lower()
        start = 0
        while True:
            p = hay.find(ll, start)
            if p < 0:
                break
            window = reply[p : p + 60]
            for m in re.finditer(r"[-+]?\d+\.?\d*", window):
                try:
                    v = float(m.group())
                except ValueError:
                    continue
                if lo <= v <= hi:
                    return True
            start = p + 1
    return False


def _one_check(record: dict, spec: dict) -> tuple[str, bool]:
    reply = _reply_text(record)
    rl = reply.lower()
    tools = set(record.get("tools_called") or [])
    if "tools_all" in spec:
        want = set(spec["tools_all"])
        return (f"tools_all={sorted(want)}", want.issubset(tools))
    if "tools_any" in spec:
        want = set(spec["tools_any"])
        return (f"tools_any={sorted(want)}", bool(want & tools))
    if "reply_contains_all" in spec:
        terms = spec["reply_contains_all"]
        return (f"contains_all={terms}", all(str(t).lower() in rl for t in terms))
    if "reply_contains_any" in spec:
        terms = spec["reply_contains_any"]
        return (f"contains_any={terms}", any(str(t).lower() in rl for t in terms))
    if "reply_numeric_near" in spec:
        s = spec["reply_numeric_near"]
        ok = _numeric_near(reply, s["label"], float(s["min"]), float(s["max"]))
        return (f"numeric_near({s['label']} in [{s['min']},{s['max']}])", ok)
    if "tool_result_status" in spec:
        s = spec["tool_result_status"]
        ok = False
        for e in record.get("events", []):
            if e.get("type") == "tool_result" and e.get("tool") == s["tool"]:
                r = e.get("result")
                if isinstance(r, dict) and str(r.get(s["key"])) == str(s["equals"]):
                    ok = True
                    break
        return (f"tool_status({s['tool']}.{s['key']}=={s['equals']})", ok)
    return (f"UNKNOWN_CHECK={spec}", False)


def evaluate_case(record: dict, case: dict) -> dict:
    """Mechanical verdict for one case. ``record`` supplies reply/events/
    tools_called; ``case`` supplies checks/forbid/group. Works live and
    offline against a previously-dumped case_<id>.json.

    Three-state verdict: PASS / SOFT-FAIL / HARD-FAIL. Only HARD-FAIL gates
    CI. A check is HARD only in an anti-fabrication group (B/C) AND when not
    marked ``soft: true``; forbid hits are hard in B/C. Group A/D/E
    (routing/quality) never hard-fails — drift is reported, not gated. The
    distinction matters: B2's "did it explicitly debunk the fake bibcode"
    is a quality nicety (soft), but "did it restate the fake 71.4" (forbid)
    is the real anti-fabrication line (hard)."""
    group = str(case.get("group") or "")
    is_hard_group = group in _HARD_GROUPS
    checks = case.get("checks") or []
    forbid = case.get("forbid") or []
    reply = _reply_text(record)
    check_results = []  # (desc, ok, soft)
    for c in checks:
        desc, ok = _one_check(record, c)
        soft = bool(c.get("soft")) or (not is_hard_group)
        check_results.append((desc, ok, soft))
    forbid_results = [(term, str(term) in reply) for term in forbid]
    forbid_hit = any(h for _, h in forbid_results)
    hard_check_fail = any((not ok) and (not soft) for _, ok, soft in check_results)
    any_fail = forbid_hit or any(not ok for _, ok, _ in check_results)
    hard_failed = is_hard_group and (forbid_hit or hard_check_fail)
    if hard_failed:
        verdict = "HARD-FAIL"
    elif any_fail:
        verdict = "SOFT-FAIL"
    else:
        verdict = "PASS"
    n_manual = max(0, len(case.get("expect_pass") or []) - len(checks))
    return {
        "case_id": case.get("id"),
        "group": group,
        "hard": is_hard_group,
        "failed": any_fail,
        "hard_failed": hard_failed,
        "verdict": verdict,
        "check_results": check_results,
        "forbid_results": forbid_results,
        "manual_count": n_manual,
    }


def write_summary(records: list[dict], out_dir: Path, cases: list[dict]) -> list[dict]:
    case_by_id = {c["id"]: c for c in cases}
    verdicts = [evaluate_case(r, case_by_id.get(r["case_id"], {})) for r in records]
    vmap = {v["case_id"]: v for v in verdicts}
    hard_fail = [v for v in verdicts if v["hard_failed"]]
    soft_fail = [v for v in verdicts if v["failed"] and not v["hard_failed"]]

    lines = ["# Cosmology M0 盲测结果\n"]
    lines.append(f"- 跑完时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Case 总数: {len(records)}")
    n_err = sum(1 for r in records if r.get("error"))
    lines.append(f"- 异常 (LLM/系统挂掉): {n_err}")
    avg_time = sum(r["elapsed_seconds"] for r in records) / max(len(records), 1)
    lines.append(f"- 平均耗时: {avg_time:.1f}s/case")
    lines.append(
        "- 反幻造硬门禁 (B/C): "
        + ("❌ " + ", ".join(v["case_id"] for v in hard_fail) if hard_fail else "✅ 全过")
    )
    lines.append(
        "- 路由软报告 (A/D/E) 未达期望: "
        + (", ".join(v["case_id"] for v in soft_fail) if soft_fail else "无")
        + "\n"
    )

    lines.append("## 机械判定一览\n")
    lines.append("| ID | group | 硬/软 | verdict | 失败明细 | 待人工核 |")
    lines.append("|---|---|---|---|---|---|")
    for r in records:
        v = vmap[r["case_id"]]
        kind = "硬" if v["hard"] else "软"
        fails = [
            f"{'软' if soft else '硬'}check未过:{desc}"
            for desc, ok, soft in v["check_results"]
            if not ok
        ]
        fails += [f"forbid命中:{term!r}" for term, hit in v["forbid_results"] if hit]
        detail = "; ".join(fails) if fails else "—"
        lines.append(
            f"| {r['case_id']} | {v['group']} | {kind} | {v['verdict']} | {detail} | {v['manual_count']} |"
        )

    lines.append("\n## 工具调用一览\n")
    lines.append("| ID | tools called | n_tools | time | error |")
    lines.append("|---|---|---|---|---|")
    for r in records:
        tools = ",".join(r.get("tools_called") or []) or "—"
        err = "✗" if r.get("error") else ""
        lines.append(
            f"| {r['case_id']} | {tools} | {r['n_tool_calls']} | {r['elapsed_seconds']}s | {err} |"
        )

    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nsummary 写到: {out_dir / 'summary.md'}", flush=True)
    return verdicts


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", help="只跑指定 case (逗号分隔, 如 A1 或 A1,B2)")
    parser.add_argument("--group", help="只跑指定 group (A/B/C/D/E)")
    parser.add_argument(
        "--provider",
        choices=["anthropic", "local", "deepseek"],
        default="anthropic",
        help="LLM backend: anthropic needs ANTHROPIC_API_KEY; deepseek needs DEEPSEEK_API_KEY; local uses the OpenAI-compatible local server.",
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
    print(f"工具数 (focus=cosmology): {len(_filter_tools_by_research_focus(TOOLS))}", flush=True)
    print(f"SYSTEM_PROMPT 长度: {len(SYSTEM_PROMPT):,} chars\n", flush=True)

    records: list[dict] = []
    for i, case in enumerate(selected, 1):
        print(f"\n=== [{i}/{len(selected)}] {case['id']} ===")
        rec = await run_one_case(case, api_key, out_dir, provider=args.provider)
        records.append(rec)

    verdicts = write_summary(records, out_dir, selected)
    hard_fail = [v for v in verdicts if v["hard_failed"]]
    soft_fail = [v for v in verdicts if v["failed"] and not v["hard_failed"]]
    print(f"\n全部完成。共 {len(records)} 个 case。")
    print(f"判定: 硬门禁失败(B/C)={len(hard_fail)}  软报告未达(A/D/E)={len(soft_fail)}")
    if hard_fail:
        ids = ", ".join(v["case_id"] for v in hard_fail)
        print(f"❌ 反幻造硬门禁失败: {ids} → exit 1")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
