"""Protected APIs for boot-only Workflow Registry activation delivery."""

from __future__ import annotations

import hmac
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.database import get_db
from app.services.foundry_registry_activation import (
    RegistryActivationError,
    confirm_registry_activation,
    export_verified_activation_material,
    preflight_registry_activation,
    registry_activation_readiness,
    serialize_activation_receipt,
)


router = APIRouter(
    prefix="/api/internal/foundry/registry-activations",
    tags=["internal-workflow-registry-activation"],
)


class RenderDeploymentReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["backend", "control_worker", "beat"]
    service_id_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    deploy_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class ActivationConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_request_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target_git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    activation_manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    deployment_provider: Literal["render_api_exact_commit"]
    deployments: list[RenderDeploymentReceipt] = Field(min_length=3, max_length=3)
    runtime_hot_switched: Literal[False]


def _require_activation_automation(request: Request) -> None:
    authorization = request.headers.get("Authorization", "")
    provided = authorization[7:] if authorization.startswith("Bearer ") else ""
    expected = str(
        getattr(settings, "foundry_registry_activation_result_secret", "")
        or os.getenv("FOUNDRY_REGISTRY_ACTIVATION_RESULT_SECRET")
        or ""
    )
    if (
        len(expected) < 32
        or not provided
        or not hmac.compare_digest(
            provided.encode("utf-8"), expected.encode("utf-8")
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Registry activation automation access required",
        )


def _raise_activation_error(exc: RegistryActivationError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error_class": exc.code, "message": str(exc)},
    ) from exc


@router.get("/{release_request_id}/bundle")
async def export_registry_activation_bundle(
    release_request_id: uuid.UUID,
    request: Request,
    release_request_hash: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Export one already-verified signed import and its public trust root."""

    if not settings.foundry_registration_enabled:
        raise HTTPException(status_code=404, detail="Foundry registration is not enabled")
    _require_activation_automation(request)
    try:
        return await export_verified_activation_material(
            db,
            release_request_id=release_request_id,
            release_request_hash=release_request_hash,
            trusted_public_keys=settings.workflow_registry_verification_keyring,
        )
    except RegistryActivationError as exc:
        _raise_activation_error(exc)


@router.get("/{release_request_id}/status")
async def get_registry_activation_status(
    release_request_id: uuid.UUID,
    request: Request,
    release_request_hash: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    target_git_commit: str = Query(pattern=r"^[0-9a-f]{40}$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return ready only from the freshly booted, exact target deployment."""

    if not settings.foundry_registration_enabled:
        raise HTTPException(status_code=404, detail="Foundry registration is not enabled")
    _require_activation_automation(request)
    try:
        return await registry_activation_readiness(
            db,
            release_request_id=release_request_id,
            release_request_hash=release_request_hash,
            target_git_commit=target_git_commit,
        )
    except RegistryActivationError as exc:
        _raise_activation_error(exc)


@router.get("/{release_request_id}/preflight")
async def preflight_registry_activation_deployment(
    release_request_id: uuid.UUID,
    request: Request,
    release_request_hash: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    registry_snapshot_hash: str = Query(pattern=r"^sha256:[0-9a-f]{64}$"),
    target_git_commit: str = Query(pattern=r"^[0-9a-f]{40}$"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Reject a stale signed import before Render receives a deploy request."""

    if not settings.foundry_registration_enabled:
        raise HTTPException(status_code=404, detail="Foundry registration is not enabled")
    _require_activation_automation(request)
    try:
        return await preflight_registry_activation(
            db,
            release_request_id=release_request_id,
            release_request_hash=release_request_hash,
            registry_snapshot_hash=registry_snapshot_hash,
            target_git_commit=target_git_commit,
        )
    except RegistryActivationError as exc:
        _raise_activation_error(exc)


@router.post("/{release_request_id}/confirm")
async def confirm_registry_activation_deployment(
    release_request_id: uuid.UUID,
    payload: ActivationConfirmation,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Append a receipt; this endpoint cannot activate or hot-switch runtime."""

    if not settings.foundry_registration_enabled:
        raise HTTPException(status_code=404, detail="Foundry registration is not enabled")
    _require_activation_automation(request)
    try:
        row, created = await confirm_registry_activation(
            db,
            release_request_id=release_request_id,
            release_request_hash=payload.release_request_sha256,
            target_git_commit=payload.target_git_commit,
            activation_manifest_hash=payload.activation_manifest_sha256,
            deployments=[item.model_dump(mode="json") for item in payload.deployments],
        )
    except RegistryActivationError as exc:
        _raise_activation_error(exc)
    return {
        **serialize_activation_receipt(row),
        "idempotent_replay": not created,
    }


__all__ = ["router"]
