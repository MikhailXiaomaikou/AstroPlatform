from __future__ import annotations


def test_field_bibcodes_roundtrip_and_unique_set():
    from app.services.provenance_v2.field_level_schema import FieldBibcodes

    payload = FieldBibcodes(
        columns={"plx_bibcode": ["2020yCat.1350....0G", "2020yCat.1350....0G"]},
        mapping={"plx_bibcode": "plx_value"},
        source_column_pattern="*_bibcode",
    )

    restored = FieldBibcodes.from_dict(payload.to_dict())

    assert restored.to_dict() == payload.to_dict()
    assert restored.all_bibcodes() == {"2020yCat.1350....0G"}


def test_field_level_coverage_roundtrip_defaults():
    from app.services.provenance_v2.field_level_schema import FieldLevelCoverage

    empty = FieldLevelCoverage.from_dict({})
    populated = FieldLevelCoverage(available=True, bibcode_columns_found=2, unique_bibcodes=3)

    assert empty.to_dict() == {
        "available": False,
        "bibcode_columns_found": 0,
        "unique_bibcodes": 0,
    }
    assert FieldLevelCoverage.from_dict(populated.to_dict()).to_dict() == populated.to_dict()


def test_result_provenance_adds_nested_object_without_removing_top_level_keys():
    from app.services.result_provenance import normalize_tool_result

    result = normalize_tool_result(
        "search_objects",
        {
            "results": [{"name": "M31"}],
            "provenance": {
                "datasets": [{"service_key": "simbad", "article": "2000A&AS..143....9W"}],
                "coverage": {"primary_citation_source": "registry"},
            },
        },
        tool_input={"query": "M31"},
    )

    assert result["data_origin"] == "real_archive"
    assert result["analysis_status"] == "completed"
    assert "reproducibility" in result
    assert result["provenance"]["reproducibility"] == result["reproducibility"]
    assert result["provenance"]["datasets"][0]["service_key"] == "simbad"
    assert result["provenance"]["field_bibcodes"] is None
