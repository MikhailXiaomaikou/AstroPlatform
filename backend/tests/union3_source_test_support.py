"""Deterministic complete source receipts for focused Union3 tests."""

from __future__ import annotations

import hashlib

from app.services.union3_reader import (
    UNION3_ARXIV_ID,
    UNION3_ATOM_URL,
    UNION3_PDF_SHA256,
    UNION3_SOURCE_TAR_SHA256,
    UNION3_SOURCE_TAR_URL,
    UNION3_SOURCE_URL,
    Union3PdfTextSnapshot,
)

_METADATA_BYTES = b"standard-astro-registered-union3-v4-atom-test-fixture"
TEST_METADATA_SHA256 = hashlib.sha256(_METADATA_BYTES).hexdigest()
_PREFIX = f"source-snapshots/union3/{UNION3_ARXIV_ID}"


def registered_union3_snapshot(text: str) -> Union3PdfTextSnapshot:
    """Return a complete server-trusted receipt without network or storage I/O."""

    return Union3PdfTextSnapshot(
        text=text,
        pdf_sha256=UNION3_PDF_SHA256,
        metadata_sha256=TEST_METADATA_SHA256,
        source_tar_sha256=UNION3_SOURCE_TAR_SHA256,
        metadata_identity={
            "schema_version": "union3_arxiv_atom_identity_v1",
            "entry_id": f"http://arxiv.org/abs/{UNION3_ARXIV_ID}",
            "canonical_identifier": UNION3_ARXIV_ID,
            "base_identifier": "2311.12098",
            "version": 4,
            "title": (
                "Union Through UNITY: Cosmology with 2,000 SNe Using a Unified "
                "Bayesian Framework"
            ),
            "authors": ["D. Rubin"],
            "published": "2023-11-20T00:00:00Z",
            "updated": "2025-06-21T00:00:00Z",
            "primary_category": "astro-ph.CO",
            "categories": ["astro-ph.CO"],
            "pdf_link": f"http://arxiv.org/pdf/{UNION3_ARXIV_ID}",
        },
        source_tar_validation={
            "schema_version": "union3_source_tar_validation_v1",
            "entry_count": 40,
            "regular_file_count": 40,
            "directory_count": 0,
            "uncompressed_bytes": 6_295_735,
            "member_manifest_sha256": (
                "40b6f1edf7e8f2df31896221446a884ea9308b97e8c3c5c493caa41f5877a40f"
            ),
            "required_root_files": ["00README.json", "merged.tex"],
            "extracted_to_disk": False,
        },
        metadata_artifact_ref=f"{_PREFIX}/{TEST_METADATA_SHA256}.atom.xml",
        source_tar_artifact_ref=(
            f"{_PREFIX}/{UNION3_SOURCE_TAR_SHA256}.tar.gz"
        ),
        metadata_resolved_url=UNION3_ATOM_URL,
        source_tar_resolved_url=UNION3_SOURCE_TAR_URL,
        pdf_resolved_url=UNION3_SOURCE_URL,
        metadata_content_type="application/atom+xml",
        source_tar_content_type="application/gzip",
        metadata_byte_size=len(_METADATA_BYTES),
        source_tar_byte_size=5_414_745,
        pdf_byte_size=5_953_972,
        source_url=UNION3_SOURCE_URL,
        content_type="application/pdf",
        artifact_ref=f"{_PREFIX}/{UNION3_PDF_SHA256}.pdf",
    )


__all__ = ["TEST_METADATA_SHA256", "registered_union3_snapshot"]
