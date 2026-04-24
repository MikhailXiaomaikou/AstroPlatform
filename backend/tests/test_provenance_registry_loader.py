from __future__ import annotations

from datetime import date
import logging

import pytest


def test_load_registry_has_six_services():
    from app.services.provenance_v2.registry_loader import load_registry

    registry = load_registry()

    assert registry["schema_version"] == 1
    assert set(registry["services"]) == {"vizier", "gaia", "simbad", "ned", "2mass", "alma"}


def test_resolve_service_by_key_url_catalog_and_ivoid():
    from app.services.provenance_v2.registry_loader import load_registry, resolve_service

    registry = load_registry()

    assert resolve_service("simbad", registry)["service_key"] == "simbad"
    assert resolve_service("https://gea.esac.esa.int/tap-server/tap", registry)["service_key"] == "gaia"
    assert resolve_service("II/246/out", registry)["service_key"] == "2mass"
    assert resolve_service("ivo://ned.ipac/objdir", registry)["service_key"] == "ned"
    assert resolve_service("https://almascience.eso.org/alma-data/archive", registry)["service_key"] == "alma"


def test_check_freshness_warns_for_stale_entries():
    from app.services.provenance_v2.registry_loader import check_freshness, load_registry

    warnings = check_freshness(
        load_registry(),
        warn_days=180,
        today=date(2026, 12, 31),
    )

    assert any("vizier" in item for item in warnings)


def test_missing_registry_file_is_graceful(tmp_path):
    from app.services.provenance_v2.registry_loader import load_registry

    registry = load_registry(tmp_path / "missing.yaml")

    assert registry["services"] == {}


def test_startup_blocks_on_registry_freshness_warning(monkeypatch, caplog):
    from app import main

    monkeypatch.setattr(
        main,
        "check_freshness",
        lambda warn_days=180: ["vizier: registry entry is 181 days old"],
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="Provenance registry freshness check failed"):
            main._enforce_provenance_registry_freshness()

    assert "provenance_registry_freshness_blocker" in caplog.text
    assert "vizier" in caplog.text
