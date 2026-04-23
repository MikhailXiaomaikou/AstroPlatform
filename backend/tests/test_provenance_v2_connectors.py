from __future__ import annotations

from astropy.table import Table


def test_normalize_adql_result_infers_dataset_provenance_for_active_services():
    from app.services.result_provenance import normalize_tool_result

    expected = {
        "vizier": ("vizier", "datacenter_ivoa_compliant"),
        "gaia": ("gaia", "datacenter_non_standard_tag"),
        "simbad": ("simbad", "project_registry"),
        "ned": ("ned", "datacenter_non_standard_info"),
        "2mass": ("2mass", "datacenter_ivoa_compliant"),
    }

    for service, (service_key, authority) in expected.items():
        result = normalize_tool_result(
            "run_adql",
            {
                "service": service,
                "columns": ["ra"],
                "data": {"ra": [1.0]},
                "row_count": 1,
            },
            tool_input={"service": service, "query": "SELECT ra FROM t"},
        )

        dataset = result["provenance"]["datasets"][0]
        assert dataset["service_key"] == service_key
        assert dataset["archive_version"]
        assert dataset["source_authority"] == authority
        assert result["source_urls"]
        assert result["archive_ids"]
        assert result["provenance"]["reproducibility"]["archive_version"] == dataset["archive_version"]


def test_connector_object_rows_carry_dataset_provenance_for_all_active_sources():
    from app.connectors.gaia import GaiaConnector
    from app.connectors.ned import NEDConnector
    from app.connectors.simbad import SIMBADConnector
    from app.connectors.twomass import TwoMASSConnector
    from app.connectors.vizier import VizierConnector

    vizier_obj = VizierConnector()._table_to_objects(
        Table({"RAJ2000": [1.0], "DEJ2000": [2.0], "Name": ["VizieR row"]})
    )[0]
    gaia_obj = GaiaConnector()._table_to_objects(
        Table({"source_id": [123], "ra": [1.0], "dec": [2.0]})
    )[0]
    simbad_obj = SIMBADConnector()._table_to_objects(
        Table({"main_id": ["M 31"], "ra": [1.0], "dec": [2.0], "otype": ["G"]})
    )[0]
    ned_obj = NEDConnector()._parse_ned_object(
        {
            "Preferred": {"Name": "M 31", "RA": "1.0", "DEC": "2.0", "Type": "G"},
            "INFO": [{"name": "PROVIDER", "value": "NASA/IPAC Extragalactic Database"}],
        }
    )
    twomass_obj = TwoMASSConnector()._table_to_objects(
        Table({"RAJ2000": [1.0], "DEJ2000": [2.0], "2MASS": ["00000000+0000000"], "Jmag": [12.3]})
    )[0]

    objects = [vizier_obj, gaia_obj, simbad_obj, ned_obj, twomass_obj]
    service_keys = [
        obj.extra["_provenance_dataset"]["service_key"]
        for obj in objects
        if obj is not None
    ]

    assert service_keys == ["vizier", "gaia", "simbad", "ned", "2mass"]
    for obj in objects:
        assert obj is not None
        dataset = obj.extra["_provenance_dataset"]
        assert dataset["archive_version"]
        assert dataset["source_authority"]


def test_search_result_row_dataset_is_lifted_into_nested_provenance():
    from app.services.provenance_v2.registry_loader import dataset_from_registry
    from app.services.result_provenance import normalize_tool_result

    row_dataset = dataset_from_registry("simbad", source_authority="project_registry")
    result = normalize_tool_result(
        "search_objects",
        {
            "results": [
                {
                    "source": "simbad",
                    "name": "M 31",
                    "extra": {"_provenance_dataset": row_dataset},
                }
            ],
            "total": 1,
        },
    )

    assert result["provenance"]["datasets"][0]["service_key"] == "simbad"
    assert result["provenance"]["coverage"]["primary_citation_source"] == "registry"
