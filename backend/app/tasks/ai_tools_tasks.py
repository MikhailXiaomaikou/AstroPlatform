"""Celery tasks for long-running ai_tools execution.

Implements ``ai_tools.run_long_tool``, the worker-side counterpart to
``async_tool_runtime.submit_async_job``. The task:

1. Marks the job ``running`` in the shared KV.
2. Calls ``ai_tools.execute_tool`` (which already wraps the result in the
   provenance envelope used by the agent loop).
3. Writes the final result, or the exception, back to the KV so
   ``get_async_job_status`` can pick it up on the next poll.

Cancellation is cooperative: long sub-paths inside the tool itself must
call ``async_tool_runtime.is_cancelled(job_id)`` between stages and bail
out cleanly. Celery's hard revoke isn't used because the worker is
usually inside a numpy/emcee call and cannot be interrupted safely.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _get_celery_app():
    # Lazy import — celery_worker pulls in Redis + Celery and we don't
    # want test-time imports to require those services.
    from celery_worker import celery_app
    return celery_app


@_get_celery_app().task(bind=True, name="ai_tools.run_long_tool")
def run_long_tool(self, tool_name: str, tool_input: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Execute ``tool_name(tool_input)`` and persist the result under ``job_id``."""
    from app.services import async_tool_runtime as atr
    from app.services.ai_tools import execute_tool
    from app.services.durable_research_records import ResearchJobPersistenceError

    # Refuse an orphan task or a message delivered after reconciliation. This
    # check has to come BEFORE the running transition; otherwise an old Redis
    # message could resurrect a durable failed/cancelled/completed job.
    initial_job = atr.get_async_job(job_id)
    if initial_job is None:
        logger.error("refusing orphan Celery task with no lifecycle row: %s", job_id)
        return {
            "status": "failed",
            "job_id": job_id,
            "error_class": "missing_lifecycle_record",
        }
    if initial_job.get("status") in atr.TERMINAL_STATUSES:
        return {"status": str(initial_job["status"]), "job_id": job_id}
    from app.services.account_deletion import (
        account_runtime_is_active,
        dispose_deleted_account_result,
    )

    owner_id = initial_job.get("user_id")
    if not account_runtime_is_active(owner_id):
        if owner_id:
            atr.purge_owner_jobs(str(owner_id))
        return {"status": "cancelled", "job_id": job_id}

    token = None
    try:
        atr.update_progress(job_id, status="running")

        # Mark ourselves as running inside the async worker so that any tool
        # implementation we call into knows to run synchronously instead of
        # re-submitting itself back through submit_async_job.
        token = atr.enter_async_worker()
        # ``execute_tool`` is async; Celery worker is sync, so spin a
        # short-lived loop. Each run_long_tool task is its own asyncio
        # context, so this is safe even if the worker concurrency > 1.
        result = asyncio.run(execute_tool(
            tool_name,
            tool_input,
            user_id=initial_job.get("user_id"),
            chat_session_id=initial_job.get("session_id"),
        ))
        if not account_runtime_is_active(owner_id):
            if owner_id:
                dispose_deleted_account_result(user_id=owner_id, result=result)
            if owner_id:
                atr.purge_owner_jobs(str(owner_id))
            return {"status": "cancelled", "job_id": job_id}
        if atr.is_cancelled(job_id):
            # Late cancellation — retain the result in the transition so an
            # operator can inspect what completed before cancellation.
            atr.write_result(job_id, result)
            atr.update_progress(job_id, status="cancelled")
            return {"status": "cancelled_with_result", "job_id": job_id}

        atr.write_result(job_id, result)
        return {"status": "completed", "job_id": job_id}
    except ResearchJobPersistenceError as exc:
        atr.mark_persistence_failure(job_id, exc)
        return {
            "status": "failed",
            "job_id": job_id,
            "error_class": "durable_persistence_failed",
        }
    except Exception as exc:
        logger.exception("ai_tools.run_long_tool failed (job_id=%s)", job_id)
        if not account_runtime_is_active(owner_id):
            if owner_id:
                atr.purge_owner_jobs(str(owner_id))
            return {"status": "cancelled", "job_id": job_id}
        try:
            atr.write_error(job_id, exc)
        except ResearchJobPersistenceError as persist_exc:
            atr.mark_persistence_failure(job_id, persist_exc)
        return {"status": "failed", "job_id": job_id, "error": str(exc)}
    finally:
        if token is not None:
            atr.leave_async_worker(token)


@_get_celery_app().task(name="ai_tools.reconcile_stale_jobs", ignore_result=True)
def reconcile_stale_jobs_task() -> dict[str, int]:
    """Periodic safety net for jobs orphaned by worker/process loss."""
    from app.services.durable_research_records import reconcile_stale_jobs

    return {"reconciled": reconcile_stale_jobs()}
