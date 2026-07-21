"""Pure content identity for immutable Foundry candidate versions."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def candidate_version_envelope(
    *,
    candidate_bundle_sha256: str,
    workflow_spec_sha256: str,
    code_tree_sha256: str,
    patch_sha256: str,
    dependency_lock_sha256: str,
    sbom_sha256: str,
    fixture_hashes: list[Any],
    data_hashes: dict[str, str],
    validation_runner_image_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "candidate_bundle_sha256": candidate_bundle_sha256,
        "workflow_spec_sha256": workflow_spec_sha256,
        "code_tree_sha256": code_tree_sha256,
        "patch_sha256": patch_sha256,
        "dependency_lock_sha256": dependency_lock_sha256,
        "sbom_sha256": sbom_sha256,
        "fixture_hashes": fixture_hashes,
        "data_hashes": data_hashes,
        "validation_runner_image_digest": validation_runner_image_digest,
    }


def candidate_version_sha256(**kwargs: Any) -> str:
    return hashlib.sha256(canonical_json(candidate_version_envelope(**kwargs))).hexdigest()


__all__ = [
    "candidate_version_envelope",
    "candidate_version_sha256",
    "canonical_json",
]
