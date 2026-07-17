"""Research-trend stats + admin visibility toggle + public endpoint.

3 admin endpoints + 1 public endpoint + 1 toggle config:
- GET  /api/admin/trending/objects?period=7d      trending objects (clicked/viewed)
- GET  /api/admin/trending/sources?period=7d      trending data sources (search.query.databases)
- GET  /api/admin/trending/delta?period=7d        top-N week-over-week risers
- GET  /api/admin/trending/visibility             current public-toggle state
- POST /api/admin/trending/visibility             update toggle (body: {key, is_public})
- GET  /api/trending/public                       no auth required; returns data per toggle state

In-memory 5-minute cache (keyed by key+period) to avoid re-aggregating
tens of thousands of rows every time admin opens the page.
Delta threshold: current-week count >= 10 to appear (prevents false rises
from a low baseline).
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_admin_any
from app.config import settings
from app.models.database import get_db
from app.models.schemas import TrendingVisibility, UserEvent

admin_router = APIRouter(prefix="/api/admin/trending", tags=["admin-trending"])
public_router = APIRouter(prefix="/api/trending", tags=["trending"])


# ── cache ────────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

def _cache_get(key: str) -> Any | None:
    item = _CACHE.get(key)
    if item is None:
        return None
    ts, value = item
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return None
    return value

def _cache_set(key: str, value: Any) -> None:
    _CACHE[key] = (time.time(), value)


def _parse_period(period: str) -> timedelta:
    p = period.strip().lower()
    if p.endswith("d"):
        delta = timedelta(days=int(p[:-1]))
        return min(delta, timedelta(days=settings.product_analytics_retention_days))
    if p.endswith("h"):
        delta = timedelta(hours=int(p[:-1]))
        return min(delta, timedelta(days=settings.product_analytics_retention_days))
    raise HTTPException(status_code=400, detail=f"bad period: {period}")


# ── data-extraction helpers ───────────────────────────────────────────────

# event_types whose event_data carries object_name (user-confirmed "clicked/viewed")
_OBJECT_NAME_EVENTS = {"search.result_click", "object.viewed"}


async def _fetch_object_counts(
    db: AsyncSession,
    cutoff: datetime,
    end: datetime | None = None,
) -> Counter[str]:
    """Aggregate object_name from result_click / object.viewed event_data."""
    q = (
        select(UserEvent.event_data)
        .where(UserEvent.timestamp >= cutoff)
        .where(UserEvent.event_type.in_(list(_OBJECT_NAME_EVENTS)))
    )
    if end is not None:
        q = q.where(UserEvent.timestamp < end)
    rows = (await db.execute(q)).scalars().all()

    counter: Counter[str] = Counter()
    for ed in rows:
        if not isinstance(ed, dict):
            continue
        name = ed.get("object_name")
        if not name:
            continue
        name = str(name).strip()
        if not name or len(name) > 80:
            continue
        counter[name] += 1
    return counter


async def _fetch_source_counts(
    db: AsyncSession,
    cutoff: datetime,
    end: datetime | None = None,
) -> Counter[str]:
    """Aggregate databases from search.query event_data.databases (list).
    Also accumulates event_data.service for ai.tool_called events where tool_name='run_adql'."""
    q = select(UserEvent.event_type, UserEvent.event_data).where(UserEvent.timestamp >= cutoff)
    if end is not None:
        q = q.where(UserEvent.timestamp < end)
    rows = (await db.execute(q)).all()

    counter: Counter[str] = Counter()
    for etype, ed in rows:
        if not isinstance(ed, dict):
            continue
        if etype == "search.query":
            dbs = ed.get("databases")
            if isinstance(dbs, list):
                for d in dbs:
                    if d:
                        counter[str(d).lower().strip()[:32]] += 1
        elif etype == "ai.tool_called":
            tool_name = ed.get("tool_name")
            if tool_name == "run_adql":
                service = ed.get("service") or ed.get("service_name")
                if service:
                    counter[str(service).lower().strip()[:32]] += 1
        elif etype == "search.adql":
            service = ed.get("service") or ed.get("service_name")
            if service:
                counter[str(service).lower().strip()[:32]] += 1
    return counter


# ── 1. Admin trending objects ─────────────────────────────────────

@admin_router.get("/objects")
async def trending_objects(
    period: str = Query("7d"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    cache_key = f"objects:{period}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = datetime.now(timezone.utc) - _parse_period(period)
    counter = await _fetch_object_counts(db, cutoff)

    result = {
        "period": period,
        "items": [
            {"object_name": name, "count": cnt}
            for name, cnt in counter.most_common(limit)
        ],
        "distinct_objects": len(counter),
        "total_events": sum(counter.values()),
    }
    _cache_set(cache_key, result)
    return result


# ── 2. Admin trending sources ─────────────────────────────────────

@admin_router.get("/sources")
async def trending_sources(
    period: str = Query("7d"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    cache_key = f"sources:{period}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = datetime.now(timezone.utc) - _parse_period(period)
    counter = await _fetch_source_counts(db, cutoff)

    result = {
        "period": period,
        "items": [
            {"source": name, "count": cnt}
            for name, cnt in counter.most_common(limit)
        ],
        "distinct_sources": len(counter),
        "total_events": sum(counter.values()),
    }
    _cache_set(cache_key, result)
    return result


# ── 3. Admin trending delta (prev-period vs this-period risers) ───────────────────

@admin_router.get("/delta")
async def trending_delta(
    period: str = Query("7d"),
    limit: int = Query(5, ge=1, le=30),
    min_count: int = Query(10, ge=1, le=1000),  # minimum this-period count threshold
    dimension: str = Query("objects"),  # objects | sources
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    """Top-N risers comparing this period against the previous period."""
    if dimension not in ("objects", "sources"):
        raise HTTPException(status_code=400, detail="dimension must be 'objects' or 'sources'")

    cache_key = f"delta:{dimension}:{period}:{limit}:{min_count}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    delta = _parse_period(period)
    now = datetime.now(timezone.utc)
    this_start = now - delta
    prev_start = now - 2 * delta

    fetcher = _fetch_object_counts if dimension == "objects" else _fetch_source_counts
    this_counts = await fetcher(db, this_start, end=now)
    prev_counts = await fetcher(db, prev_start, end=this_start)

    # Compute delta: (this - prev) / max(prev, 1), but only include entries with this_n >= min_count.
    rows = []
    for key, this_n in this_counts.items():
        if this_n < min_count:
            continue
        prev_n = prev_counts.get(key, 0)
        raw_delta = this_n - prev_n
        pct = (raw_delta / prev_n * 100.0) if prev_n > 0 else float("inf")
        rows.append({
            "key": key,
            "this_period": this_n,
            "prev_period": prev_n,
            "delta": raw_delta,
            "pct_change": pct if pct != float("inf") else None,  # None = newly appeared
            "is_new": prev_n == 0,
        })

    # Sort: newly appeared (is_new) first (descending by this_period), then by pct_change descending.
    rows.sort(
        key=lambda r: (0 if r["is_new"] else 1, -(r["pct_change"] or 0), -r["this_period"]),
    )

    result = {
        "period": period,
        "dimension": dimension,
        "min_count_threshold": min_count,
        "items": rows[:limit],
    }
    _cache_set(cache_key, result)
    return result


# ── 4. Visibility toggle ────────────────────────────────────────────

class VisibilityRequest(BaseModel):
    key: str   # "objects" | "sources" | "delta"
    is_public: bool


_VALID_KEYS = {"objects", "sources", "delta"}


async def _ensure_default_row(db: AsyncSession, key: str) -> TrendingVisibility:
    """Insert a default is_public=False row if none exists."""
    row = (await db.execute(
        select(TrendingVisibility).where(TrendingVisibility.key == key)
    )).scalar_one_or_none()
    if row is None:
        row = TrendingVisibility(key=key, is_public=False)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@admin_router.get("/visibility")
async def get_visibility(
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    out = {}
    for key in _VALID_KEYS:
        row = await _ensure_default_row(db, key)
        out[key] = bool(row.is_public)
    return {"visibility": out}


@admin_router.post("/visibility")
async def set_visibility(
    req: VisibilityRequest,
    db: AsyncSession = Depends(get_db),
    _admin: None = Depends(require_admin_any),
) -> dict[str, Any]:
    if req.key not in _VALID_KEYS:
        raise HTTPException(status_code=400, detail=f"key must be in {_VALID_KEYS}")
    row = await _ensure_default_row(db, req.key)
    row.is_public = bool(req.is_public)
    await db.commit()
    return {"key": req.key, "is_public": bool(req.is_public)}


# ── 5. public endpoint ──────────────────────────────────────────────

@public_router.get("/public")
async def public_trending(
    period: str = Query("7d"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return only the sections enabled by the admin toggle; disabled sections are omitted. No auth required."""
    visibility = {}
    for key in _VALID_KEYS:
        row = (await db.execute(
            select(TrendingVisibility).where(TrendingVisibility.key == key)
        )).scalar_one_or_none()
        visibility[key] = bool(row.is_public) if row else False

    out: dict[str, Any] = {"period": period, "visibility": visibility}

    cutoff = datetime.now(timezone.utc) - _parse_period(period)

    if visibility.get("objects"):
        objs = await _fetch_object_counts(db, cutoff)
        out["objects"] = [
            {"object_name": n, "count": c} for n, c in objs.most_common(20)
        ]
    if visibility.get("sources"):
        srcs = await _fetch_source_counts(db, cutoff)
        out["sources"] = [
            {"source": n, "count": c} for n, c in srcs.most_common(20)
        ]
    if visibility.get("delta"):
        # Expose only the objects dimension publicly (sources dimension is too technical for general audiences); apply the >=10 filter consistently.
        delta_t = _parse_period(period)
        now = datetime.now(timezone.utc)
        this_counts = await _fetch_object_counts(db, now - delta_t, end=now)
        prev_counts = await _fetch_object_counts(db, now - 2 * delta_t, end=now - delta_t)
        items = []
        for k, this_n in this_counts.items():
            if this_n < 10:
                continue
            prev_n = prev_counts.get(k, 0)
            pct = (this_n - prev_n) / prev_n * 100.0 if prev_n > 0 else None
            items.append({
                "key": k, "this_period": this_n, "prev_period": prev_n,
                "pct_change": pct, "is_new": prev_n == 0,
            })
        items.sort(
            key=lambda r: (0 if r["is_new"] else 1, -(r["pct_change"] or 0), -r["this_period"]),
        )
        out["delta"] = items[:5]

    return out
