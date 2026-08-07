"""Model-visible wrapper for deterministic scalar source verification."""

from __future__ import annotations

from copy import deepcopy
import time
from typing import Any

from app.config import settings
from app.observability.metrics import record_counter, record_histogram
from app.services.scalar_derivation import (
    ScalarDerivationError,
    canonical_receipt_sha256,
    derive_scalar,
)
from app.services.source_packet_resolver import (
    SourceResolutionError,
    resolve_sources,
)


TOOL_SCHEMAS = [
    {
        "name": "verify_scalar_derivation",
        "description": (
            "Verify a small scalar derivation from explicit values, uncertainties, "
            "units, covariance assumptions, and source locators. Supports only ratio, "
            "difference, product, and generalized inverse-covariance weighted_mean. "
            "Use this for a paper table consistency calculation; do not use it for a "
            "likelihood, fit, sampler, posterior, or arbitrary expression. The tool "
            "separately reports whether the arithmetic and the cited source values "
            "were verified. Independence must be explicit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["ratio", "difference", "product", "weighted_mean"],
                },
                "quantities": {
                    "type": "array",
                    "minItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                            "value": {"type": "number"},
                            "standard_uncertainty": {"type": "number", "minimum": 0},
                            "unit": {"type": "string"},
                            "source_ref": {"type": "string"},
                            "source_locator": {"type": "string"},
                        },
                        "required": [
                            "id",
                            "label",
                            "value",
                            "standard_uncertainty",
                            "unit",
                            "source_ref",
                            "source_locator",
                        ],
                        "additionalProperties": False,
                    },
                },
                "uncertainty_model": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": [
                                "independent",
                                "correlation_matrix",
                                "covariance_matrix",
                            ],
                        },
                        "matrix": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                            },
                        },
                        "source_ref": {"type": "string"},
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "kind": {
                                "type": "string",
                                "enum": ["arxiv", "doi", "zenodo", "url", "user_supplied"],
                            },
                            "identifier": {"type": "string"},
                            "locator": {"type": "string"},
                        },
                        "required": ["id", "kind", "identifier", "locator"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["operation", "quantities", "uncertainty_model", "sources"],
            "additionalProperties": False,
        },
    }
]


def _source_expected_claims(
    quantities: list[dict[str, Any]],
    uncertainty_model: dict[str, Any],
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Expected paper claims plus whether a matrix attribution is unmatchable.

    Codex review P1 (PR #46, round 3): only the 2x2 correlation-matrix shape
    can be matched against the paper text. Any other source-attributed
    uncertainty matrix (covariance kind, larger correlation) must be flagged
    so the caller caps the aggregate below verified_exact instead of letting
    unchecked matrix values ride along on the quantities' match.
    """
    user_source_ids = {
        str(source.get("id") or "")
        for source in sources
        if source.get("kind") == "user_supplied"
    }
    claims = [
        deepcopy(quantity)
        for quantity in quantities
        if str(quantity.get("source_ref") or "") not in user_source_ids
    ]
    source_ref = str(uncertainty_model.get("source_ref") or "").strip()
    matrix = uncertainty_model.get("matrix")
    matrix_attribution_unverifiable = False
    has_matrix_attribution = (
        uncertainty_model.get("kind") in ("correlation_matrix", "covariance_matrix")
        or matrix is not None
    )
    if source_ref and source_ref not in user_source_ids and has_matrix_attribution:
        if (
            uncertainty_model.get("kind") == "correlation_matrix"
            and isinstance(matrix, list)
            and len(matrix) == 2
            and all(isinstance(row, list) and len(row) == 2 for row in matrix)
        ):
            claims.append(
                {
                    "id": "correlation",
                    "label": "rho",
                    "value": matrix[0][1],
                    "standard_uncertainty": None,
                    "source_ref": source_ref,
                }
            )
        else:
            matrix_attribution_unverifiable = True
    return claims, matrix_attribution_unverifiable


def _aggregate_source_status(
    evidence: list[dict[str, Any]], referenced_source_ids: set[str]
) -> str:
    relevant = [
        source
        for source in evidence
        if str(source.get("id") or "") in referenced_source_ids
    ]
    statuses = {str(source.get("status") or "unavailable") for source in relevant}
    # Codex review P1 (PR #46, round 3): a referenced source with no evidence
    # record at all must count as missing, not silently drop out of the
    # aggregation and leave verified_exact standing on the rest.
    covered_ids = {str(source.get("id") or "") for source in relevant}
    missing_ids = referenced_source_ids - covered_ids
    if "conflict" in statuses:
        return "conflict"
    if relevant and not missing_ids and statuses == {"verified_exact"}:
        return "verified_exact"
    if "unavailable" in statuses or missing_ids:
        return "unavailable"
    if "resolved_unmatched" in statuses:
        return "resolved_unmatched"
    return "user_supplied_unverified"


def _boundary_statement(operation: str) -> str:
    """Return the backend-owned receipt boundary for this operation."""
    return (
        f"This is a controlled {operation} consistency calculation from the "
        "listed scalar inputs. It is not a likelihood fit, sampler run, posterior "
        "reconstruction, or proof that the scientific method is applicable."
    )


async def execute_scalar_verification(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Return a compact receipt even when source retrieval degrades."""
    started = time.monotonic()
    if not settings.lightweight_verification_enabled:
        result = {
            "success": False,
            "error": "Lightweight verification is disabled.",
            "error_class": "feature_disabled",
            "schema_version": 1,
            "task_kind": "deterministic_source_check",
            "calculation_status": "not_run",
            "source_status": "unavailable",
            "response_disposition": "abstention",
            "publication_ready": False,
            "__tool_status__": "FAILED",
            "__do_not_claim__": True,
        }
        record_histogram(
            "lightweight_verification_duration_seconds",
            time.monotonic() - started,
            disposition="abstention",
        )
        return result

    operation = str(tool_input.get("operation") or "")
    quantities = tool_input.get("quantities")
    uncertainty_model = tool_input.get("uncertainty_model")
    sources = tool_input.get("sources")
    if not isinstance(quantities, list):
        quantities = []
    if not isinstance(uncertainty_model, dict):
        uncertainty_model = {}
    if not isinstance(sources, list):
        sources = []

    try:
        calculation = derive_scalar(
            operation=operation,  # type: ignore[arg-type]
            quantities=quantities,
            uncertainty_model=uncertainty_model,
        )
    except ScalarDerivationError as exc:
        record_counter(
            "lightweight_verification_disposition_total",
            task_kind="deterministic_source_check",
            disposition="abstention",
            limiting_stage="calculation_input",
        )
        receipt = {
            "success": False,
            "schema_version": 1,
            "task_kind": "deterministic_source_check",
            "operation": operation,
            "result": None,
            "inputs": quantities,
            "formula": None,
            "uncertainty_model": uncertainty_model,
            "calculation_status": "abstention",
            "source_status": "unavailable",
            "claim_scopes": {
                "derived_numeric": False,
                "source_measurement": False,
            },
            "source_evidence": [],
            "assumptions": [],
            "boundary_statement": _boundary_statement(operation),
            "response_disposition": "abstention",
            "earliest_limiting_stage": "calculation_input",
            "missing_dependencies": [exc.code],
            "safe_fallback": "Supply valid scalar inputs, compatible units, and an explicit uncertainty model.",
            "error": str(exc),
            "error_class": exc.code,
            "publication_ready": False,
            "__tool_status__": "PARTIAL",
            "__do_not_claim__": True,
        }
        receipt["receipt_sha256"] = canonical_receipt_sha256(receipt)
        record_histogram(
            "lightweight_verification_duration_seconds",
            time.monotonic() - started,
            disposition="abstention",
        )
        return receipt

    source_error: SourceResolutionError | None = None
    _expected_claims, matrix_attribution_unverifiable = _source_expected_claims(
        quantities, uncertainty_model, sources
    )
    try:
        source_evidence = await resolve_sources(
            sources, _expected_claims
        )
    except SourceResolutionError as exc:
        source_error = exc
        source_evidence = []
    user_source_ids = {
        str(source.get("id") or "")
        for source in sources
        if source.get("kind") == "user_supplied"
    }
    declared_source_ids = {
        str(source.get("id") or "").strip()
        for source in sources
        if str(source.get("id") or "").strip()
    }
    quantity_source_ref_issues: list[str] = []
    for index, quantity in enumerate(quantities):
        quantity_id = str(quantity.get("id") or index)
        quantity_source_ref = str(quantity.get("source_ref") or "").strip()
        if not quantity_source_ref:
            quantity_source_ref_issues.append(
                f"quantity_source_ref_missing:{quantity_id}"
            )
        elif quantity_source_ref not in declared_source_ids:
            quantity_source_ref_issues.append(
                f"quantity_source_ref_undeclared:{quantity_id}"
            )
    referenced_source_ids = {
        str(quantity.get("source_ref") or "")
        for quantity in quantities
        if str(quantity.get("source_ref") or "")
        and str(quantity.get("source_ref") or "") not in user_source_ids
    }
    uncertainty_source_ref = str(uncertainty_model.get("source_ref") or "")
    if uncertainty_source_ref and uncertainty_source_ref not in user_source_ids:
        referenced_source_ids.add(uncertainty_source_ref)
    source_status = (
        "unavailable"
        if source_error
        else _aggregate_source_status(source_evidence, referenced_source_ids)
    )
    if quantity_source_ref_issues and source_status == "verified_exact":
        # Codex review P1 (PR #46, round 24): every input quantity must be
        # attributed to a declared external or user-supplied source. Otherwise
        # an omitted ref can vanish from referenced_source_ids and inherit the
        # exact status of the remaining paper-backed quantities.
        source_status = "unavailable"
    if matrix_attribution_unverifiable and source_status == "verified_exact":
        # The quantities matched, but the source-attributed uncertainty matrix
        # has a shape we cannot match against the paper — the attribution as a
        # whole is not exact.
        source_status = "resolved_unmatched"
    source_measurement = source_status == "verified_exact"
    nonzero_uncertainty_count = sum(
        1
        for quantity in quantities
        if float(quantity.get("standard_uncertainty") or 0.0) > 0
    )
    correlation_missing = (
        uncertainty_model.get("kind") == "independent"
        and nonzero_uncertainty_count > 1
    )
    disposition = (
        "full" if source_measurement and not correlation_missing else "limited"
    )
    assumptions = []
    if uncertainty_model.get("kind") == "independent":
        assumptions.append("Input uncertainties were explicitly treated as independent.")
    if user_source_ids:
        assumptions.append(
            "Fixed user-supplied comparators are assumptions, not measurements attributed to the external source."
        )
    if correlation_missing:
        assumptions.append(
            "No cross-covariance was supplied; the uncertainty is an independence approximation."
        )
    if not source_measurement:
        assumptions.append(
            "The derivation is valid for the supplied inputs, but the source measurements were not independently matched exactly."
        )
    if disposition == "full":
        limiting_stage = None
    elif correlation_missing and source_measurement:
        limiting_stage = "uncertainty_model"
    else:
        limiting_stage = "source_resolution"
    missing_dependencies = []
    if source_error:
        missing_dependencies.append(source_error.code)
    elif source_status != "verified_exact":
        missing_dependencies.append(f"source_status:{source_status}")
    missing_dependencies.extend(quantity_source_ref_issues)
    if correlation_missing:
        missing_dependencies.append("cross_covariance_not_provided")

    receipt: dict[str, Any] = {
        "success": True,
        "schema_version": 1,
        "task_kind": "deterministic_source_check",
        "operation": operation,
        "result": calculation["result"],
        "inputs": calculation["inputs"],
        "formula": calculation["formula"],
        "uncertainty_model": calculation["uncertainty_model"],
        "calculation_status": calculation["calculation_status"],
        "source_status": source_status,
        "claim_scopes": {
            "derived_numeric": True,
            "source_measurement": source_measurement,
        },
        "source_evidence": source_evidence,
        "assumptions": assumptions,
        "boundary_statement": _boundary_statement(operation),
        "response_disposition": disposition,
        "earliest_limiting_stage": limiting_stage,
        "missing_dependencies": missing_dependencies,
        "safe_fallback": (
            None
            if disposition == "full"
            else (
                "Report the result as an independence approximation and preserve "
                "the verified source attribution."
                if correlation_missing and source_measurement
                else "Report the deterministic result as based on supplied inputs; "
                "do not attribute the values to the paper."
            )
        ),
        # A source-matched scalar consistency check is still not a paper-ready
        # analysis or evidence that the broader scientific method is applicable.
        "publication_ready": False,
        "supports_measurement_claims": source_measurement,
        "supports_derived_numeric_claims": True,
        "__tool_status__": "COMPLETED" if disposition == "full" else "PARTIAL",
        # This narrower marker is intentionally not __do_not_claim__: the
        # derived result remains claimable while paper attribution does not.
        "__do_not_claim_source_measurement__": not source_measurement,
    }
    receipt["receipt_sha256"] = canonical_receipt_sha256(receipt)
    record_counter(
        "lightweight_verification_disposition_total",
        task_kind="deterministic_source_check",
        disposition=disposition,
        limiting_stage=limiting_stage or "none",
    )
    cache_hits = sum(bool(item.get("cache_hit")) for item in source_evidence)
    record_histogram(
        "lightweight_verification_source_cache_hits", cache_hits, source_count=len(sources)
    )
    record_histogram(
        "lightweight_verification_duration_seconds",
        time.monotonic() - started,
        disposition=disposition,
    )
    return receipt
