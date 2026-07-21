"""Pure, dependency-free wire contract shared by control plane and worker."""

from __future__ import annotations

import hashlib
import json
from typing import Any

WORKER_PROTOCOL_VERSION = "2"
LEGACY_WORKER_PROTOCOL_VERSION = "1"
SUPPORTED_WORKER_PROTOCOL_VERSIONS = frozenset(
    {LEGACY_WORKER_PROTOCOL_VERSION, WORKER_PROTOCOL_VERSION}
)
DEVELOPMENT_RELEASE_COMMIT = "development"
UNRESOLVED_RELEASE_COMMITS = frozenset(
    {"", "dev", DEVELOPMENT_RELEASE_COMMIT, "none", "unknown", "unset"}
)


def normalize_release_commit(value: Any) -> str:
    """Normalize a release identity before comparing signed envelopes."""

    return str(value or "").strip().lower()


def release_commits_compatible(required: Any, local: Any) -> bool:
    """Treat development sentinels alike while preserving pinned mismatches."""

    required_value = normalize_release_commit(required)
    local_value = normalize_release_commit(local)
    return required_value == local_value or (
        required_value in UNRESOLVED_RELEASE_COMMITS
        and local_value in UNRESOLVED_RELEASE_COMMITS
    )


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_result_hash(value: Any) -> str:
    """Return the server-computed SHA-256 for a worker result."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_worker_request(
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body_sha256: str,
    worker_id: str,
    protocol_version: str,
) -> bytes:
    """Build the exact bytes every local worker request signs."""

    values = (
        method.upper().strip(),
        path.strip(),
        timestamp.strip(),
        nonce.strip(),
        body_sha256.strip().lower(),
        worker_id.strip().lower(),
        protocol_version.strip(),
    )
    if any("\n" in value or "\r" in value for value in values):
        raise ValueError("invalid_worker_signature_input")
    return ("\n".join(values) + "\n").encode("utf-8")
