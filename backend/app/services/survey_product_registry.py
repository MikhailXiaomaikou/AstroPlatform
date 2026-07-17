"""Fail-closed schema adapters for future Rubin, Euclid, and Roman products.

The registry deliberately stops at schema-fixture validation.  It does not
query an archive, map a physical released table, or turn catalogue rows into
scientific evidence.  Promotion requires a versioned source artifact, an
exact field mapping, a product checksum, and separate execution review.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


class SurveyProductMaturity(StrEnum):
    """Evidence maturity for a survey-product registration."""

    SCHEMA_FIXTURE_ONLY = "SCHEMA_FIXTURE_ONLY"
    SOURCE_PINNED = "SOURCE_PINNED"
    EXECUTABLE = "EXECUTABLE"


class SurveyRegistryIntegrityError(RuntimeError):
    """Raised when a vendored fixture no longer matches its pinned digest."""


@dataclass(frozen=True)
class SurveyFieldSpec:
    """One logical field in an adapter fixture.

    ``source_name`` remains ``None`` until an exact physical release column is
    selected and pinned.  Logical names must never be sent directly to an
    archive query as guessed source columns.
    """

    name: str
    source_name: str | None
    data_type: str
    unit: str | None
    required: bool
    description: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> SurveyFieldSpec:
        return cls(
            name=str(value["name"]),
            source_name=(
                str(value["source_name"])
                if value.get("source_name") is not None
                else None
            ),
            data_type=str(value["data_type"]),
            unit=str(value["unit"]) if value.get("unit") is not None else None,
            required=bool(value["required"]),
            description=str(value["description"]),
        )


@dataclass(frozen=True)
class SurveyProductSpec:
    """Immutable view of one locally verified survey schema fixture."""

    key: str
    survey: str
    display_name: str
    maturity: SurveyProductMaturity
    release: Mapping[str, Any]
    schema: Mapping[str, Any]
    fields: tuple[SurveyFieldSpec, ...]
    coordinates: Mapping[str, Any]
    time_system: Mapping[str, Any]
    redshift: Mapping[str, Any]
    linked_products: Mapping[str, Any]
    coverage: Mapping[str, Any]
    integrity: Mapping[str, Any]
    access: Mapping[str, Any]
    license: Mapping[str, Any]
    supported_claim_scope: tuple[str, ...]
    official_sources: tuple[Mapping[str, Any], ...]
    limitations: tuple[str, ...]
    fixture_sha256: str
    _raw: Mapping[str, Any]

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        fixture_sha256: str,
    ) -> SurveyProductSpec:
        return cls(
            key=str(value["key"]),
            survey=str(value["survey"]),
            display_name=str(value["display_name"]),
            maturity=SurveyProductMaturity(str(value["maturity"])),
            release=copy.deepcopy(value["release"]),
            schema=copy.deepcopy(value["schema"]),
            fields=tuple(
                SurveyFieldSpec.from_mapping(item) for item in value["fields"]
            ),
            coordinates=copy.deepcopy(value["coordinates"]),
            time_system=copy.deepcopy(value["time_system"]),
            redshift=copy.deepcopy(value["redshift"]),
            linked_products=copy.deepcopy(value["linked_products"]),
            coverage=copy.deepcopy(value["coverage"]),
            integrity=copy.deepcopy(value["integrity"]),
            access=copy.deepcopy(value["access"]),
            license=copy.deepcopy(value["license"]),
            supported_claim_scope=tuple(value["supported_claim_scope"]),
            official_sources=tuple(copy.deepcopy(value["official_sources"])),
            limitations=tuple(str(item) for item in value["limitations"]),
            fixture_sha256=fixture_sha256,
            _raw=copy.deepcopy(value),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = copy.deepcopy(dict(self._raw))
        payload["fixture_sha256"] = self.fixture_sha256
        return payload


_FIXTURE_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "survey_schemas"

# These hashes bind the reviewed logical fixtures.  They are not data-product
# hashes and must not be presented as proof that any archive product was read.
SURVEY_SCHEMA_FIXTURE_SHA256: dict[str, str] = {
    "euclid_q1_catalog_fixture_v1.json": (
        "d8eb1d1b6d632590fee6a1d02e898a18722280ba16b96e91fd38c5adc1fedf2c"
    ),
    "roman_prelaunch_catalog_fixture_v1.json": (
        "c9193c8d8dd412e6f7ef1e4186a186f723ddc38373fca71310019e1982e6c3de"
    ),
    "rubin_edp2_object_catalog_fixture_v1.json": (
        "170c7136cb606bfa1de6238e9cb6ce1e24bb50eebfe2a6c3dbf59d2e692f37d9"
    ),
}

_EXPECTED_SURVEYS = frozenset({"rubin", "euclid", "roman"})
_OFFICIAL_HOSTS_BY_SURVEY: dict[str, frozenset[str]] = {
    "rubin": frozenset({"rubinobservatory.org", "dp2.lsst.io"}),
    "euclid": frozenset(
        {
            "euclid.esac.esa.int",
            "eas.esac.esa.int",
            "www.esa.int",
            "www.cosmos.esa.int",
        }
    ),
    "roman": frozenset(
        {
            "science.nasa.gov",
            "assets.science.nasa.gov",
            "roman.gsfc.nasa.gov",
            "archive.stsci.edu",
        }
    ),
}
_ALLOWED_DATA_TYPES = frozenset({"string", "float64", "int64", "boolean"})
_ALLOWED_REDSHIFT_TYPES = frozenset({"photometric", "spectroscopic", "grism", "prism"})
_PINNED_OR_REVIEWED_NOT_APPLICABLE = frozenset({"pinned", "reviewed_not_applicable"})
_ALLOWED_METADATA_CLAIM_SCOPES = frozenset(
    {
        "schema_fixture_compatibility",
        "declared_release_status",
        "logical_field_presence",
        "officially_declared_coverage",
    }
)
_REQUIRED_ROOT_KEYS = frozenset(
    {
        "registry_schema_version",
        "key",
        "survey",
        "display_name",
        "maturity",
        "release",
        "schema",
        "fields",
        "coordinates",
        "time_system",
        "redshift",
        "linked_products",
        "coverage",
        "integrity",
        "access",
        "license",
        "supported_claim_scope",
        "official_sources",
        "limitations",
    }
)


def _fixture_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_fixture(
    path: Path,
    *,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    actual_sha256 = _fixture_digest(path)
    if actual_sha256 != expected_sha256:
        raise SurveyRegistryIntegrityError(
            f"Survey fixture hash mismatch for {path.name}: "
            f"expected {expected_sha256}, got {actual_sha256}."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SurveyRegistryIntegrityError(
            f"Survey fixture {path.name} is unreadable: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SurveyRegistryIntegrityError(
            f"Survey fixture {path.name} must contain one JSON object."
        )
    return payload, actual_sha256


def _load_specs(
    *,
    fixture_directory: Path = _FIXTURE_DIRECTORY,
    expected_hashes: Mapping[str, str] = SURVEY_SCHEMA_FIXTURE_SHA256,
) -> dict[str, SurveyProductSpec]:
    specs: dict[str, SurveyProductSpec] = {}
    for filename, expected_sha256 in sorted(expected_hashes.items()):
        path = fixture_directory / filename
        if not path.is_file():
            raise SurveyRegistryIntegrityError(
                f"Registered survey fixture is missing: {path}."
            )
        payload, actual_sha256 = _read_fixture(
            path,
            expected_sha256=expected_sha256,
        )
        issues = _audit_payload(payload)
        if issues:
            raise SurveyRegistryIntegrityError(
                f"Survey fixture {filename} is invalid: {'; '.join(issues)}"
            )
        spec = SurveyProductSpec.from_mapping(
            payload,
            fixture_sha256=actual_sha256,
        )
        if spec.key in specs:
            raise SurveyRegistryIntegrityError(
                f"Duplicate survey fixture key: {spec.key}."
            )
        specs[spec.key] = spec
    return specs


def get_survey_product_spec(key: str) -> SurveyProductSpec:
    """Return a hash-verified local fixture by registry key."""

    specs = _load_specs()
    normalized = str(key).strip().lower()
    try:
        return specs[normalized]
    except KeyError as exc:
        raise KeyError(
            f"Unknown survey product fixture {key!r}; available: "
            f"{', '.join(sorted(specs))}."
        ) from exc


def list_survey_product_specs() -> dict[str, Any]:
    """Return the reviewed registry without performing any network access."""

    specs = _load_specs()
    return {
        "registry_schema_version": 1,
        "product_count": len(specs),
        "execution_available": False,
        "products": [specs[key].to_dict() for key in sorted(specs)],
        "limitations": [
            "Schema fixtures validate logical contracts only.",
            "No archive query or scientific measurement is supported.",
        ],
    }


class SurveyProductAdapter:
    """Validate logical fixture records while refusing archive execution."""

    def __init__(self, key: str):
        self.spec = get_survey_product_spec(key)

    def describe(self) -> dict[str, Any]:
        return self.spec.to_dict()

    def validate_fixture_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Validate a synthetic/logical record against the local fixture.

        A passing result says only that the dictionary matches the fixture.  It
        remains non-claimable because no physical source column or product
        checksum was used.
        """

        missing_fields: list[str] = []
        type_errors: list[dict[str, str]] = []
        for field in self.spec.fields:
            if field.required and (
                field.name not in record or record.get(field.name) is None
            ):
                missing_fields.append(field.name)
                continue
            if field.name not in record or record.get(field.name) is None:
                continue
            value = record[field.name]
            if not _value_matches_type(value, field.data_type):
                type_errors.append(
                    {
                        "field": field.name,
                        "expected": field.data_type,
                        "observed": type(value).__name__,
                    }
                )

        fixture_valid = not missing_fields and not type_errors
        return {
            "success": fixture_valid,
            "__tool_status__": "COMPLETED" if fixture_valid else "FAILED",
            "analysis_status": (
                "SURVEY_SCHEMA_FIXTURE_VALID"
                if fixture_valid
                else "SURVEY_SCHEMA_FIXTURE_INVALID"
            ),
            "scientific_status": "CAPABILITY_GAP",
            "fixture_key": self.spec.key,
            "maturity": self.spec.maturity.value,
            "missing_fields": missing_fields,
            "type_errors": type_errors,
            "publication_ready": False,
            "scientific_claim_ready": False,
            "claim_scope": "schema_fixture_compatibility",
            "coverage_status": {
                "maturity": self.spec.maturity.value,
                "physical_source_mapped": False,
                "executable": False,
            },
            "__do_not_claim__": True,
            "limitations": [
                "A valid logical fixture is not evidence that an archive row exists.",
                "No source column, data product, selection function, or checksum was consumed.",
            ],
        }

    def execution_status(self) -> dict[str, Any]:
        """Return the explicit capability gap for the current P1 phase."""

        return {
            "success": False,
            "__tool_status__": "UNAVAILABLE",
            "analysis_status": "SURVEY_PRODUCT_NOT_EXECUTABLE",
            "scientific_status": "CAPABILITY_GAP",
            "fixture_key": self.spec.key,
            "maturity": self.spec.maturity.value,
            "publication_ready": False,
            "__do_not_claim__": True,
            "capability_gap": {
                "missing": [
                    "version-pinned physical source mapping",
                    "source product SHA-256",
                    "registered mask/selection/covariance where required",
                    "reviewed archive authentication and rate-limit policy",
                    "executable connector and scientific validation",
                ],
                "next_maturity": SurveyProductMaturity.SOURCE_PINNED.value,
            },
        }


def _value_matches_type(value: Any, data_type: str) -> bool:
    if data_type == "string":
        return isinstance(value, str)
    if data_type == "float64":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if data_type == "int64":
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type == "boolean":
        return isinstance(value, bool)
    return False


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _audit_payload(payload: Mapping[str, Any]) -> list[str]:
    issues: list[str] = []
    missing_root = sorted(_REQUIRED_ROOT_KEYS - payload.keys())
    if missing_root:
        return [f"missing root keys: {missing_root}"]

    if payload.get("registry_schema_version") != 1:
        issues.append("registry_schema_version must be 1")
    survey = str(payload.get("survey", ""))
    if survey not in _EXPECTED_SURVEYS:
        issues.append(f"unsupported survey: {payload.get('survey')!r}")
    official_hosts = _OFFICIAL_HOSTS_BY_SURVEY.get(survey, frozenset())

    try:
        maturity = SurveyProductMaturity(str(payload.get("maturity")))
    except ValueError:
        maturity = None
        issues.append(f"invalid maturity: {payload.get('maturity')!r}")
    if maturity is SurveyProductMaturity.EXECUTABLE:
        issues.append("P1 survey registry must not contain EXECUTABLE products")

    release = payload.get("release")
    if not isinstance(release, Mapping):
        issues.append("release must be an object")
    else:
        for key in ("name", "version", "status", "official_url", "checked_utc"):
            if not release.get(key):
                issues.append(f"release.{key} is required")
        if not str(release.get("official_url", "")).startswith("https://"):
            issues.append("release.official_url must use https")
        elif urlparse(str(release.get("official_url"))).hostname not in official_hosts:
            issues.append("release.official_url must use a registered authority host")

    schema = payload.get("schema")
    if not isinstance(schema, Mapping):
        issues.append("schema must be an object")
    else:
        for key in ("name", "version", "source_document_url"):
            if not schema.get(key):
                issues.append(f"schema.{key} is required")
        if not str(schema.get("source_document_url", "")).startswith("https://"):
            issues.append("schema.source_document_url must use https")
        elif (
            urlparse(str(schema.get("source_document_url"))).hostname
            not in official_hosts
        ):
            issues.append(
                "schema.source_document_url must use a registered authority host"
            )

    fields = payload.get("fields")
    field_names: list[str] = []
    if not isinstance(fields, list) or not fields:
        issues.append("fields must be a non-empty list")
    else:
        for index, field in enumerate(fields):
            if not isinstance(field, Mapping):
                issues.append(f"fields[{index}] must be an object")
                continue
            required_keys = {
                "name",
                "source_name",
                "data_type",
                "unit",
                "required",
                "description",
            }
            missing = sorted(required_keys - field.keys())
            if missing:
                issues.append(f"fields[{index}] missing keys: {missing}")
                continue
            name = str(field.get("name", ""))
            field_names.append(name)
            if not name:
                issues.append(f"fields[{index}].name is required")
            if field.get("data_type") not in _ALLOWED_DATA_TYPES:
                issues.append(
                    f"fields[{index}].data_type is unsupported: "
                    f"{field.get('data_type')!r}"
                )
            if not isinstance(field.get("required"), bool):
                issues.append(f"fields[{index}].required must be boolean")
            if field.get("data_type") in {"float64", "int64"} and not field.get("unit"):
                issues.append(
                    f"fields[{index}].unit is required for numeric logical fields"
                )
            if not str(field.get("description", "")).strip():
                issues.append(f"fields[{index}].description is required")
        duplicates = sorted(
            {name for name in field_names if field_names.count(name) > 1}
        )
        if duplicates:
            issues.append(f"duplicate logical field names: {duplicates}")

    coordinates = payload.get("coordinates")
    if not isinstance(coordinates, Mapping):
        issues.append("coordinates must be an object")
    else:
        for key in (
            "spatial_frame",
            "longitude_field",
            "latitude_field",
            "unit",
            "equinox",
            "status",
        ):
            if key not in coordinates:
                issues.append(f"coordinates.{key} is required")
        for axis in ("longitude_field", "latitude_field"):
            if coordinates.get(axis) not in field_names:
                issues.append(f"coordinates.{axis} must name a logical field")

    time_system = payload.get("time_system")
    if not isinstance(time_system, Mapping):
        issues.append("time_system must be an object")
    else:
        for key in ("field", "scale", "format", "reference_position", "status"):
            if key not in time_system:
                issues.append(f"time_system.{key} is required")
        if time_system.get("field") not in field_names:
            issues.append("time_system.field must name a logical field")

    redshift = payload.get("redshift")
    if not isinstance(redshift, Mapping):
        issues.append("redshift must be an object")
    else:
        for key in (
            "types",
            "value_fields",
            "uncertainty_fields",
            "uncertainty_status",
            "status",
        ):
            if key not in redshift:
                issues.append(f"redshift.{key} is required")
        unknown_redshift_types = (
            set(redshift.get("types", [])) - _ALLOWED_REDSHIFT_TYPES
        )
        if unknown_redshift_types:
            issues.append(
                f"redshift.types contains unsupported values: "
                f"{sorted(unknown_redshift_types)}"
            )
        for name in redshift.get("value_fields", []):
            if name not in field_names:
                issues.append(f"redshift.value_fields contains unknown field {name!r}")

    linked_products = payload.get("linked_products")
    if not isinstance(linked_products, Mapping):
        issues.append("linked_products must be an object")
    else:
        for key in ("covariance", "mask", "selection"):
            item = linked_products.get(key)
            if not isinstance(item, Mapping):
                issues.append(f"linked_products.{key} must be an object")
                continue
            linked_required = {"status", "kind", "field", "artifact"}
            linked_missing = sorted(linked_required - item.keys())
            if linked_missing:
                issues.append(f"linked_products.{key} missing keys: {linked_missing}")
                continue
            artifact = item.get("artifact")
            if not isinstance(artifact, Mapping):
                issues.append(f"linked_products.{key}.artifact must be an object")
                continue
            artifact_required = {"source_url", "version", "sha256"}
            artifact_missing = sorted(artifact_required - artifact.keys())
            if artifact_missing:
                issues.append(
                    f"linked_products.{key}.artifact missing keys: {artifact_missing}"
                )

    coverage = payload.get("coverage")
    if not isinstance(coverage, Mapping):
        issues.append("coverage must be an object")
    else:
        for key in (
            "status",
            "sky_area_sq_deg",
            "regions",
            "temporal_start",
            "temporal_stop",
            "source_url",
        ):
            if key not in coverage:
                issues.append(f"coverage.{key} is required")
        if not str(coverage.get("source_url", "")).startswith("https://"):
            issues.append("coverage.source_url must use https")
        elif urlparse(str(coverage.get("source_url"))).hostname not in official_hosts:
            issues.append("coverage.source_url must use a registered authority host")

    integrity = payload.get("integrity")
    if not isinstance(integrity, Mapping):
        issues.append("integrity must be an object")
    else:
        for key in (
            "algorithm",
            "product_sha256",
            "checksum_scope",
            "required_before_source_pinned",
        ):
            if key not in integrity:
                issues.append(f"integrity.{key} is required")
        if integrity.get("algorithm") != "sha256":
            issues.append("integrity.algorithm must be sha256")
        product_sha256 = integrity.get("product_sha256")
        if product_sha256 is not None and not _is_sha256(product_sha256):
            issues.append("integrity.product_sha256 must be null or lowercase SHA-256")
        if maturity in {
            SurveyProductMaturity.SOURCE_PINNED,
            SurveyProductMaturity.EXECUTABLE,
        } and not _is_sha256(product_sha256):
            issues.append("SOURCE_PINNED or EXECUTABLE requires a product SHA-256")

    # A maturity label alone must never promote a logical fixture.  These
    # additional bindings make the promotion fail closed even if somebody
    # adds a product digest but forgets the physical schema and usage terms.
    if maturity in {
        SurveyProductMaturity.SOURCE_PINNED,
        SurveyProductMaturity.EXECUTABLE,
    }:
        if isinstance(release, Mapping) and release.get("status") in {
            "planned",
            "prelaunch",
        }:
            issues.append("SOURCE_PINNED requires a released source product")
        if isinstance(schema, Mapping) and str(schema.get("version", "")).startswith(
            "fixture-"
        ):
            issues.append("SOURCE_PINNED requires a physical schema version")
        if isinstance(fields, list):
            unmapped_required = sorted(
                str(field.get("name", ""))
                for field in fields
                if isinstance(field, Mapping)
                and field.get("required") is True
                and not field.get("source_name")
            )
            if unmapped_required:
                issues.append(
                    "SOURCE_PINNED required fields lack physical source_name: "
                    f"{unmapped_required}"
                )
        source_names = (
            {
                str(field.get("name", "")): field.get("source_name")
                for field in fields
                if isinstance(field, Mapping)
            }
            if isinstance(fields, list)
            else {}
        )

        if isinstance(coordinates, Mapping):
            if coordinates.get("status") != "pinned":
                issues.append("SOURCE_PINNED requires a pinned coordinate mapping")
            coordinate_fields = [
                coordinates.get("longitude_field"),
                coordinates.get("latitude_field"),
            ]
            unmapped_coordinates = sorted(
                str(name)
                for name in coordinate_fields
                if not source_names.get(str(name))
            )
            if unmapped_coordinates:
                issues.append(
                    "SOURCE_PINNED coordinate fields lack physical source_name: "
                    f"{unmapped_coordinates}"
                )

        if isinstance(time_system, Mapping):
            time_status = str(time_system.get("status", ""))
            if time_status not in _PINNED_OR_REVIEWED_NOT_APPLICABLE:
                issues.append(
                    "SOURCE_PINNED time_system.status must be pinned or "
                    "reviewed_not_applicable"
                )
            elif time_status == "pinned":
                if any(
                    str(time_system.get(key, "")) in {"", "unknown"}
                    for key in ("scale", "format", "reference_position")
                ):
                    issues.append(
                        "Pinned time_system requires scale, format, and "
                        "reference_position"
                    )
                time_field = str(time_system.get("field", ""))
                if not source_names.get(time_field):
                    issues.append(
                        "Pinned time_system field lacks physical source_name: "
                        f"{time_field!r}"
                    )
            elif any(
                str(time_system.get(key, "")) != "not_applicable"
                for key in ("scale", "format", "reference_position")
            ):
                issues.append(
                    "reviewed_not_applicable time_system must mark scale, "
                    "format, and reference_position as not_applicable"
                )

        if isinstance(redshift, Mapping):
            redshift_status = str(redshift.get("status", ""))
            redshift_types = list(redshift.get("types", []))
            redshift_fields = list(redshift.get("value_fields", []))
            uncertainty_fields = list(redshift.get("uncertainty_fields", []))
            uncertainty_status = str(redshift.get("uncertainty_status", ""))
            if redshift_status not in _PINNED_OR_REVIEWED_NOT_APPLICABLE:
                issues.append(
                    "SOURCE_PINNED redshift.status must be pinned or "
                    "reviewed_not_applicable"
                )
            elif redshift_status == "pinned":
                if not redshift_types or not redshift_fields:
                    issues.append("Pinned redshift requires types and value_fields")
                unmapped_redshift = sorted(
                    str(name)
                    for name in [*redshift_fields, *uncertainty_fields]
                    if not source_names.get(str(name))
                )
                if unmapped_redshift:
                    issues.append(
                        "Pinned redshift fields lack physical source_name: "
                        f"{unmapped_redshift}"
                    )
                if uncertainty_status not in {"pinned", "reviewed_not_available"}:
                    issues.append(
                        "Pinned redshift uncertainty_status must be pinned or "
                        "reviewed_not_available"
                    )
                elif uncertainty_status == "pinned" and not uncertainty_fields:
                    issues.append(
                        "Pinned redshift uncertainty_status requires uncertainty_fields"
                    )
                elif (
                    uncertainty_status == "reviewed_not_available"
                    and uncertainty_fields
                ):
                    issues.append(
                        "reviewed_not_available redshift must not declare "
                        "uncertainty_fields"
                    )
            else:
                if redshift_types or redshift_fields or uncertainty_fields:
                    issues.append(
                        "reviewed_not_applicable redshift must not declare types or fields"
                    )
                if uncertainty_status != "reviewed_not_applicable":
                    issues.append(
                        "reviewed_not_applicable redshift requires matching "
                        "uncertainty_status"
                    )

        if isinstance(linked_products, Mapping):
            access_for_artifacts = payload.get("access")
            allowed_artifact_hosts = (
                set(access_for_artifacts.get("artifact_host_allowlist", []))
                if isinstance(access_for_artifacts, Mapping)
                else set()
            )
            mapped_source_fields = {
                str(value) for value in source_names.values() if value
            }
            for product_role in ("covariance", "mask", "selection"):
                linked = linked_products.get(product_role)
                if not isinstance(linked, Mapping):
                    continue
                linked_status = str(linked.get("status", ""))
                linked_kind = linked.get("kind")
                linked_field = linked.get("field")
                linked_artifact = linked.get("artifact")
                if linked_status not in _PINNED_OR_REVIEWED_NOT_APPLICABLE:
                    issues.append(
                        f"SOURCE_PINNED linked_products.{product_role}.status "
                        "must be pinned or reviewed_not_applicable"
                    )
                elif linked_status == "pinned":
                    if linked_kind not in {"field", "artifact"}:
                        issues.append(
                            f"Pinned linked_products.{product_role}.kind must be "
                            "field or artifact"
                        )
                    elif linked_kind == "field":
                        if linked_field not in mapped_source_fields:
                            issues.append(
                                f"Pinned linked_products.{product_role}.field must "
                                "match a physical source_name"
                            )
                        if isinstance(linked_artifact, Mapping) and any(
                            linked_artifact.get(key) is not None
                            for key in ("source_url", "version", "sha256")
                        ):
                            issues.append(
                                f"Field-linked {product_role} must not declare an artifact"
                            )
                    elif linked_kind == "artifact":
                        if linked_field is not None:
                            issues.append(
                                f"Artifact-linked {product_role} must not declare field"
                            )
                        if not isinstance(linked_artifact, Mapping):
                            continue
                        artifact_url = str(linked_artifact.get("source_url", ""))
                        artifact_host = urlparse(artifact_url).hostname
                        if (
                            not artifact_url.startswith("https://")
                            or artifact_host not in allowed_artifact_hosts
                        ):
                            issues.append(
                                f"Pinned linked_products.{product_role} artifact URL "
                                "must use an allowlisted HTTPS host"
                            )
                        artifact_version = str(linked_artifact.get("version", ""))
                        if not artifact_version or any(
                            marker in artifact_version
                            for marker in ("unknown", "unpinned", "live")
                        ):
                            issues.append(
                                f"Pinned linked_products.{product_role} artifact "
                                "requires a fixed version"
                            )
                        if not _is_sha256(linked_artifact.get("sha256")):
                            issues.append(
                                f"Pinned linked_products.{product_role} artifact "
                                "requires SHA-256"
                            )
                elif (
                    linked_kind is not None
                    or linked_field is not None
                    or (
                        isinstance(linked_artifact, Mapping)
                        and any(
                            linked_artifact.get(key) is not None
                            for key in ("source_url", "version", "sha256")
                        )
                    )
                ):
                    issues.append(
                        f"reviewed_not_applicable linked_products.{product_role} "
                        "must not declare a field or artifact"
                    )

        if isinstance(coverage, Mapping):
            coverage_status = str(coverage.get("status", ""))
            if coverage_status not in _PINNED_OR_REVIEWED_NOT_APPLICABLE:
                issues.append(
                    "SOURCE_PINNED coverage.status must be pinned or "
                    "reviewed_not_applicable"
                )
            elif coverage_status == "pinned" and not any(
                (
                    coverage.get("sky_area_sq_deg") is not None,
                    bool(coverage.get("regions")),
                    coverage.get("temporal_start") is not None,
                    coverage.get("temporal_stop") is not None,
                )
            ):
                issues.append("Pinned coverage requires a spatial or temporal bound")

        if isinstance(integrity, Mapping):
            checksum_scope = str(integrity.get("checksum_scope", ""))
            if (
                not checksum_scope
                or "unregistered" in checksum_scope
                or checksum_scope.startswith("no_")
            ):
                issues.append(
                    "SOURCE_PINNED requires an exact data-product checksum_scope"
                )

    access = payload.get("access")
    if not isinstance(access, Mapping):
        issues.append("access must be an object")
    else:
        for key in (
            "archive_url",
            "authentication",
            "rate_limit",
            "artifact_host_allowlist",
            "offline_behavior",
        ):
            if key not in access:
                issues.append(f"access.{key} is required")
        if not str(access.get("archive_url", "")).startswith("https://"):
            issues.append("access.archive_url must use https")
        elif urlparse(str(access.get("archive_url"))).hostname not in official_hosts:
            issues.append("access.archive_url must use a registered authority host")
        if access.get("offline_behavior") != "fail_closed":
            issues.append("access.offline_behavior must be fail_closed")
        artifact_host_allowlist = access.get("artifact_host_allowlist")
        if (
            not isinstance(artifact_host_allowlist, list)
            or not artifact_host_allowlist
            or any(
                not isinstance(host, str) or not host or "/" in host or ":" in host
                for host in artifact_host_allowlist
            )
        ):
            issues.append(
                "access.artifact_host_allowlist must contain plain host names"
            )
        elif not set(artifact_host_allowlist) <= official_hosts:
            issues.append("access.artifact_host_allowlist contains non-authority hosts")
        if maturity in {
            SurveyProductMaturity.SOURCE_PINNED,
            SurveyProductMaturity.EXECUTABLE,
        }:
            authentication = str(access.get("authentication", ""))
            rate_limit = str(access.get("rate_limit", ""))
            if "verify" in authentication or "not_applicable" in authentication:
                issues.append("SOURCE_PINNED requires resolved authentication policy")
            if rate_limit in {"", "unknown"}:
                issues.append("SOURCE_PINNED requires a resolved rate-limit policy")

    license_info = payload.get("license")
    if not isinstance(license_info, Mapping):
        issues.append("license must be an object")
    else:
        for key in ("identifier", "url", "status"):
            if key not in license_info:
                issues.append(f"license.{key} is required")
        if not str(license_info.get("url", "")).startswith("https://"):
            issues.append("license.url must use https")
        elif urlparse(str(license_info.get("url"))).hostname not in official_hosts:
            issues.append("license.url must use a registered authority host")
        if not license_info.get("status"):
            issues.append("license.status is required")
        if maturity in {
            SurveyProductMaturity.SOURCE_PINNED,
            SurveyProductMaturity.EXECUTABLE,
        }:
            if not license_info.get("identifier"):
                issues.append("SOURCE_PINNED requires a product licence identifier")
            if "verify" in str(license_info.get("status", "")):
                issues.append("SOURCE_PINNED requires verified product licence terms")

    scopes = payload.get("supported_claim_scope")
    if not isinstance(scopes, list) or not scopes:
        issues.append("supported_claim_scope must be a non-empty list")
    else:
        unknown_scopes = set(scopes) - _ALLOWED_METADATA_CLAIM_SCOPES
        if unknown_scopes:
            issues.append(
                f"supported_claim_scope contains non-metadata scopes: "
                f"{sorted(unknown_scopes)}"
            )

    official_sources = payload.get("official_sources")
    if not isinstance(official_sources, list) or not official_sources:
        issues.append("official_sources must be a non-empty list")
    else:
        for index, source in enumerate(official_sources):
            if not isinstance(source, Mapping):
                issues.append(f"official_sources[{index}] must be an object")
                continue
            for key in ("authority", "url", "document_version", "checked_utc"):
                if not source.get(key):
                    issues.append(f"official_sources[{index}].{key} is required")
            if not str(source.get("url", "")).startswith("https://"):
                issues.append(f"official_sources[{index}].url must use https")
            elif urlparse(str(source.get("url"))).hostname not in official_hosts:
                issues.append(
                    f"official_sources[{index}].url must use a registered "
                    "authority host"
                )
            if maturity in {
                SurveyProductMaturity.SOURCE_PINNED,
                SurveyProductMaturity.EXECUTABLE,
            } and any(
                marker in str(source.get("document_version", ""))
                for marker in ("unpinned", "live-documentation")
            ):
                issues.append(
                    f"official_sources[{index}] requires a pinned document_version"
                )

    limitations = payload.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        issues.append("limitations must be a non-empty list")
    return issues


def audit_survey_product_registry(
    *,
    fixture_directory: Path = _FIXTURE_DIRECTORY,
    expected_hashes: Mapping[str, str] = SURVEY_SCHEMA_FIXTURE_SHA256,
) -> dict[str, list[str]]:
    """Audit fixture integrity and every required science-safety field."""

    issues_by_fixture: dict[str, list[str]] = {}
    observed_surveys: set[str] = set()

    expected_files = set(expected_hashes)
    actual_files = {path.name for path in fixture_directory.glob("*.json")}
    for filename in sorted(expected_files - actual_files):
        issues_by_fixture[filename] = ["registered fixture file is missing"]
    for filename in sorted(actual_files - expected_files):
        issues_by_fixture[filename] = ["unregistered JSON fixture file"]

    observed_keys: set[str] = set()
    for filename, expected_sha256 in sorted(expected_hashes.items()):
        path = fixture_directory / filename
        if not path.is_file():
            continue
        fixture_issues: list[str] = []
        actual_sha256 = _fixture_digest(path)
        if actual_sha256 != expected_sha256:
            fixture_issues.append(
                f"fixture SHA-256 mismatch: expected {expected_sha256}, "
                f"got {actual_sha256}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            fixture_issues.append(f"fixture is unreadable: {exc}")
            issues_by_fixture[filename] = fixture_issues
            continue
        if not isinstance(payload, dict):
            fixture_issues.append("fixture root must be a JSON object")
        else:
            fixture_issues.extend(_audit_payload(payload))
            key = str(payload.get("key", ""))
            if key in observed_keys:
                fixture_issues.append(f"duplicate fixture key: {key}")
            observed_keys.add(key)
            observed_surveys.add(str(payload.get("survey", "")))
        if fixture_issues:
            issues_by_fixture[filename] = fixture_issues

    missing_surveys = sorted(_EXPECTED_SURVEYS - observed_surveys)
    if missing_surveys:
        issues_by_fixture["__registry__"] = [
            f"missing required survey fixtures: {missing_surveys}"
        ]
    return issues_by_fixture


__all__ = [
    "SURVEY_SCHEMA_FIXTURE_SHA256",
    "SurveyFieldSpec",
    "SurveyProductAdapter",
    "SurveyProductMaturity",
    "SurveyProductSpec",
    "SurveyRegistryIntegrityError",
    "audit_survey_product_registry",
    "get_survey_product_spec",
    "list_survey_product_specs",
]
