"""Narrow GitHub Actions dispatcher for isolated candidate Demo validation."""

from __future__ import annotations

import re
import uuid

import httpx

from app.config import settings


_CANDIDATE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{2,96}$")


class FoundryValidationDispatchError(RuntimeError):
    """Sanitized retryable dispatch failure."""

    def __init__(self, failure_class: str):
        super().__init__(failure_class)
        self.failure_class = failure_class


async def dispatch_candidate_validation(
    *,
    validation_run_id: uuid.UUID,
    candidate_key: str,
) -> None:
    """Dispatch only opaque identifiers; candidate code/data never cross this API."""

    if not _CANDIDATE_KEY_RE.fullmatch(str(candidate_key or "")):
        raise FoundryValidationDispatchError("invalid_candidate_key")
    if settings.foundry_validation_dispatch_backend != "github_actions":
        raise FoundryValidationDispatchError("validation_dispatch_disabled")
    repository = str(settings.foundry_validation_github_repository or "").strip()
    workflow = str(settings.foundry_validation_github_workflow or "").strip()
    ref = str(settings.foundry_validation_github_ref or "").strip().lower()
    token = str(settings.foundry_validation_github_token or "")
    if (
        repository.count("/") != 1
        or not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml", workflow)
        or not re.fullmatch(r"[0-9a-f]{40}", ref)
        or len(token) < 32
    ):
        raise FoundryValidationDispatchError("validation_dispatch_misconfigured")
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
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
                    "inputs": {
                        "candidate_key": candidate_key,
                        "validation_run_id": str(validation_run_id),
                    },
                },
            )
    except httpx.TimeoutException as exc:
        raise FoundryValidationDispatchError("validation_dispatch_timeout") from exc
    except httpx.HTTPError as exc:
        raise FoundryValidationDispatchError("validation_dispatch_network_error") from exc
    if response.status_code != 204:
        raise FoundryValidationDispatchError(
            f"validation_dispatch_http_{response.status_code}"
        )


__all__ = [
    "FoundryValidationDispatchError",
    "dispatch_candidate_validation",
]
