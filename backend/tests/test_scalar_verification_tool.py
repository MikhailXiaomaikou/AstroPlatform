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
