"""Owner and signed-node APIs for the HTTPS local-science worker protocol."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.config import settings
from app.models.database import get_db
from app.models.research_records import ResearchJob
from app.models.schemas import User
from app.models.worker_records import (
    ScienceExecutionAttempt,
    WorkerArtifactIssuance,
    WorkerNode,
)
from app.services.worker_contract import (
    DEVELOPMENT_RELEASE_COMMIT,
    LEGACY_WORKER_PROTOCOL_VERSION,
    SUPPORTED_WORKER_PROTOCOL_VERSIONS,
    UNRESOLVED_RELEASE_COMMITS,
)
from app.services.worker_protocol import (
    WORKER_PROTOCOL_VERSION,
    WorkerProtocolError,
    acknowledge_cancel,
    complete_attempt,
    create_enrollment_token,
    enroll_worker_node,
    fail_attempt,
    heartbeat_attempt,
    lease_next_task,
    lock_active_attempt_state,
    prepare_attempt_completion,
    verify_worker_request,
)
from app.services.workflow_registry_v2 import (
    WorkflowRegistryError,
    list_static_worker_entrypoint_capabilities,
    list_worker_execution_bindings,
    worker_image_digest_is_approved,
)
from app.storage import (
    StorageIntegrityError,
    create_presigned_upload_url,
    lock_active_storage_owner,
    promote_verified_storage_object,
    verify_storage_object,
)

router = APIRouter(prefix="/api/compute/v1", tags=["local-science-workers"])
logger = logging.getLogger(__name__)
_FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}", re.IGNORECASE)
_ARTIFACT_UPLOAD_URL_TTL_SECONDS = 15 * 60
_MAX_ARTIFACT_ISSUANCES_PER_ATTEMPT = 256
_MAX_ARTIFACT_ISSUED_BYTES_PER_ATTEMPT = 8 * 1024 * 1024 * 1024


class EnrollNodeRequest(BaseModel):
    enrollment_code: str = Field(min_length=32, max_length=256)
    name: str = Field(min_length=1, max_length=160)
    public_key: str = Field(min_length=40, max_length=128)
    protocol_version: str = WORKER_PROTOCOL_VERSION
    capabilities: dict[str, Any] = Field(default_factory=dict)
    release_manifest: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    lease_id: str = Field(min_length=64, max_length=64)
    progress: float | None = Field(default=None, ge=0.0, le=1.0)
    checkpoint: dict[str, Any] | None = None


class CompleteAttemptRequest(BaseModel):
    lease_id: str = Field(min_length=64, max_length=64)
    result: dict[str, Any]
    result_hash: str | None = Field(default=None, max_length=71)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


class FailAttemptRequest(BaseModel):
    lease_id: str = Field(min_length=64, max_length=64)
    error_class: str = Field(min_length=1, max_length=255)
    retryable: bool = True


class CancelAckRequest(BaseModel):
    lease_id: str = Field(min_length=64, max_length=64)


class ArtifactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(min_length=64, max_length=71)
    size_bytes: int = Field(gt=0, le=2 * 1024 * 1024 * 1024)
    content_type: str = Field(default="application/octet-stream", max_length=255)


class ArtifactUrlsRequest(BaseModel):
    lease_id: str = Field(min_length=64, max_length=64)
    artifacts: list[ArtifactRequest] = Field(min_length=1, max_length=64)


def _require_enabled() -> None:
    if not bool(getattr(settings, "local_science_worker_enabled", False)):
        raise HTTPException(
            status_code=404, detail="Local science workers are not enabled"
        )


def _raise_protocol(exc: WorkerProtocolError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


def _is_production() -> bool:
    return os.getenv("ENV", "dev").strip().lower() in {"prod", "production"}


def _serialize_node(node: WorkerNode) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_seen = node.last_seen_at
    if last_seen is not None and last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    online = bool(
        node.status in {"ACTIVE", "DRAINING"}
        and last_seen is not None
        and (now - last_seen).total_seconds() <= 180
    )
    return {
        "node_id": str(node.id),
        "name": node.name,
        "status": node.status,
        "online": online,
        "protocol_version": node.protocol_version,
        "public_key_fingerprint": node.public_key_fingerprint,
        "capabilities": dict(node.capabilities or {}),
        "release_manifest": dict(node.release_manifest or {}),
        "last_seen_at": node.last_seen_at.isoformat() if node.last_seen_at else None,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "revoked_at": node.revoked_at.isoformat() if node.revoked_at else None,
    }


def _serialize_attempt(attempt: ScienceExecutionAttempt) -> dict[str, Any]:
    return {
        "attempt_id": str(attempt.id),
        "job_id": attempt.job_id,
        "audit_id": str(attempt.audit_id) if attempt.audit_id else None,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "lease_expires_at": attempt.lease_expires_at.isoformat(),
        "result_hash": f"sha256:{attempt.result_hash}" if attempt.result_hash else None,
        "completed_at": attempt.completed_at.isoformat()
        if attempt.completed_at
        else None,
    }


async def _signed_worker(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> WorkerNode:
    _require_enabled()
    try:
        return await verify_worker_request(
            db,
            method=request.method,
            path=request.url.path,
            headers=request.headers,
            body=await request.body(),
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)


def _release_commit() -> str:
    value = (
        str(
            os.getenv("RENDER_GIT_COMMIT")
            or os.getenv("GIT_COMMIT")
            or os.getenv("TOOL_VERSION")
            or ""
        )
        .strip()
        .lower()
    )
    if _FULL_GIT_SHA.fullmatch(value):
        return value
    if _is_production():
        raise HTTPException(status_code=503, detail="release_commit_unavailable")
    return DEVELOPMENT_RELEASE_COMMIT


@router.post("/enrollments", status_code=status.HTTP_201_CREATED)
async def create_enrollment(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    try:
        token, raw_code = await create_enrollment_token(db, user_id=user.id)
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    return {
        "enrollment_id": str(token.id),
        "enrollment_code": raw_code,
        "expires_at": token.expires_at.isoformat(),
        "display_once": True,
    }


@router.post("/nodes/enroll", status_code=status.HTTP_201_CREATED)
async def enroll_node(req: EnrollNodeRequest, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    if req.protocol_version not in SUPPORTED_WORKER_PROTOCOL_VERSIONS:
        raise HTTPException(status_code=426, detail="worker_upgrade_required")
    advertised_workflows = req.capabilities.get("workflows")
    if req.capabilities.get("concurrency") != 1:
        raise HTTPException(status_code=422, detail="worker_concurrency_must_be_one")
    required_digest = str(settings.docker_image_digest or "").strip().lower()
    observed_digest = (
        str(req.release_manifest.get("image_digest") or "").strip().lower()
    )
    observed_commit = str(req.release_manifest.get("git_commit") or "").strip().lower()
    if req.protocol_version == LEGACY_WORKER_PROTOCOL_VERSION:
        if advertised_workflows != ["union3_flat_lcdm_sn_only_v1"]:
            raise HTTPException(
                status_code=422, detail="worker_capabilities_not_registered"
            )
    else:
        try:
            if set(req.capabilities) == {"entrypoints", "concurrency"}:
                expected_capabilities = list_static_worker_entrypoint_capabilities(
                    worker_image_digest=observed_digest or "unknown"
                )
                advertised_capabilities = req.capabilities.get("entrypoints")
            else:
                # Exact workflow receipts remain accepted for nodes enrolled
                # before the static-entrypoint capability rollout. They cannot
                # acquire later aliases and are not widened during leasing.
                expected_capabilities = list_worker_execution_bindings(
                    worker_image_digest=observed_digest or "unknown"
                )
                advertised_capabilities = advertised_workflows
        except WorkflowRegistryError as exc:
            raise HTTPException(status_code=422, detail=exc.code) from exc
        if (
            set(req.capabilities)
            not in (
                {"entrypoints", "concurrency"},
                {"workflows", "concurrency"},
            )
            or advertised_capabilities != expected_capabilities
        ):
            raise HTTPException(
                status_code=422, detail="worker_capabilities_not_registered"
            )
    if req.protocol_version == LEGACY_WORKER_PROTOCOL_VERSION:
        if required_digest and observed_digest != required_digest:
            raise HTTPException(status_code=422, detail="worker_image_digest_mismatch")
    elif (required_digest or _is_production()) and not worker_image_digest_is_approved(
        observed_digest,
        builtin_worker_image_digest=required_digest,
    ):
        # A v2 node's self-reported entrypoint/digest tuple proves only what it
        # claims to contain.  Trust comes from the signed candidate release or,
        # for built-ins, the operator-pinned official image digest.
        raise HTTPException(status_code=422, detail="worker_image_digest_mismatch")
    if _is_production():
        if not _FULL_GIT_SHA.fullmatch(observed_commit):
            raise HTTPException(status_code=422, detail="worker_release_commit_invalid")
        if (
            req.protocol_version == LEGACY_WORKER_PROTOCOL_VERSION
            and observed_commit != _release_commit()
        ):
            raise HTTPException(
                status_code=422, detail="worker_release_commit_mismatch"
            )
    try:
        node = await enroll_worker_node(
            db,
            enrollment_code=req.enrollment_code,
            name=req.name,
            public_key=req.public_key,
            protocol_version=req.protocol_version,
            capabilities=req.capabilities,
            release_manifest=req.release_manifest,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    return {
        **_serialize_node(node),
        "task_signing_key": {
            "algorithm": "ed25519",
            "key_id": settings.worker_task_signing_key_id,
            "public_key": settings.worker_task_signing_public_key,
        },
    }


@router.get("/nodes")
async def list_nodes(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    nodes = (
        await db.execute(
            select(WorkerNode)
            .where(WorkerNode.user_id == user.id)
            .order_by(WorkerNode.created_at.desc())
        )
    ).scalars()
    return {"nodes": [_serialize_node(node) for node in nodes]}


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_node(
    node_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _require_enabled()
    node = (
        await db.execute(
            select(WorkerNode)
            .where(WorkerNode.id == node_id, WorkerNode.user_id == user.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Worker node not found")
    if node.status != "REVOKED":
        revoked_at = datetime.now(timezone.utc)
        node.status = "REVOKED"
        node.revoked_at = revoked_at
        active_attempts = (
            await db.execute(
                select(ScienceExecutionAttempt)
                .where(
                    ScienceExecutionAttempt.worker_node_id == node.id,
                    ScienceExecutionAttempt.status.in_({"LEASED", "RUNNING"}),
                )
                .with_for_update()
            )
        ).scalars()
        for attempt in active_attempts:
            attempt.status = "EXPIRED"
            attempt.error_class = "worker_revoked"
            attempt.completed_at = revoked_at
            job = await db.get(ResearchJob, attempt.job_id)
            if job is not None and job.status == "RUNNING":
                job.status = "QUEUED"
                job.current_attempt_id = None
                job.progress_message = "waiting_for_worker"
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/tasks/claim")
async def claim_task(
    wait_seconds: int = Query(default=25, ge=0, le=25),
    node: WorkerNode = Depends(_signed_worker),
    db: AsyncSession = Depends(get_db),
):
    control_release_commit = _release_commit()
    observed_manifest = {
        "git_commit": str(node.release_manifest.get("git_commit") or "")
        .strip()
        .lower(),
        "image_digest": str(node.release_manifest.get("image_digest") or "")
        .strip()
        .lower(),
    }
    pinned_control_commit = (
        control_release_commit
        if _FULL_GIT_SHA.fullmatch(control_release_commit)
        else ""
    )
    node_protocol_version = str(
        getattr(node, "protocol_version", LEGACY_WORKER_PROTOCOL_VERSION)
    )
    required_manifest = {
        # Development enrollment permits an absent or descriptive commit. Do
        # not turn that accepted node into an unclaimable one. A real control-
        # plane SHA remains an exact legacy-worker gate. Protocol v2 binds an
        # independently signed image digest through the active Registry.
        "git_commit": (
            pinned_control_commit
            if node_protocol_version == LEGACY_WORKER_PROTOCOL_VERSION
            else ""
        ),
        "image_digest": (
            str(settings.docker_image_digest or "").strip().lower()
            if node_protocol_version == LEGACY_WORKER_PROTOCOL_VERSION
            else ""
        ),
    }
    if any(
        required_manifest[key] and observed_manifest[key] != required_manifest[key]
        for key in required_manifest
    ):
        raise HTTPException(status_code=409, detail="worker_release_manifest_stale")
    release_commit = control_release_commit
    if node_protocol_version == WORKER_PROTOCOL_VERSION:
        release_commit = observed_manifest["git_commit"]
    elif (
        not pinned_control_commit
        and observed_manifest["git_commit"] not in UNRESOLVED_RELEASE_COMMITS
    ):
        # The control plane has no pinned development revision, but this node
        # has an enrolled identity. Preserve it in the signed task so the
        # Worker can verify the durable lease it is about to receive. This is
        # an execution binding only; the evidence gate still requires a full
        # Git SHA before a result can support a scientific verdict.
        release_commit = observed_manifest["git_commit"]
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        try:
            attempt = await lease_next_task(
                db,
                node=node,
                private_key=settings.worker_task_signing_private_key,
                key_id=settings.worker_task_signing_key_id,
                release_commit=release_commit,
                image_digest=observed_manifest["image_digest"],
            )
        except WorkerProtocolError as exc:
            _raise_protocol(exc)
        if attempt is not None:
            return attempt.task_envelope
        if asyncio.get_running_loop().time() >= deadline:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        # Release the PostgreSQL connection between poll iterations. The
        # request remains open, but dozens of idle user nodes must not exhaust
        # the small control-plane connection pool.
        await db.rollback()
        await asyncio.sleep(1)


@router.put("/attempts/{attempt_id}/heartbeat")
async def heartbeat(
    attempt_id: uuid.UUID,
    req: HeartbeatRequest,
    node: WorkerNode = Depends(_signed_worker),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await heartbeat_attempt(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=req.lease_id,
            progress=req.progress,
            checkpoint=req.checkpoint,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)


def _clean_artifact_name(value: str) -> str:
    name = PurePosixPath(value.strip()).name
    if name in {"", ".", ".."} or name != value.strip() or "\\" in value:
        raise HTTPException(status_code=422, detail="invalid_artifact_name")
    return name


def _clean_artifact_digest(value: str) -> str:
    digest = value.removeprefix("sha256:").lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise HTTPException(status_code=422, detail="invalid_artifact_sha256")
    return digest


def _clean_artifact_content_type(value: str) -> str:
    content_type = str(value or "").strip()
    if not content_type or any(ord(ch) < 32 for ch in content_type):
        raise HTTPException(status_code=422, detail="invalid_artifact_content_type")
    return content_type


def _sort_issuances(
    rows: list[WorkerArtifactIssuance],
) -> list[WorkerArtifactIssuance]:
    return sorted(rows, key=lambda row: str(row.id))


@router.post("/attempts/{attempt_id}/artifact-urls")
async def artifact_urls(
    attempt_id: uuid.UUID,
    req: ArtifactUrlsRequest,
    node: WorkerNode = Depends(_signed_worker),
    db: AsyncSession = Depends(get_db),
):
    # Account deletion takes this same User lock before changing account_status.
    # Taking it before the Attempt lock gives both paths one lock order and
    # makes it impossible to return a new upload capability after deletion won.
    try:
        await lock_active_storage_owner(db, node.user_id)
        attempt, _job, _audit = await lock_active_attempt_state(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=req.lease_id,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail="artifact_owner_inactive") from exc
    artifact_names = [_clean_artifact_name(item.name) for item in req.artifacts]
    artifact_digests = [_clean_artifact_digest(item.sha256) for item in req.artifacts]
    artifact_content_types = [
        _clean_artifact_content_type(item.content_type) for item in req.artifacts
    ]
    if len(set(artifact_names)) != len(artifact_names):
        raise HTTPException(status_code=422, detail="duplicate_artifact_name")
    if sum(item.size_bytes for item in req.artifacts) > 4 * 1024 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="artifact_batch_too_large")

    existing = list(
        (
            await db.execute(
                select(WorkerArtifactIssuance)
                .where(WorkerArtifactIssuance.attempt_id == attempt.id)
                .order_by(WorkerArtifactIssuance.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if len(existing) + len(req.artifacts) > _MAX_ARTIFACT_ISSUANCES_PER_ATTEMPT:
        raise HTTPException(status_code=409, detail="artifact_issuance_limit_reached")
    if (
        sum(row.size_bytes for row in existing)
        + sum(item.size_bytes for item in req.artifacts)
        > _MAX_ARTIFACT_ISSUED_BYTES_PER_ATTEMPT
    ):
        raise HTTPException(status_code=413, detail="artifact_attempt_bytes_exceeded")

    batch_id = uuid.uuid4()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=_ARTIFACT_UPLOAD_URL_TTL_SECONDS)
    issued_rows: list[WorkerArtifactIssuance] = []
    for item, name, digest, content_type in zip(
        req.artifacts,
        artifact_names,
        artifact_digests,
        artifact_content_types,
        strict=True,
    ):
        artifact_id = hashlib.sha256(f"{name}\0{digest}".encode("utf-8")).hexdigest()[
            :16
        ]
        issuance_id = uuid.uuid4()
        staging_key = (
            f"science-attempts/{attempt.user_id}/{attempt.id}/"
            f"uploads/{issuance_id}-{artifact_id}-{name}"
        )
        authoritative_key = (
            f"science-attempts/{attempt.user_id}/{attempt.id}/"
            f"verified/{issuance_id}-{artifact_id}-{name}"
        )
        issued_rows.append(
            WorkerArtifactIssuance(
                id=issuance_id,
                batch_id=batch_id,
                attempt_id=attempt.id,
                user_id=attempt.user_id,
                worker_node_id=node.id,
                artifact_name=name,
                artifact_ref=staging_key,
                authoritative_ref=authoritative_key,
                sha256=digest,
                size_bytes=item.size_bytes,
                content_type=content_type,
                expires_at=expires_at,
            )
        )

    all_rows = _sort_issuances([*existing, *issued_rows])
    attempt.artifact_manifest = [
        _issuance_manifest_record(row, status="UPLOAD_REQUESTED") for row in all_rows
    ]
    db.add_all(issued_rows)
    # The append-only discovery receipt is authoritative before any bearer URL
    # can leave the API process.  If URL generation or response delivery fails,
    # deletion still knows every possible object key.
    await db.commit()

    uploads: list[dict[str, Any]] = []
    for row in issued_rows:
        try:
            upload = await asyncio.to_thread(
                create_presigned_upload_url,
                row.artifact_ref,
                sha256=row.sha256,
                size_bytes=row.size_bytes,
                content_type=row.content_type,
                expires_seconds=_ARTIFACT_UPLOAD_URL_TTL_SECONDS,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        uploads.append(
            {
                **upload,
                "issuance_id": str(row.id),
                "artifact_name": row.artifact_name,
            }
        )
    return {"batch_id": str(batch_id), "uploads": uploads}


def _issuance_manifest_record(
    row: WorkerArtifactIssuance,
    *,
    status: str,
    verification: dict[str, Any] | None = None,
    authoritative: bool = False,
) -> dict[str, Any]:
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    record = {
        "issuance_id": str(row.id),
        "batch_id": str(row.batch_id),
        "artifact_name": row.artifact_name,
        "artifact_ref": row.authoritative_ref if authoritative else row.artifact_ref,
        "staging_artifact_ref": row.artifact_ref,
        "authoritative_artifact_ref": row.authoritative_ref,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "content_type": row.content_type,
        "upload_expires_at": expires_at.astimezone(timezone.utc).isoformat(),
        "status": status,
    }
    if verification is not None:
        record["verification_method"] = verification["verification_method"]
        record["version_id"] = verification.get("version_id")
    return record


async def _verify_completed_artifacts(
    db: AsyncSession,
    attempt: ScienceExecutionAttempt,
    supplied: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = list(
        (
            await db.execute(
                select(WorkerArtifactIssuance)
                .where(WorkerArtifactIssuance.attempt_id == attempt.id)
                .order_by(WorkerArtifactIssuance.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if any(
        row.user_id != attempt.user_id or row.worker_node_id != attempt.worker_node_id
        for row in rows
    ):
        raise HTTPException(status_code=409, detail="artifact_ledger_binding_mismatch")
    rows = _sort_issuances(rows)
    supplied_refs = [str(item.get("artifact_ref") or "") for item in supplied]
    if attempt.status == "SUCCEEDED":
        final_manifest = list(attempt.artifact_manifest or [])
        verified_refs = {
            str(item.get("staging_artifact_ref") or "")
            for item in final_manifest
            if isinstance(item, dict) and item.get("status") == "VERIFIED"
        }
        if (
            any(not value for value in supplied_refs)
            or len(supplied_refs) != len(set(supplied_refs))
            or set(supplied_refs) != verified_refs
        ):
            raise HTTPException(status_code=409, detail="conflicting_artifact_replay")
        return final_manifest

    expected_pending_manifest = [
        _issuance_manifest_record(row, status="UPLOAD_REQUESTED") for row in rows
    ]
    if list(attempt.artifact_manifest or []) != expected_pending_manifest:
        raise HTTPException(status_code=409, detail="artifact_ledger_manifest_mismatch")

    if any(not value for value in supplied_refs) or len(supplied_refs) != len(
        set(supplied_refs)
    ):
        raise HTTPException(status_code=422, detail="artifact_manifest_mismatch")
    rows_by_ref = {row.artifact_ref: row for row in rows}
    if any(artifact_ref not in rows_by_ref for artifact_ref in supplied_refs):
        raise HTTPException(status_code=422, detail="artifact_manifest_mismatch")

    rows_by_name: dict[str, list[WorkerArtifactIssuance]] = defaultdict(list)
    for row in rows:
        rows_by_name[row.artifact_name].append(row)
    chosen_rows = [rows_by_ref[artifact_ref] for artifact_ref in supplied_refs]
    chosen_by_name: dict[str, WorkerArtifactIssuance] = {}
    for row in chosen_rows:
        if row.artifact_name in chosen_by_name:
            raise HTTPException(status_code=422, detail="duplicate_artifact_name")
        chosen_by_name[row.artifact_name] = row
    if set(chosen_by_name) != set(rows_by_name):
        raise HTTPException(status_code=422, detail="artifact_manifest_mismatch")

    verifications: dict[uuid.UUID, dict[str, Any]] = {}
    prefix = f"science-attempts/{attempt.user_id}/{attempt.id}/"
    for row in chosen_rows:
        if not row.artifact_ref.startswith(f"{prefix}uploads/") or not str(
            row.authoritative_ref
        ).startswith(f"{prefix}verified/"):
            raise HTTPException(
                status_code=422, detail="artifact_owner_binding_mismatch"
            )
        try:
            source_observed = await asyncio.to_thread(
                verify_storage_object,
                row.artifact_ref,
                expected_sha256=row.sha256,
                expected_size_bytes=row.size_bytes,
            )
            observed = await asyncio.to_thread(
                promote_verified_storage_object,
                row.artifact_ref,
                row.authoritative_ref,
                expected_sha256=row.sha256,
                expected_size_bytes=row.size_bytes,
                content_type=row.content_type,
                source_version_id=source_observed.get("version_id"),
                source_etag=source_observed.get("etag"),
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=422, detail="artifact_upload_missing"
            ) from exc
        except StorageIntegrityError as exc:
            raise HTTPException(
                status_code=422, detail="artifact_integrity_mismatch"
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="artifact_verification_unavailable"
            ) from exc
        row.authoritative_version_id = observed.get("version_id")
        row.verification_method = str(observed["verification_method"])
        row.verified_at = datetime.now(timezone.utc)
        verifications[row.id] = observed

    # Keep every staging key discoverable until its URL expires.  A separate
    # VERIFIED record points only at the server-controlled authoritative key;
    # the Worker has never received a write capability for that destination.
    final_manifest: list[dict[str, Any]] = []
    for row in rows:
        final_manifest.append(
            _issuance_manifest_record(
                row,
                status=(
                    "STAGING_PENDING_CLEANUP"
                    if row.id in verifications
                    else "SUPERSEDED_PENDING_CLEANUP"
                ),
            )
        )
        if row.id in verifications:
            final_manifest.append(
                _issuance_manifest_record(
                    row,
                    status="VERIFIED",
                    verification=verifications[row.id],
                    authoritative=True,
                )
            )
    return final_manifest


@router.post("/attempts/{attempt_id}/complete")
async def complete(
    attempt_id: uuid.UUID,
    req: CompleteAttemptRequest,
    node: WorkerNode = Depends(_signed_worker),
    db: AsyncSession = Depends(get_db),
):
    try:
        await lock_active_storage_owner(db, node.user_id)
        prepared = await prepare_attempt_completion(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=req.lease_id,
            result=req.result,
            claimed_result_hash=req.result_hash,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail="artifact_owner_inactive") from exc
    verified_artifacts = await _verify_completed_artifacts(
        db, prepared.attempt, req.artifacts
    )
    try:
        attempt, replayed = await complete_attempt(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=req.lease_id,
            result=req.result,
            claimed_result_hash=req.result_hash,
            diagnostics=req.diagnostics,
            artifact_manifest=verified_artifacts,
            prepared=prepared,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    if not replayed:
        try:
            from app.tasks.union3_research_tasks import (
                enqueue_union3_verification,
            )

            enqueue_union3_verification(attempt.id)
        except Exception:
            # PostgreSQL already contains the completed attempt.  Beat's
            # reconciler will rediscover it, so a broker outage must not turn a
            # valid worker submission into a false client failure.
            logger.exception(
                "Could not wake Union3 verifier for attempt %s; reconciliation pending",
                attempt.id,
            )
    return {**_serialize_attempt(attempt), "idempotent_replay": replayed}


@router.post("/attempts/{attempt_id}/fail")
async def fail(
    attempt_id: uuid.UUID,
    req: FailAttemptRequest,
    node: WorkerNode = Depends(_signed_worker),
    db: AsyncSession = Depends(get_db),
):
    try:
        attempt = await fail_attempt(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=req.lease_id,
            error_class=req.error_class,
            retryable=req.retryable,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    return _serialize_attempt(attempt)


@router.post("/attempts/{attempt_id}/cancel-ack")
async def cancel_ack(
    attempt_id: uuid.UUID,
    req: CancelAckRequest,
    node: WorkerNode = Depends(_signed_worker),
    db: AsyncSession = Depends(get_db),
):
    try:
        attempt = await acknowledge_cancel(
            db,
            node=node,
            attempt_id=attempt_id,
            lease_id=req.lease_id,
        )
    except WorkerProtocolError as exc:
        _raise_protocol(exc)
    return _serialize_attempt(attempt)
