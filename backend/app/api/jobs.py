"""Owner-scoped lifecycle API for long-running scientific jobs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.models.database import get_db
from app.models.research_records import ResearchJob
from app.models.schemas import User

router = APIRouter(prefix="/api/jobs", tags=["research-jobs"])


def _serialize(row: ResearchJob, *, include_payload: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "job_id": row.job_id,
        "tool_name": row.tool_name,
        "inputs_hash": row.inputs_hash,
        "description": row.description,
        "status": row.status,
        "progress": row.progress,
        "progress_message": row.progress_message,
        "error": row.error,
        "error_class": row.error_class,
        "background_backend": row.background_backend,
        "session_id": str(row.session_id) if row.session_id else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "can_cancel": row.status in {"queued", "running"},
        "can_retry": row.status in {"failed", "cancelled"} and bool(row.args_replayable),
    }
    if include_payload:
        result["args"] = row.args if row.args_replayable else None
        result["args_replayable"] = bool(row.args_replayable)
        result["result"] = row.result
        if isinstance(row.result, dict) and row.result.get("_artifact_ref"):
            result["result_url"] = f"/api/jobs/{row.job_id}/result"
    return result


@router.get("")
async def list_research_jobs(
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    filters = [ResearchJob.user_id == user.id]
    if status:
        filters.append(ResearchJob.status == status)
    total = await db.scalar(select(func.count()).select_from(ResearchJob).where(*filters))
    rows = (
        await db.execute(
            select(ResearchJob)
            .where(*filters)
            .order_by(ResearchJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return {"items": [_serialize(row) for row in rows], "total": int(total or 0)}


async def _owned_job(db: AsyncSession, user: User, job_id: str) -> ResearchJob:
    row = (
        await db.execute(
            select(ResearchJob).where(
                ResearchJob.job_id == job_id,
                ResearchJob.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # Deliberately return 404 for both missing and foreign-owned ids.
        raise HTTPException(status_code=404, detail="Research job not found")
    return row


@router.get("/{job_id}")
async def get_research_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _serialize(await _owned_job(db, user, job_id), include_payload=True)


@router.get("/{job_id}/result")
async def get_research_job_result(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = await _owned_job(db, user, job_id)
    try:
        from app.services.durable_research_records import hydrate_result

        return hydrate_result(row.result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="Job result artifact is missing") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Job result artifact is unreadable") from exc


@router.post("/{job_id}/cancel")
async def cancel_research_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _owned_job(db, user, job_id)
    from app.services.async_tool_runtime import cancel_async_job

    result = cancel_async_job(job_id, owner_id=str(user.id))
    if result.get("error_class") == "not_found":
        # A database row with no active KV record is either already beyond the
        # cooperative-cancel window or was restored from backup.  Never claim
        # it was cancelled when the worker cannot observe the flag.
        raise HTTPException(status_code=409, detail="Job is no longer cancellable")
    return result


@router.post("/{job_id}/retry")
async def retry_research_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = await _owned_job(db, user, job_id)
    if row.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")
    if not row.args_replayable or not isinstance(row.args, dict):
        raise HTTPException(
            status_code=409,
            detail="Original arguments exceeded the durable replay limit",
        )
    from app.services.async_tool_runtime import submit_async_job

    return submit_async_job(
        row.tool_name,
        row.args,
        dedup=False,
        description=f"Retry of {row.job_id}",
        user_id=str(user.id),
        session_id=str(row.session_id) if row.session_id else None,
    )
