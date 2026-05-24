"""Admin endpoints for inference routing and cost tracking."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.inference_router import inference_router
from app.api.auth import require_admin_any
from app.models.database import get_db
from app.models.schemas import InferenceLog, User

router = APIRouter(prefix="/api/admin/inference", tags=["admin-inference"])


class InferenceConfigUpdate(BaseModel):
    routing: dict[str, str]


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _shared_deepseek_available() -> bool:
    flag = os.getenv("SHARED_DEEPSEEK_API_KEY_ENABLED")
    try:
        from app.config import settings

        settings_enabled = bool(getattr(settings, "shared_deepseek_api_key_enabled", True))
        settings_key = (
            str(getattr(settings, "platform_deepseek_api_key", "") or "").strip()
            or str(getattr(settings, "deepseek_api_key", "") or "").strip()
        )
    except Exception:
        settings_enabled = True
        settings_key = ""
    if flag is not None and flag.strip().lower() in {"0", "false", "no", "off"}:
        return False
    if flag is None and not settings_enabled:
        return False
    return bool(
        os.getenv("PLATFORM_DEEPSEEK_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or settings_key
    )


def _require_admin(user: User) -> None:
    admin_usernames = {name.strip() for name in os.getenv("ADMIN_USERNAMES", "").split(",") if name.strip()}
    if user.username in admin_usernames or user.subscription_tier in {"admin", "institution"}:
        return
    raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/stats")
async def get_inference_stats(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin_any),
):
    now = datetime.now(timezone.utc)
    windows = {
        "today": now - timedelta(days=1),
        "week": now - timedelta(days=7),
        "month": now - timedelta(days=30),
    }
    totals = {}
    for label, cutoff in windows.items():
        rows = (
            await db.execute(
                select(InferenceLog).where(InferenceLog.timestamp >= cutoff)
            )
        ).scalars().all()
        total_count = len(rows)
        failure_count = sum(1 for row in rows if not row.success)
        avg_latency = (sum((row.latency_ms or 0) for row in rows) / total_count) if total_count else 0.0
        total_cost = sum(float(row.cost_usd or 0.0) for row in rows)
        input_tokens = sum(int(row.input_tokens or 0) for row in rows)
        output_tokens = sum(int(row.output_tokens or 0) for row in rows)
        totals[label] = {
            "cost_usd": total_cost,
            "avg_latency_ms": avg_latency,
            "failure_rate": (failure_count / total_count) if total_count else 0.0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    by_agent = (
        await db.execute(
            select(
                InferenceLog.agent_name,
                func.count(InferenceLog.id),
                func.coalesce(func.sum(InferenceLog.cost_usd), 0.0),
            ).group_by(InferenceLog.agent_name)
        )
    ).all()
    by_backend = (
        await db.execute(
            select(
                InferenceLog.backend_name,
                func.count(InferenceLog.id),
                func.coalesce(func.sum(InferenceLog.cost_usd), 0.0),
                func.coalesce(func.avg(InferenceLog.latency_ms), 0.0),
                func.coalesce(func.sum(InferenceLog.input_tokens), 0),
                func.coalesce(func.sum(InferenceLog.output_tokens), 0),
            ).group_by(InferenceLog.backend_name)
        )
    ).all()
    recent_logs = (
        await db.execute(
            select(InferenceLog)
            .order_by(InferenceLog.timestamp.desc())
            .limit(200)
        )
    ).scalars().all()
    trends: dict[str, dict[str, float]] = {}
    for row in recent_logs:
        bucket = row.timestamp.strftime("%Y-%m-%d") if row.timestamp else "unknown"
        entry = trends.setdefault(bucket, {"count": 0.0, "input_tokens": 0.0, "output_tokens": 0.0, "cost_usd": 0.0})
        entry["count"] += 1
        entry["input_tokens"] += float(row.input_tokens or 0)
        entry["output_tokens"] += float(row.output_tokens or 0)
        entry["cost_usd"] += float(row.cost_usd or 0.0)

    return {
        "totals": totals,
        "cost_per_agent": [{"agent_name": row[0], "count": int(row[1]), "cost_usd": float(row[2] or 0.0)} for row in by_agent],
        "cost_per_backend": [{
            "backend_name": row[0],
            "count": int(row[1]),
            "cost_usd": float(row[2] or 0.0),
            "avg_latency_ms": float(row[3] or 0.0),
            "input_tokens": int(row[4] or 0),
            "output_tokens": int(row[5] or 0),
        } for row in by_backend],
        "token_usage_trends": [
            {"date": key, **value}
            for key, value in sorted(trends.items())
        ],
    }


@router.get("/config")
async def get_inference_config(_: None = Depends(require_admin_any)):
    return {
        "routing": inference_router.agent_routing,
        "fallback_chain": inference_router.fallback_chain,
    }


@router.put("/config")
async def update_inference_config(
    req: InferenceConfigUpdate,
    _: None = Depends(require_admin_any),
):
    for agent_name, backend_name in req.routing.items():
        if backend_name not in inference_router.backends:
            raise HTTPException(status_code=400, detail=f"Unknown backend: {backend_name}")
        inference_router.agent_routing[agent_name] = backend_name
    return {"saved": True, "routing": inference_router.agent_routing}


@router.get("/health")
async def inference_health(_: None = Depends(require_admin_any)):
    health = []
    for name, backend in inference_router.backends.items():
        configured = True
        message = "configured"
        if name == "claude" and not os.getenv("ANTHROPIC_API_KEY", ""):
            configured = False
            message = "ANTHROPIC_API_KEY missing"
        if name == "openai" and not os.getenv("OPENAI_API_KEY", ""):
            configured = False
            message = "OPENAI_API_KEY missing"
        if name == "deepseek" and not _shared_deepseek_available():
            configured = False
            message = "DEEPSEEK_API_KEY or PLATFORM_DEEPSEEK_API_KEY missing"
        if name == "local" and not (_env_truthy("LOCAL_MODEL_ENABLED") or _env_truthy("OPENAI_CLI_ENABLED")):
            configured = False
            message = "LOCAL_MODEL_ENABLED or OPENAI_CLI_ENABLED not set"
        health.append({
            "backend": name,
            "model": backend.model_name(),
            "configured": configured,
            "message": message,
            "max_context_length": backend.max_context_length(),
        })
    return {"backends": health}
