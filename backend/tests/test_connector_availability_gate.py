"""Tests for the provenance-v2 connector availability gate."""

from __future__ import annotations

import sys

import pytest


def test_only_v2_connectors_are_available():
    from app.connectors.availability import V2_AVAILABLE_CONNECTORS, is_available
    from app.connectors.registry import CONNECTORS_KEYS

    # M0 Commit 2 (2026-05-18): jpl + mpc 加入 V2(solar_system 模块)
    assert V2_AVAILABLE_CONNECTORS == {
        "vizier", "gaia", "simbad", "ned", "2mass", "alma", "jpl", "mpc"
    }

    gated = set(CONNECTORS_KEYS) - V2_AVAILABLE_CONNECTORS
    # CONNECTORS_KEYS: 25 项(原 24 + mpc),V2: 8 项,gated: 17 项
    assert len(gated) == 17
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
    # M0 Commit 2: jpl + mpc 实例同步注册
    assert set(registry._connectors) == {
        "gaia", "alma", "simbad", "vizier", "ned", "2mass", "jpl", "mpc"
    }
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
    # M0 Commit 2: jpl + mpc 加入 available_alternatives
    assert result["available_alternatives"] == [
        "vizier", "gaia", "simbad", "ned", "2mass", "alma", "jpl", "mpc"
    ]

    counters = metrics.snapshot()["counters"]["connector_gated_total"]
    assert counters[((("connector_name", "sdss"),))] == 1.0


# ── M0 Commit 2 (2026-05-18): solar_system connectors ──────────────


def test_solar_system_v2_connectors_register_under_get_connector():
    """M0 Commit 2: jpl + mpc 走 BaseConnector 标准路径,
    is_available 返 True, get_connector 返回正确 class 实例."""
    from app.connectors import registry

    registry._connectors = None
    jpl = registry.get_connector("jpl")
    mpc = registry.get_connector("mpc")
    assert type(jpl).__name__ == "JPLHorizonsConnector"
    assert jpl.source_name == "jpl"
    assert type(mpc).__name__ == "MPCConnector"
    assert mpc.source_name == "mpc"


def test_solar_system_connectors_have_fallback_registry_entries():
    """fallback_registry.yaml 必须包含 jpl/mpc/sbdb/sentry/damit 5 个 entry,
    否则 resolve_ivoa_dataorigin 返 None → _provenance_dataset silent drop."""
    from app.services.provenance_v2.registry_loader import dataset_from_registry

    for service_hint in ("jpl", "mpc", "sbdb", "sentry", "damit"):
        dataset = dataset_from_registry(
            service_hint, source_authority="datacenter_ivoa_compliant",
            archive_version=f"{service_hint}-2026", supplements={},
        )
        assert dataset is not None, f"fallback_registry missing entry: {service_hint}"
        assert dataset["service_key"] == service_hint
        assert dataset["archive_version"]
        assert dataset.get("publisher") or dataset.get("creator")
