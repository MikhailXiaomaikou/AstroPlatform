"""Fail-closed reader for the registered Union3 arXiv v4 source chain."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal


UNION3_SOURCE_PROFILE_KEY = "union3_arxiv_v1"
UNION3_ARXIV_ID = "2311.12098v4"
UNION3_ATOM_URL = "https://export.arxiv.org/api/query?id_list=2311.12098v4"
UNION3_SOURCE_TAR_URL = "https://export.arxiv.org/e-print/2311.12098v4"
UNION3_SOURCE_URL = "https://arxiv.org/pdf/2311.12098v4"
UNION3_SOURCE_TAR_SHA256 = (
    "13d14b96ba72b0a548642c7d9e7c7cf6000de062cbc0dbe17bf30198ba1e1189"
)
# arXiv regenerated the v4 PDF container on 2025-06-23 without changing the
# extracted paper text. This is the currently served 5,953,972-byte artifact,
# re-pinned after byte-level download and old/new pdftotext equality checks on
# 2026-07-20. Any future container change still fails closed for re-review.
UNION3_PDF_SHA256 = "6a8fccccecfc083d24c07f508d15ba273ebec1333fe4702d226520ffbaa603c9"
UNION3_READER_VERSION = "union3_arxiv_pdf_table9_reader_v1"
UNION3_EXTRACTION_SCHEMA_VERSION = "union3_source_extraction_v1"
UNION3_SOURCE_DOCUMENT_VERSION = 1
UNION3_TEXT_PROJECTION_METHOD = "pdftotext_raw_utf8_v1"


class Union3ReaderError(ValueError):
    """A classified, fail-closed reader error safe to expose through the API."""

    def __init__(self, error_class: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.error_class = error_class
        self.retryable = retryable


@dataclass(frozen=True)
class Union3PdfTextSnapshot:
    """Trusted PDF projection plus its complete, version-pinned source receipt.

    The PDF remains the authoritative evidence artifact for Table 9.  Atom
    metadata and the exact source tarball establish the requested arXiv identity
    and preserve acquisition provenance; neither is allowed to replace the PDF
    when extracting a scientific claim.
    """

    text: str
    pdf_sha256: str
    metadata_sha256: str = ""
    source_tar_sha256: str = ""
    metadata_identity: dict[str, object] = field(default_factory=dict)
    source_tar_validation: dict[str, object] = field(default_factory=dict)
    metadata_artifact_ref: str = ""
    source_tar_artifact_ref: str = ""
    metadata_resolved_url: str = UNION3_ATOM_URL
    source_tar_resolved_url: str = UNION3_SOURCE_TAR_URL
    pdf_resolved_url: str = UNION3_SOURCE_URL
    metadata_content_type: str = "application/atom+xml"
    source_tar_content_type: str = "application/x-eprint-tar"
    metadata_byte_size: int = 0
    source_tar_byte_size: int = 0
    pdf_byte_size: int = 0
    source_url: str = UNION3_SOURCE_URL
    content_type: str = "application/pdf"
    artifact_ref: str = UNION3_SOURCE_URL


SnapshotLoader = Callable[
    [str],
    Union3PdfTextSnapshot | Awaitable[Union3PdfTextSnapshot],
]
_snapshot_loader: SnapshotLoader | None = None


def set_union3_snapshot_loader(loader: SnapshotLoader | None) -> None:
    """Configure the server-trusted artifact loader used by HTTP ingestion."""

    global _snapshot_loader
    _snapshot_loader = loader


async def load_registered_union3_snapshot(
    canonical_identifier: str,
) -> Union3PdfTextSnapshot:
    """Load a registered snapshot without accepting client-authored source text."""

    if canonical_identifier != UNION3_ARXIV_ID:
        raise Union3ReaderError(
            "source_identifier_not_registered",
            "The source identifier is not registered for the Union3 reader",
        )
    if _snapshot_loader is None:
        raise Union3ReaderError(
            "authoritative_source_loader_not_configured",
            "The authoritative Union3 source-chain loader is not configured",
            retryable=True,
        )
    try:
        snapshot = _snapshot_loader(canonical_identifier)
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
    except Union3ReaderError:
        raise
    except Exception as exc:
        raise Union3ReaderError(
            "authoritative_pdf_load_failed",
            "The authoritative Union3 PDF snapshot could not be loaded",
            retryable=True,
        ) from exc
    if not isinstance(snapshot, Union3PdfTextSnapshot):
        raise Union3ReaderError(
            "authoritative_source_loader_invalid",
            "The authoritative source-chain loader returned an invalid snapshot",
            retryable=True,
        )
    return snapshot


def normalize_union3_identifier(source_profile_key: str, identifier: str) -> str:
    """Accept only the explicitly registered Union3 v4 identity."""

    if source_profile_key != UNION3_SOURCE_PROFILE_KEY:
        raise Union3ReaderError(
            "source_profile_not_registered",
            "The requested source profile is not registered",
        )
    value = str(identifier).strip()
    accepted = {
        UNION3_ARXIV_ID.lower(),
        f"arxiv:{UNION3_ARXIV_ID}".lower(),
        f"https://arxiv.org/abs/{UNION3_ARXIV_ID}".lower(),
        f"https://arxiv.org/pdf/{UNION3_ARXIV_ID}".lower(),
        f"https://arxiv.org/pdf/{UNION3_ARXIV_ID}.pdf".lower(),
    }
    if value.lower() not in accepted:
        raise Union3ReaderError(
            "source_identifier_not_registered",
            "Only the version-pinned arXiv identifier 2311.12098v4 is registered",
        )
    return UNION3_ARXIV_ID


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object, *, prefix: bool = False) -> str:
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}" if prefix else digest


def _normalize_pdf_text(text: str) -> str:
    translations = str.maketrans(
        {
            "Ω": "Ω",
            "−": "-",
            "–": "-",
            "—": "-",
            "∆": "Δ",
        }
    )
    return text.translate(translations).replace("χ²", "χ2").replace("\u00a0", " ")


def _semantic_pdf_text(text: str) -> str:
    """Collapse a projection for marker checks without changing stored quotes."""

    dehyphenated = re.sub(
        r"(?<=[A-Za-z])-\s*\n\s*(?=[A-Za-z])",
        "",
        text,
    )
    return " ".join(dehyphenated.split())


def build_union3_source_document_hash(
    *,
    pdf_sha256: str = UNION3_PDF_SHA256,
    version: int = UNION3_SOURCE_DOCUMENT_VERSION,
) -> str:
    """Build the immutable source identity used by all extraction anchors."""

    identity = {
        "source_profile_key": UNION3_SOURCE_PROFILE_KEY,
        "canonical_identifier": UNION3_ARXIV_ID,
        "version": version,
        "raw_artifact_hashes": {"pdf": pdf_sha256.lower()},
    }
    return _sha256_json(identity)


def _anchor(
    *,
    source_document_hash: str,
    page_label: str,
    role: str,
    raw_text: str,
    projection_sha256: str,
    char_start: int,
    char_end: int,
    interpretation: str,
) -> dict:
    locator = {
        "source_kind": "arxiv_pdf_text",
        "section_label": "5.3",
        "pdf_page_label": page_label,
        "table_label": "Table 9",
        "role": role,
        "text_projection": UNION3_TEXT_PROJECTION_METHOD,
        "projection_sha256": projection_sha256,
        "char_start": char_start,
        "char_end": char_end,
    }
    anchor_material = source_document_hash + _canonical_json(locator) + raw_text
    return {
        "anchor_id": "sha256:"
        + hashlib.sha256(anchor_material.encode("utf-8")).hexdigest(),
        "source_document_hash": source_document_hash,
        "locator": locator,
        "raw_text": raw_text,
        "interpretation": interpretation,
    }


def _require_method_markers(text: str) -> None:
    semantic_text = _semantic_pdf_text(text)
    markers = (
        "Table 9 summarizes our cosmological constraints.",
        "where χ2 increases by 1. This gives us the quoted plus",
        "and minus confidence intervals.",
        "We compute frequentist contours (Δχ2 compared to",
    )
    if any(marker not in semantic_text for marker in markers):
        raise Union3ReaderError(
            "table9_method_not_found",
            "The registered Table 9 profile-likelihood method text was not found",
        )


def _extract_table9_row(text: str) -> tuple[str, str, str, str, int]:
    semantic_text = _semantic_pdf_text(text)
    header_match = re.search(
        r"Probes\s+χ2\s*\(DoF\)\s+h\s+Ωm\s+Ωk\s+w or w0\s+wa\s+DETF FoM",
        semantic_text,
    )
    if header_match is None:
        raise Union3ReaderError(
            "table9_header_not_found",
            "The registered Table 9 header was not found",
        )
    table_tail = semantic_text[header_match.end() :]
    open_model = re.search(r"\bOpen\s+ΛCDM\b", table_tail)
    flat_table = table_tail[: open_model.start()] if open_model else table_tail[:4000]
    if re.search(r"\bFlat\s+ΛCDM\b", flat_table) is None:
        raise Union3ReaderError(
            "table9_model_not_found",
            "The Flat Lambda-CDM section of Table 9 was not found",
        )
    row_match = re.search(
        r"\bSNe\s+(24\.0)\s+\((20)\)\s+.*?"
        r"(0\.356)\+(0\.028)\s+-(0\.026)\b",
        flat_table,
    )
    if row_match is None:
        raise Union3ReaderError(
            "table9_row_not_found",
            "The registered Flat Lambda-CDM SNe row was not found",
        )
    chi_square, dof, central, plus, minus = row_match.groups()
    return central, plus, minus, chi_square, int(dof)


def _unique_span(text: str, start_marker: str, end_marker: str, error_class: str) -> tuple[int, int]:
    start = text.find(start_marker)
    if start < 0 or text.find(start_marker, start + 1) >= 0:
        raise Union3ReaderError(error_class, "The registered source anchor is missing or ambiguous")
    end_start = text.find(end_marker, start)
    if end_start < 0:
        raise Union3ReaderError(error_class, "The registered source anchor is incomplete")
    return start, end_start + len(end_marker)


def _extract_anchor_spans(text: str) -> list[tuple[str, str, int, int, str]]:
    """Return verbatim normalized pdftotext slices plus human interpretations."""

    method_start, method_end = _unique_span(
        text,
        "Table 9 summarizes our cosmological constraints.",
        "and minus confidence intervals.",
        "table9_method_anchor_not_found",
    )
    frequentist_start, frequentist_end = _unique_span(
        text,
        "We compute frequentist contours (Δχ2",
        "shown in the plane and fitting for the others.",
        "table9_frequentist_anchor_not_found",
    )
    header_match = re.search(
        r"(?m)^[ \t]*Probes\s+χ2.*?DETF FoM[ \t]*$",
        text,
        flags=re.DOTALL,
    )
    if header_match is None:
        raise Union3ReaderError(
            "table9_header_anchor_not_found",
            "The registered Table 9 header anchor was not found",
        )
    header_start = header_match.start() + len(header_match.group(0)) - len(
        header_match.group(0).lstrip()
    )
    header_end = header_match.end()
    row_match = re.search(
        r"(?m)^[ \t]*Flat ΛCDM[ \t]*\n"
        r"[ \t]*SNe[ \t]+24\.0 \(20\).*?0\.356\+0\.028[ \t]*\n"
        r"[ \t]*-0\.026(?:[ \t]+···){4}[ \t]*$",
        text,
    )
    if row_match is None:
        raise Union3ReaderError(
            "table9_row_anchor_not_found",
            "The registered Flat Lambda-CDM SNe row anchor was not found",
        )
    row_start = row_match.start() + len(row_match.group(0)) - len(
        row_match.group(0).lstrip()
    )
    row_end = row_match.end()
    caption_start, caption_end = _unique_span(
        text,
        "Table 9. Constraints on cosmological parameters.",
        "the number of SNe (2087).",
        "table9_caption_anchor_not_found",
    )
    return [
        (
            "57",
            "interval_method",
            method_start,
            method_end,
            "The quoted one-parameter interval endpoints use Δχ2=1 after refitting the other parameters.",
        ),
        (
            "57",
            "frequentist_semantics",
            frequentist_start,
            frequentist_end,
            "The paper explicitly describes these contours as frequentist.",
        ),
        (
            "58",
            "table_header",
            header_start,
            header_end,
            "The Ωm value is read from the registered Table 9 Ωm column.",
        ),
        (
            "58",
            "table_row",
            row_start,
            row_end,
            "The registered row is the SNe-only Flat ΛCDM fit.",
        ),
        (
            "58",
            "table_caption",
            caption_start,
            caption_end,
            "The table uses the 22-node spline-interpolated SN distances.",
        ),
    ]


def extract_union3_table9(
    pdf_text: str,
    *,
    pdf_sha256: str,
    source_profile_key: str = UNION3_SOURCE_PROFILE_KEY,
    identifier: str = UNION3_ARXIV_ID,
    source_document_version: int = UNION3_SOURCE_DOCUMENT_VERSION,
    artifact_ref: str = UNION3_SOURCE_URL,
) -> dict:
    """Extract the one registered Union3 Table 9 frequentist interval."""

    canonical_identifier = normalize_union3_identifier(source_profile_key, identifier)
    normalized_pdf_sha256 = str(pdf_sha256).strip().lower()
    if normalized_pdf_sha256 != UNION3_PDF_SHA256:
        raise Union3ReaderError(
            "pdf_checksum_mismatch",
            "The Union3 PDF checksum does not match the registered v4 artifact",
        )
    if not isinstance(pdf_text, str) or not pdf_text.strip():
        raise Union3ReaderError("pdf_text_empty", "The trusted PDF text projection is empty")

    text = _normalize_pdf_text(pdf_text)
    _require_method_markers(text)
    central, plus, minus, chi_square, degrees_of_freedom = _extract_table9_row(text)
    caption_marker = (
        "Table 9. Constraints on cosmological parameters. The SN χ2 values are based "
        "on spline-interpolated distances"
    )
    if caption_marker not in _semantic_pdf_text(text):
        raise Union3ReaderError(
            "table9_caption_not_found",
            "The registered Table 9 caption was not found",
        )

    source_document_hash = build_union3_source_document_hash(
        pdf_sha256=normalized_pdf_sha256,
        version=source_document_version,
    )
    projection_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    anchors = [
        _anchor(
            source_document_hash=source_document_hash,
            page_label=page_label,
            role=role,
            raw_text=text[char_start:char_end],
            projection_sha256=projection_sha256,
            char_start=char_start,
            char_end=char_end,
            interpretation=interpretation,
        )
        for page_label, role, char_start, char_end, interpretation in _extract_anchor_spans(text)
    ]

    central_decimal = Decimal(central)
    plus_decimal = Decimal(plus)
    minus_decimal = Decimal(minus)
    candidate_core = {
        "candidate_type": "parameter_interval_report",
        "claim_text": (
            "Union3 Table 9 reports Ωm = 0.356 +0.028/-0.026 for the SNe-only "
            "Flat ΛCDM fit, using a one-parameter Δχ2=1 profile interval."
        ),
        "parameter": "omegam",
        "reported_value": {
            "central": central,
            "plus": plus,
            "minus": minus,
            "lower": format(central_decimal - minus_decimal, ".3f"),
            "upper": format(central_decimal + plus_decimal, ".3f"),
            "confidence_level": "0.683",
        },
        "central": central,
        "plus": plus,
        "minus": minus,
        "lower": format(central_decimal - minus_decimal, ".3f"),
        "upper": format(central_decimal + plus_decimal, ".3f"),
        "confidence_level": "0.683",
        "model_scope": "flat_lcdm",
        "data_scope": "union3_sn_only",
        "interval_kind": "frequentist_profile_chi_square",
        "statistical_semantics": "frequentist_profile_chi_square",
        "confidence_definition": "delta_chi_square_1_one_parameter",
        "delta_chi_square": "1",
        "fit_diagnostics": {
            "chi_square": chi_square,
            "degrees_of_freedom": degrees_of_freedom,
        },
        "claim_scope": "paper_reported_frequentist_interval",
        "source_anchor_ids": [anchor["anchor_id"] for anchor in anchors],
        "publication_ready": False,
        "review_required": True,
    }
    claim_hash = hashlib.sha256(
        " ".join(candidate_core["claim_text"].split()).encode("utf-8")
    ).hexdigest()
    candidate_hash = _sha256_json(candidate_core)
    candidate = {
        "candidate_id": f"sha256:{candidate_hash}",
        "candidate_hash": candidate_hash,
        "claim_hash": claim_hash,
        **candidate_core,
    }
    extraction = {
        "schema_version": UNION3_EXTRACTION_SCHEMA_VERSION,
        "reader_version": UNION3_READER_VERSION,
        "source": {
            "source_profile_key": UNION3_SOURCE_PROFILE_KEY,
            "canonical_identifier": canonical_identifier,
            "source_url": UNION3_SOURCE_URL,
            "authority": "arxiv_pdf",
            "pdf_sha256": UNION3_PDF_SHA256,
            "source_document_hash": source_document_hash,
            "version": source_document_version,
            "raw_artifacts": [
                {
                    "role": "authoritative_pdf",
                    "artifact_ref": artifact_ref,
                    "content_type": "application/pdf",
                }
            ],
            "raw_artifact_hashes": {"pdf": UNION3_PDF_SHA256},
            "text_projection": {
                "method": UNION3_TEXT_PROJECTION_METHOD,
                "sha256": projection_sha256,
            },
        },
        "anchors": anchors,
        "candidates": [candidate],
        "coverage_status": "UNION3_TABLE9_INTERVAL_READY",
        "limitations": [
            "This reader maps one paper-reported Table 9 interval only.",
            "Human review cannot override a failed machine or independent-verification gate.",
        ],
        "publication_ready": False,
        "review_required": True,
    }
    extraction["extraction_hash"] = _sha256_json(extraction)
    return extraction


__all__ = [
    "UNION3_ARXIV_ID",
    "UNION3_ATOM_URL",
    "UNION3_EXTRACTION_SCHEMA_VERSION",
    "UNION3_PDF_SHA256",
    "UNION3_READER_VERSION",
    "UNION3_SOURCE_PROFILE_KEY",
    "UNION3_SOURCE_DOCUMENT_VERSION",
    "UNION3_SOURCE_TAR_SHA256",
    "UNION3_SOURCE_TAR_URL",
    "UNION3_SOURCE_URL",
    "UNION3_TEXT_PROJECTION_METHOD",
    "Union3PdfTextSnapshot",
    "Union3ReaderError",
    "build_union3_source_document_hash",
    "extract_union3_table9",
    "load_registered_union3_snapshot",
    "normalize_union3_identifier",
    "set_union3_snapshot_loader",
]
