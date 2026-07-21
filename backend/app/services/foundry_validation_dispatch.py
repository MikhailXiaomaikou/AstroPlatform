"""Narrow GitHub Actions dispatcher for isolated candidate Demo validation."""

from __future__ import annotations

import json
import re
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.foundry_records import FoundryCandidateVersion, FoundryValidationRun
from app.services.foundry_catalog import (
    FoundryCatalogError,
    ai_draft_validation_binding,
    candidate_validation_binding,
    ensure_validation_run,
    record_validation_dispatch,
)


_CANDIDATE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
)


class FoundryValidationDispatchError(RuntimeError):
    """Sanitized dispatch failure with explicit delivery semantics."""

    def __init__(
        self,
        failure_class: str,
        *,
        retryable: bool | None = None,
        delivery_uncertain: bool | None = None,
    ):
        super().__init__(failure_class)
        self.failure_class = failure_class
        inferred_uncertain = failure_class in {
            "validation_dispatch_timeout",
            "validation_dispatch_network_error",
            "validation_dispatch_internal_error",
        } or bool(re.fullmatch(r"validation_dispatch_http_5[0-9]{2}", failure_class))
        inferred_retryable = inferred_uncertain or failure_class == "validation_dispatch_http_429"
        self.delivery_uncertain = (
            inferred_uncertain
            if delivery_uncertain is None
            else bool(delivery_uncertain)
        )
        self.retryable = (
            inferred_retryable if retryable is None else bool(retryable)
        )


async def dispatch_candidate_validation(
    *,
    validation_run_id: uuid.UUID,
    candidate_key: str,
    version_binding: dict[str, str],
    draft_binding: dict[str, str] | None = None,
) -> None:
    """Dispatch only opaque identifiers; candidate code/data never cross this API."""

    if not _CANDIDATE_KEY_RE.fullmatch(str(candidate_key or "")):
        raise FoundryValidationDispatchError("invalid_candidate_key")
    if settings.foundry_validation_dispatch_backend != "github_actions":
        raise FoundryValidationDispatchError("validation_dispatch_disabled")
    repository = str(settings.foundry_validation_github_repository or "").strip()
    workflow = str(settings.foundry_validation_github_workflow or "").strip()
    ref = str(settings.foundry_validation_github_ref or "").strip()
    token = str(settings.foundry_validation_github_token or "")
    if (
        repository.count("/") != 1
        or not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow)
        or ref != "main"
        or len(token) < 32
    ):
        raise FoundryValidationDispatchError("validation_dispatch_misconfigured")
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    inputs = {
        "candidate_key": candidate_key,
        "validation_run_id": str(validation_run_id),
    }
    version_required = {
        "candidate_id",
        "candidate_version_id",
        "candidate_key",
        "candidate_version_number",
        "candidate_version_hash",
        "candidate_bundle_hash",
        "validation_runner_image_digest",
    }
    if set(version_binding) != version_required:
        raise FoundryValidationDispatchError("version_binding_shape_invalid")
    try:
        uuid.UUID(version_binding["candidate_id"])
        uuid.UUID(version_binding["candidate_version_id"])
    except ValueError as exc:
        raise FoundryValidationDispatchError(
            "version_binding_identifier_invalid"
        ) from exc
    if (
        version_binding["candidate_key"] != candidate_key
        or not version_binding["candidate_version_number"].isdigit()
        or int(version_binding["candidate_version_number"]) < 1
        or not _HEX64_RE.fullmatch(version_binding["candidate_version_hash"])
        or not _HEX64_RE.fullmatch(version_binding["candidate_bundle_hash"])
        or not _IMAGE_DIGEST_RE.fullmatch(
            version_binding["validation_runner_image_digest"]
        )
    ):
        raise FoundryValidationDispatchError("version_binding_invalid")
    inputs["version_binding"] = json.dumps(
        version_binding,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    if draft_binding is not None:
        required = {
            "candidate_id",
            "candidate_version_id",
            "candidate_version_number",
            "candidate_version_hash",
            "candidate_bundle_hash",
            "candidate_artifact_hash",
            "draft_run_id",
            "artifact_id",
            "artifact_workflow_run_id",
            "artifact_name",
            "artifact_sha256",
            "artifact_repository",
            "base_commit",
            "base_source_tree_sha256",
            "post_patch_source_tree_sha256",
            "patch_sha256",
            "sbom_sha256",
            "validation_runner_image_digest",
        }
        if set(draft_binding) != required:
            raise FoundryValidationDispatchError("draft_binding_shape_invalid")
        try:
            uuid.UUID(draft_binding["candidate_id"])
            uuid.UUID(draft_binding["candidate_version_id"])
            uuid.UUID(draft_binding["draft_run_id"])
        except ValueError as exc:
            raise FoundryValidationDispatchError(
                "draft_binding_identifier_invalid"
            ) from exc
        if (
            not draft_binding["candidate_version_number"].isdigit()
            or int(draft_binding["candidate_version_number"]) < 1
            or not draft_binding["artifact_id"].isdigit()
            or not draft_binding["artifact_workflow_run_id"].isdigit()
            or not re.fullmatch(
                r"^[A-Za-z0-9_.-]{1,128}$", draft_binding["artifact_name"]
            )
            or not _REPOSITORY_RE.fullmatch(draft_binding["artifact_repository"])
            or not re.fullmatch(r"^[0-9a-f]{40}$", draft_binding["base_commit"])
            or any(
                not _HEX64_RE.fullmatch(draft_binding[field])
                for field in (
                    "candidate_version_hash",
                    "candidate_bundle_hash",
                    "candidate_artifact_hash",
                    "artifact_sha256",
                    "base_source_tree_sha256",
                    "post_patch_source_tree_sha256",
                    "patch_sha256",
                    "sbom_sha256",
                )
            )
            or not _IMAGE_DIGEST_RE.fullmatch(
                draft_binding["validation_runner_image_digest"]
            )
        ):
            raise FoundryValidationDispatchError("draft_binding_invalid")
        for field in (
            "candidate_id",
            "candidate_version_id",
            "candidate_version_number",
            "candidate_version_hash",
            "candidate_bundle_hash",
            "validation_runner_image_digest",
        ):
            if draft_binding[field] != version_binding[field]:
                raise FoundryValidationDispatchError(
                    "draft_version_binding_mismatch"
                )
        inputs["draft_binding"] = json.dumps(
            draft_binding,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={
                    "ref": ref,
                    "inputs": inputs,
                },
            )
    except httpx.TimeoutException as exc:
        raise FoundryValidationDispatchError("validation_dispatch_timeout") from exc
    except httpx.HTTPError as exc:
        raise FoundryValidationDispatchError("validation_dispatch_network_error") from exc
    if response.status_code != 204:
        raise FoundryValidationDispatchError(
            f"validation_dispatch_http_{response.status_code}",
            retryable=response.status_code == 429 or response.status_code >= 500,
            delivery_uncertain=response.status_code >= 500,
        )


async def queue_and_dispatch_candidate_validation(
    db: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    candidate_version_id: uuid.UUID,
    candidate_version_hash: str,
    actor_kind: str,
    actor_user_id: uuid.UUID | None,
    idempotent_replay: bool = False,
) -> FoundryValidationRun:
    """Create and dispatch one exact validation run.

    The durable run is committed before the external GitHub call.  A dispatch
    failure is therefore recorded on that run and never rolls back the
    candidate version.  ``idempotent_replay`` also reuses terminal runs, which
    lets an internal Draft-result callback be safely replayed without silently
    retrying a failed Demo or launching a duplicate successful one.
    """

    run, created = await ensure_validation_run(
        db,
        candidate_id=candidate_id,
        candidate_version_id=candidate_version_id,
        candidate_version_hash=candidate_version_hash,
        actor_kind=actor_kind,
        actor_user_id=actor_user_id,
        reuse_terminal=idempotent_replay,
    )
    if not created:
        return run

    # Commit a long-lease uncertainty marker *before* the external request.
    # If this process dies after GitHub accepts the request but before the 204
    # is recorded, another API call will not launch a competing workflow.
    await record_validation_dispatch(
        db,
        validation_run_id=run.id,
        dispatched=False,
        failure_class="validation_dispatch_in_progress",
        retryable=True,
        delivery_uncertain=True,
    )

    failure_class: str | None = None
    retryable = False
    delivery_uncertain = False
    version = await db.get(FoundryCandidateVersion, run.candidate_version_id)
    if version is None:
        failure_class = "validation_dispatch_internal_error"
    else:
        try:
            draft_binding = await ai_draft_validation_binding(db, version=version)
            await dispatch_candidate_validation(
                validation_run_id=run.id,
                candidate_key=version.candidate_key,
                version_binding=candidate_validation_binding(version),
                draft_binding=draft_binding,
            )
        except FoundryValidationDispatchError as exc:
            failure_class = exc.failure_class
            retryable = exc.retryable
            delivery_uncertain = exc.delivery_uncertain
        except FoundryCatalogError:
            failure_class = "validation_dispatch_binding_error"
        except Exception:
            failure_class = "validation_dispatch_internal_error"
            retryable = True
            delivery_uncertain = True

    return await record_validation_dispatch(
        db,
        validation_run_id=run.id,
        dispatched=failure_class is None,
        failure_class=failure_class,
        retryable=retryable,
        delivery_uncertain=delivery_uncertain,
    )


__all__ = [
    "FoundryValidationDispatchError",
    "dispatch_candidate_validation",
    "queue_and_dispatch_candidate_validation",
]
