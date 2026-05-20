"""N'-1: 后台数据统计 endpoints (供桌面 admin HTML 调).

设计跟现有 /api/admin/events/stats + /api/admin/inference/stats 互补:
- /kpi:        汇总卡片 (event_total / unique_users / inference_calls /
              inference_cost / comments_visible)
- /by-tool:   AI 工具使用排行 (从 user_events.event_type='ai.tool_called'
              的 event_data.tool_name 聚合)  ← 这是"方向"维度的核心
- /by-page:   按 page 字段聚合
- /timeline:  时间桶 × event_type, 看趋势 (period<=3d 用 hour, 其他 day)
- /comments:  评论增长 + 可见/隐藏统计

所有 endpoint 用 require_admin_any (X-Admin-Secret 优先, JWT+ADMIN_USERNAMES
旁路), 跟桌面 HTML 工具共用一套 auth.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.auth import require_admin_any
from app.models.database import get_db
from app.models.schemas import Comment, InferenceLog, UserEvent

router = APIRouter(prefix="/api/admin/stats", tags=["admin-stats"])


def _parse_period(period: str) -> timedelta:
    """支持 'Nd' / 'Nh' (e.g. '24h', '7d', '30d', '90d')."""
    p = period.strip().lower()
    if p.endswith("d"):
        return timedelta(days=int(p[:-1]))
    if p.endswith("h"):
        return timedelta(hours=int(p[:-1]))
    raise HTTPException(status_code=400, detail=f"unsupported period: {period}")


# ── 1. KPI: 顶部汇总卡片 ─────────────────────────────────────────────

@router.get("/kpi")
async def kpi(
    period: str = Query("7d"),
    _admin: None = Depends(require_admin_any),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - _parse_period(period)

    # 事件总数
    event_total = (
        await db.execute(
            select(func.count(UserEvent.id)).where(UserEvent.timestamp >= cutoff)
        )
    ).scalar_one() or 0

    # 活跃用户 (这段时间至少 1 个 event 的不同 user_id)
    unique_users = (
        await db.execute(
            select(func.count(func.distinct(UserEvent.user_id)))
            .where(UserEvent.timestamp >= cutoff, UserEvent.user_id.is_not(None))
        )
    ).scalar_one() or 0

    # AI 推理总数 + 总成本
    inf_count = (
        await db.execute(
            select(func.count(InferenceLog.id)).where(InferenceLog.timestamp >= cutoff)
        )
    ).scalar_one() or 0
    inf_cost = (
        await db.execute(
            select(func.coalesce(func.sum(InferenceLog.cost_usd), 0.0))
            .where(InferenceLog.timestamp >= cutoff)
        )
    ).scalar_one() or 0.0

    # 评论 (可见 + 总, 不限 period 看历史; period 内增量另外算)
    comments_visible = (
        await db.execute(
            select(func.count(Comment.id)).where(Comment.is_visible == True)  # noqa: E712
        )
    ).scalar_one() or 0
    comments_total = (
        await db.execute(select(func.count(Comment.id)))
    ).scalar_one() or 0
    comments_in_period = (
        await db.execute(
            select(func.count(Comment.id)).where(Comment.created_at >= cutoff)
        )
    ).scalar_one() or 0

    return {
        "period": period,
        "start_time": cutoff.isoformat(),
        "event_total": int(event_total),
        "unique_users": int(unique_users),
        "inference_calls": int(inf_count),
        "inference_cost_usd": round(float(inf_cost), 4),
        "comments_visible": int(comments_visible),
        "comments_total": int(comments_total),
        "comments_in_period": int(comments_in_period),
    }


# ── 2. by-tool: AI 工具调用排行 (方向维度) ───────────────────────────

@router.get("/by-tool")
async def by_tool(
    period: str = Query("7d"),
    _admin: None = Depends(require_admin_any),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """从 ai.tool_called 事件的 event_data.tool_name 聚合.

    用 Python 端 Counter 而不是 SQL json_extract — 跨方言简单, 几万行
    user_events 在内存里聚合 < 100 ms.
    """
    cutoff = datetime.now(timezone.utc) - _parse_period(period)

    rows = (
        await db.execute(
            select(UserEvent.event_data, UserEvent.timestamp)
            .where(UserEvent.timestamp >= cutoff)
            .where(UserEvent.event_type == "ai.tool_called")
        )
    ).all()

    counter: Counter[str] = Counter()
    last_used: dict[str, datetime] = {}
    for ed, ts in rows:
        if not isinstance(ed, dict):
            continue
        tool_name = ed.get("tool_name") or ed.get("tool") or "unknown"
        tool_name = str(tool_name)[:80]
        counter[tool_name] += 1
        if tool_name not in last_used or ts > last_used[tool_name]:
            last_used[tool_name] = ts

    items = [
        {
            "tool_name": name,
            "count": cnt,
            "last_used_at": last_used[name].isoformat() if last_used.get(name) else None,
        }
        for name, cnt in counter.most_common(limit)
    ]
    return {"period": period, "items": items, "total_tools_seen": len(counter)}


# ── 2b. telemetry/tool_usage: enriched dump for M2 第二轮砍工具决策 ────


@router.get("/telemetry/tool_usage")
async def telemetry_tool_usage(
    period: str = Query("7d"),
    limit: int = Query(100, ge=1, le=200),
    _admin: None = Depends(require_admin_any),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Stage 6 P0c-F (2026-05-19): tool 使用率 telemetry dump.

    跟 by-tool 同源 (user_events.ai.tool_called 聚合), 但额外加:
      - 总调用数 (total_calls)
      - 每工具占比 (pct_of_total)
      - 低使用标记 (low_usage: pct < 0.5%) — 明天用户用这个决策 M2 第二轮
        砍哪些工具

    Default period=7d, limit=100 — 一次性 dump 全部工具, 让用户能
    `curl <prod>/api/admin/stats/telemetry/tool_usage | jq` 看 JSON 分布.
    """
    cutoff = datetime.now(timezone.utc) - _parse_period(period)
    rows = (
        await db.execute(
            select(UserEvent.event_data, UserEvent.timestamp)
            .where(UserEvent.timestamp >= cutoff)
            .where(UserEvent.event_type == "ai.tool_called")
        )
    ).all()

    counter: Counter[str] = Counter()
    last_used: dict[str, datetime] = {}
    # 2026-05-20: 加 per-tool input keys breakdown. 让用户能看 fit_line_lfr
    # 是不是真用 arxiv_id 入口 (跟 prompt.md 默认规则对齐), 还是 LLM 仍习惯
    # cache_key 两步路径.
    input_keys_per_tool: dict[str, Counter[str]] = {}
    for ed, ts in rows:
        if not isinstance(ed, dict):
            continue
        tool_name = ed.get("tool_name") or ed.get("tool") or "unknown"
        tool_name = str(tool_name)[:80]
        counter[tool_name] += 1
        if tool_name not in last_used or ts > last_used[tool_name]:
            last_used[tool_name] = ts
        input_keys = ed.get("input_keys")
        if isinstance(input_keys, list):
            key_signature = "+".join(sorted(str(k) for k in input_keys)) or "(empty)"
            input_keys_per_tool.setdefault(tool_name, Counter())[key_signature] += 1

    total_calls = sum(counter.values())
    low_usage_pct_threshold = 0.5  # 占比 < 0.5% 视为低使用, 候选砍

    items = []
    for name, cnt in counter.most_common(limit):
        pct = (cnt / total_calls * 100) if total_calls else 0.0
        input_breakdown = []
        per_tool = input_keys_per_tool.get(name)
        if per_tool:
            for sig, k_cnt in per_tool.most_common(5):
                input_breakdown.append({
                    "input_keys": sig,
                    "count": k_cnt,
                    "pct_of_tool": round((k_cnt / cnt * 100) if cnt else 0.0, 1),
                })
        items.append({
            "tool_name": name,
            "count": cnt,
            "pct_of_total": round(pct, 3),
            "low_usage": pct <= low_usage_pct_threshold,
            "last_used_at": last_used[name].isoformat() if last_used.get(name) else None,
            "input_keys_top5": input_breakdown,
        })
    low_usage_tools = [item["tool_name"] for item in items if item["low_usage"]]

    return {
        "period": period,
        "start_time": cutoff.isoformat(),
        "total_calls": total_calls,
        "total_tools_seen": len(counter),
        "low_usage_pct_threshold": low_usage_pct_threshold,
        "low_usage_tools": low_usage_tools,
        "items": items,
    }


# ── 3. by-page: 按页面访问量 ─────────────────────────────────────────

@router.get("/by-page")
async def by_page(
    period: str = Query("7d"),
    _admin: None = Depends(require_admin_any),
    limit: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - _parse_period(period)

    rows = (
        await db.execute(
            select(
                UserEvent.page,
                func.count(UserEvent.id).label("count"),
                func.avg(UserEvent.duration_ms).label("avg_dur"),
            )
            .where(UserEvent.timestamp >= cutoff, UserEvent.page.is_not(None))
            .group_by(UserEvent.page)
            .order_by(func.count(UserEvent.id).desc())
            .limit(limit)
        )
    ).all()

    return {
        "period": period,
        "items": [
            {
                "page": r.page,
                "count": int(r.count),
                "avg_duration_ms": float(r.avg_dur) if r.avg_dur is not None else None,
            }
            for r in rows
        ],
    }


# ── 4. timeline: 时间桶 × event_type ─────────────────────────────────

@router.get("/timeline")
async def timeline(
    period: str = Query("7d"),
    _admin: None = Depends(require_admin_any),
    bucket: str = Query("auto"),  # auto | hour | day
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """时间桶聚合, 返回 [{ts, counts: {event_type: n, ...}}]"""
    delta = _parse_period(period)
    cutoff = datetime.now(timezone.utc) - delta

    if bucket == "auto":
        bucket = "hour" if delta <= timedelta(days=3) else "day"
    if bucket not in ("hour", "day"):
        raise HTTPException(status_code=400, detail="bucket must be hour|day|auto")

    rows = (
        await db.execute(
            select(UserEvent.event_type, UserEvent.timestamp)
            .where(UserEvent.timestamp >= cutoff)
        )
    ).all()

    fmt = "%Y-%m-%dT%H:00:00" if bucket == "hour" else "%Y-%m-%d"
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for et, ts in rows:
        if ts is None:
            continue
        key = ts.strftime(fmt)
        buckets[key][et or "unknown"] += 1

    series = sorted(
        [
            {"ts": ts_key, "counts": dict(type_counts)}
            for ts_key, type_counts in buckets.items()
        ],
        key=lambda x: x["ts"],
    )
    # 找 top-N event_type 让前端渲染 (避免几十种类堆叠图眼花)
    overall = Counter()
    for tc in buckets.values():
        for et, cnt in tc.items():
            overall[et] += cnt
    top_types = [t for t, _ in overall.most_common(10)]

    return {
        "period": period,
        "bucket": bucket,
        "series": series,
        "top_event_types": top_types,
    }


# ── 5. comments: 评论统计 + 趋势 ─────────────────────────────────────

@router.get("/comments")
async def comments_stats(
    period: str = Query("30d"),
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - _parse_period(period)

    total_visible = (
        await db.execute(
            select(func.count(Comment.id)).where(Comment.is_visible == True)  # noqa: E712
        )
    ).scalar_one() or 0
    total_hidden = (
        await db.execute(
            select(func.count(Comment.id)).where(Comment.is_visible == False)  # noqa: E712
        )
    ).scalar_one() or 0

    # 按日 count
    rows = (
        await db.execute(
            select(Comment.created_at, Comment.is_visible)
            .where(Comment.created_at >= cutoff)
        )
    ).all()
    per_day: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "visible": 0})
    for created_at, is_visible in rows:
        if not created_at:
            continue
        d = created_at.strftime("%Y-%m-%d")
        per_day[d]["total"] += 1
        if is_visible:
            per_day[d]["visible"] += 1

    series = sorted(
        [{"date": d, **counts} for d, counts in per_day.items()],
        key=lambda x: x["date"],
    )

    return {
        "period": period,
        "total_visible": int(total_visible),
        "total_hidden": int(total_hidden),
        "per_day": series,
    }
