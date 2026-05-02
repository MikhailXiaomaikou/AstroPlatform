"""Source-mapping registry consistency tests."""

from __future__ import annotations


def test_archive_source_mapping_matches_connector_gate() -> None:
    from app.connectors.availability import V2_AVAILABLE_CONNECTORS
    from app.connectors.registry import CONNECTORS_KEYS
    from app.services.source_mapping import list_archive_source_mappings

    mapping = list_archive_source_mappings()
    active_keys = {entry["key"] for entry in mapping["active"]}
    gated_keys = {entry["key"] for entry in mapping["gated"]}

    assert active_keys == set(V2_AVAILABLE_CONNECTORS)
    assert gated_keys == set(CONNECTORS_KEYS) - set(V2_AVAILABLE_CONNECTORS)
    assert mapping["active_count"] == 6
    assert mapping["gated_count"] == 18

    alma = next(entry for entry in mapping["active"] if entry["key"] == "alma")
    assert "metadata only" in alma["measurement_scope"]
    assert "no derived line luminosity" in alma["notes"].lower()


def test_literature_mapping_tracks_only_verified_default_seed() -> None:
    from app.api.admin_literature import DEFAULT_CII_ARXIV_IDS
    from app.services.source_mapping import list_literature_measurement_mappings

    mapping = list_literature_measurement_mappings()
    verified = mapping["verified"]
    pending = mapping["pending"]

    assert mapping["verified_count"] == 1
    assert {entry["arxiv_id"] for entry in verified} == set(DEFAULT_CII_ARXIV_IDS)
    assert verified[0]["expected_line_measurements"] == 74
    assert verified[0]["usable_for_fit_line_lfr"] is True
    assert all(entry["usable_for_fit_line_lfr"] is False for entry in pending)
    assert all(entry["status"] == "pending_verification" for entry in pending)


def test_source_mapping_summary_includes_cosmology_registry_progress() -> None:
    from app.services.source_mapping import source_mapping_summary

    summary = source_mapping_summary()

    assert summary["archive_active_count"] == 6
    assert summary["archive_gated_count"] == 18
    assert summary["literature_verified_seed_count"] == 1
    assert summary["literature_pending_seed_count"] >= 3
    assert summary["cosmology_dataset_count"] >= 8
    assert "ALPINE" in summary["status"]["literature_measurements"]
