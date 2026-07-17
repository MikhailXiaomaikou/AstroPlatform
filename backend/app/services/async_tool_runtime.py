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
from contextlib import contextmanager
from typing import Any

from app.services._kv_store import JsonKvStore

logger = logging.getLogger(__name__)

# Redis/KV is the active-worker coordination layer, not the system of record.
# Keep active state for one day; the database projection remains after expiry.
DEFAULT_TTL = 24 * 3600
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

# Anonymous chat requests must not share the old implicit ``None`` owner.  The
# HTTP stream installs a server-generated scope in this context variable before
# it creates the orchestrator task.  Context propagation keeps that scope stable
# for every submit/poll in the one stream without pretending the anonymous
# caller is an authenticated database user.
_ANONYMOUS_OWNER_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "async_tool_runtime_anonymous_owner_scope", default=None
)


def in_async_worker() -> bool:
    """Return True iff the current context is running inside ``run_long_tool``."""
    return _IN_ASYNC_WORKER.get()


def enter_async_worker():
    """Context manager hook for ``tasks/ai_tools_tasks.run_long_tool``."""
    return _IN_ASYNC_WORKER.set(True)


def leave_async_worker(token) -> None:
    _IN_ASYNC_WORKER.reset(token)


@contextmanager
def anonymous_owner_scope(scope_id: str | None):
    """Bind one server-generated anonymous owner scope to the current task.

    The scope is hot-KV-only: ``user_id`` remains ``None`` on the job record,
    so the durable database projection does not invent a user or violate its
    foreign key.  Child asyncio tasks inherit the binding at creation time.
    """
    token = _ANONYMOUS_OWNER_SCOPE.set(str(scope_id) if scope_id else None)
    try:
        yield
    finally:
        _ANONYMOUS_OWNER_SCOPE.reset(token)


def _effective_owner_scope(owner_id: str | None) -> str | None:
    if owner_id is not None:
        return str(owner_id)
    return _ANONYMOUS_OWNER_SCOPE.get()


def _job_owner_scope(job: dict[str, Any]) -> str:
    # ``owner_scope`` was added after durable authenticated jobs already used
    # user_id directly; fall back so those hot records remain readable.
    return str(job.get("owner_scope") or job.get("user_id") or "")


def _owner_deletion_requested(job: dict[str, Any]) -> bool:
    owner_id = job.get("user_id")
    if not owner_id:
        return False
    try:
        owner_uuid = uuid.UUID(str(owner_id))
    except (TypeError, ValueError, AttributeError):
        return False
    from app.services.account_deletion import external_deletion_tombstone_exists

    return external_deletion_tombstone_exists(owner_uuid)


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
        task_id=job_id,
    )


# Tests / future in-process backends can swap this. Keep production code
# free of conditional ``if testing:`` branches.
_dispatcher = _default_dispatch


def _default_persist(job: dict[str, Any]) -> None:
    from app.services.durable_research_records import save_job

    save_job(job)


_persister = _default_persist


def set_dispatcher(fn) -> None:
    """Override the Celery dispatcher. Test / alternative-backend hook."""
    global _dispatcher
    _dispatcher = fn


def reset_dispatcher() -> None:
    """Restore the real Celery dispatcher."""
    global _dispatcher
    _dispatcher = _default_dispatch


def set_persister(fn) -> None:
    """Override lifecycle persistence for isolated tests."""
    global _persister
    _persister = fn


def reset_persister() -> None:
    """Restore strict PostgreSQL-backed lifecycle persistence."""
    global _persister
    _persister = _default_persist


# ---------------------------------------------------------------------------
# Hashing for singleflight dedup
# ---------------------------------------------------------------------------
def _hash_args(tool_name: str, args: dict[str, Any]) -> str:
    try:
        payload = json.dumps({"_tool": tool_name, **args}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr({"_tool": tool_name, **args})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _persist_job(job: dict[str, Any]) -> None:
    """Persist critical lifecycle state; failures must reach the caller."""
    _persister(job)


def mark_persistence_failure(job_id: str, exc: BaseException) -> dict[str, Any]:
    """Expose a terminal hot-state failure when durable persistence is unsafe.

    This function deliberately does not call the database again.  The failing
    transition already exhausted bounded retries; stale reconciliation will
    terminalize any older durable queued/running record once the DB recovers.
    """
    raw = _JOBS_STORE.get(str(job_id))
    job = dict(raw) if isinstance(raw, dict) else {"job_id": str(job_id)}
    job["status"] = "failed"
    job["error_class"] = "durable_persistence_failed"
    job["error"] = (
        "Critical research-job state could not be persisted after retries; "
        "the computation is not claimable as a durable result."
    )
    job["durability_status"] = "failed"
    job["completed_at"] = time.time()
    _JOBS_STORE.set(job_id, job, ttl=int(job.get("ttl") or DEFAULT_TTL))
    logger.critical(
        "research job %s entered visible persistence failure: %s",
        job_id,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return job


def _find_running_job_by_hash(
    tool_name: str,
    inputs_hash: str,
    *,
    owner_id: str | None = None,
) -> dict | None:
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
            and _job_owner_scope(entry) == str(owner_id or "")
        ):
            return entry
    return None


def _enforce_job_cap(
    *,
    owner_id: str | None,
    max_jobs: int | None = None,
) -> None:
    """Best-effort soft cap for stored async job records.

    TTL bounds job lifetime, but high-throughput submissions can still leave a
    large number of completed/failed records. Keep each owner's hot records
    bounded without allowing one account to evict another's jobs. Queued and
    running jobs are never deleted; if active work alone exceeds the cap this
    deliberately remains a soft cap until those jobs become terminal.
    """
    limit = int(MAX_STORED_JOBS if max_jobs is None else max_jobs)
    if limit <= 0:
        return
    try:
        keys = _JOBS_STORE.scan_keys()
    except Exception:
        return
    owner_key = str(owner_id or "")
    owner_jobs: list[tuple[str, float, str]] = []
    for key in keys:
        entry = _JOBS_STORE.get(key)
        if (
            isinstance(entry, dict)
            and _job_owner_scope(entry) == owner_key
        ):
            owner_jobs.append(
                (
                    key,
                    float(entry.get("created_at") or 0),
                    str(entry.get("status") or ""),
                )
            )
    over = len(owner_jobs) - limit
    if over <= 0:
        return
    terminal_jobs = sorted(
        (item for item in owner_jobs if item[2] in TERMINAL_STATUSES),
        key=lambda item: item[1],
    )
    for key, _created_at, _status in terminal_jobs[:over]:
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
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Schedule ``tool_name(args)`` on the Celery worker, return a PARTIAL banner.

    The shape mirrors ``cosmology_mcmc.submit_emcee_job`` so the agent loop
    can route the response through the same poll-and-continue iteration.
    """
    args = args or {}
    inputs_hash = _hash_args(tool_name, args)
    effective_owner = _effective_owner_scope(user_id)

    if user_id:
        probe = {"user_id": str(user_id)}
        if _owner_deletion_requested(probe):
            return {
                "success": False,
                "status": "FAILED",
                "error": "Account deletion requested; background work was not queued.",
                "error_class": "account_deletion_requested",
                "__tool_status__": "FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
            }

    if dedup:
        existing = _find_running_job_by_hash(
            tool_name, inputs_hash, owner_id=effective_owner,
        )
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
        "user_id": str(user_id) if user_id else None,
        "owner_scope": effective_owner,
        "session_id": str(session_id) if session_id else None,
        "ttl": ttl,
        "background_backend": "celery",
    }
    _JOBS_STORE.set(job_id, job, ttl=ttl)
    try:
        _persist_job(job)
    except Exception as exc:
        failed = mark_persistence_failure(job_id, exc)
        _enforce_job_cap(owner_id=effective_owner)
        return _failed_banner(failed)
    _enforce_job_cap(owner_id=effective_owner)

    try:
        _dispatcher(tool_name, args, job_id)
    except Exception:
        logger.warning("Celery dispatch failed for %s", job_id, exc_info=True)
        job["status"] = "failed"
        job["error"] = "Background worker is temporarily unavailable."
        job["error_class"] = "celery_unavailable"
        job["completed_at"] = time.time()
        _JOBS_STORE.set(job_id, job, ttl=ttl)
        try:
            _persist_job(job)
        except Exception as persist_exc:
            job = mark_persistence_failure(job_id, persist_exc)
        _enforce_job_cap(owner_id=_job_owner_scope(job))
        return _failed_banner(job)

    return _partial_banner(job)


def get_async_job(
    job_id: str,
    *,
    owner_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the raw job record, or None if not found."""
    effective_owner = _effective_owner_scope(owner_id)
    job = _JOBS_STORE.get(str(job_id))
    if isinstance(job, dict):
        if (
            effective_owner is not None
            and _job_owner_scope(job) != effective_owner
        ):
            return None
        return dict(job)
    try:
        from app.services.durable_research_records import load_job

        restored = load_job(str(job_id), owner_id=effective_owner)
        if (
            isinstance(restored, dict)
            and restored.get("status") in {"queued", "running"}
        ):
            # Rehydrate Redis after restart/expiry so worker transitions do not
            # silently no-op merely because the durable row outlived hot KV.
            _JOBS_STORE.set(
                str(job_id),
                restored,
                ttl=int(restored.get("ttl") or DEFAULT_TTL),
            )
        return restored
    except Exception:
        return None


def cancel_async_job(job_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
    """Mark a job cancelled so the Celery task self-aborts on its next checkpoint.

    Celery's hard revoke is unreliable for CPU-bound Python (the worker is
    inside a numpy call most of the time), so cooperative cancellation via
    ``is_cancelled`` is the contract.
    """
    job = get_async_job(job_id, owner_id=owner_id)
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
    try:
        _persist_job(job)
    except Exception as exc:
        job = mark_persistence_failure(job_id, exc)
    _enforce_job_cap(owner_id=_job_owner_scope(job))
    return job


def is_cancelled(job_id: str) -> bool:
    """Probe used by the Celery task between long stages."""
    job = _JOBS_STORE.get(str(job_id))
    return isinstance(job, dict) and (
        job.get("status") == "cancelled" or job.get("erasing") is True
    )


def purge_owner_jobs(owner_id: str) -> int:
    """Strictly cancel, mark, then remove one account's hot worker records."""

    removed = 0
    owner_key = str(owner_id)
    for key in list(_JOBS_STORE.scan_keys_strict()):
        job = _JOBS_STORE.get_strict(key)
        if not isinstance(job, dict) or _job_owner_scope(job) != owner_key:
            continue
        job["status"] = "cancelled"
        job["erasing"] = True
        job["cancelled_at"] = time.time()
        _JOBS_STORE.set_strict(key, job, ttl=int(job.get("ttl") or DEFAULT_TTL))
        _JOBS_STORE.delete_strict(key)
        removed += 1
    return removed


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
    if job.get("erasing") is True or _owner_deletion_requested(job):
        _JOBS_STORE.delete(str(job_id))
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
    _persist_job(job)
    if job.get("status") in TERMINAL_STATUSES:
        _enforce_job_cap(owner_id=_job_owner_scope(job))


def write_result(job_id: str, result: Any) -> None:
    """Store the final result and mark the job completed."""
    job = _JOBS_STORE.get(str(job_id))
    if not isinstance(job, dict):
        return
    if job.get("erasing") is True or _owner_deletion_requested(job):
        _JOBS_STORE.delete(str(job_id))
        return
    job["status"] = "completed"
    job["result"] = result
    job["completed_at"] = time.time()
    _JOBS_STORE.set(job_id, job, ttl=int(job.get("ttl") or DEFAULT_TTL))
    _persist_job(job)
    _enforce_job_cap(owner_id=_job_owner_scope(job))


def write_error(job_id: str, exc: BaseException | str, error_class: str | None = None) -> None:
    """Store a failure and mark the job failed."""
    job = _JOBS_STORE.get(str(job_id))
    if not isinstance(job, dict):
        return
    if job.get("erasing") is True or _owner_deletion_requested(job):
        _JOBS_STORE.delete(str(job_id))
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
    _persist_job(job)
    _enforce_job_cap(owner_id=_job_owner_scope(job))


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
