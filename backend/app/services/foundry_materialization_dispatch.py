"""Narrow GitHub Actions dispatch boundary for Candidate materialization."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import httpx


_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_WORKFLOW = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.ya?ml$")


class FoundryMaterializationDispatchError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True, slots=True)
class FoundryMaterializationDispatchConfig:
    repository: str
    token: str
    materialize_workflow: str = "foundry-materialize-candidate.yml"
    finalize_workflow: str = "foundry-finalize-materialization.yml"
    ref: str = "main"
    api_base_url: str = "https://api.github.com"

    def validate(self) -> None:
        if not _REPOSITORY.fullmatch(self.repository):
            raise FoundryMaterializationDispatchError("materialization_repository_invalid")
        if self.ref != "main":
            raise FoundryMaterializationDispatchError("materialization_ref_not_protected")
        if not all(_WORKFLOW.fullmatch(item) for item in (self.materialize_workflow, self.finalize_workflow)):
            raise FoundryMaterializationDispatchError("materialization_workflow_invalid")
        if len(self.token.strip()) < 20:
            raise FoundryMaterializationDispatchError("materialization_token_unavailable")
        if self.api_base_url != "https://api.github.com":
            raise FoundryMaterializationDispatchError("materialization_api_origin_invalid")


async def _dispatch(
    config: FoundryMaterializationDispatchConfig,
    *,
    workflow: str,
    request_id: uuid.UUID,
    binding: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> None:
    config.validate()
    if workflow not in {config.materialize_workflow, config.finalize_workflow}:
        raise FoundryMaterializationDispatchError("materialization_workflow_not_allowlisted")
    binding_json = json.dumps(
        binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(binding_json.encode("utf-8")) > 32 * 1024:
        raise FoundryMaterializationDispatchError("materialization_binding_too_large")
    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False
    )
    try:
        response = await http.post(
            f"{config.api_base_url}/repos/{config.repository}/actions/workflows/{workflow}/dispatches",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {config.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "ref": config.ref,
                "inputs": {"request_id": str(request_id), "binding": binding_json},
            },
        )
    except httpx.HTTPError as exc:
        # A protocol/read failure can happen after GitHub accepted the
        # workflow_dispatch request, not only during TCP connection setup.
        # Preserve the reservation under the long callback lease so a signed
        # receipt can settle the ambiguity without an immediate duplicate.
        raise FoundryMaterializationDispatchError(
            "materialization_dispatch_unavailable",
            retryable=True,
            outcome_unknown=True,
        ) from exc
    finally:
        if owns_client:
            await http.aclose()
    if 500 <= response.status_code <= 599:
        # GitHub or an intermediary may emit a 5xx after accepting the
        # workflow_dispatch request. Treat the delivery result as unknown so
        # the control plane waits for a callback instead of dispatching a
        # duplicate immediately.
        raise FoundryMaterializationDispatchError(
            "materialization_dispatch_outcome_unknown",
            retryable=True,
            outcome_unknown=True,
        )
    if response.status_code != 204:
        raise FoundryMaterializationDispatchError(
            "materialization_dispatch_rejected",
            retryable=response.status_code == 429,
        )


async def dispatch_materialization_pr(
    config: FoundryMaterializationDispatchConfig,
    *,
    request_id: uuid.UUID,
    binding: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> None:
    await _dispatch(
        config,
        workflow=config.materialize_workflow,
        request_id=request_id,
        binding=binding,
        client=client,
    )


async def dispatch_materialization_finalization(
    config: FoundryMaterializationDispatchConfig,
    *,
    request_id: uuid.UUID,
    binding: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> None:
    await _dispatch(
        config,
        workflow=config.finalize_workflow,
        request_id=request_id,
        binding=binding,
        client=client,
    )


__all__ = [
    "FoundryMaterializationDispatchConfig",
    "FoundryMaterializationDispatchError",
    "dispatch_materialization_finalization",
    "dispatch_materialization_pr",
]
