"""DOI metadata must never fabricate an identifier under a real registrar prefix."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_doi_metadata_carries_no_fabricated_doi():
    # Regression (2026-07-24): generate_doi_metadata used to emit
    # "10.5281/standard-astro.<id>" — a non-resolving identifier under
    # Zenodo's real DataCite prefix. An anti-fabrication platform must not
    # fabricate identifiers; the record now carries a content-addressed URN
    # and an explicit not-minted status instead.
    from app.services.provenance import generate_doi_metadata, record_activity

    entity_id = "doi-regression-entity"
    record_activity(
        entity_type="analysis",
        entity_id=entity_id,
        activity="test_fixture",
        params={},
        user_id="doi-regression-owner",
    )
    metadata = generate_doi_metadata(entity_id, owner_id="doi-regression-owner")
    serialized = json.dumps(metadata)

    assert "10.5281" not in serialized
    attributes = metadata["data"]["attributes"]
    assert attributes["doi"] is None
    assert attributes["doi_status"] == "not_minted"
    alternates = attributes["alternateIdentifiers"]
    assert len(alternates) == 1
    assert alternates[0]["alternateIdentifierType"] == "URN"
    assert alternates[0]["alternateIdentifier"].startswith("urn:sha256:")
    assert len(alternates[0]["alternateIdentifier"]) == len("urn:sha256:") + 64


def test_citation_cff_parses_with_required_keys():
    citation_path = Path(__file__).resolve().parents[2] / "CITATION.cff"
    data = yaml.safe_load(citation_path.read_text())

    assert data["cff-version"] == "1.2.0"
    assert data["title"] == "Standard Astro"
    assert data["license"] == "Apache-2.0"
    assert data["authors"], "authors must not be empty"
    # A fabricated DOI must never reappear through the citation file either.
    assert "doi" not in data
