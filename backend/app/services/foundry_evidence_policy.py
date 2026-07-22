"""Shared fail-closed evidence policy for non-formal Foundry output."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Any


NON_FORMAL_EVIDENCE_CLASS = "NON_FORMAL_DEMO"

# Keep the executable validators bound to the same closed top-level shape as
# ``foundry_candidates/demo-report-schema-v1.json``.  JSON Schema is useful to
# callers, but the trust boundary must not depend on every caller remembering
# to invoke a separate schema library before persisting or replaying a receipt.
DEMO_REPORT_FIELDS_V1 = frozenset(
    {
        "schema_version",
        "candidate_id",
        "candidate_version",
        "demo_run_id",
        "status",
        "evidence_class",
        "publication_ready",
        "claim_eligible",
        "evidence_pack_allowed",
        "candidate_bundle_sha256",
        "candidate_version_sha256",
        "workflow_spec_sha256",
        "dependency_lock_sha256",
        "runner_definition_sha256",
        "runner_image_digest",
        "environment",
        "environment_sha256",
        "generation",
        "source_pins",
        "fixture_hashes",
        "started_at",
        "completed_at",
        "duration_ms",
        "stdout_sha256",
        "stderr_sha256",
        "stdout_bytes",
        "stderr_bytes",
        "artifact_manifest",
        "resource_usage",
        "failure_class",
        "validation_summary",
        "limitations",
        "result",
        "demo_report_sha256",
    }
)

_EVIDENCE_PACK_ID_ASSIGNMENT = re.compile(
    r"\bevidence[^a-z0-9]*pack[^a-z0-9]*id\b[\"']?\s*[:=]\s*"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_EVIDENCE_PACK_ASSIGNMENT = re.compile(
    r"\bevidence[^a-z0-9]*pack\b[\"']?\s*[:=]\s*"
    r"(?P<value>\{|\[|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)",
    re.IGNORECASE,
)
_EMPTY_EVIDENCE_PACK_ID_VALUES = {"", "false", "none", "null"}

_FORMAL_TEXT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bscientific[^a-z0-9]*verdict\b[\"']?\s*[:=]\s*[\"']?supported\b",
        r"\bstatus\b[\"']?\s*[:=]\s*[\"']?supported\b",
        (
            r"\b(?:publication[^a-z0-9]*ready|claim[^a-z0-9]*eligible|"
            r"evidence[^a-z0-9]*pack[^a-z0-9]*allowed)\b[\"']?\s*[:=]\s*"
            r"[\"']?(?:true|yes|1)\b"
        ),
        (
            r"\bevidence[^a-z0-9]*class\b[\"']?\s*[:=]\s*[\"']?"
            r"(?:formal|registered|publication[^a-z0-9]*ready)\b"
        ),
    )
)
_SUPPORTED_TOKEN = re.compile(r"\bsupported\b", re.IGNORECASE)
_HEX64 = re.compile(r"[0-9a-f]{64}")
_CANDIDATE_ID = re.compile(r"[a-z][a-z0-9_]{2,96}")
_INVISIBLE_CODEPOINTS = frozenset(
    {
        0x034F,
        0x115F,
        0x1160,
        0x17B4,
        0x17B5,
        0x2800,
        0x3164,
        0xFFA0,
    }
)
_INVISIBLE_RANGES = (
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCAF),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
_ASCII_CONFUSABLES = str.maketrans(
    {
        "Ѕ": "S",
        "ѕ": "s",
        "У": "Y",
        "у": "y",
        "Р": "P",
        "р": "p",
        "О": "O",
        "о": "o",
        "Т": "T",
        "т": "t",
        "Е": "E",
        "е": "e",
        "А": "A",
        "а": "a",
        "С": "C",
        "с": "c",
        "І": "I",
        "і": "i",
        "Ρ": "P",
        "ρ": "p",
        "Ο": "O",
        "ο": "o",
        "Τ": "T",
        "τ": "t",
        "Ε": "E",
        "ε": "e",
        "Α": "A",
        "α": "a",
        "Ϲ": "C",
        "ϲ": "c",
    }
)


def _visually_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) in {"Cf", "Mn", "Me"}
        or codepoint in _INVISIBLE_CODEPOINTS
        or any(start <= codepoint <= end for start, end in _INVISIBLE_RANGES)
    )


def _normalized_text(value: Any) -> str:
    """Normalize compatibility forms and remove invisible format controls."""

    normalized = unicodedata.normalize("NFKC", str(value)).translate(
        _ASCII_CONFUSABLES
    )
    shadow: list[str] = []
    for character in normalized:
        if _visually_ignorable(character):
            continue
        if character.isascii():
            shadow.append(character)
        else:
            # Formal state markers are ASCII.  Preserve a boundary for visible
            # non-ASCII text while removing only explicitly invisible fillers.
            shadow.append(" ")
    return "".join(shadow)


def _canonical_key(value: Any) -> str:
    """Map JSON-key spelling variants to one policy identity."""

    return re.sub(r"[^a-z0-9]+", "", _normalized_text(value).casefold())


def demo_report_contract_issue(report: Any) -> str | None:
    """Return the first closed-schema/type violation in a DemoReport v1."""

    if not isinstance(report, dict):
        return "report_object"
    if set(report) != DEMO_REPORT_FIELDS_V1:
        return "report_shape"
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        return "schema_version"
    candidate_id = report.get("candidate_id")
    if not isinstance(candidate_id, str) or _CANDIDATE_ID.fullmatch(candidate_id) is None:
        return "candidate_id"
    candidate_version = report.get("candidate_version")
    if type(candidate_version) is not int or candidate_version < 1:
        return "candidate_version"
    demo_run_id = report.get("demo_run_id")
    try:
        parsed_demo_id = uuid.UUID(str(demo_run_id))
    except (AttributeError, ValueError):
        return "demo_run_id"
    if not isinstance(demo_run_id, str) or str(parsed_demo_id) != demo_run_id:
        return "demo_run_id"
    status = report.get("status")
    if status not in {"PASSED", "PARTIAL", "FAILED"}:
        return "status"
    failure_class = report.get("failure_class")
    if failure_class is not None and (
        not isinstance(failure_class, str) or not failure_class.strip()
    ):
        return "failure_class"
    if (status == "PASSED") != (failure_class is None):
        return "status_failure_class"
    if report.get("evidence_class") != NON_FORMAL_EVIDENCE_CLASS:
        return "evidence_class"
    for field in ("publication_ready", "claim_eligible", "evidence_pack_allowed"):
        if report.get(field) is not False:
            return field
    for field in (
        "candidate_bundle_sha256",
        "candidate_version_sha256",
        "workflow_spec_sha256",
        "dependency_lock_sha256",
        "runner_definition_sha256",
        "environment_sha256",
        "stdout_sha256",
        "stderr_sha256",
        "demo_report_sha256",
    ):
        value = report.get(field)
        if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
            return field
    runner_image_digest = report.get("runner_image_digest")
    if not isinstance(runner_image_digest, str) or not runner_image_digest:
        return "runner_image_digest"
    for field in ("environment", "generation", "resource_usage", "validation_summary", "result"):
        if not isinstance(report.get(field), dict):
            return field
    for field in ("source_pins", "fixture_hashes"):
        value = report.get(field)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            return field
    limitations = report.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or any(not isinstance(item, str) for item in limitations)
    ):
        return "limitations"
    for field in ("started_at", "completed_at"):
        value = report.get(field)
        if not isinstance(value, str) or not value:
            return field
        try:
            parsed_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return field
        if parsed_time.tzinfo is None:
            return field
        canonical_time = (
            parsed_time.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        if value != canonical_time:
            return field
    for field in ("duration_ms", "stdout_bytes", "stderr_bytes"):
        value = report.get(field)
        if type(value) is not int or value < 0:
            return field
    artifacts = report.get("artifact_manifest")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        return "artifact_manifest"
    for artifact, (expected_path, expected_kind) in zip(
        artifacts,
        (("stdout.log", "STDOUT"), ("stderr.log", "STDERR")),
        strict=True,
    ):
        if not isinstance(artifact, dict) or set(artifact) != {
            "path",
            "kind",
            "sha256",
            "bytes",
        }:
            return "artifact_manifest"
        if (
            not isinstance(artifact.get("path"), str)
            or not isinstance(artifact.get("kind"), str)
            or artifact["path"] != expected_path
            or artifact["kind"] != expected_kind
            or not isinstance(artifact.get("sha256"), str)
            or _HEX64.fullmatch(artifact["sha256"]) is None
            or type(artifact.get("bytes")) is not int
            or artifact["bytes"] < 0
        ):
            return "artifact_manifest"
    return None


def contains_formal_claim_escape(
    value: Any,
    *,
    scan_text_leaves: bool = False,
) -> bool:
    """Return whether nested candidate output impersonates formal evidence."""

    if isinstance(value, (bytes, str)):
        return scan_text_leaves and contains_formal_claim_escape_text(value)
    if isinstance(value, (list, tuple)):
        return any(
            contains_formal_claim_escape(
                item,
                scan_text_leaves=scan_text_leaves,
            )
            for item in value
        )
    if not isinstance(value, dict):
        return False
    for raw_key, item in value.items():
        if scan_text_leaves and contains_formal_claim_escape_text(str(raw_key)):
            return True
        key = _canonical_key(raw_key)
        if key in {
            "publicationready",
            "claimeligible",
            "evidencepackallowed",
        } and item is not False:
            return True
        if key in {"scientificverdict", "status"} and (
            _normalized_text(item).strip().casefold() == "supported"
        ):
            return True
        if key == "evidenceclass" and item != NON_FORMAL_EVIDENCE_CLASS:
            return True
        if key in {
            "evidencepack",
            "formalevidencepack",
        } and item:
            return True
        if key == "evidencepackid" and item:
            return True
        if contains_formal_claim_escape(
            item,
            scan_text_leaves=scan_text_leaves,
        ):
            return True
    return False


def contains_formal_claim_escape_text(value: bytes | str) -> bool:
    """Detect formal-evidence fields printed through untrusted text streams."""

    if isinstance(value, bytes) and any(
        (byte < 0x20 and byte not in {0x09, 0x0A, 0x0D})
        or 0x7F <= byte <= 0x9F
        for byte in value
    ):
        return True
    raw_text = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value)
    )
    if any(
        (ord(character) < 0x20 and character not in {"\n", "\r", "\t"})
        or 0x7F <= ord(character) <= 0x9F
        for character in raw_text
    ):
        return True
    text = _normalized_text(raw_text)
    if any(pattern.search(text) is not None for pattern in _FORMAL_TEXT_PATTERNS):
        return True
    if _SUPPORTED_TOKEN.search(text) is not None:
        return True
    for match in _EVIDENCE_PACK_ID_ASSIGNMENT.finditer(text):
        identifier = match.group("value").strip().strip("\"'").strip().lower()
        if identifier not in _EMPTY_EVIDENCE_PACK_ID_VALUES:
            return True
    for match in _EVIDENCE_PACK_ASSIGNMENT.finditer(text):
        pack = match.group("value").strip().strip("\"'").strip().lower()
        if pack not in _EMPTY_EVIDENCE_PACK_ID_VALUES:
            return True
    return False


__all__ = [
    "DEMO_REPORT_FIELDS_V1",
    "NON_FORMAL_EVIDENCE_CLASS",
    "contains_formal_claim_escape",
    "contains_formal_claim_escape_text",
    "demo_report_contract_issue",
]
