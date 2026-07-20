"""Pipeline scheduler API — create, list, delete scheduled pipeline runs."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.models.database import get_db
from app.models.schemas import ScheduledRun, User

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class ScheduleRequest(BaseModel):
    name: str
    dag: dict
    input_data_id: str
    cron_expr: str  # simplified cron: "every_hour", "every_6h", "daily", "weekly", or standard cron


class ScheduleResponse(BaseModel):
    id: str
    name: str
    cron_expr: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    created_at: str | None


# Simplified cron presets
CRON_PRESETS = {
    "every_hour": "0 * * * *",
    "every_6h": "0 */6 * * *",
    "daily": "0 0 * * *",
    "weekly": "0 0 * * 1",
    "every_30m": "*/30 * * * *",
}


def _require_scheduled_pipeline_executor() -> None:
    if (
        settings.pipeline_mode != "celery"
        or settings.science_execution_backend != "celery"
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "Scheduled arbitrary pipelines are unavailable in this "
                "deployment; use a registered science workflow."
            ),
        )


def _compute_next_run(cron_expr: str) -> datetime | None:
    """Simple next-run computation. For production, use croniter."""
    try:
        from croniter import croniter
        cron = croniter(cron_expr, datetime.now(timezone.utc))
        return cron.get_next(datetime)
    except ImportError:
        # Without croniter, just return 1 hour from now as default
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(hours=1)


@router.post("/schedules", response_model=ScheduleResponse)
async def create_schedule(
    req: ScheduleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a new scheduled pipeline run."""
    _require_scheduled_pipeline_executor()

    from app.api.pipeline import _bind_owned_pipeline_inputs
    from app.pipeline.engine import topological_sort
    from app.pipeline.nodes import registry
    from app.pipeline.validate import DAGValidationError, validate_dag

    if "nodes" not in req.dag or "edges" not in req.dag:
        raise HTTPException(status_code=400, detail="DAG must have 'nodes' and 'edges'")
    try:
        validate_dag(req.dag)
        topological_sort(req.dag)
    except (DAGValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for node in req.dag.get("nodes", []):
        if node.get("type") not in registry:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown node type: {node.get('type')}",
            )

    bound_dag, bound_input_data_id = await _bind_owned_pipeline_inputs(
        dag=req.dag,
        input_data_id=req.input_data_id,
        user=user,
        db=db,
    )
    cron = CRON_PRESETS.get(req.cron_expr, req.cron_expr)

    # Basic cron validation
    parts = cron.split()
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {cron}")

    next_run = _compute_next_run(cron)

    schedule = ScheduledRun(
        user_id=user.id,
        name=req.name,
        dag=bound_dag,
        input_data_id=bound_input_data_id,
        cron_expr=cron,
        next_run_at=next_run,
    )
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)

    return ScheduleResponse(
        id=str(schedule.id),
        name=schedule.name,
        cron_expr=schedule.cron_expr,
        enabled=schedule.enabled,
        last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        created_at=schedule.created_at.isoformat() if schedule.created_at else None,
    )


@router.get("/schedules", response_model=list[ScheduleResponse])
async def list_schedules(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List user's scheduled runs."""
    result = await db.execute(
        select(ScheduledRun)
        .where(ScheduledRun.user_id == user.id)
        .order_by(ScheduledRun.created_at.desc())
    )
    schedules = result.scalars().all()
    return [
        ScheduleResponse(
            id=str(s.id),
            name=s.name,
            cron_expr=s.cron_expr,
            enabled=s.enabled,
            last_run_at=s.last_run_at.isoformat() if s.last_run_at else None,
            next_run_at=s.next_run_at.isoformat() if s.next_run_at else None,
            created_at=s.created_at.isoformat() if s.created_at else None,
        )
        for s in schedules
    ]


@router.patch("/schedules/{schedule_id}")
async def toggle_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Enable or disable a scheduled run."""
    sid = uuid.UUID(schedule_id)
    result = await db.execute(
        select(ScheduledRun).where(ScheduledRun.id == sid, ScheduledRun.user_id == user.id)
    )
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")

    if not schedule.enabled:
        _require_scheduled_pipeline_executor()
    schedule.enabled = not schedule.enabled
    if schedule.enabled:
        schedule.next_run_at = _compute_next_run(schedule.cron_expr)
    else:
        schedule.next_run_at = None
    await db.commit()

    return {"id": str(schedule.id), "enabled": schedule.enabled}


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(
    schedule_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Delete a scheduled run."""
    sid = uuid.UUID(schedule_id)
    await db.execute(
        delete(ScheduledRun).where(ScheduledRun.id == sid, ScheduledRun.user_id == user.id)
    )
    await db.commit()
    return {"deleted": True}
