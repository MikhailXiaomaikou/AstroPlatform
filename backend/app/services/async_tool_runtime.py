"""Generic submit/poll/cancel runtime for long-running AI tools.

Wraps ``submit_async_job(tool_name, args)`` so any tool flagged
``async_capable`` in its manifest can be off-loaded to the Celery worker
without writing per-tool job glue. ``cosmology_mcmc`` previously rolled
its own ``threading.Thread + in-memory _JOBS`` for this; P1.1.b moved the
storage onto the shared KV, and P1.2 generalises the entry point.

Backed by the same ``JsonKvStore("async_job")`` namespace that
``cosmology_mcmc.get_cosmology_job_status`` reads from, so the existing
``get_cosmology_run_status`` tool keeps working while a generic
``get_async_job_status`` tool serves new submissions.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import time
import uuid
from typing import Any

from app.services._kv_store import JsonKvStore

logger = logging.getLogger(__name__)

DEFAULT_TTL = 3600  # 1 hour, matches cosmology_mcmc._JOB_TTL_SECONDS
CELERY_TASK_NAME = "ai_tools.run_long_tool"
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
MAX_STORED_JOBS = 64

_JOBS_STORE = JsonKvStore("async_job")


# ---------------------------------------------------------------------------
# Worker-context flag — prevents async-capable tools from re-submitting
# themselves to Celery when they are *already* running inside the Celery
# worker. tasks/ai_tools_tasks.py sets this before calling execute_tool;
# tool implementations check it and bypass the "submit to background"
# branch when True.
# ---------------------------------------------------------------------------
_IN_ASYNC_WORKER: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "async_tool_runtime_in_worker", default=False
)


def in_async_worker() -> bool:
    """Return True iff the current context is running inside ``run_long_tool``."""
    return _IN_ASYNC_WORKER.get()


def enter_async_worker():
    """Context manager hook for ``tasks/ai_tools_tasks.run_long_tool``."""
    return _IN_ASYNC_WORKER.set(True)


def leave_async_worker(token) -> None:
    _IN_ASYNC_WORKER.reset(token)


# ---------------------------------------------------------------------------
# Pluggable dispatcher (real Celery in prod, no-op in tests)
# ---------------------------------------------------------------------------
def _default_dispatch(tool_name: str, args: dict[str, Any], job_id: str) -> None:
    """Send the task to Celery via the global ``celery_app``.

    Importing ``celery_worker`` is heavy (pulls in Redis client + Celery
    bootstrap), so do it lazily — unit tests that monkeypatch the
    dispatcher never pay the cost.
    """
    from celery_worker import celery_app

    celery_app.send_task(
        CELERY_TASK_NAME,
        args=[tool_name, args, job_id],
        kwargs={},
    )


# Tests / future in-process backends can swap this. Keep production code
# free of conditional ``if testing:`` branches.
_dispatcher = _default_dispatch


def set_dispatcher(fn) -> None:
    """Override the Celery dispatcher. Test / alternative-backend hook."""
    global _dispatcher
    _dispatcher = fn


def reset_dispatcher() -> None:
    """Restore the real Celery dispatcher."""
    global _dispatcher
    _dispatcher = _default_dispatch


# ---------------------------------------------------------------------------
# Hashing for singleflight dedup
# ---------------------------------------------------------------------------
def _hash_args(tool_name: str, args: dict[str, Any]) -> str:
    try:
        payload = json.dumps({"_tool": tool_name, **args}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr({"_tool": tool_name, **args})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _find_running_job_by_hash(tool_name: str, inputs_hash: str) -> dict | None:
    try:
        keys = _JOBS_STORE.scan_keys()
    except Exception:
        return None
    for k in keys:
        entry = _JOBS_STORE.get(k)
        if not isinstance(entry, dict):
            continue
        if (
            entry.get("tool_name") == tool_name
            and entry.get("inputs_hash") == inputs_hash
            and entry.get("status") in ("queued", "running")
        ):
            return entry
    return None


def _enforce_job_cap(max_jobs: int | None = None) -> None:
    """Best-effort soft cap for stored async job records.

    TTL bounds job lifetime, but high-throughput submissions can still leave a
    large number of completed/failed records for up to an hour. Keep only the
    newest MAX_STORED_JOBS records so Redis/SQLite scans stay cheap.
    """
    limit = int(MAX_STORED_JOBS if max_jobs is None else max_jobs)
    if limit <= 0:
        return
    try:
        keys = _JOBS_STORE.scan_keys()
    except Exception:
        return
    if len(keys) <= limit:
        return
    jobs_with_age: list[tuple[str, float]] = []
    for key in keys:
        entry = _JOBS_STORE.get(key)
        if isinstance(entry, dict):
            jobs_with_age.append((key, float(entry.get("created_at") or 0)))
    jobs_with_age.sort(key=lambda item: item[1])
    over = len(jobs_with_age) - limit
    for key, _created_at in jobs_with_age[:over]:
        _JOBS_STORE.delete(key)


# ---------------------------------------------------------------------------
# Public API: submit / poll / cancel
# ---------------------------------------------------------------------------
def submit_async_job(
    tool_name: str,
    args: dict[str, Any] | None = None,
    *,
    ttl: int = DEFAULT_TTL,
    dedup: bool = True,
    description: str | None = None,
) -> dict[str, Any]:
    """Schedule ``tool_name(args)`` on the Celery worker, return a PARTIAL banner.

    The shape mirrors ``cosmology_mcmc.submit_emcee_job`` so the agent loop
    can route the response through the same poll-and-continue iteration.
    """
    args = args or {}
    inputs_hash = _hash_args(tool_name, args)

    if dedup:
        existing = _find_running_job_by_hash(tool_name, inputs_hash)
        if existing:
            return _partial_banner(existing, deduplicated=True)

    job_id = f"{tool_name}-{uuid.uuid4().hex[:12]}"
    now = time.time()
    job: dict[str, Any] = {
        "job_id": job_id,
        "tool_name": tool_name,
        "args": args,
        "inputs_hash": inputs_hash,
        "status": "queued",
        "created_at": now,
        "description": description,
        "ttl": ttl,
        "background_backend": "celery",
    }
    _JOBS_STORE.set(job_id, job, ttl=ttl)
    _enforce_job_cap()

    try:
        _dispatcher(tool_name, args, job_id)
    except Exception as exc:
        logger.warning("Celery dispatch failed for %s: %s", job_id, exc)
        job["status"] = "failed"
        job["error"] = f"Celery worker unavailable: {exc}"
        job["error_class"] = "celery_unavailable"
        job["completed_at"] = time.time()
        _JOBS_STORE.set(job_id, job, ttl=ttl)
        return _failed_banner(job)

    return _partial_banner(job)


def get_async_job(job_id: str) -> dict[str, Any] | None:
    """Return the raw job record, or None if not found."""
    job = _JOBS_STORE.get(str(job_id))
    return dict(job) if isinstance(job, dict) else None


def cancel_async_job(job_id: str) -> dict[str, Any]:
    """Mark a job cancelled so the Celery task self-aborts on its next checkpoint.

    Celery's hard revoke is unreliable for CPU-bound Python (the worker is
    inside a numpy call most of the time), so cooperative cancellation via
    ``is_cancelled`` is the contract.
    """
    job = get_async_job(job_id)
    if job is None:
        return {
            "success": False,
            "error": f"Unknown job_id: {job_id}",
            "error_class": "not_found",
        }
    if job.get("status") in TERMINAL_STATUSES:
        return job
    job["status"] = "cancelled"
    job["cancelled_at"] = time.time()
    _JOBS_STORE.set(job_id, job, ttl=int(job.get("ttl") or DEFAULT_TTL))
    return job


def is_cancelled(job_id: str) -> bool:
    """Probe used by the Celery task between long stages."""
    job = _JOBS_STORE.get(str(job_id))
    return isinstance(job, dict) and job.get("status") == "cancelled"


# ---------------------------------------------------------------------------
# Hooks called from inside the Celery task
# ---------------------------------------------------------------------------
def update_progress(
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    progress_message: str | None = None,
) -> None:
    """Report status / progress from inside the running task."""
    job = _JOBS_STORE.get(str(job_id))
    if not isinstance(job, dict):
        return
    if status is not None:
        job["status"] = status
        if status == "running" and "started_at" not in job:
            job["started_at"] = time.time()
        if status in TERMINAL_STATUSES:
            job["completed_at"] = time.time()
    if progress is not None:
        job["progress"] = float(progress)
    if progress_message is not None:
        job["progress_message"] = progress_message
    _JOBS_STORE.set(job_id, job, ttl=int(job.get("ttl") or DEFAULT_TTL))


def write_result(job_id: str, result: Any) -> None:
    """Store the final result and mark the job completed."""
    job = _JOBS_STORE.get(str(job_id))
    if not isinstance(job, dict):
        return
    job["status"] = "completed"
    job["result"] = result
    job["completed_at"] = time.time()
    _JOBS_STORE.set(job_id, job, ttl=int(job.get("ttl") or DEFAULT_TTL))


def write_error(job_id: str, exc: BaseException | str, error_class: str | None = None) -> None:
    """Store a failure and mark the job failed."""
    job = _JOBS_STORE.get(str(job_id))
    if not isinstance(job, dict):
        return
    job["status"] = "failed"
    if isinstance(exc, BaseException):
        job["error"] = str(exc)
        job["error_class"] = error_class or exc.__class__.__name__
    else:
        job["error"] = str(exc)
        job["error_class"] = error_class or "task_error"
    job["completed_at"] = time.time()
    _JOBS_STORE.set(job_id, job, ttl=int(job.get("ttl") or DEFAULT_TTL))


# ---------------------------------------------------------------------------
# Banner formatters
# ---------------------------------------------------------------------------
def _partial_banner(job: dict, deduplicated: bool = False) -> dict[str, Any]:
    msg = (
        f"Tool '{job.get('tool_name')}' is running as background job "
        f"{job.get('job_id')}. Poll get_async_job_status with this job_id "
        "to retrieve the result. Do not quote any numeric output until the "
        "job completes."
    )
    if deduplicated:
        msg = "[Deduplicated to existing in-flight job] " + msg
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "QUEUED",
        "__job_id__": job.get("job_id"),
        "__do_not_claim__": True,
        "data_origin": "unavailable",
        "job_id": job.get("job_id"),
        "tool_name": job.get("tool_name"),
        "status": job.get("status"),
        "created_at": job.get("created_at"),
        "background_backend": job.get("background_backend", "celery"),
        "deduplicated": deduplicated,
        "message": msg,
    }


def _failed_banner(job: dict) -> dict[str, Any]:
    return {
        "success": False,
        "__tool_status__": "FAILED",
        "analysis_status": "FAILED",
        "job_id": job.get("job_id"),
        "tool_name": job.get("tool_name"),
        "status": "failed",
        "error": job.get("error"),
        "error_class": job.get("error_class"),
    }


# Status-poll formatter for the get_async_job_status tool.
def format_status_for_tool(job: dict[str, Any] | None, *, requested_job_id: str) -> dict[str, Any]:
    """Render a job record into a tool-result-shaped envelope.

    The shape matches what cosmology_mcmc.get_cosmology_job_status already
    returns so the AI agent's existing decision tree (FAILED → abort,
    PARTIAL → poll again, COMPLETED → unwrap result) keeps working.
    """
    if job is None:
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "error": f"Unknown job_id: {requested_job_id}",
            "error_class": "not_found",
        }
    status = job.get("status")
    if status == "completed":
        result = job.get("result")
        if isinstance(result, dict):
            # Pass-through: caller sees the underlying tool's full result envelope.
            merged = {**result}
            merged.setdefault("job_id", job.get("job_id"))
            merged.setdefault("tool_name", job.get("tool_name"))
            merged.setdefault("status", "completed")
            return merged
        return {
            "success": True,
            "__tool_status__": "OK",
            "analysis_status": "COMPLETED",
            "job_id": job.get("job_id"),
            "tool_name": job.get("tool_name"),
            "status": "completed",
            "result": result,
        }
    if status == "failed":
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "job_id": job.get("job_id"),
            "tool_name": job.get("tool_name"),
            "status": "failed",
            "error": job.get("error"),
            "error_class": job.get("error_class"),
        }
    if status == "cancelled":
        return {
            "success": False,
            "__tool_status__": "FAILED",
            "analysis_status": "FAILED",
            "job_id": job.get("job_id"),
            "tool_name": job.get("tool_name"),
            "status": "cancelled",
            "error": "Job was cancelled.",
            "error_class": "cancelled",
        }
    # queued / running
    return {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "QUEUED" if status == "queued" else "RUNNING",
        "__job_id__": job.get("job_id"),
        "__do_not_claim__": True,
        "job_id": job.get("job_id"),
        "tool_name": job.get("tool_name"),
        "status": status,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "progress": job.get("progress"),
        "progress_message": job.get("progress_message"),
        "message": (
            f"Job {job.get('job_id')} still {status}. Poll again."
        ),
    }
