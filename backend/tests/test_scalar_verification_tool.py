from __future__ import annotations

from typing import Any

import pytest

from app.services.ai_tools import scalar_verification
from app.services.ai_tools.scalar_verification import execute_scalar_verification


def _input(**overrides: Any) -> dict[str, Any]:
    return {
        "operation": "ratio",
        "quantities": [
            {
                "id": "D_M",
                "label": "D_M",
                "value": 17.351,
                "standard_uncertainty": 0.177,
                "unit": "Mpc",
                "source_ref": "desi",
                "source_locator": "Table 4, LRG2",
            },
            {
                "id": "D_H",
                "label": "D_H",
                "value": 19.455,
                "standard_uncertainty": 0.330,
                "unit": "Mpc",
                "source_ref": "desi",
                "source_locator": "Table 4, LRG2",
            },
        ],
        "uncertainty_model": {
            "kind": "correlation_matrix",
            "matrix": [[1, -0.404], [-0.404, 1]],
            "source_ref": "desi",
        },
        "sources": [
            {
                "id": "desi",
                "kind": "arxiv",
                "identifier": "2503.14738",
                "locator": "Table 4, LRG2",
            }
        ],
        **overrides,
    }


@pytest.mark.asyncio
async def test_tool_is_dark_when_feature_flag_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", False
    )

    result = await execute_scalar_verification(_input())

    assert result["success"] is False
    assert result["error_class"] == "feature_disabled"
    assert result["response_disposition"] == "abstention"


@pytest.mark.asyncio
async def test_exact_source_match_produces_full_receipt(monkeypatch) -> None:
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )

    async def verified(_sources, _claims):
        return [
            {
                "id": "desi",
                "status": "verified_exact",
                "locator": "Table 4, LRG2",
                "extraction_method": "ar5iv_html",
                "sha256": "a" * 64,
                "cache_hit": False,
            }
        ]

    monkeypatch.setattr(scalar_verification, "resolve_sources", verified)

    result = await execute_scalar_verification(_input(source_status="verified_exact"))

    assert result["success"] is True
    assert result["result"]["value"] == pytest.approx(0.891852994, abs=1e-9)
    assert result["result"]["standard_uncertainty"] == pytest.approx(
        0.020562805, abs=1e-9
    )
    assert result["response_disposition"] == "full"
    assert result["claim_scopes"] == {
        "derived_numeric": True,
        "source_measurement": True,
    }
    assert result["source_status"] == "verified_exact"
    assert result["__do_not_claim_source_measurement__"] is False
    assert result["publication_ready"] is False
    assert len(result["receipt_sha256"]) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("source_ref", ["", "not-declared"])
async def test_missing_quantity_source_ref_cannot_grant_exact_attribution(
    monkeypatch, source_ref: str,
) -> None:
    # Codex review P1 (PR #46, round 24): an unreferenced quantity must not
    # vanish from the external-source id set and inherit the other quantity's
    # verified_exact status.
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )

    async def verified(_sources, _claims):
        return [{"id": "desi", "status": "verified_exact", "cache_hit": False}]

    monkeypatch.setattr(scalar_verification, "resolve_sources", verified)
    tool_input = _input()
    tool_input["quantities"][1]["source_ref"] = source_ref

    result = await execute_scalar_verification(tool_input)

    assert result["source_status"] != "verified_exact"
    assert result["response_disposition"] == "limited"
    assert result["claim_scopes"]["source_measurement"] is False
    assert result["supports_measurement_claims"] is False
    assert result["__do_not_claim_source_measurement__"] is True


@pytest.mark.asyncio
async def test_exact_values_without_cross_covariance_are_limited(monkeypatch) -> None:
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )

    async def verified(_sources, _claims):
        return [{"id": "desi", "status": "verified_exact", "cache_hit": False}]

    monkeypatch.setattr(scalar_verification, "resolve_sources", verified)
    result = await execute_scalar_verification(
        _input(uncertainty_model={"kind": "independent", "source_ref": "desi"})
    )

    assert result["response_disposition"] == "limited"
    assert result["earliest_limiting_stage"] == "uncertainty_model"
    assert result["claim_scopes"]["source_measurement"] is True
    assert result["missing_dependencies"] == ["cross_covariance_not_provided"]
    assert "independence approximation" in result["safe_fallback"]


@pytest.mark.asyncio
async def test_source_timeout_keeps_arithmetic_but_limits_attribution(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )

    async def unavailable(_sources, _claims):
        return [
            {
                "id": "desi",
                "status": "unavailable",
                "error_class": "source_timeout",
                "cache_hit": False,
            }
        ]

    monkeypatch.setattr(scalar_verification, "resolve_sources", unavailable)

    result = await execute_scalar_verification(_input())

    assert result["success"] is True
    assert result["calculation_status"] == "verified_deterministic"
    assert result["response_disposition"] == "limited"
    assert result["claim_scopes"]["derived_numeric"] is True
    assert result["claim_scopes"]["source_measurement"] is False
    assert result["__do_not_claim_source_measurement__"] is True
    assert "unavailable" in result["missing_dependencies"][0]
    assert "__do_not_claim__" not in result


@pytest.mark.asyncio
async def test_source_conflict_never_allows_paper_attribution(monkeypatch) -> None:
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )

    async def conflict(_sources, _claims):
        return [
            {
                "id": "desi",
                "status": "conflict",
                "match": {"reason": "labels_found_values_differ_or_missing"},
                "cache_hit": False,
            }
        ]

    monkeypatch.setattr(scalar_verification, "resolve_sources", conflict)

    result = await execute_scalar_verification(
        _input(source_status="verified_exact", claim_scopes={"source_measurement": True})
    )

    assert result["source_status"] == "conflict"
    assert result["response_disposition"] == "limited"
    assert result["claim_scopes"]["source_measurement"] is False


@pytest.mark.asyncio
async def test_invalid_covariance_returns_actionable_abstention(monkeypatch) -> None:
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )
    invalid = _input(
        uncertainty_model={
            "kind": "correlation_matrix",
            "matrix": [[1, 2], [2, 1]],
            "source_ref": "desi",
        }
    )

    result = await execute_scalar_verification(invalid)

    assert result["success"] is False
    assert result["response_disposition"] == "abstention"
    assert result["earliest_limiting_stage"] == "calculation_input"
    assert result["missing_dependencies"] == ["non_psd_matrix"]


@pytest.mark.asyncio
async def test_boundary_statement_is_backend_controlled(monkeypatch) -> None:
    # Codex review P1 (PR #46, round 21): a model-authored tool call must not
    # stamp arbitrary prose into a digest-backed deterministic receipt.
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )
    invalid = _input(
        boundary_statement="The posterior was reproduced.",
        uncertainty_model={
            "kind": "correlation_matrix",
            "matrix": [[1, 2], [2, 1]],
            "source_ref": "desi",
        },
    )

    result = await execute_scalar_verification(invalid)

    assert "posterior was reproduced" not in result["boundary_statement"].lower()
    assert result["boundary_statement"].startswith(
        "This is a controlled ratio consistency calculation"
    )


def test_feature_flag_controls_tool_visibility(monkeypatch) -> None:
    from app.api import chat

    tools = [
        {"name": "verify_scalar_derivation"},
        {"name": "compare_luminosity_distances"},
    ]
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", False
    )
    hidden = chat._filter_tools_by_research_focus(tools)
    monkeypatch.setattr(
        scalar_verification.settings, "lightweight_verification_enabled", True
    )
    visible = chat._filter_tools_by_research_focus(tools)

    assert {tool["name"] for tool in hidden} == {"compare_luminosity_distances"}
    assert {tool["name"] for tool in visible} == {
        "compare_luminosity_distances",
        "verify_scalar_derivation",
    }


def test_aggregate_requires_evidence_for_every_referenced_source() -> None:
    # Codex review P1 (PR #46, round 3): a referenced source with no evidence
    # record at all must not vanish from the aggregation and leave
    # verified_exact standing on the sources that happened to resolve.
    from app.services.ai_tools.scalar_verification import _aggregate_source_status

    status = _aggregate_source_status(
        [{"id": "A", "status": "verified_exact"}],
        {"A", "B"},
    )

    assert status != "verified_exact"


def test_unsupported_uncertainty_matrix_attribution_is_not_verified_exact() -> None:
    # Codex review P1 (PR #46, round 3): only the 2x2 correlation matrix shape
    # is matchable against the paper; any other source-attributed uncertainty
    # matrix must flag as unverifiable instead of riding along on the
    # quantities' verified_exact.
    from app.services.ai_tools.scalar_verification import _source_expected_claims

    _claims_out, unverifiable = _source_expected_claims(
        [
            {"id": "a", "label": "A", "value": 1.0, "standard_uncertainty": 0.1, "source_ref": "s1"},
            {"id": "b", "label": "B", "value": 2.0, "standard_uncertainty": 0.2, "source_ref": "s1"},
        ],
        {
            "kind": "covariance_matrix",
            "matrix": [[0.01, 0.002], [0.002, 0.04]],
            "source_ref": "s1",
        },
        [{"id": "s1", "kind": "arxiv", "identifier": "2503.14738"}],
    )

    assert unverifiable is True
