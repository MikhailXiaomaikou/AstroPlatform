"""Fail-closed account erasure with a non-PII restore tombstone."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.claim_audit_records import (
    AccountDeletionTombstone,
    ArtifactCleanupQueue,
    EvidencePack,
)
from app.models.database import Base, async_session
from app.models.research_records import ResearchJob
from app.models.schemas import ChatSession, DataFile, PipelineRun, RunResult, User
from app.storage import (
    delete_fits_all_versions,
    download_fits,
    normalize_storage_key,
    upload_fits,
)

logger = logging.getLogger(__name__)
_EXTERNAL_TOMBSTONE_CACHE: dict[uuid.UUID, tuple[float, bool]] = {}
_EXTERNAL_TOMBSTONE_CACHE_SECONDS = 60.0
_DELETION_LEASE_SECONDS = 10 * 60


class AccountDeletionLeaseLost(RuntimeError):
    """The erasure worker no longer owns the tombstone transition."""


class AccountArtifactOwnerInactive(RuntimeError):
    """A tool output cannot be registered after account deletion starts."""


def deletion_user_fingerprint(user_id: uuid.UUID) -> str:
    """Pseudonymous stable identifier used to suppress restored accounts."""

    return hashlib.sha256(
        f"standard-astro:account-deletion:v2:{user_id}".encode("utf-8")
    ).hexdigest()


def _legacy_deletion_user_fingerprint(user_id: uuid.UUID, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"account-deletion:{user_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def deletion_receipt_hash(receipt: str) -> str:
    return hashlib.sha256(receipt.encode("utf-8")).hexdigest()


def external_tombstone_key(user_id: uuid.UUID) -> str:
    # The object location is independent of signing-key rotation. Existence
    # remains discoverable after a restore even if a verification key was
    # accidentally omitted; malformed/unverifiable ledgers fail closed.
    return f"privacy-tombstones/v2/{deletion_user_fingerprint(user_id)}.json"


def _legacy_external_tombstone_key(user_id: uuid.UUID, secret: str) -> str:
    return f"privacy-tombstones/{_legacy_deletion_user_fingerprint(user_id, secret)}.json"


def _deletion_lease_deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=_DELETION_LEASE_SECONDS)


async def claim_account_deletion_lease(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    tombstone_id: uuid.UUID,
    lease_id: str | None = None,
    commit: bool = True,
) -> str | None:
    """Atomically claim eligible erasure work or renew a dispatched claim."""

    tombstone = await db.get(AccountDeletionTombstone, tombstone_id)
    if tombstone is None:
        raise LookupError("Account deletion tombstone not found")
    fingerprint = deletion_user_fingerprint(user_id)
    if not hmac.compare_digest(tombstone.user_fingerprint, fingerprint):
        raise PermissionError("Account deletion tombstone binding mismatch")

    now = datetime.now(timezone.utc)
    deadline = _deletion_lease_deadline()
    if lease_id is not None:
        renewed = await db.execute(
            update(AccountDeletionTombstone)
            .where(
                AccountDeletionTombstone.id == tombstone_id,
                AccountDeletionTombstone.user_fingerprint == fingerprint,
                AccountDeletionTombstone.status == "RUNNING",
                AccountDeletionTombstone.lease_id == lease_id,
                AccountDeletionTombstone.lease_expires_at.is_not(None),
                AccountDeletionTombstone.lease_expires_at >= now,
            )
            .values(lease_expires_at=deadline, last_error_class=None)
        )
        if commit:
            await db.commit()
        return lease_id if renewed.rowcount == 1 else None

    claimed_lease_id = uuid.uuid4().hex
    claimed = await db.execute(
        update(AccountDeletionTombstone)
        .where(
            AccountDeletionTombstone.id == tombstone_id,
            AccountDeletionTombstone.user_fingerprint == fingerprint,
            or_(
                AccountDeletionTombstone.status.in_({"PENDING", "RETRYABLE"}),
                and_(
                    AccountDeletionTombstone.status == "RUNNING",
                    or_(
                        AccountDeletionTombstone.lease_expires_at.is_(None),
                        AccountDeletionTombstone.lease_expires_at < now,
                    ),
                ),
            ),
        )
        .values(
            status="RUNNING",
            lease_id=claimed_lease_id,
            lease_expires_at=deadline,
            attempt_count=AccountDeletionTombstone.attempt_count + 1,
            last_error_class=None,
        )
    )
    if commit:
        await db.commit()
    return claimed_lease_id if claimed.rowcount == 1 else None


async def _renew_account_deletion_lease(
    db: AsyncSession,
    *,
    tombstone_id: uuid.UUID,
    lease_id: str,
) -> None:
    renewed = await db.execute(
        update(AccountDeletionTombstone)
        .where(
            AccountDeletionTombstone.id == tombstone_id,
            AccountDeletionTombstone.status == "RUNNING",
            AccountDeletionTombstone.lease_id == lease_id,
        )
        .values(lease_expires_at=_deletion_lease_deadline())
    )
    await db.commit()
    if renewed.rowcount != 1:
        raise AccountDeletionLeaseLost("Account deletion worker lease was lost")


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def write_external_deletion_tombstone(
    *,
    user_id: uuid.UUID,
    receipt_hash: str,
    requested_at: datetime,
) -> str:
    """Persist deletion suppression outside database point-in-time restores."""

    payload = {
        "schema_version": 2,
        "user_fingerprint": deletion_user_fingerprint(user_id),
        "key_id": settings.deletion_tombstone_key_id,
        "receipt_hash": receipt_hash,
        "requested_at": requested_at.astimezone(timezone.utc).isoformat(),
    }
    signature = hmac.new(
        settings.deletion_tombstone_key.encode("utf-8"),
        _canonical_json(payload),
        hashlib.sha256,
    ).hexdigest()
    document = {**payload, "signature": f"hmac-sha256:{signature}"}
    key = external_tombstone_key(user_id)
    upload_fits(key, _canonical_json(document))
    _EXTERNAL_TOMBSTONE_CACHE[user_id] = (time.monotonic(), True)
    return key


def external_deletion_tombstone_exists(user_id: uuid.UUID) -> bool:
    """Check the restore-safe ledger and fail closed on a malformed record."""

    now = time.monotonic()
    cached = _EXTERNAL_TOMBSTONE_CACHE.get(user_id)
    if cached is not None and now - cached[0] < _EXTERNAL_TOMBSTONE_CACHE_SECONDS:
        return cached[1]
    current_key = settings.deletion_tombstone_key
    retired_keys = settings.deletion_tombstone_verification_keyring
    candidates: list[tuple[str, str | None]] = [
        (external_tombstone_key(user_id), None)
    ]
    seen_legacy_paths: set[str] = set()
    for secret in [current_key, *retired_keys.values()]:
        legacy_path = _legacy_external_tombstone_key(user_id, secret)
        if legacy_path not in seen_legacy_paths:
            candidates.append((legacy_path, secret))
            seen_legacy_paths.add(legacy_path)

    raw: bytes | None = None
    legacy_secret: str | None = None
    for candidate_path, candidate_secret in candidates:
        try:
            raw = download_fits(candidate_path)
            legacy_secret = candidate_secret
            break
        except FileNotFoundError:
            continue
    if raw is None:
        _EXTERNAL_TOMBSTONE_CACHE[user_id] = (now, False)
        return False
    try:
        document = json.loads(raw)
        signature = str(document.pop("signature", ""))
        schema_version = int(document.get("schema_version") or 1)
        if schema_version >= 2:
            key_id = str(document.get("key_id") or "")
            if key_id == settings.deletion_tombstone_key_id:
                verification_secret = current_key
            else:
                verification_secret = retired_keys.get(key_id)
            expected_fingerprint = deletion_user_fingerprint(user_id)
        else:
            verification_secret = legacy_secret
            expected_fingerprint = (
                _legacy_deletion_user_fingerprint(user_id, verification_secret)
                if verification_secret
                else ""
            )
        if not verification_secret:
            raise ValueError("Deletion tombstone verification key unavailable")
        expected = "hmac-sha256:" + hmac.new(
            verification_secret.encode("utf-8"),
            _canonical_json(document),
            hashlib.sha256,
        ).hexdigest()
        valid = (
            document.get("user_fingerprint") == expected_fingerprint
            and hmac.compare_digest(signature, expected)
        )
    except Exception:
        valid = False
    if not valid:
        logger.error(
            "Malformed external deletion tombstone; disabling restored account"
        )
    # Existence itself suppresses login. A corrupt ledger is an operator
    # incident, never permission to resurrect a deleted account.
    _EXTERNAL_TOMBSTONE_CACHE[user_id] = (now, True)
    return True


def account_runtime_is_active(user_id: str | uuid.UUID | None) -> bool:
    """Fail closed for background work owned by a deleted account."""

    if user_id in (None, ""):
        return True
    try:
        owner_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    except (TypeError, ValueError, AttributeError):
        return False
    if external_deletion_tombstone_exists(owner_id):
        return False
    try:
        from sqlalchemy.orm import Session

        from app.services.durable_research_records import _engine

        with Session(_engine()) as session:
            account_status = session.scalar(
                select(User.account_status).where(User.id == owner_id)
            )
        return str(account_status or "").upper() == "ACTIVE"
    except Exception:
        logger.exception("Could not verify background-work account status")
        return False


def collect_result_output_paths(value: Any) -> set[str]:
    """Return canonical server-output references embedded in a tool result."""

    raw_paths: set[str] = set()

    def _collect(item: Any) -> None:
        if isinstance(item, dict):
            for field, nested in item.items():
                if field == "output_path" and isinstance(nested, str) and nested.strip():
                    raw_paths.add(nested)
                _collect(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                _collect(nested)

    _collect(value)
    return {normalize_storage_key(path) for path in raw_paths}


def stage_result_artifacts_for_registration(
    *,
    user_id: str | uuid.UUID,
    result: Any,
) -> int:
    """Durably discover tool outputs before attempting their owner-ledger commit.

    The cleanup queue is committed in a separate transaction first.  A true
    owner-ledger failure therefore leaves the object discoverable, while a
    commit whose acknowledgement is lost can later be reconciled against the
    trusted ``DataFile`` row instead of deleting a valid object.
    """

    from app.services.artifact_cleanup import stage_artifact_cleanup_sync

    paths = collect_result_output_paths(result)
    for path in sorted(paths):
        stage_artifact_cleanup_sync(
            path,
            user_id=user_id,
            reason_class="uncommitted_tool_output",
        )
    return len(paths)


def register_result_artifacts(
    *,
    user_id: str | uuid.UUID,
    result: Any,
) -> int:
    """Create trusted owner ledgers for server-generated tool outputs.

    DataFile is the existing storage ownership boundary. Locking the User in
    the same transaction serializes this ledger write with account deletion;
    client-authored chat transcripts are never consulted for delete authority.
    """

    owner_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    paths = collect_result_output_paths(result)
    if not paths:
        return 0

    from pathlib import PurePosixPath

    from sqlalchemy.orm import Session

    from app.services.durable_research_records import _engine
    from app.storage import StorageOwnershipError

    with Session(_engine()) as db:
        owner = db.scalar(
            select(User).where(User.id == owner_id).with_for_update()
        )
        if owner is None or str(owner.account_status or "").upper() != "ACTIVE":
            raise AccountArtifactOwnerInactive(
                "Account deletion revoked tool-output persistence"
            )
        foreign_owners = set(
            db.scalars(
                select(DataFile.user_id).where(
                    DataFile.user_id != owner_id,
                    DataFile.fits_path.in_(paths),
                )
            ).all()
        )
        if foreign_owners:
            # A tool result is not authority to transfer another account's
            # object.  Keep the pre-committed cleanup row: the sweeper will see
            # the existing trusted reference and reconcile it without deleting
            # the bytes.
            raise StorageOwnershipError(
                "Tool output path is already owned by another account"
            )
        existing = set(
            db.scalars(
                select(DataFile.fits_path).where(
                    DataFile.user_id == owner_id,
                    DataFile.fits_path.in_(paths),
                )
            ).all()
        )
        for path in sorted(paths - existing):
            db.add(
                DataFile(
                    user_id=owner_id,
                    source="tool_output",
                    object_id=PurePosixPath(path).name,
                    fits_path=path,
                    metadata_={"owner_ledger": "server_tool_result_v1"},
                )
            )
        # Ledger creation (or confirmation of an existing same-owner ledger)
        # and cleanup-queue removal are one transaction.  If commit truly
        # fails, the queue remains.  If commit succeeds but the acknowledgement
        # is lost, the trusted ledger remains and no eager deletion occurs.
        db.execute(
            delete(ArtifactCleanupQueue).where(
                ArtifactCleanupQueue.artifact_ref.in_(paths)
            )
        )
        db.commit()
    return len(paths - existing)


def _queue_late_artifact_cleanup(
    *,
    user_id: uuid.UUID,
    artifact_refs: set[str],
) -> None:
    """Durably attach late worker artifacts to the account tombstone."""

    if not artifact_refs:
        return
    from sqlalchemy.orm import Session

    from app.services.durable_research_records import _engine

    with Session(_engine()) as db:
        tombstone = db.scalar(
            select(AccountDeletionTombstone)
            .where(
                AccountDeletionTombstone.user_fingerprint
                == deletion_user_fingerprint(user_id)
            )
            .with_for_update()
        )
        if tombstone is None:
            raise LookupError("Deletion tombstone missing for late artifact cleanup")
        existing = {
            normalize_storage_key(str(path))
            for path in list(tombstone.pending_artifact_refs or [])
        }
        tombstone.pending_artifact_refs = sorted(existing | artifact_refs)
        tombstone.pending_user_id = user_id
        tombstone.status = "RETRYABLE"
        tombstone.completed_at = None
        tombstone.lease_id = None
        tombstone.lease_expires_at = None
        tombstone.last_error_class = "late_worker_artifact_cleanup"
        tombstone_id = tombstone.id
        db.commit()

    # Dispatch is best effort because Beat reconciliation also scans the
    # durable pending_user_id. Never drop the database queue if Redis is down.
    try:
        from app.tasks.privacy_tasks import erase_account_task

        erase_account_task.delay(str(user_id), str(tombstone_id))
    except Exception:
        logger.exception("Late artifact cleanup dispatch failed; Beat will retry")


def dispose_deleted_account_result(
    *,
    user_id: str | uuid.UUID,
    result: Any,
) -> int:
    """Permanently remove late tool outputs or queue them for retry."""

    owner_id = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    paths = collect_result_output_paths(result)
    failed: set[str] = set()
    for path in sorted(paths):
        try:
            delete_fits_all_versions(path)
        except Exception:
            logger.exception("Could not immediately erase late tool output %s", path)
            failed.add(path)
    if failed:
        _queue_late_artifact_cleanup(user_id=owner_id, artifact_refs=failed)
    return len(paths) - len(failed)


def _pk_clause(table: Any, values: set[tuple[Any, ...]]) -> Any:
    columns = list(table.primary_key.columns)
    if len(columns) == 1:
        return columns[0].in_([row[0] for row in values])
    return tuple_(*columns).in_(list(values))


async def _connected_rows(
    db: AsyncSession,
    user_id: uuid.UUID,
) -> dict[str, set[tuple[Any, ...]]]:
    """Find every row reachable through a foreign key from this account.

    This follows child references rather than maintaining a fragile hand-made
    table list. New owner-scoped tables therefore join account erasure as soon
    as they declare their foreign key to ``users`` or another owned record.
    """

    selected: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
    selected["users"].add((user_id,))
    tables = list(Base.metadata.tables.values())

    changed = True
    while changed:
        changed = False
        for table in tables:
            primary_key = list(table.primary_key.columns)
            if not primary_key or table.name == "account_deletion_tombstones":
                continue
            predicates = []
            for foreign_key in table.foreign_keys:
                parent_table = foreign_key.column.table
                parent_rows = selected.get(parent_table.name)
                if not parent_rows:
                    continue
                parent_primary_key = list(parent_table.primary_key.columns)
                target_index = next(
                    (
                        index
                        for index, column in enumerate(parent_primary_key)
                        if column is foreign_key.column
                        or (
                            column.name == foreign_key.column.name
                            and column.table.name == foreign_key.column.table.name
                        )
                    ),
                    None,
                )
                if target_index is None:
                    # Standard Astro currently references primary keys only.
                    # Refuse to infer an unsafe relationship if that changes.
                    continue
                parent_values = {row[target_index] for row in parent_rows}
                predicates.append(foreign_key.parent.in_(parent_values))
            if not predicates:
                continue
            rows = (
                await db.execute(select(*primary_key).where(or_(*predicates)))
            ).all()
            before = len(selected[table.name])
            selected[table.name].update(tuple(row) for row in rows)
            changed = changed or len(selected[table.name]) > before
    return dict(selected)


async def _owned_storage_keys(
    db: AsyncSession,
    selected: dict[str, set[tuple[Any, ...]]],
    *,
    user_id: uuid.UUID,
) -> set[str]:
    keys: set[str] = set()

    # Pre-upload cleanup discovery survives a failed ResearchJob commit and
    # has no FK to the User row by design. Include it explicitly so an account
    # deletion cannot complete while an uncommitted owner artifact remains.
    keys.update(
        str(value)
        for value in (
            await db.scalars(
                select(ArtifactCleanupQueue.artifact_ref).where(
                    ArtifactCleanupQueue.user_fingerprint
                    == deletion_user_fingerprint(user_id)
                )
            )
        ).all()
        if value
    )

    pipeline_ids = selected.get("pipeline_runs", set())
    if pipeline_ids:
        results = (
            await db.scalars(
                select(PipelineRun.results).where(
                    _pk_clause(PipelineRun.__table__, pipeline_ids)
                )
            )
        ).all()
        for result in results:
            keys.update(collect_result_output_paths(result))

    data_file_ids = selected.get("data_files", set())
    if data_file_ids:
        rows = (
            await db.execute(
                select(DataFile.id, DataFile.fits_path).where(
                    _pk_clause(DataFile.__table__, data_file_ids)
                )
            )
        ).all()
        selected_ids = {row[0] for row in data_file_ids}
        for row_id, raw_key in rows:
            if not raw_key:
                continue
            # Do not remove a legacy object still referenced by another owner.
            other_reference = await db.scalar(
                select(DataFile.id)
                .where(DataFile.fits_path == raw_key, DataFile.id.not_in(selected_ids))
                .limit(1)
            )
            if other_reference is None:
                keys.add(str(raw_key))

    pack_ids = selected.get("evidence_packs", set())
    if pack_ids:
        keys.update(
            str(value)
            for value in (
                await db.scalars(
                    select(EvidencePack.artifact_ref).where(
                        _pk_clause(EvidencePack.__table__, pack_ids)
                    )
                )
            ).all()
            if value
        )

    job_ids = selected.get("research_jobs", set())
    if job_ids:
        results = (
            await db.scalars(
                select(ResearchJob.result).where(
                    _pk_clause(ResearchJob.__table__, job_ids)
                )
            )
        ).all()
        from app.services.durable_research_records import hydrate_result

        for result in results:
            if isinstance(result, dict) and result.get("_artifact_ref"):
                keys.add(str(result["_artifact_ref"]))
            # Large results are signed artifact stubs. Hydration verifies both
            # storage integrity and the immutable stub digest before nested
            # output keys can be trusted for deletion. Any failure propagates,
            # leaving the tombstone RETRYABLE instead of falsely COMPLETED.
            hydrated = await asyncio.to_thread(hydrate_result, result)
            keys.update(collect_result_output_paths(hydrated))

    run_result_ids = selected.get("run_results", set())
    if run_result_ids:
        rows = (
            await db.execute(
                select(RunResult.id, RunResult.output_path).where(
                    _pk_clause(RunResult.__table__, run_result_ids)
                )
            )
        ).all()
        selected_ids = {row[0] for row in run_result_ids}
        for _row_id, raw_key in rows:
            if not raw_key:
                continue
            other_run_reference = await db.scalar(
                select(RunResult.id)
                .where(
                    RunResult.output_path == raw_key,
                    RunResult.id.not_in(selected_ids),
                )
                .limit(1)
            )
            other_file_reference = await db.scalar(
                select(DataFile.id)
                .where(DataFile.fits_path == raw_key)
                .limit(1)
            )
            if other_run_reference is None and other_file_reference is None:
                keys.add(str(raw_key))

    normalized: set[str] = set()
    for key in keys:
        try:
            normalized.add(normalize_storage_key(key))
        except ValueError as exc:
            # Dropping the database ledger while silently retaining an object
            # would make the deletion receipt false. Operators must repair or
            # explicitly migrate malformed legacy paths, then retry erasure.
            raise ValueError(
                "Malformed owned storage key blocks verified account deletion"
            ) from exc
    return normalized


async def purge_user_runtime_state(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    selected: dict[str, set[tuple[Any, ...]]] | None = None,
    strict_pipeline_cache: bool = True,
) -> dict[str, int]:
    """Strictly remove non-relational state before durable rows disappear.

    ``strict_pipeline_cache`` remains as a compatibility guard for older
    callers, but disabling strictness is forbidden: an empty result from an
    unavailable Redis/SQLite backend is not proof of erasure.
    """

    if not strict_pipeline_cache:
        raise ValueError("Account deletion runtime purge must be strict")

    selected_rows = selected or await _connected_rows(db, user_id)
    chat_ids = selected_rows.get("chat_sessions", set())
    session_ids: set[str] = set()
    if chat_ids:
        chats = (
            await db.execute(
                select(ChatSession.id, ChatSession.current_run_id).where(
                    _pk_clause(ChatSession.__table__, chat_ids)
                )
            )
        ).all()
        for chat_id, current_run_id in chats:
            session_ids.add(str(chat_id))
            if current_run_id:
                session_ids.add(str(current_run_id))

    def _purge_sync() -> dict[str, int]:
        from app.pipeline.engine import delete_owner_pipeline_cache_sync
        from app.services import async_tool_runtime, code_executor, workflow_checkpoint
        from app.services.user_tools import _STORE as user_tool_store

        # The durable owner index is authoritative even if a worker crashed
        # before writing its workflow checkpoint, or the user only ran a cache
        # producer (search/ADQL) and never invoked run_python.
        python_session_ids = code_executor.list_user_sessions_strict(str(user_id))
        for session_id in session_ids:
            checkpoint = workflow_checkpoint.get_checkpoint_strict(session_id)
            if checkpoint is not None:
                for step in checkpoint.steps:
                    for reference in step.cache_refs:
                        if str(reference).startswith("python_session:"):
                            python_session_ids.add(str(reference).split(":", 1)[1])
            workflow_checkpoint.mark_session_deleted(session_id)

        for python_session_id in python_session_ids:
            code_executor.mark_session_deleted(python_session_id)
            code_executor.delete_user_session_registration_strict(
                str(user_id), python_session_id
            )

        tool_prefix = f"user:{user_id}:"
        user_tools_deleted = 0
        for key in list(user_tool_store.scan_keys_strict()):
            if key.startswith(tool_prefix):
                user_tool_store.delete_strict(key)
                user_tools_deleted += 1

        return {
            "async_jobs_deleted": async_tool_runtime.purge_owner_jobs(str(user_id)),
            "user_tools_deleted": user_tools_deleted,
            "workflow_sessions_deleted": len(session_ids),
            "python_sessions_deleted": len(python_session_ids),
            "pipeline_cache_keys_deleted": delete_owner_pipeline_cache_sync(
                user_id,
                strict=strict_pipeline_cache,
            ),
        }

    return await asyncio.to_thread(_purge_sync)


async def erase_account_data(
    *,
    user_id: uuid.UUID,
    tombstone_id: uuid.UUID,
    lease_id: str | None = None,
    db: AsyncSession | None = None,
) -> dict[str, int]:
    """Erase account-connected rows and objects, leaving only the tombstone."""

    async def _erase(session: AsyncSession) -> dict[str, int]:
        active_lease_id = await claim_account_deletion_lease(
            session,
            user_id=user_id,
            tombstone_id=tombstone_id,
            lease_id=lease_id,
        )
        if active_lease_id is None:
            tombstone = (
                await session.execute(
                    select(AccountDeletionTombstone)
                    .where(AccountDeletionTombstone.id == tombstone_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if tombstone is not None and tombstone.status == "COMPLETED":
                return {"rows_deleted": 0, "objects_deleted": 0, "claimed": 0}
            return {"rows_deleted": 0, "objects_deleted": 0, "claimed": 0}

        try:
            selected = await _connected_rows(session, user_id)
            object_keys = await _owned_storage_keys(
                session,
                selected,
                user_id=user_id,
            )
            tombstone = await session.get(AccountDeletionTombstone, tombstone_id)
            if tombstone is None:
                raise LookupError("Account deletion tombstone not found")
            object_keys.update(
                normalize_storage_key(str(path))
                for path in list(tombstone.pending_artifact_refs or [])
            )
            runtime_counts = await purge_user_runtime_state(
                session,
                user_id=user_id,
                selected=selected,
                strict_pipeline_cache=True,
            )
            for key in sorted(object_keys):
                await _renew_account_deletion_lease(
                    session,
                    tombstone_id=tombstone_id,
                    lease_id=active_lease_id,
                )
                await asyncio.to_thread(delete_fits_all_versions, key)

            await session.execute(
                delete(ArtifactCleanupQueue).where(
                    ArtifactCleanupQueue.user_fingerprint
                    == deletion_user_fingerprint(user_id)
                )
            )

            await _renew_account_deletion_lease(
                session,
                tombstone_id=tombstone_id,
                lease_id=active_lease_id,
            )
            tombstone = (
                await session.execute(
                    select(AccountDeletionTombstone)
                    .where(
                        AccountDeletionTombstone.id == tombstone_id,
                        AccountDeletionTombstone.status == "RUNNING",
                        AccountDeletionTombstone.lease_id == active_lease_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if tombstone is None:
                raise AccountDeletionLeaseLost(
                    "Account deletion worker lost its lease before row erasure"
                )

            rows_deleted = 0
            # Reverse dependency order deletes children before their parents.
            for table in reversed(Base.metadata.sorted_tables):
                if table.name == "account_deletion_tombstones":
                    continue
                values = selected.get(table.name)
                if not values:
                    continue
                result = await session.execute(
                    delete(table).where(_pk_clause(table, values))
                )
                rows_deleted += int(result.rowcount or 0)

            tombstone.status = "COMPLETED"
            tombstone.completed_at = datetime.now(timezone.utc)
            tombstone.lease_id = None
            tombstone.lease_expires_at = None
            tombstone.pending_user_id = None
            tombstone.pending_artifact_refs = []
            await session.commit()
            return {
                "rows_deleted": rows_deleted,
                "objects_deleted": len(object_keys),
                "claimed": 1,
                **runtime_counts,
            }
        except Exception as exc:
            await session.rollback()
            await session.execute(
                update(AccountDeletionTombstone)
                .where(
                    AccountDeletionTombstone.id == tombstone_id,
                    AccountDeletionTombstone.status == "RUNNING",
                    AccountDeletionTombstone.lease_id == active_lease_id,
                )
                .values(
                    status="RETRYABLE",
                    lease_id=None,
                    lease_expires_at=None,
                    last_error_class=exc.__class__.__name__[:255],
                )
            )
            await session.commit()
            raise

    if db is not None:
        return await _erase(db)
    async with async_session() as session:
        return await _erase(session)
