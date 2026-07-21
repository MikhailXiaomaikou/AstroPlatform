"""Narrow GitHub Actions dispatcher for protected Registry signing."""

from __future__ import annotations

import re
import uuid

import httpx

from app.config import settings


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
)
_WORKFLOW = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.ya?ml$")


class FoundryRegistryDispatchError(RuntimeError):
    """Stable secret-free dispatch error suitable for the candidate ledger."""

    def __init__(self, failure_class: str, *, retryable: bool) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.retryable = retryable


async def dispatch_registry_release(
    *,
    release_request_id: uuid.UUID | str,
    release_request_hash: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Dispatch only the exact opaque request id/hash to protected CI."""

    if settings.foundry_registry_dispatch_backend != "github_actions":
        raise FoundryRegistryDispatchError(
            "registry_dispatch_disabled", retryable=True
        )
    try:
        request_id = str(uuid.UUID(str(release_request_id)))
    except ValueError as exc:
        raise FoundryRegistryDispatchError(
            "registry_dispatch_request_id_invalid", retryable=False
        ) from exc
    request_hash = str(release_request_hash or "").lower()
    if not _SHA256.fullmatch(request_hash):
        raise FoundryRegistryDispatchError(
            "registry_dispatch_request_hash_invalid", retryable=False
        )
    repository = str(settings.foundry_registry_github_repository or "").strip()
    workflow = str(settings.foundry_registry_github_workflow or "").strip()
    ref = str(settings.foundry_registry_github_ref or "").strip()
    token = str(settings.foundry_registry_github_token or "")
    if (
        not _REPOSITORY.fullmatch(repository)
        or not _WORKFLOW.fullmatch(workflow)
        or ref != "main"
        or len(token) < 32
    ):
        raise FoundryRegistryDispatchError(
            "registry_dispatch_misconfigured", retryable=True
        )
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/"
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
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "ref": ref,
                "inputs": {
                    "release_request_id": request_id,
                    "release_request_sha256": request_hash,
                },
            },
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise FoundryRegistryDispatchError(
            "registry_dispatch_unavailable", retryable=True
        ) from exc
    finally:
        if owns_client:
            await request_client.aclose()
    if response.status_code != 204:
        raise FoundryRegistryDispatchError(
            "registry_dispatch_rejected",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )


__all__ = ["FoundryRegistryDispatchError", "dispatch_registry_release"]
