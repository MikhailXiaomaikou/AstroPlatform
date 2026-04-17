"""Tests for Measurement + unit annotation (Phase 1 / R4)."""

from __future__ import annotations

from app.services.measurement import (
    Measurement,
    annotate_known_fields,
)
from app.services.result_provenance import normalize_tool_result


def test_measurement_as_dict_drops_none():
    m = Measurement(value=1.5, unit="mag")
    assert m.as_dict() == {"value": 1.5, "unit": "mag"}


def test_measurement_with_err_and_frame():
    m = Measurement(value=10.68, unit="deg", err=0.001, frame="icrs")
    d = m.as_dict()
    assert d["value"] == 10.68
    assert d["frame"] == "icrs"
    assert d["err"] == 0.001


def test_annotate_tags_ra_dec_with_frame_and_units():
    payload = {"ra": 10.68, "dec": 41.27, "name": "M31"}
    annotate_known_fields(payload)
    assert payload["ra_unit"] == "deg"
    assert payload["dec_unit"] == "deg"
    assert payload["frame"] == "icrs"
    assert "name_unit" not in payload  # only tags known columns


def test_annotate_walks_nested_rows():
    payload = {
        "results": [
            {"ra": 10.0, "dec": 20.0, "parallax": 7.5},
            {"ra": 30.0, "dec": 40.0, "parallax": 2.1},
        ]
    }
    annotate_known_fields(payload)
    for row in payload["results"]:
        assert row["ra_unit"] == "deg"
        assert row["dec_unit"] == "deg"
        assert row["parallax_unit"] == "mas"
        assert row["frame"] == "icrs"


def test_annotate_is_idempotent():
    payload = {"teff": 5800}
    annotate_known_fields(payload)
    annotate_known_fields(payload)
    assert payload["teff_unit"] == "K"
    # No double-annotation like teff_unit_unit
    assert "teff_unit_unit" not in payload


def test_normalize_tool_result_applies_unit_annotations():
    """normalize_tool_result must wire R4 into the provenance pipeline."""
    r = normalize_tool_result("search_objects", {"ra": 10.68, "dec": 41.27})
    assert r["ra_unit"] == "deg"
    assert r["frame"] == "icrs"
