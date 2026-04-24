"""Tests for the provenance-v2 connector availability gate."""

from __future__ import annotations

import sys

import pytest


def test_only_v2_connectors_are_available():
    from app.connectors.availability import V2_AVAILABLE_CONNECTORS, is_available
    from app.connectors.registry import CONNECTORS_KEYS

    assert V2_AVAILABLE_CONNECTORS == {"vizier", "gaia", "simbad", "ned", "2mass", "alma"}

    gated = set(CONNECTORS_KEYS) - V2_AVAILABLE_CONNECTORS
    assert len(gated) == 18
    assert all(not is_available(source) for source in gated)
    assert all(is_available(source) for source in V2_AVAILABLE_CONNECTORS)


def test_gated_connector_is_blocked_before_import_and_records_metric():
    from app.connectors import registry
    from app.connectors.availability import ConnectorUnavailableError
    from app.observability.metrics import get_registry

    registry._connectors = None
    sys.modules.pop("app.connectors.chandra", None)
    metrics = get_registry()
    metrics.reset()

    with pytest.raises(ConnectorUnavailableError) as exc_info:
        registry.get_connector("chandra")

    response = exc_info.value.response
    assert "app.connectors.chandra" not in sys.modules
    assert response["__tool_status__"] == "UNAVAILABLE"
    assert response["__do_not_claim__"] is True
    assert response["data_origin"] == "unavailable"
    assert response["analysis_status"] == "failed"
    assert response["unavailable_sources"] == ["chandra"]
    assert "gaia" in response["available_alternatives"]

    counters = metrics.snapshot()["counters"]["connector_gated_total"]
    assert counters[((("connector_name", "chandra"),))] == 1.0


def test_active_connector_initialization_skips_gated_modules():
    from app.connectors import registry

    registry._connectors = None
    for module_name in (
        "app.connectors.sdss",
        "app.connectors.chandra",
        "app.connectors.allwise",
    ):
        sys.modules.pop(module_name, None)

    connector = registry.get_connector("gaia")

    assert connector.source_name == "gaia"
    assert set(registry._connectors) == {"gaia", "alma", "simbad", "vizier", "ned", "2mass"}
    assert "app.connectors.sdss" not in sys.modules
    assert "app.connectors.chandra" not in sys.modules
    assert "app.connectors.allwise" not in sys.modules


async def test_search_objects_returns_unavailable_banner_for_gated_source():
    from app.observability.metrics import get_registry
    from app.services.ai_tools import _exec_search

    sys.modules.pop("app.connectors.chandra", None)
    metrics = get_registry()
    metrics.reset()

    result = await _exec_search(
        {"query": "Crab Nebula", "sources": ["chandra"], "radius": 0.1},
        python_session_id="availability-test",
    )

    assert "app.connectors.chandra" not in sys.modules
    assert result["__tool_status__"] == "UNAVAILABLE"
    assert result["__do_not_claim__"] is True
    assert "VizieR, Gaia DR3, SIMBAD, NED, 2MASS, or ALMA" in result["__message_to_model__"]
    assert result["results"] == []
    assert result["total"] == 0
    assert result["per_source"][0]["source"] == "chandra"

    counters = metrics.snapshot()["counters"]["connector_gated_total"]
    assert counters[((("connector_name", "chandra"),))] == 1.0


async def test_run_sdss_sql_is_gated_until_provenance_ready():
    from app.observability.metrics import get_registry
    from app.services.ai_tools import _exec_run_sdss_sql

    sys.modules.pop("app.connectors.sdss_sql", None)
    metrics = get_registry()
    metrics.reset()

    result = await _exec_run_sdss_sql(
        {"query": "SELECT TOP 1 objID FROM PhotoObjAll"},
        python_session_id="availability-test",
    )

    assert "app.connectors.sdss_sql" not in sys.modules
    assert result["__tool_status__"] == "UNAVAILABLE"
    assert result["__do_not_claim__"] is True
    assert result["unavailable_sources"] == ["sdss"]
    assert result["available_alternatives"] == ["vizier", "gaia", "simbad", "ned", "2mass", "alma"]

    counters = metrics.snapshot()["counters"]["connector_gated_total"]
    assert counters[((("connector_name", "sdss"),))] == 1.0
