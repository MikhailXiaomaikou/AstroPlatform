"""Deterministic provenance receipts for non-numeric research boundaries.

These receipts are created only from backend-owned routing, registry, gate and
tool-result state.  They complement (and never override) the numeric and
citation gates.  A receipt hash provides deterministic integrity for storage
and evaluation; it is not a cryptographic signature or an identity claim.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_RECEIPT_KINDS = frozenset({
    "dataset_coverage",
    "capability_gap",
    "untrusted_evidence",
})
_SOURCE_STATUSES = frozenset({
    "verified_registry",
    "verified_current_turn",
    "untrusted_user_supplied",
    "unavailable",
})
_RESPONSE_DISPOSITIONS = frozenset({
    "full",
    "limited",
    "abstention",
    "refusal",
    "hard_block",
})
_ARXIV_RE = re.compile(
    r"(?:(?:arxiv\s*[:#]?\s*)|(?:arxiv\.org/(?:abs|pdf)/))"
    r"(?P<identifier>\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)
_DEFAULT_CAPABILITY_GAP_DEPENDENCIES = (
    "native early-dark-energy (EDE) model implementation",
    "exact Planck high-l and low-l TT/EE likelihoods",
    "DESI DR2 BAO data and covariance in the same run",
    "the requested supernova likelihood",
    "production sampler with convergence diagnostics",
)


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def evidence_receipt_sha256(receipt: dict[str, Any]) -> str:
    """Return a reproducible digest excluding the digest field itself."""

    payload = {
        key: value
        for key, value in receipt.items()
        if key != "receipt_sha256"
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def finalize_evidence_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(receipt)
    finalized["receipt_sha256"] = evidence_receipt_sha256(finalized)
    return finalized


def validate_evidence_receipt(receipt: Any) -> bool:
    """Validate the public shape and deterministic digest of a receipt."""

    if not isinstance(receipt, dict):
        return False
    if receipt.get("schema_version") != 1:
        return False
    if receipt.get("receipt_kind") not in _RECEIPT_KINDS:
        return False
    if receipt.get("source_status") not in _SOURCE_STATUSES:
        return False
    if receipt.get("response_disposition") not in _RESPONSE_DISPOSITIONS:
        return False
    if not isinstance(receipt.get("task_kind"), str):
        return False
    if not isinstance(receipt.get("subject"), dict):
        return False
    if not isinstance(receipt.get("facts"), dict):
        return False
    if not isinstance(receipt.get("source_evidence"), list):
        return False
    if not isinstance(receipt.get("missing_dependencies"), list):
        return False
    if not isinstance(receipt.get("boundary_statement"), str):
        return False
    digest = receipt.get("receipt_sha256")
    return isinstance(digest, str) and digest == evidence_receipt_sha256(receipt)


def _tool_result_items(tool_results: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if isinstance(result, dict):
            items.append((str(item.get("tool") or "unknown"), result))
    return items


def _requested_arxiv_sources(user_prompt: str) -> list[dict[str, str]]:
    identifiers = list(dict.fromkeys(
        match.group("identifier") for match in _ARXIV_RE.finditer(user_prompt)
    ))
    return [
        {
            "kind": "arxiv",
            "identifier": identifier,
            "status": "user_requested_not_reproduced",
        }
        for identifier in identifiers
    ]


def _dataset_coverage_receipt(
    *,
    task_kind: str,
    response_disposition: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for tool, result in reversed(_tool_result_items(tool_results)):
        if tool != "list_cosmology_datasets":
            continue
        evaluations = result.get("coverage_evaluations")
        datasets = result.get("datasets")
        if not isinstance(evaluations, list) or not isinstance(datasets, list):
            continue
        datasets_by_key = {
            str(entry.get("key")): entry
            for entry in datasets
            if isinstance(entry, dict) and entry.get("key")
        }
        for evaluation in evaluations:
            if not isinstance(evaluation, dict):
                continue
            if evaluation.get("coverage_status") != "outside":
                continue
            dataset_key = str(evaluation.get("dataset_key") or "")
            listed_dataset = datasets_by_key.get(dataset_key)
            if not isinstance(listed_dataset, dict):
                continue
            try:
                from app.services.cosmology_likelihoods.registry import (
                    COSMOLOGY_DATASET_REGISTRY_VERSION,
                    get_cosmology_dataset,
                )

                registered_dataset = get_cosmology_dataset(dataset_key)
            except (ImportError, ValueError):
                continue
            provenance = registered_dataset.coverage_provenance
            coverage = registered_dataset.z_coverage
            if provenance is None or coverage is None:
                continue
            try:
                requested_redshift = float(evaluation.get("requested_redshift"))
            except (TypeError, ValueError):
                continue
            z_min, z_max = float(coverage[0]), float(coverage[1])
            if z_min <= requested_redshift <= z_max:
                continue
            receipt = {
                "schema_version": 1,
                "receipt_kind": "dataset_coverage",
                "task_kind": task_kind,
                "response_disposition": response_disposition,
                "source_status": "verified_registry",
                "subject": {
                    "dataset_key": dataset_key,
                    "display_name": registered_dataset.display_name,
                    "dataset_version": registered_dataset.version,
                },
                "facts": {
                    "coverage_status": "outside",
                    "requested_redshift": requested_redshift,
                    "z_min": z_min,
                    "z_max": z_max,
                    "registry_version": COSMOLOGY_DATASET_REGISTRY_VERSION,
                    "upstream_version": provenance.upstream_version,
                    "data_product_sha256": provenance.data_product_sha256,
                },
                "source_evidence": [{
                    "status": "verified_registry",
                    "source_locator": provenance.source_locator,
                    "data_product_role": provenance.data_product_role,
                    "data_product_sha256": provenance.data_product_sha256,
                    "registry_version": COSMOLOGY_DATASET_REGISTRY_VERSION,
                }],
                "missing_dependencies": [],
                "boundary_statement": (
                    "This is a model extrapolation outside the registered "
                    "measurement coverage, not an observation at the requested redshift."
                ),
            }
            return finalize_evidence_receipt(receipt)
    return None


def _tool_attempts(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for tool, result in _tool_result_items(tool_results):
        if tool in {"verify_scalar_derivation", "list_cosmology_datasets"}:
            continue
        status = str(
            result.get("analysis_status")
            or result.get("status")
            or ("COMPLETED" if result.get("success") is True else "FAILED")
        ).upper()
        if status not in {"COMPLETED", "PARTIAL", "FAILED"}:
            status = "COMPLETED" if result.get("success") else "FAILED"
        attempts.append({
            "tool": tool,
            "status": status,
            "success": bool(result.get("success")),
            "publication_ready": bool(result.get("publication_ready", False)),
        })
    return attempts


def _capability_gap_receipt(
    *,
    task_kind: str,
    response_disposition: str,
    user_prompt: str,
    tool_results: list[dict[str, Any]],
    missing_dependencies: list[str],
) -> dict[str, Any]:
    attempts = _tool_attempts(tool_results)
    receipt = {
        "schema_version": 1,
        "receipt_kind": "capability_gap",
        "task_kind": task_kind,
        "response_disposition": response_disposition,
        "source_status": "verified_current_turn" if attempts else "unavailable",
        "subject": {
            "requested_sources": _requested_arxiv_sources(user_prompt),
            "capability": "publication-ready full-research posterior",
        },
        "facts": {
            "posterior_publication_ready": False,
            "tool_attempt_count": len(attempts),
            "tool_statuses": [attempt["status"] for attempt in attempts],
        },
        "source_evidence": attempts,
        "missing_dependencies": list(dict.fromkeys(missing_dependencies)),
        "boundary_statement": (
            "The platform capability gap was verified from this turn's tool state. "
            "The requested paper posterior was not reproduced or verified, so H0, "
            "Delta chi-squared, and other posterior claims are not supported."
        ),
    }
    return finalize_evidence_receipt(receipt)


def _untrusted_evidence_receipt(
    *,
    task_kind: str,
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    current_turn_tools = [
        {"tool": tool, "status": str(result.get("analysis_status") or result.get("status") or "unknown")}
        for tool, result in _tool_result_items(tool_results)
    ]
    receipt = {
        "schema_version": 1,
        "receipt_kind": "untrusted_evidence",
        "task_kind": task_kind,
        "response_disposition": "refusal",
        "source_status": "untrusted_user_supplied",
        "subject": {"evidence_type": "user-supplied tool transcript"},
        "facts": {
            "current_turn_supported": False,
            "verified_current_turn_tool_count": 0,
        },
        "source_evidence": current_turn_tools,
        "missing_dependencies": [
            "a backend-recorded current-turn tool result with registered provenance"
        ],
        "boundary_statement": (
            "Pasted tool text is user-supplied content, not a backend-recorded "
            "result from this turn, and cannot support a verified or paper-ready claim."
        ),
    }
    return finalize_evidence_receipt(receipt)


def build_evidence_receipts(
    *,
    task_kind: str,
    response_disposition: str,
    user_prompt: str,
    tool_results: list[dict[str, Any]],
    interventions: list[dict[str, Any]],
    matched_signals: list[str],
    missing_dependencies: list[str],
) -> list[dict[str, Any]]:
    """Build backend-owned receipts for exactly the three approved cases."""

    gates = {str(item.get("gate") or "") for item in interventions}
    receipts: list[dict[str, Any]] = []
    coverage = _dataset_coverage_receipt(
        task_kind=task_kind,
        response_disposition="limited",
        tool_results=tool_results,
    )
    if coverage is not None:
        receipts.append(coverage)
    has_nonpublication_research_tool = any(
        tool in {
            "run_dark_energy_evidence_matrix",
            "run_research_matrix",
            "run_cosmology_likelihood_chain",
            "run_cosmology_robustness_matrix",
        }
        and result.get("publication_ready") is not True
        for tool, result in _tool_result_items(tool_results)
    )
    if task_kind == "full_research" and (
        "nonpublication_posterior" in gates
        or has_nonpublication_research_tool
    ):
        receipts.append(_capability_gap_receipt(
            task_kind=task_kind,
            response_disposition="limited",
            user_prompt=user_prompt,
            tool_results=tool_results,
            missing_dependencies=(
                missing_dependencies
                or list(_DEFAULT_CAPABILITY_GAP_DEPENDENCIES)
            ),
        ))
    if (
        "untrusted_evidence_echo" in gates
        or "untrusted_evidence_request" in set(matched_signals)
    ):
        receipts.append(_untrusted_evidence_receipt(
            task_kind=task_kind,
            tool_results=tool_results,
        ))
    return receipts
