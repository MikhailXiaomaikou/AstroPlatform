from __future__ import annotations

from datetime import date


def test_load_registry_has_five_services():
    from app.services.provenance_v2.registry_loader import load_registry

    registry = load_registry()

    assert registry["schema_version"] == 1
    assert set(registry["services"]) == {"vizier", "gaia", "simbad", "ned", "2mass"}


def test_resolve_service_by_key_url_catalog_and_ivoid():
    from app.services.provenance_v2.registry_loader import load_registry, resolve_service

    registry = load_registry()

    assert resolve_service("simbad", registry)["service_key"] == "simbad"
    assert resolve_service("https://gea.esac.esa.int/tap-server/tap", registry)["service_key"] == "gaia"
    assert resolve_service("II/246/out", registry)["service_key"] == "2mass"
    assert resolve_service("ivo://ned.ipac/objdir", registry)["service_key"] == "ned"


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
