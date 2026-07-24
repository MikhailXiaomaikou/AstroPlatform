from __future__ import annotations

from datetime import date
import logging

import pytest


def test_load_registry_has_current_services():
    from app.services.provenance_v2.registry_loader import load_registry

    registry = load_registry()

    assert registry["schema_version"] == 1
    assert set(registry["services"]) == {
        "vizier", "gaia", "simbad", "ned", "2mass", "alma",
        # cosmology-only repo: the solar/exo service entries (jpl, mpc, sbdb,
        # sentry, damit, nasa_exoplanet_archive) were removed with their
        # connectors on 2026-06-03.
        # arxiv + the unified ADS/arxiv object lookup back the literature path.
        "arxiv", "ads_or_arxiv_object",
    }


def test_resolve_service_by_key_url_catalog_and_ivoid():
    from app.services.provenance_v2.registry_loader import load_registry, resolve_service

    registry = load_registry()

    assert resolve_service("simbad", registry)["service_key"] == "simbad"
    assert resolve_service("https://gea.esac.esa.int/tap-server/tap", registry)["service_key"] == "gaia"
    assert resolve_service("II/246/out", registry)["service_key"] == "2mass"
    assert resolve_service("ivo://ned.ipac/objdir", registry)["service_key"] == "ned"
    assert resolve_service("https://almascience.eso.org/alma-data/archive", registry)["service_key"] == "alma"


def test_check_freshness_warns_for_stale_entries():
    # Uses a synthetic registry: pinning "the shipped registry is stale at
    # date X" breaks every time the registry is legitimately re-verified
    # (it did on 2026-07-24). The warning logic is what this test owns.
    from app.services.provenance_v2.registry_loader import check_freshness

    registry = {
        "services": {
            "vizier": {"metadata": {"last_verified": "2026-01-01"}},
            "gaia": {"metadata": {"last_verified": "2026-08-01"}},
        }
    }
    warnings = check_freshness(
        registry,
        warn_days=180,
        today=date(2026, 12, 31),
    )

    assert any("vizier" in item for item in warnings)
    assert not any("gaia" in item for item in warnings)


def test_shipped_registry_is_fresh_today():
    # The real registry must never ship already-stale: this is the guard
    # that fails in CI ahead of the runtime startup refusal.
    from app.services.provenance_v2.registry_loader import check_freshness, load_registry

    assert check_freshness(load_registry(), warn_days=180) == []


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
