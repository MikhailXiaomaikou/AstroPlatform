"""Two stable model tools for the signed formal-workflow registry.

The model can discover and start only server-registered workflows. Candidate
Catalog rows, candidate patches, execution parameters, and registry trust
material never enter these schemas.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.config import settings


REGISTERED_WORKFLOW_TOOL_NAMES = frozenset(
    {"discover_registered_workflows", "start_registered_workflow"}
)

REGISTERED_WORKFLOW_TOOL_SCHEMAS = [
    {
        "name": "discover_registered_workflows",
        "description": (
            "List formal, signed scientific workflows that the server currently "
            "allows. This reads only the Formal Registry; non-formal Foundry "
            "candidates and Demo results are never returned as executable tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "Optional exact formal workflow id to filter.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "start_registered_workflow",
        "description": (
            "Create an Audit for one compatible formal workflow. Supply only "
            "server-issued Workspace, Source, and extracted-candidate ids; the "
            "server reconstructs datasets, parameters, thresholds, entrypoint, "
            "and registry binding. Never use this for Foundry candidates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "workspace_id": {"type": "string", "format": "uuid"},
                "source_id": {"type": "string", "format": "uuid"},
                "candidate_id": {
                    "type": "string",
                    "description": (
                        "Server-issued candidate id from the registered source "
                        "extraction, not a Workflow Foundry candidate id."
                    ),
                },
            },
            "required": [
                "workflow_id",
                "workspace_id",
                "source_id",
                "candidate_id",
            ],
            "additionalProperties": False,
        },
    },
]


def _failure(error_class: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "analysis_status": "FAILED",
        "error_class": error_class,
        "error": message,
        "formal_registry_only": True,
        "candidate_catalog_included": False,
        "publication_ready": False,
        "__do_not_claim__": True,
        "__tool_status__": "FAILED",
        "__message_to_model__": (
            f"Formal workflow operation failed: {message} Do not infer a "
            "scientific result or treat a Foundry Demo as evidence."
        ),
    }


def _registry_enabled() -> bool:
    return bool(getattr(settings, "workflow_registry_v2_enabled", False))


def _discover_registered_workflows(inp: dict[str, Any]) -> dict[str, Any]:
    if not _registry_enabled():
        return _failure(
            "workflow_registry_disabled",
            "The Formal Workflow Registry is not enabled.",
        )
    try:
        from app.services.workflow_registry_v2 import list_formal_workflows

        workflows = list_formal_workflows()
    except Exception as exc:
        return _failure(
            str(getattr(exc, "code", None) or "workflow_registry_unavailable"),
            "The Formal Workflow Registry could not be verified.",
        )
    requested = str(inp.get("workflow_id") or "").strip()
    if requested:
        workflows = [
            workflow
            for workflow in workflows
            if workflow.get("workflow_id") == requested
        ]
    return {
        "success": True,
        "analysis_status": "COMPLETED",
        "formal_registry_only": True,
        "candidate_catalog_included": False,
        "workflow_count": len(workflows),
        "workflows": workflows,
        "publication_ready": False,
        "__do_not_claim__": True,
        "__tool_status__": "COMPLETED",
        "__message_to_model__": (
            "This is a capability catalog, not a scientific result. Only a "
            "completed Audit with independent verification and human result "
            "review can support a claim."
        ),
    }


async def _start_registered_workflow(
    inp: dict[str, Any],
    *,
    user_id: str | None,
) -> dict[str, Any]:
    if not _registry_enabled():
        return _failure(
            "workflow_registry_disabled",
            "The Formal Workflow Registry is not enabled.",
        )
    if not user_id:
        return _failure(
            "authentication_required",
            "Sign in before starting a registered scientific workflow.",
        )
    required_features = (
        "claim_audit_enabled",
        "research_workspace_enabled",
        "arxiv_reader_enabled",
        "union3_reproduction_enabled",
        "local_science_worker_enabled",
        "evidence_pack_v2_enabled",
    )
    if not all(bool(getattr(settings, name, False)) for name in required_features):
        return _failure(
            "registered_workflow_execution_disabled",
            "Registered workflow execution is still behind its release gates.",
        )

    try:
        owner_id = uuid.UUID(str(user_id))
        workspace_id = uuid.UUID(str(inp.get("workspace_id") or ""))
        source_id = uuid.UUID(str(inp.get("source_id") or ""))
    except ValueError:
        return _failure(
            "registered_workflow_identifier_invalid",
            "Workspace, Source, and account ids must be valid UUIDs.",
        )
    workflow_id = str(inp.get("workflow_id") or "").strip()
    candidate_id = str(inp.get("candidate_id") or "").strip()
    if not workflow_id or not candidate_id or len(candidate_id) > 128:
        return _failure(
            "registered_workflow_identifier_invalid",
            "A formal workflow id and server-issued source candidate id are required.",
        )

    try:
        from app.services.workflow_registry_v2 import (
            UNION3_REPRODUCTION_WORKFLOW_ID,
            assert_workflow_executable,
        )

        binding = assert_workflow_executable(workflow_id)
    except Exception as exc:
        return _failure(
            str(getattr(exc, "code", None) or "workflow_not_registered"),
            "The requested workflow is not active in the verified Formal Registry.",
        )
    if workflow_id != UNION3_REPRODUCTION_WORKFLOW_ID:
        return _failure(
            "registered_workflow_start_not_available",
            "This formal workflow has no Workspace start adapter in this release.",
        )

    try:
        from app.models.database import async_session
        from app.services.union3_research_loop import (
            create_union3_reproduction_audit,
        )

        async with async_session() as db:
            audit, job = await create_union3_reproduction_audit(
                db,
                user_id=owner_id,
                workspace_id=workspace_id,
                source_document_id=source_id,
                candidate_id=candidate_id,
                workflow_key=workflow_id,
            )
    except Exception as exc:
        error_code = str(
            getattr(exc, "code", None) or "registered_workflow_start_failed"
        )
        return _failure(
            error_code,
            (
                str(exc)
                if getattr(exc, "code", None)
                else "The registered workflow could not be started."
            ),
        )
    return {
        "success": True,
        "analysis_status": "QUEUED",
        "formal_registry_only": True,
        "candidate_catalog_included": False,
        "audit_id": str(audit.id),
        "job_id": job.job_id,
        "lifecycle_status": audit.lifecycle_status,
        "progress_stage": audit.progress_stage,
        "workflow_id": binding["workflow_id"],
        "workflow_version": binding["workflow_version"],
        "registry_epoch": binding["registry_epoch"],
        "registry_entry_hash": binding["registry_entry_hash"],
        "publication_ready": False,
        "__do_not_claim__": True,
        "__tool_status__": "COMPLETED",
        "__message_to_model__": (
            "The formal Audit was queued. This is not a scientific result; wait "
            "for independent verification and human result review."
        ),
    }


async def dispatch_registered_workflow_tool(
    tool_name: str,
    inp: dict[str, Any],
    *,
    user_id: str | None,
) -> dict[str, Any]:
    if tool_name == "discover_registered_workflows":
        return _discover_registered_workflows(inp)
    if tool_name == "start_registered_workflow":
        return await _start_registered_workflow(inp, user_id=user_id)
    return _failure("unknown_registered_workflow_tool", "Unknown formal workflow tool.")


__all__ = [
    "REGISTERED_WORKFLOW_TOOL_NAMES",
    "REGISTERED_WORKFLOW_TOOL_SCHEMAS",
    "dispatch_registered_workflow_tool",
]
