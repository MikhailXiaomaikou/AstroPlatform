"""Synchronous persistence bridge for provenance and Celery worker state.

Tool dispatch and Celery hooks are synchronous in several call paths, while
the web application otherwise uses SQLAlchemy's async engine.  This module
uses the corresponding synchronous driver for tiny, best-effort record
writes.  User-facing APIs read the same tables through the normal async
session and therefore remain non-blocking.
"""

from __future__ import annotations

import hashlib
import gzip
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.research_records import ProvenanceRecord, ResearchJob

logger = logging.getLogger(__name__)

MAX_REPLAY_ARGS_BYTES = 2 * 1024 * 1024
MAX_INLINE_RESULT_BYTES = 2 * 1024 * 1024


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nonnegative_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


JOB_PERSIST_MAX_ATTEMPTS = _positive_int_env("JOB_PERSIST_MAX_ATTEMPTS", 3)
JOB_PERSIST_RETRY_BASE_SECONDS = _nonnegative_float_env(
    "JOB_PERSIST_RETRY_BASE_SECONDS", 0.2
)
RESEARCH_JOB_STALE_SECONDS = _positive_int_env(
    "RESEARCH_JOB_STALE_SECONDS", 14 * 60 * 60
)
_RESULT_NOT_PRESENT = object()


class ResearchJobPersistenceError(RuntimeError):
    """A critical research-job lifecycle transition was not made durable."""


def _uuid_or_none(value: Any) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _sync_url() -> str:
    url = settings.database_url
    if "+aiosqlite" in url:
        return url.replace("+aiosqlite", "")
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg2")
    return url


@lru_cache(maxsize=1)
def _engine():
    return create_engine(_sync_url(), pool_pre_ping=True)


def reset_engine() -> None:
    """Dispose the cached engine (tests and configuration reloads)."""
    if _engine.cache_info().currsize:
        try:
            _engine().dispose()
        except Exception:
            pass
    _engine.cache_clear()


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return {"_unserializable": True, "repr": repr(value)[:2000]}


def _persistable_args(args: dict[str, Any]) -> tuple[dict, bool]:
    safe = _json_safe(args)
    encoded = json.dumps(safe, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_REPLAY_ARGS_BYTES:
        return safe, True
    return {
        "_omitted": "arguments exceed durable replay limit",
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "keys": sorted(str(key) for key in args.keys()),
    }, False


def _persistable_result(job: dict[str, Any]) -> Any:
    if "result" not in job:
        return None
    safe = _json_safe(job.get("result"))
    encoded = json.dumps(
        safe, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) <= MAX_INLINE_RESULT_BYTES:
        return safe

    owner = str(job.get("user_id") or "unowned").replace("/", "_")
    job_id = str(job.get("job_id") or "unknown").replace("/", "_")
    key = f"jobs/{owner}/{job_id}/result.json.gz"
    try:
        from app.storage import get_storage_metadata, upload_fits

        compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
        upload_fits(key, compressed)
        metadata = get_storage_metadata(key)
        return {
            "_artifact_ref": key,
            "content_type": "application/json",
            "content_encoding": "gzip",
            "uncompressed_size_bytes": len(encoded),
            "stored_size_bytes": len(compressed),
            "sha256": metadata.get("sha256"),
            "storage_backend": metadata.get("backend"),
            "storage_version_id": metadata.get("version_id"),
        }
    except Exception as exc:
        # Keep the durable result in PostgreSQL when object storage is briefly
        # unavailable.  This is less space-efficient but never discards the
        # only completed scientific output.
        logger.warning("large job-result artifact write failed: %s", exc)
        return safe


def hydrate_result(result: Any) -> Any:
    """Resolve a large-result artifact stub back to its JSON value."""
    if not isinstance(result, dict) or not result.get("_artifact_ref"):
        return result
    from app.storage import download_fits

    raw = download_fits(str(result["_artifact_ref"]))
    if result.get("content_encoding") == "gzip":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def save_provenance(record: dict[str, Any]) -> None:
    """Insert an immutable provenance record; duplicate ids are ignored."""
    try:
        with Session(_engine()) as db:
            record_id = _uuid_or_none(record.get("id")) or uuid.uuid4()
            if db.get(ProvenanceRecord, record_id) is not None:
                return
            db.add(ProvenanceRecord(
                id=record_id,
                user_id=_uuid_or_none(record.get("user_id")),
                session_id=_uuid_or_none(record.get("session_id")),
                entity_type=str(record.get("entity_type") or "unknown")[:100],
                entity_id=str(record.get("entity_id") or "")[:255],
                activity=str(record.get("activity") or "unknown")[:255],
                params=_json_safe(record.get("params") or {}),
                parent_ids=_json_safe(record.get("parent_ids") or []),
                agent=str(record.get("agent") or "system")[:100],
                environment=_json_safe(record.get("environment") or {}),
                data_release=(str(record["data_release"])[:255] if record.get("data_release") else None),
                artifact_sha256=(str(record["artifact_sha256"])[:64] if record.get("artifact_sha256") else None),
                created_at=_datetime_from_value(record.get("timestamp")),
            ))
            db.commit()
    except Exception as exc:
        # Provenance is important, but a telemetry table outage must not erase
        # the scientific calculation itself.  The in-process copy remains
        # available and the failure is visible to operational logging.
        logger.warning("durable provenance write failed: %s", exc)


def load_provenance(entity_id: str, owner_id: str | uuid.UUID | None = None) -> list[dict]:
    """Load records for one entity, optionally enforcing user ownership."""
    try:
        with Session(_engine()) as db:
            stmt = select(ProvenanceRecord).where(ProvenanceRecord.entity_id == str(entity_id))
            owner = _uuid_or_none(owner_id)
            if owner_id is not None:
                if owner is None:
                    return []
                stmt = stmt.where(ProvenanceRecord.user_id == owner)
            rows = db.execute(stmt.order_by(ProvenanceRecord.created_at.asc())).scalars().all()
            return [_provenance_to_dict(row) for row in rows]
    except Exception as exc:
        logger.debug("durable provenance read failed: %s", exc)
        return []


def resolve_record_owner(*, session_id: Any = None, pipeline_run_id: Any = None) -> str | None:
    """Resolve legacy call sites to an owner without trusting caller params."""
    try:
        from app.models.schemas import ChatSession, PipelineRun

        with Session(_engine()) as db:
            sid = _uuid_or_none(session_id)
            if sid is not None:
                owner = db.execute(
                    select(ChatSession.user_id).where(ChatSession.id == sid)
                ).scalar_one_or_none()
                if owner is not None:
                    return str(owner)
            rid = _uuid_or_none(pipeline_run_id)
            if rid is not None:
                owner = db.execute(
                    select(PipelineRun.user_id).where(PipelineRun.id == rid)
                ).scalar_one_or_none()
                if owner is not None:
                    return str(owner)
    except Exception as exc:
        logger.debug("provenance owner resolution failed: %s", exc)
    return None


def _save_job_once(
    job: dict[str, Any],
    *,
    args: dict,
    replayable: bool,
    result_payload: Any = _RESULT_NOT_PRESENT,
) -> None:
    """Perform one idempotent lifecycle upsert attempt."""
    with Session(_engine()) as db:
        row = db.get(ResearchJob, str(job["job_id"]))
        if row is None:
            row = ResearchJob(
                job_id=str(job["job_id"]),
                user_id=_uuid_or_none(job.get("user_id")),
                session_id=_uuid_or_none(job.get("session_id")),
                tool_name=str(job.get("tool_name") or "unknown")[:255],
                inputs_hash=str(job.get("inputs_hash") or "")[:64],
                args=args,
                args_replayable=replayable,
                description=job.get("description"),
                status=str(job.get("status") or "queued")[:32],
                background_backend=str(
                    job.get("background_backend") or "celery"
                )[:32],
                created_at=_datetime_from_value(job.get("created_at")),
            )
            db.add(row)
        row.status = str(job.get("status") or row.status)[:32]
        row.progress = _float_or_none(job.get("progress"))
        row.progress_message = _str_or_none(job.get("progress_message"))
        if result_payload is not _RESULT_NOT_PRESENT:
            row.result = result_payload
        row.error = _str_or_none(job.get("error"))
        row.error_class = _str_or_none(job.get("error_class"), limit=255)
        row.started_at = _datetime_from_value(job.get("started_at"), allow_none=True)
        row.completed_at = _datetime_from_value(
            job.get("completed_at"), allow_none=True
        )
        db.commit()


def save_job(job: dict[str, Any]) -> None:
    """Durably upsert a lifecycle transition or raise after bounded retries.

    A job transition is user-visible scientific state, not telemetry.  Callers
    must never continue as though it were durable after this function raises.
    """
    args, replayable = _persistable_args(job.get("args") or {})
    result_payload = (
        _persistable_result(job) if "result" in job else _RESULT_NOT_PRESENT
    )
    last_error: Exception | None = None
    for attempt in range(1, JOB_PERSIST_MAX_ATTEMPTS + 1):
        try:
            _save_job_once(
                job,
                args=args,
                replayable=replayable,
                result_payload=result_payload,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt >= JOB_PERSIST_MAX_ATTEMPTS:
                break
            delay = JOB_PERSIST_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "research-job persistence attempt %d/%d failed for %s; "
                "retrying in %.2fs: %s",
                attempt,
                JOB_PERSIST_MAX_ATTEMPTS,
                job.get("job_id"),
                delay,
                exc,
            )
            # Drop stale pooled connections before the retry. Checked-out
            # connections are unaffected; new attempts receive a fresh one.
            try:
                _engine().dispose()
            except Exception:
                pass
            if delay:
                time.sleep(delay)

    logger.error(
        "critical research-job persistence failed after %d attempts for %s",
        JOB_PERSIST_MAX_ATTEMPTS,
        job.get("job_id"),
        exc_info=(
            type(last_error),
            last_error,
            last_error.__traceback__,
        )
        if last_error is not None
        else None,
    )
    raise ResearchJobPersistenceError(
        f"Could not persist research job {job.get('job_id')} after "
        f"{JOB_PERSIST_MAX_ATTEMPTS} attempts"
    ) from last_error


def reconcile_stale_jobs(
    *,
    stale_after_seconds: int | None = None,
    now: datetime | None = None,
    limit: int = 500,
) -> int:
    """Mark orphaned queued/running jobs failed and mirror that to hot KV.

    The threshold intentionally exceeds Celery's hard task and Redis visibility
    limits.  A live task therefore gets its normal completion/redelivery window;
    only records that can no longer be a valid in-flight execution are failed.
    """
    threshold = int(
        RESEARCH_JOB_STALE_SECONDS
        if stale_after_seconds is None
        else stale_after_seconds
    )
    if threshold <= 0 or limit <= 0:
        raise ValueError("stale_after_seconds and limit must be positive")
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    cutoff = observed_at - timedelta(seconds=threshold)
    reconciled: list[dict[str, Any]] = []

    with Session(_engine()) as db:
        rows = db.execute(
            select(ResearchJob)
            .where(
                ResearchJob.status.in_(("queued", "running")),
                ResearchJob.updated_at < cutoff,
            )
            .order_by(ResearchJob.updated_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for row in rows:
            previous_status = row.status
            row.status = "failed"
            row.error_class = "stale_job_reconciled"
            row.error = (
                f"Job remained {previous_status} beyond the configured "
                f"{threshold}s lifecycle limit after worker/process loss."
            )
            row.progress_message = "failed during stale-job reconciliation"
            row.completed_at = observed_at
            row.updated_at = observed_at
            reconciled.append(_job_to_dict(row))
        db.commit()

    if reconciled:
        try:
            from app.services import async_tool_runtime as runtime

            for durable_job in reconciled:
                job_id = str(durable_job["job_id"])
                hot_job = runtime._JOBS_STORE.get(job_id)  # noqa: SLF001
                merged = dict(hot_job) if isinstance(hot_job, dict) else {}
                merged.update(durable_job)
                merged["durability_status"] = "durable"
                runtime._JOBS_STORE.set(  # noqa: SLF001
                    job_id,
                    merged,
                    ttl=int(merged.get("ttl") or runtime.DEFAULT_TTL),
                )
        except Exception:
            # PostgreSQL is the system of record. A hot-cache mirror failure is
            # visible but does not undo the durable terminal transition.
            logger.exception("failed to mirror stale-job reconciliation to hot KV")
    return len(reconciled)


def load_job(
    job_id: str,
    owner_id: str | uuid.UUID | None = None,
    *,
    hydrate: bool = True,
) -> dict | None:
    try:
        with Session(_engine()) as db:
            stmt = select(ResearchJob).where(ResearchJob.job_id == str(job_id))
            owner = _uuid_or_none(owner_id)
            if owner_id is not None:
                if owner is None:
                    return None
                stmt = stmt.where(ResearchJob.user_id == owner)
            row = db.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            result = _job_to_dict(row)
            if hydrate:
                try:
                    result["result"] = hydrate_result(result.get("result"))
                except Exception as exc:
                    logger.warning("job-result artifact hydration failed: %s", exc)
                    result["result_unavailable"] = True
            return result
    except Exception as exc:
        logger.debug("durable research-job read failed: %s", exc)
        return None


def _datetime_from_value(value: Any, *, allow_none: bool = False) -> datetime | None:
    if value in (None, ""):
        return None if allow_none else datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None if allow_none else datetime.now(timezone.utc)


def _timestamp(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    result = str(value)
    return result[:limit] if limit else result


def _provenance_to_dict(row: ProvenanceRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id) if row.user_id else None,
        "session_id": str(row.session_id) if row.session_id else None,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "activity": row.activity,
        "params": row.params or {},
        "parent_ids": row.parent_ids or [],
        "agent": row.agent,
        "environment": row.environment or {},
        "data_release": row.data_release,
        "artifact_sha256": row.artifact_sha256,
        "timestamp": row.created_at.isoformat(),
    }


def _job_to_dict(row: ResearchJob) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "user_id": str(row.user_id) if row.user_id else None,
        "session_id": str(row.session_id) if row.session_id else None,
        "tool_name": row.tool_name,
        "inputs_hash": row.inputs_hash,
        "args": row.args or {},
        "args_replayable": bool(row.args_replayable),
        "description": row.description,
        "status": row.status,
        "progress": row.progress,
        "progress_message": row.progress_message,
        "result": row.result,
        "error": row.error,
        "error_class": row.error_class,
        "background_backend": row.background_backend,
        "created_at": _timestamp(row.created_at),
        "started_at": _timestamp(row.started_at),
        "completed_at": _timestamp(row.completed_at),
    }
