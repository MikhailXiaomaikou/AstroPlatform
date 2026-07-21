"""Narrow dispatcher for an AI Draft Job.

The dispatch envelope deliberately contains no claim text, prompt, source
identifier, user id, or private workspace data.  GitHub receives only an
opaque ledger id and the small structured gap descriptor that the control
plane has already classified as safe for Foundry de-duplication.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from typing import Any

import httpx

from app.config import settings


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_GAP_CODE = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$"
)
_WORKFLOW = re.compile(r"^[A-Za-z0-9_.-]{1,128}\.ya?ml$")
_DESCRIPTOR_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:+/@-]{0,127}$")
_DESCRIPTOR_KEYS = frozenset(
    {
        "gap_code",
        "dataset_key",
        "dataset_keys",
        "workflow_key",
        "supported_selection",
        "source_profile_key",
        "claim_schema",
        "claim_type",
        "model",
        "parameter",
        "statistic",
        "evidence_kind",
        "research_domain",
    }
)


class FoundryDraftDispatchError(RuntimeError):
    """Stable, secret-free dispatch error suitable for an event ledger."""

    def __init__(self, failure_class: str, *, retryable: bool) -> None:
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.retryable = retryable


def _uuid(value: uuid.UUID | str, field: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise FoundryDraftDispatchError(
            f"{field}_invalid", retryable=False
        ) from exc


def _safe_descriptor_value(value: Any, *, depth: int = 0) -> bool:
    if depth > 2:
        return False
    if isinstance(value, str):
        return bool(_DESCRIPTOR_TOKEN.fullmatch(value))
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return abs(value) <= 10**12
    if isinstance(value, float):
        return math.isfinite(value) and abs(value) <= 10**12
    if isinstance(value, list):
        return len(value) <= 16 and all(
            _safe_descriptor_value(item, depth=depth + 1) for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 16 and all(
            isinstance(key, str)
            and bool(_DESCRIPTOR_TOKEN.fullmatch(key))
            and _safe_descriptor_value(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def canonical_gap_descriptor(
    value: dict[str, Any], *, fingerprint: str, gap_code: str
) -> str:
    """Return the bounded canonical provider descriptor or fail closed."""

    if (
        not isinstance(value, dict)
        or not {"gap_code", "research_domain"}.issubset(value)
        or not set(value).issubset(_DESCRIPTOR_KEYS)
        or value.get("gap_code") != gap_code
        or value.get("research_domain") != "cosmology"
        or any(not _safe_descriptor_value(item) for item in value.values())
    ):
        raise FoundryDraftDispatchError(
            "gap_descriptor_invalid", retryable=False
        )
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FoundryDraftDispatchError(
            "gap_descriptor_invalid", retryable=False
        ) from exc
    if len(encoded.encode("utf-8")) > 4096:
        raise FoundryDraftDispatchError(
            "gap_descriptor_too_large", retryable=False
        )
    if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != fingerprint:
        raise FoundryDraftDispatchError(
            "gap_descriptor_fingerprint_mismatch", retryable=False
        )
    return encoded


async def dispatch_candidate_draft(
    *,
    draft_run_id: uuid.UUID | str,
    candidate_id: uuid.UUID | str,
    gap_fingerprint: str,
    gap_code: str,
    gap_descriptor: dict[str, Any],
    generation_route: str,
    risk_level: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Dispatch one data-only draft request to a pinned GitHub workflow."""

    if settings.foundry_draft_dispatch_backend != "github_actions":
        raise FoundryDraftDispatchError(
            "draft_dispatch_disabled", retryable=True
        )
    fingerprint = str(gap_fingerprint or "").lower()
    code = str(gap_code or "")
    route = str(generation_route or "").upper()
    risk = str(risk_level or "").upper()
    if not _HEX64.fullmatch(fingerprint):
        raise FoundryDraftDispatchError(
            "gap_fingerprint_invalid", retryable=False
        )
    if not _GAP_CODE.fullmatch(code):
        raise FoundryDraftDispatchError("gap_code_invalid", retryable=False)
    descriptor_json = canonical_gap_descriptor(
        gap_descriptor,
        fingerprint=fingerprint,
        gap_code=code,
    )
    if route not in {"COMPOSITION", "DATA_ADAPTER", "SCIENCE_CODE"}:
        raise FoundryDraftDispatchError(
            "generation_route_invalid", retryable=False
        )
    if risk not in {"R0", "R1", "R2", "R3"}:
        raise FoundryDraftDispatchError("risk_level_invalid", retryable=False)

    repository = str(settings.foundry_draft_github_repository or "").strip()
    workflow = str(settings.foundry_draft_github_workflow or "").strip()
    ref = str(settings.foundry_draft_github_ref or "").strip()
    token = str(settings.foundry_draft_github_token or "")
    if (
        not _REPOSITORY.fullmatch(repository)
        or not _WORKFLOW.fullmatch(workflow)
        # GitHub workflow_dispatch accepts a branch or tag, not a raw commit.
        # The protected Environment and workflow both require main; the run
        # records its exact GITHUB_SHA as the immutable source receipt.
        or ref != "main"
        or len(token) < 32
    ):
        raise FoundryDraftDispatchError(
            "draft_dispatch_misconfigured", retryable=True
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
                    "draft_run_id": _uuid(draft_run_id, "draft_run_id"),
                    "candidate_id": _uuid(candidate_id, "candidate_id"),
                    "gap_fingerprint": fingerprint,
                    "gap_code": code,
                    "gap_descriptor": descriptor_json,
                    "generation_route": route,
                    "risk_level": risk,
                },
            },
        )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise FoundryDraftDispatchError(
            "draft_dispatch_unavailable", retryable=True
        ) from exc
    finally:
        if owns_client:
            await request_client.aclose()
    if response.status_code != 204:
        # Never persist GitHub's response body: it may echo repository details
        # or provider diagnostics that do not belong in the Candidate ledger.
        raise FoundryDraftDispatchError(
            "draft_dispatch_rejected",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )


__all__ = [
    "canonical_gap_descriptor",
    "FoundryDraftDispatchError",
    "dispatch_candidate_draft",
]
