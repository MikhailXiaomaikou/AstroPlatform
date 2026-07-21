"""Append-only receipts for boot-time Workflow Registry activations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base
from app.models.schemas import JSONType, UUIDType


class WorkflowRegistryActivationReceipt(Base):
    """Immutable proof that one imported release booted on an exact commit."""

    __tablename__ = "workflow_registry_activation_receipts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), primary_key=True, default=uuid.uuid4
    )
    release_import_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("workflow_registry_release_imports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    release_request_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("workflow_registry_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    release_request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    registry_epoch: Mapped[str] = mapped_column(String(128), nullable=False)
    registry_snapshot_hash: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    activation_manifest_hash: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    signed_snapshot_file_hash: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    trusted_keyring_file_hash: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    target_git_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    deployment_provider: Mapped[str] = mapped_column(
        String(64), nullable=False, default="render_api_exact_commit"
    )
    deployment_receipts: Mapped[list] = mapped_column(
        JSONType(), nullable=False, default=list
    )
    deployment_set_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    projection_summary: Mapped[dict] = mapped_column(
        JSONType(), nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    runtime_hot_switched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    projection_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "deployment_provider = 'render_api_exact_commit'",
            name="ck_registry_activation_exact_commit_provider",
        ),
        CheckConstraint(
            "status = 'ACTIVE'", name="ck_registry_activation_active_only"
        ),
        CheckConstraint(
            "runtime_hot_switched IS FALSE",
            name="ck_registry_activation_no_hot_switch",
        ),
        CheckConstraint(
            "projection_verified IS TRUE",
            name="ck_registry_activation_projection_verified",
        ),
        UniqueConstraint(
            "release_import_id", name="uq_registry_activation_release_import"
        ),
        UniqueConstraint(
            "release_request_id", name="uq_registry_activation_release_request"
        ),
        UniqueConstraint(
            "registry_epoch", name="uq_registry_activation_epoch"
        ),
        UniqueConstraint(
            "registry_snapshot_hash", name="uq_registry_activation_snapshot_hash"
        ),
        UniqueConstraint(
            "activation_manifest_hash",
            name="uq_registry_activation_manifest_hash",
        ),
        UniqueConstraint(
            "receipt_hash", name="uq_registry_activation_receipt_hash"
        ),
    )


def _reject_activation_receipt_mutation(_mapper, _connection, target) -> None:
    raise ValueError(f"{type(target).__name__} records are append-only")


event.listen(
    WorkflowRegistryActivationReceipt,
    "before_update",
    _reject_activation_receipt_mutation,
)
event.listen(
    WorkflowRegistryActivationReceipt,
    "before_delete",
    _reject_activation_receipt_mutation,
)


__all__ = ["WorkflowRegistryActivationReceipt"]
