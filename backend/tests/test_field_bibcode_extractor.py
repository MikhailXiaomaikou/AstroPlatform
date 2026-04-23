from __future__ import annotations


def test_simbad_style_bibcode_columns_are_extracted():
    from app.services.provenance_v2.field_bibcode_extractor import FieldBibcodeExtractor

    field_bibcodes, coverage = FieldBibcodeExtractor().extract(
        ["main_id", "plx_value", "plx_bibcode", "ra", "dec", "coo_bibcode"],
        [
            {
                "main_id": "M 45",
                "plx_value": 7.3,
                "plx_bibcode": "2020yCat.1350....0G",
                "ra": 56.75,
                "dec": 24.12,
                "coo_bibcode": "2000A&AS..143....9W",
            }
        ],
    )

    assert coverage.available is True
    assert coverage.bibcode_columns_found == 2
    assert field_bibcodes.columns["plx_bibcode"] == ["2020yCat.1350....0G"]
    assert field_bibcodes.mapping["plx_bibcode"] == "plx_value"
    assert field_bibcodes.mapping["coo_bibcode"] == ["ra", "dec"]


def test_ned_reference_column_is_value_citation():
    from app.services.provenance_v2.field_bibcode_extractor import FieldBibcodeExtractor

    field_bibcodes, coverage = FieldBibcodeExtractor().extract(
        ["Object Name", "Redshift", "Reference"],
        [{"Object Name": "M31", "Redshift": -0.001, "Reference": "1991ASSL..171...89H"}],
    )

    assert coverage.available is True
    assert field_bibcodes.mapping["Reference"] == "Reference"
    assert field_bibcodes.all_bibcodes() == {"1991ASSL..171...89H"}


def test_no_bibcode_columns_returns_empty_valid_schema():
    from app.services.provenance_v2.field_bibcode_extractor import FieldBibcodeExtractor

    field_bibcodes, coverage = FieldBibcodeExtractor().extract(
        ["source_id", "ra", "dec"],
        [{"source_id": 1, "ra": 1.0, "dec": 2.0}],
    )

    assert coverage.available is False
    assert coverage.bibcode_columns_found == 0
    assert field_bibcodes.columns == {}


def test_null_values_are_filtered():
    from app.services.provenance_v2.field_bibcode_extractor import FieldBibcodeExtractor

    field_bibcodes, coverage = FieldBibcodeExtractor().extract(
        ["plx_bibcode"],
        [{"plx_bibcode": None}, {"plx_bibcode": "--"}, {"plx_bibcode": "2023A&A...674A...1G"}],
    )

    assert coverage.unique_bibcodes == 1
    assert field_bibcodes.columns["plx_bibcode"] == ["2023A&A...674A...1G"]


def test_normalize_tool_result_populates_field_bibcodes_from_columnar_payload():
    from app.services.result_provenance import normalize_tool_result

    result = normalize_tool_result(
        "run_adql",
        {
            "columns": ["main_id", "plx_value", "plx_bibcode"],
            "data": {
                "main_id": ["M 45"],
                "plx_value": [7.3],
                "plx_bibcode": ["2020yCat.1350....0G"],
            },
            "row_count": 1,
        },
        tool_input={"query": "SELECT ..."},
    )

    provenance = result["provenance"]
    assert provenance["field_bibcodes"]["columns"]["plx_bibcode"] == ["2020yCat.1350....0G"]
    assert provenance["coverage"]["field_level"]["available"] is True
    assert provenance["coverage"]["primary_citation_source"] == "field_level"


def test_normalize_tool_result_populates_field_bibcodes_from_adql_row_payload():
    from app.services.result_provenance import normalize_tool_result

    result = normalize_tool_result(
        "run_adql",
        {
            "columns": ["main_id", "plx_value", "plx_bibcode"],
            "data": [["M 45", 7.3, "2020yCat.1350....0G"]],
            "row_count": 1,
        },
        tool_input={"query": "SELECT ..."},
    )

    assert result["provenance"]["field_bibcodes"]["columns"]["plx_bibcode"] == ["2020yCat.1350....0G"]


def test_normalize_tool_result_populates_field_bibcodes_from_results_rows():
    from app.services.result_provenance import normalize_tool_result

    result = normalize_tool_result(
        "search_objects",
        {
            "results": [
                {
                    "name": "M 45",
                    "plx_value": 7.3,
                    "plx_bibcode": "2020yCat.1350....0G",
                }
            ]
        },
        tool_input={"query": "Pleiades"},
    )

    assert result["provenance"]["field_bibcodes"]["columns"]["plx_bibcode"] == ["2020yCat.1350....0G"]
