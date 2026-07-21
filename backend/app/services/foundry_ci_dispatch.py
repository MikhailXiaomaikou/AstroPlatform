"""Narrow GitHub Actions dispatcher for Foundry build and validation jobs.

Only durable, server-issued identifiers cross this boundary.  Claims, source
documents, prompts, candidate code, registry keys, and scientific parameters
must never be included in a workflow-dispatch request.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Final

import httpx


_REPOSITORY: Final = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_PROTECTED_WORKFLOW_REF: Final = "main"
_WORKFLOW: Final = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.ya?ml$")
_CANDIDATE_KEY: Final = re.compile(r"^[a-z][a-z0-9_]{2,96}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA: Final = re.compile(r"^[0-9a-f]{40}$")


class FoundryCIDispatchError(RuntimeError):
    """Stable, secret-free CI dispatch failure."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class FoundryCIDispatchConfig:
    repository: str
    ref: str
    token: str
    validation_workflow: str = "foundry-candidate-demo.yml"
    formal_build_workflow: str = "foundry-formal-worker.yml"
    api_base_url: str = "https://api.github.com"

    def validate(self) -> None:
        if not _REPOSITORY.fullmatch(self.repository):
            raise FoundryCIDispatchError("foundry_ci_repository_invalid")
        if self.ref != _PROTECTED_WORKFLOW_REF:
            raise FoundryCIDispatchError("foundry_ci_ref_invalid")
        for workflow in (self.validation_workflow, self.formal_build_workflow):
            if not _WORKFLOW.fullmatch(workflow):
                raise FoundryCIDispatchError("foundry_ci_workflow_invalid")
        if len(self.token.strip()) < 20:
            raise FoundryCIDispatchError("foundry_ci_token_unavailable")
        if self.api_base_url != "https://api.github.com":
            raise FoundryCIDispatchError("foundry_ci_api_origin_invalid")


def _uuid(value: uuid.UUID | str, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise FoundryCIDispatchError(f"{field}_invalid") from exc


async def _dispatch(
    config: FoundryCIDispatchConfig,
    *,
    workflow: str,
    inputs: dict[str, str],
    client: httpx.AsyncClient | None = None,
) -> None:
    config.validate()
    if workflow not in {config.validation_workflow, config.formal_build_workflow}:
        raise FoundryCIDispatchError("foundry_ci_workflow_not_allowlisted")
    url = (
        f"{config.api_base_url}/repos/{config.repository}/actions/workflows/"
        f"{workflow}/dispatches"
    )
    owns_client = client is None
    request_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
    )
    try:
        response = await request_client.post(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {config.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"ref": config.ref, "inputs": inputs},
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise FoundryCIDispatchError(
            "foundry_ci_dispatch_unavailable", retryable=True
        ) from exc
    finally:
        if owns_client:
            await request_client.aclose()
    if response.status_code != 204:
        # Do not include the response body: GitHub errors can reflect secret or
        # repository details and have no place in a user-visible record.
        raise FoundryCIDispatchError(
            "foundry_ci_dispatch_rejected",
            retryable=response.status_code >= 500 or response.status_code == 429,
        )


async def dispatch_candidate_validation(
    config: FoundryCIDispatchConfig,
    *,
    candidate_key: str,
    validation_run_id: uuid.UUID | str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Start one no-network candidate Demo by durable ledger id."""

    if not _CANDIDATE_KEY.fullmatch(str(candidate_key)):
        raise FoundryCIDispatchError("candidate_key_invalid")
    await _dispatch(
        config,
        workflow=config.validation_workflow,
        inputs={
            "candidate": str(candidate_key),
            "validation_run_id": _uuid(validation_run_id, "validation_run_id"),
        },
        client=client,
    )


async def dispatch_formal_worker_build(
    config: FoundryCIDispatchConfig,
    *,
    candidate_id: uuid.UUID | str,
    candidate_version_id: uuid.UUID | str,
    candidate_version_hash: str,
    source_commit: str,
    source_tree_sha256: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Start a protected build for one exact, already-approved version."""

    if not _SHA256.fullmatch(str(candidate_version_hash)):
        raise FoundryCIDispatchError("candidate_version_hash_invalid")
    if not _GIT_SHA.fullmatch(str(source_commit)):
        raise FoundryCIDispatchError("source_commit_invalid")
    if not _SHA256.fullmatch(str(source_tree_sha256)):
        raise FoundryCIDispatchError("source_tree_sha256_invalid")
    await _dispatch(
        config,
        workflow=config.formal_build_workflow,
        inputs={
            "candidate_id": _uuid(candidate_id, "candidate_id"),
            "candidate_version_id": _uuid(
                candidate_version_id, "candidate_version_id"
            ),
            "candidate_version_hash": str(candidate_version_hash),
            "source_commit": str(source_commit),
            "source_tree_sha256": str(source_tree_sha256),
        },
        client=client,
    )


__all__ = [
    "FoundryCIDispatchConfig",
    "FoundryCIDispatchError",
    "dispatch_candidate_validation",
    "dispatch_formal_worker_build",
]
