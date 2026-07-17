from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.services.survey_product_registry import (
    SURVEY_SCHEMA_FIXTURE_SHA256,
    SurveyProductAdapter,
    SurveyProductMaturity,
    audit_survey_product_registry,
    get_survey_product_spec,
    list_survey_product_specs,
)


FIXTURE_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "survey_schemas"


def _make_fully_promoted_euclid_fixture(
    tmp_path: Path,
) -> tuple[Path, str, Path, dict[str, str]]:
    fixture_directory = tmp_path / "survey_schemas"
    shutil.copytree(FIXTURE_DIRECTORY, fixture_directory)
    filename = "euclid_q1_catalog_fixture_v1.json"
    target = fixture_directory / filename
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["maturity"] = "SOURCE_PINNED"
    payload["schema"]["version"] = "EUCL-EC-ICD-8-001-v2.0"
    payload["integrity"]["product_sha256"] = "a" * 64
    payload["integrity"]["checksum_scope"] = "exact_q1_catalogue_export"
    source_names = {
        "object_id": "SOURCE_ID",
        "ra": "RIGHT_ASCENSION",
        "dec": "DECLINATION",
        "photometric_redshift": "PHOTOMETRIC_REDSHIFT",
        "spectroscopic_redshift": "SPECTROSCOPIC_REDSHIFT",
    }
    for field in payload["fields"]:
        field["source_name"] = source_names.get(field["name"])
    payload["coordinates"]["status"] = "pinned"
    payload["time_system"].update(
        {
            "status": "reviewed_not_applicable",
            "scale": "not_applicable",
            "format": "not_applicable",
            "reference_position": "not_applicable",
        }
    )
    payload["redshift"]["status"] = "pinned"
    payload["redshift"]["uncertainty_status"] = "reviewed_not_available"
    payload["linked_products"] = {
        "covariance": {
            "status": "reviewed_not_applicable",
            "kind": None,
            "field": None,
            "artifact": {"source_url": None, "version": None, "sha256": None},
        },
        "mask": {
            "status": "pinned",
            "kind": "artifact",
            "field": None,
            "artifact": {
                "source_url": "https://euclid.esac.esa.int/dr/q1/pinned-mask",
                "version": "Q1-mask-v1",
                "sha256": "b" * 64,
            },
        },
        "selection": {
            "status": "reviewed_not_applicable",
            "kind": None,
            "field": None,
            "artifact": {"source_url": None, "version": None, "sha256": None},
        },
    }
    payload["coverage"]["status"] = "pinned"
    payload["access"]["authentication"] = "anonymous_public"
    payload["access"]["rate_limit"] = "documented_service_policy"
    payload["license"]["identifier"] = "q1-product-terms-reference"
    payload["license"]["status"] = "reviewed_for_registered_product"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    expected_hashes = dict(SURVEY_SCHEMA_FIXTURE_SHA256)
    expected_hashes[filename] = hashlib.sha256(target.read_bytes()).hexdigest()
    return fixture_directory, filename, target, expected_hashes


def test_registry_covers_all_three_surveys_at_fixture_only_maturity():
    snapshot = list_survey_product_specs()

    assert snapshot["product_count"] == 3
    assert snapshot["execution_available"] is False
    assert {item["survey"] for item in snapshot["products"]} == {
        "rubin",
        "euclid",
        "roman",
    }
    assert {item["maturity"] for item in snapshot["products"]} == {
        SurveyProductMaturity.SCHEMA_FIXTURE_ONLY.value
    }


@pytest.mark.parametrize(
    "key",
    [
        "rubin_edp2_object_catalog_fixture_v1",
        "euclid_q1_catalog_fixture_v1",
        "roman_prelaunch_catalog_fixture_v1",
    ],
)
def test_fixture_has_complete_adapter_contract(key: str):
    spec = get_survey_product_spec(key)
    field_names = {field.name for field in spec.fields}

    assert {"object_id", "ra", "dec", "observation_time"} <= field_names
    assert spec.coordinates["longitude_field"] in field_names
    assert spec.coordinates["latitude_field"] in field_names
    assert spec.time_system["field"] in field_names
    assert {
        "types",
        "value_fields",
        "uncertainty_fields",
        "uncertainty_status",
        "status",
    } <= set(spec.redshift)
    assert set(spec.linked_products) == {"covariance", "mask", "selection"}
    assert {
        "status",
        "sky_area_sq_deg",
        "regions",
        "temporal_start",
        "temporal_stop",
        "source_url",
    } <= set(spec.coverage)
    assert spec.integrity["algorithm"] == "sha256"
    assert spec.integrity["product_sha256"] is None
    assert spec.access["offline_behavior"] == "fail_closed"
    assert "authentication" in spec.access
    assert "rate_limit" in spec.access
    assert {"identifier", "url", "status"} <= set(spec.license)
    assert spec.supported_claim_scope
    assert all(source["url"].startswith("https://") for source in spec.official_sources)


def test_registry_audit_passes_and_fixture_hashes_are_not_product_hashes():
    assert audit_survey_product_registry() == {}

    for filename, expected in SURVEY_SCHEMA_FIXTURE_SHA256.items():
        payload = (FIXTURE_DIRECTORY / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected
        decoded = json.loads(payload)
        assert decoded["integrity"]["product_sha256"] is None
        assert decoded["maturity"] == "SCHEMA_FIXTURE_ONLY"


def test_adapter_validates_logical_shape_but_never_makes_it_claimable():
    adapter = SurveyProductAdapter("euclid_q1_catalog_fixture_v1")

    result = adapter.validate_fixture_record(
        {
            "object_id": "fixture-object-1",
            "ra": 10.0,
            "dec": -2.5,
            "photometric_redshift": 0.8,
        }
    )

    assert result["success"] is True
    assert result["analysis_status"] == "SURVEY_SCHEMA_FIXTURE_VALID"
    assert result["scientific_status"] == "CAPABILITY_GAP"
    assert result["publication_ready"] is False
    assert result["scientific_claim_ready"] is False
    assert result["__do_not_claim__"] is True
    assert result["claim_scope"] == "schema_fixture_compatibility"
    assert result["coverage_status"] == {
        "maturity": "SCHEMA_FIXTURE_ONLY",
        "physical_source_mapped": False,
        "executable": False,
    }


def test_adapter_reports_missing_and_wrong_typed_fixture_fields():
    adapter = SurveyProductAdapter("rubin_edp2_object_catalog_fixture_v1")

    result = adapter.validate_fixture_record(
        {"object_id": "fixture-object-1", "ra": True, "dec": None}
    )

    assert result["success"] is False
    assert result["missing_fields"] == ["dec"]
    assert result["type_errors"] == [
        {"field": "ra", "expected": "float64", "observed": "bool"}
    ]
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True


def test_adapter_execution_fails_closed_with_actionable_capability_gap():
    result = SurveyProductAdapter(
        "roman_prelaunch_catalog_fixture_v1"
    ).execution_status()

    assert result["success"] is False
    assert result["analysis_status"] == "SURVEY_PRODUCT_NOT_EXECUTABLE"
    assert result["scientific_status"] == "CAPABILITY_GAP"
    assert result["publication_ready"] is False
    assert result["__do_not_claim__"] is True
    assert result["capability_gap"]["next_maturity"] == "SOURCE_PINNED"


def test_changed_fixture_fails_checksum_audit(tmp_path: Path):
    fixture_directory = tmp_path / "survey_schemas"
    shutil.copytree(FIXTURE_DIRECTORY, fixture_directory)
    target = fixture_directory / "euclid_q1_catalog_fixture_v1.json"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    issues = audit_survey_product_registry(fixture_directory=fixture_directory)

    assert "euclid_q1_catalog_fixture_v1.json" in issues
    assert "fixture SHA-256 mismatch" in issues["euclid_q1_catalog_fixture_v1.json"][0]


def test_source_pinned_promotion_without_product_hash_fails_audit(tmp_path: Path):
    fixture_directory = tmp_path / "survey_schemas"
    shutil.copytree(FIXTURE_DIRECTORY, fixture_directory)
    filename = "euclid_q1_catalog_fixture_v1.json"
    target = fixture_directory / filename
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["maturity"] = "SOURCE_PINNED"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    expected_hashes = dict(SURVEY_SCHEMA_FIXTURE_SHA256)
    expected_hashes[filename] = hashlib.sha256(target.read_bytes()).hexdigest()

    issues = audit_survey_product_registry(
        fixture_directory=fixture_directory,
        expected_hashes=expected_hashes,
    )

    assert "SOURCE_PINNED or EXECUTABLE requires a product SHA-256" in issues[filename]
    assert "SOURCE_PINNED requires a physical schema version" in issues[filename]
    assert any(
        issue.startswith("SOURCE_PINNED required fields lack physical source_name")
        for issue in issues[filename]
    )
    assert "SOURCE_PINNED requires resolved authentication policy" in issues[filename]
    assert "SOURCE_PINNED requires a resolved rate-limit policy" in issues[filename]
    assert "SOURCE_PINNED requires a product licence identifier" in issues[filename]


def test_source_pinned_cannot_leave_science_sections_unresolved(tmp_path: Path):
    """Regression for the independent adversarial-review promotion bypass."""

    fixture_directory = tmp_path / "survey_schemas"
    shutil.copytree(FIXTURE_DIRECTORY, fixture_directory)
    filename = "euclid_q1_catalog_fixture_v1.json"
    target = fixture_directory / filename
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["maturity"] = "SOURCE_PINNED"
    payload["schema"]["version"] = "EUCL-EC-ICD-8-001-v2.0"
    payload["integrity"]["product_sha256"] = "a" * 64
    payload["integrity"]["checksum_scope"] = "exact_q1_catalogue_export"
    for field in payload["fields"]:
        if field["name"] in {"object_id", "ra", "dec"}:
            field["source_name"] = field["name"].upper()
    payload["coordinates"]["status"] = "pinned"
    payload["access"]["authentication"] = "anonymous_public"
    payload["access"]["rate_limit"] = "documented_service_policy"
    payload["license"]["identifier"] = "q1-product-terms-reference"
    payload["license"]["status"] = "reviewed_for_registered_product"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    expected_hashes = dict(SURVEY_SCHEMA_FIXTURE_SHA256)
    expected_hashes[filename] = hashlib.sha256(target.read_bytes()).hexdigest()

    issues = audit_survey_product_registry(
        fixture_directory=fixture_directory,
        expected_hashes=expected_hashes,
    )[filename]

    assert (
        "SOURCE_PINNED time_system.status must be pinned or reviewed_not_applicable"
    ) in issues
    assert (
        "SOURCE_PINNED redshift.status must be pinned or reviewed_not_applicable"
    ) in issues
    for role in ("covariance", "mask", "selection"):
        assert (
            f"SOURCE_PINNED linked_products.{role}.status must be pinned or "
            "reviewed_not_applicable"
        ) in issues
    assert (
        "SOURCE_PINNED coverage.status must be pinned or reviewed_not_applicable"
    ) in issues


def test_source_pinned_accepts_only_explicit_pins_or_reviewed_not_applicable(
    tmp_path: Path,
):
    fixture_directory, _, _, expected_hashes = _make_fully_promoted_euclid_fixture(
        tmp_path
    )

    assert (
        audit_survey_product_registry(
            fixture_directory=fixture_directory,
            expected_hashes=expected_hashes,
        )
        == {}
    )


def test_source_pinned_external_mask_requires_allowlisted_versioned_hash(
    tmp_path: Path,
):
    fixture_directory, filename, target, expected_hashes = (
        _make_fully_promoted_euclid_fixture(tmp_path)
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["linked_products"]["mask"]["artifact"] = {
        "source_url": "https://example.invalid/moving-mask",
        "version": "live",
        "sha256": None,
    }
    payload["access"]["artifact_host_allowlist"].append("example.invalid")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    expected_hashes[filename] = hashlib.sha256(target.read_bytes()).hexdigest()

    issues = audit_survey_product_registry(
        fixture_directory=fixture_directory,
        expected_hashes=expected_hashes,
    )[filename]

    assert "access.artifact_host_allowlist contains non-authority hosts" in issues
    assert "Pinned linked_products.mask artifact requires a fixed version" in issues
    assert "Pinned linked_products.mask artifact requires SHA-256" in issues


def test_released_euclid_q1_still_remains_fixture_only():
    spec = get_survey_product_spec("euclid_q1_catalog_fixture_v1")

    assert spec.release["status"] == "released"
    assert spec.maturity is SurveyProductMaturity.SCHEMA_FIXTURE_ONLY
    assert spec.integrity["product_sha256"] is None
    assert spec.coverage["sky_area_sq_deg"] == pytest.approx(63.1)


def test_unknown_fixture_key_lists_available_keys():
    with pytest.raises(KeyError, match="available"):
        get_survey_product_spec("not-a-survey")
