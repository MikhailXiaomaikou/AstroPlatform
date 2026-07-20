"""Focused tests for the checksum-pinned Union3 Table 9 reader."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_PDF_SHA256,
    UNION3_SOURCE_PROFILE_KEY,
    Union3ReaderError,
    extract_union3_table9,
    load_registered_union3_snapshot,
    normalize_union3_identifier,
    set_union3_snapshot_loader,
)


FIXTURE = Path(__file__).parent / "fixtures" / "union3_2311_12098v4_table9.txt"


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_union3_reader_extracts_only_registered_frequentist_table9_candidate():
    result = extract_union3_table9(
        _fixture_text(),
        pdf_sha256=UNION3_PDF_SHA256,
    )

    assert result["source"]["canonical_identifier"] == UNION3_ARXIV_ID
    assert result["source"]["source_profile_key"] == UNION3_SOURCE_PROFILE_KEY
    assert result["coverage_status"] == "UNION3_TABLE9_INTERVAL_READY"
    assert result["publication_ready"] is False
    assert len(result["candidates"]) == 1

    candidate = result["candidates"][0]
    assert candidate["parameter"] == "omegam"
    assert candidate["reported_value"] == {
        "central": "0.356",
        "plus": "0.028",
        "minus": "0.026",
        "lower": "0.330",
        "upper": "0.384",
        "confidence_level": "0.683",
    }
    assert candidate["model_scope"] == "flat_lcdm"
    assert candidate["data_scope"] == "union3_sn_only"
    assert candidate["interval_kind"] == "frequentist_profile_chi_square"
    assert candidate["statistical_semantics"] == "frequentist_profile_chi_square"
    assert candidate["delta_chi_square"] == "1"
    assert candidate["fit_diagnostics"] == {
        "chi_square": "24.0",
        "degrees_of_freedom": 20,
    }
    assert candidate["publication_ready"] is False
    assert "posterior" not in json.dumps(result, ensure_ascii=False).lower()


def test_union3_reader_hashes_are_deterministic_and_anchor_source_document():
    first = extract_union3_table9(_fixture_text(), pdf_sha256=UNION3_PDF_SHA256)
    second = extract_union3_table9(_fixture_text(), pdf_sha256=UNION3_PDF_SHA256)

    assert first == second
    source_hash = first["source"]["source_document_hash"]
    assert len(source_hash) == 64
    assert first["extraction_hash"] == second["extraction_hash"]
    for anchor in first["anchors"]:
        material = (
            source_hash
            + json.dumps(
                anchor["locator"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + anchor["raw_text"]
        )
        expected = "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()
        assert anchor["anchor_id"] == expected
        assert anchor["source_document_hash"] == source_hash


@pytest.mark.parametrize(
    "identifier",
    [
        "2311.12098",
        "2311.12098v3",
        "https://arxiv.org/abs/2311.12098",
        "https://arxiv.org/pdf/2311.12098v4?download=1",
        "2503.14738v1",
    ],
)
def test_union3_reader_rejects_unregistered_identifiers(identifier):
    with pytest.raises(Union3ReaderError) as exc_info:
        normalize_union3_identifier(UNION3_SOURCE_PROFILE_KEY, identifier)
    assert exc_info.value.error_class == "source_identifier_not_registered"


def test_union3_reader_rejects_unregistered_profile_and_checksum():
    with pytest.raises(Union3ReaderError) as profile_error:
        normalize_union3_identifier("generic_arxiv", UNION3_ARXIV_ID)
    assert profile_error.value.error_class == "source_profile_not_registered"

    with pytest.raises(Union3ReaderError) as checksum_error:
        extract_union3_table9(_fixture_text(), pdf_sha256="0" * 64)
    assert checksum_error.value.error_class == "pdf_checksum_mismatch"


def test_union3_reader_fails_closed_when_method_or_row_changes():
    with pytest.raises(Union3ReaderError) as method_error:
        extract_union3_table9(
            _fixture_text().replace("increases by 1", "increases by 2"),
            pdf_sha256=UNION3_PDF_SHA256,
        )
    assert method_error.value.error_class == "table9_method_not_found"

    with pytest.raises(Union3ReaderError) as row_error:
        extract_union3_table9(
            _fixture_text().replace("0.356+0.028", "0.355+0.028"),
            pdf_sha256=UNION3_PDF_SHA256,
        )
    assert row_error.value.error_class == "table9_row_not_found"


async def test_registered_snapshot_loader_classifies_operational_failure():
    def _failing_loader(_identifier: str):
        raise RuntimeError("network details must not escape")

    set_union3_snapshot_loader(_failing_loader)
    try:
        with pytest.raises(Union3ReaderError) as error:
            await load_registered_union3_snapshot(UNION3_ARXIV_ID)
        assert error.value.error_class == "authoritative_pdf_load_failed"
        assert error.value.retryable is True
        assert "network details" not in str(error.value)
    finally:
        set_union3_snapshot_loader(None)
