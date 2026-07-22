"""Shared fail-closed evidence policy for non-formal Foundry output."""

from __future__ import annotations

import json
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

_DIGIT_CONFUSABLE_OPTIONS = {
    "0": frozenset({"o"}),
    "1": frozenset({"i", "l"}),
    "2": frozenset({"z"}),
    "3": frozenset({"e"}),
    "4": frozenset({"a"}),
    "5": frozenset({"s"}),
    "6": frozenset({"g"}),
    "7": frozenset({"t"}),
    "8": frozenset({"b"}),
    "9": frozenset({"g"}),
}


def _confusable_text_key_pattern(value: str) -> str:
    """Build an ASCII regex for one protected key and digit lookalikes."""

    parts: list[str] = []
    for character in value:
        alternatives = {
            digit
            for digit, letters in _DIGIT_CONFUSABLE_OPTIONS.items()
            if character in letters
        }
        parts.append(f"[{re.escape(character + ''.join(sorted(alternatives)))}]")
    return r"[^a-z0-9]*".join(parts)


_SCIENTIFIC_VERDICT_MARKER = _confusable_text_key_pattern("scientificverdict")
_STATUS_MARKER = _confusable_text_key_pattern("status")
_PUBLICATION_READY_MARKER = _confusable_text_key_pattern("publicationready")
_CLAIM_ELIGIBLE_MARKER = _confusable_text_key_pattern("claimeligible")
_EVIDENCE_PACK_ALLOWED_MARKER = _confusable_text_key_pattern(
    "evidencepackallowed"
)
_EVIDENCE_CLASS_MARKER = _confusable_text_key_pattern("evidenceclass")
_EVIDENCE_PACK_MARKER = _confusable_text_key_pattern("evidencepack")
_EVIDENCE_PACK_ID_MARKER = _confusable_text_key_pattern("evidencepackid")
_FORMAL_EVIDENCE_PACK_MARKER = _confusable_text_key_pattern(
    "formalevidencepack"
)
_ID_MARKER = _confusable_text_key_pattern("id")
_IDENTIFIER_MARKER = _confusable_text_key_pattern("identifier")
_EVIDENCE_PACK_IDENTIFIER_MARKER = (
    rf"(?:{_EVIDENCE_PACK_ID_MARKER}|"
    rf"{_EVIDENCE_PACK_MARKER}[^a-z0-9]*s[^a-z0-9]+{_ID_MARKER}|"
    rf"{_EVIDENCE_PACK_MARKER}[^a-z0-9]+{_IDENTIFIER_MARKER})"
)
_ASCII_FIELD_START = r"(?<![a-z0-9_�])"
_ASCII_FIELD_END = r"(?![a-z0-9_�])"
_ASCII_VALUE_START = r"(?<![a-z0-9])"
_ASCII_VALUE_END = r"(?![a-z0-9])"
_TEXT_ASSIGNMENT_RELATION = r"[\"']?\s*[:=]\s*"
_TEXT_LABEL_RELATION = (
    r"[\"']?(?:\s*(?:[-=]+>|>+)\s*|[ \t\f\v]+(?:-+|\|)"
    r"[ \t\f\v]+)"
)
_TEXT_DIRECT_RELATION = (
    rf"(?:{_TEXT_LABEL_RELATION}|{_TEXT_ASSIGNMENT_RELATION})"
)
_TEXT_IS_RELATION = (
    r"[^a-z0-9]*is"
    + _ASCII_VALUE_END
    + r"[\s_:=\(\[\{\"',;/\-]*"
)
_TEXT_VALUE_RELATION = (
    rf"(?:{_TEXT_LABEL_RELATION}|{_TEXT_ASSIGNMENT_RELATION}|"
    rf"{_TEXT_IS_RELATION})"
)

_EVIDENCE_PACK_ID_ASSIGNMENT = re.compile(
    rf"{_ASCII_FIELD_START}{_EVIDENCE_PACK_IDENTIFIER_MARKER}"
    rf"{_ASCII_FIELD_END}{_TEXT_DIRECT_RELATION}"
    r"(?P<value>[^\r\n]*)",
    re.IGNORECASE,
)
_EVIDENCE_PACK_ASSIGNMENT = re.compile(
    rf"{_ASCII_FIELD_START}(?:{_EVIDENCE_PACK_MARKER}|"
    rf"{_FORMAL_EVIDENCE_PACK_MARKER}){_ASCII_FIELD_END}"
    rf"{_TEXT_DIRECT_RELATION}"
    r"(?P<value>[^\r\n]*)",
    re.IGNORECASE,
)
_EVIDENCE_PACK_ID_PROSE = re.compile(
    rf"{_ASCII_FIELD_START}{_EVIDENCE_PACK_IDENTIFIER_MARKER}"
    rf"{_ASCII_FIELD_END}{_TEXT_IS_RELATION}"
    r"(?P<value>[^\r\n]*)",
    re.IGNORECASE,
)
_EVIDENCE_PACK_PROSE = re.compile(
    rf"{_ASCII_FIELD_START}(?:{_EVIDENCE_PACK_MARKER}|"
    rf"{_FORMAL_EVIDENCE_PACK_MARKER}){_ASCII_FIELD_END}"
    rf"{_TEXT_IS_RELATION}(?P<value>[^\r\n]*)",
    re.IGNORECASE,
)
_EVIDENCE_CLASS_RELATION = re.compile(
    rf"{_ASCII_FIELD_START}{_EVIDENCE_CLASS_MARKER}{_ASCII_FIELD_END}"
    rf"{_TEXT_VALUE_RELATION}",
    re.IGNORECASE,
)
_EMPTY_EVIDENCE_PACK_ASSIGNMENT_LINE = re.compile(
    r"(?:\s*|(?:(?:\"\"|'')|(?:[\(\[\{]\s*)?[\"']?"
    r"(?:(?:intentionally\s+)?(?:unavailable|not\s+available|"
    r"not\s+assigned|absent|empty)|false|none|null)[\"']?)"
    r"\s*(?:[\)\]\}])?\s*[.!?|]*\s*)",
    re.IGNORECASE,
)
_EMPTY_EVIDENCE_PACK_PROSE_LINE = re.compile(
    r"(?:[\(\[\{]\s*)?[\"']?(?:intentionally\s+)?(?:unavailable|"
    r"not\s+available|not\s+assigned|absent|empty|none|null|false)"
    r"[\"']?\s*(?:[\)\]\}])?\s*[.!?]*\s*",
    re.IGNORECASE,
)

_FORMAL_TEXT_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"{_ASCII_FIELD_START}{_SCIENTIFIC_VERDICT_MARKER}"
        rf"{_ASCII_FIELD_END}{_TEXT_VALUE_RELATION}"
        rf"[\"']?supported{_ASCII_VALUE_END}",
        rf"{_ASCII_FIELD_START}{_STATUS_MARKER}{_ASCII_FIELD_END}"
        rf"{_TEXT_VALUE_RELATION}[\"']?supported{_ASCII_VALUE_END}",
        (
            rf"{_ASCII_FIELD_START}(?:{_PUBLICATION_READY_MARKER}|"
            rf"{_CLAIM_ELIGIBLE_MARKER}|{_EVIDENCE_PACK_ALLOWED_MARKER})"
            rf"{_ASCII_FIELD_END}{_TEXT_VALUE_RELATION}"
            rf"[\"']?(?:true|yes|1){_ASCII_VALUE_END}"
        ),
    )
)
_SUPPORTED_TOKEN = re.compile(
    rf"{_ASCII_VALUE_START}supported{_ASCII_VALUE_END}",
    re.IGNORECASE,
)
_FORMAL_RESERVED_TOKEN = "supported"
_PROTECTED_POLICY_KEYS = frozenset(
    {
        "claimeligible",
        "evidenceclass",
        "evidencepack",
        "evidencepackallowed",
        "evidencepackid",
        "formalevidencepack",
        "publicationready",
        "scientificverdict",
        "status",
    }
)
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
        "ᴀ": "a",
        "ʙ": "b",
        "ᴄ": "c",
        "ᴅ": "d",
        "ᴇ": "e",
        "ꜰ": "f",
        "ɢ": "g",
        "ʜ": "h",
        "ɪ": "i",
        "ᴊ": "j",
        "ᴋ": "k",
        "ʟ": "l",
        "ᴍ": "m",
        "ɴ": "n",
        "ᴏ": "o",
        "ᴘ": "p",
        "ʀ": "r",
        "ꜱ": "s",
        "ᴛ": "t",
        "ᴜ": "u",
        "ᴠ": "v",
        "ᴡ": "w",
        "ʏ": "y",
        "ᴢ": "z",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "。": ".",
        "｡": ".",
        "…": ".",
    }
)


def _visually_ignorable(character: str) -> bool:
    codepoint = ord(character)
    return (
        unicodedata.category(character) in {"Cf", "Mn", "Me"}
        or codepoint in _INVISIBLE_CODEPOINTS
        or any(start <= codepoint <= end for start, end in _INVISIBLE_RANGES)
    )


def _decimal_digit(character: str) -> str | None:
    """Return one ASCII digit for Unicode decimal and numeric forms."""

    try:
        return str(unicodedata.decimal(character))
    except (TypeError, ValueError):
        pass
    try:
        numeric = float(unicodedata.numeric(character))
    except (TypeError, ValueError):
        return None
    if numeric.is_integer() and 0 <= numeric <= 9:
        return str(int(numeric))
    return None


def _normalized_text(
    value: Any,
    *,
    preserve_invisible_boundaries: bool = False,
) -> str:
    """Normalize compatibility forms while preserving visible value content."""

    normalized = unicodedata.normalize("NFKC", str(value)).translate(
        _ASCII_CONFUSABLES
    )
    shadow: list[str] = []
    for character in normalized:
        if _visually_ignorable(character):
            if preserve_invisible_boundaries:
                shadow.append(" ")
            continue
        decimal_digit = _decimal_digit(character)
        if decimal_digit is not None:
            shadow.append(decimal_digit)
            continue
        if character.isascii():
            shadow.append(character)
        elif unicodedata.category(character).startswith("Z"):
            shadow.append(" ")
        else:
            category = unicodedata.category(character)
            unicode_name = unicodedata.name(character, "")
            if (
                category == "Pd"
                or "MINUS SIGN" in unicode_name
            ):
                shadow.append("-")
            elif "BULLET" in unicode_name or "MIDDLE DOT" in unicode_name:
                shadow.append("|")
            elif "RIGHT" in unicode_name and "ARROW" in unicode_name:
                shadow.append("->")
            elif (
                "VERTICAL" in unicode_name
                and (
                    "LINE" in unicode_name
                    or "BAR" in unicode_name
                    or "BOX DRAWINGS" in unicode_name
                )
            ):
                shadow.append("|")
            elif category.startswith("P"):
                shadow.append(".")
            else:
                # Formal state markers are ASCII. Visible Unicode letters,
                # numbers, symbols, and spacing marks remain observably
                # non-empty (for example an Evidence Pack ID) and remain part
                # of a larger field token instead of disappearing into a
                # protected ASCII key.
                shadow.append("�")
    return "".join(shadow)


def _contains_confusable_supported_token(value: Any) -> bool:
    """Reject visual substitutions in the reserved SUPPORTED token.

    NFKC handles compatibility glyphs and ``_ASCII_CONFUSABLES`` handles the
    common Greek/Cyrillic lookalikes.  Some phonetic letters, such as the small
    capital U in ``SᴜPPORTED``, intentionally survive NFKC.  At this trust
    boundary, a same-length word that otherwise spells the reserved formal
    verdict is rejected when a remaining non-ASCII letter or a common decimal
    digit lookalike (for example ``SUPP0RTED`` or ``SUPP٠RTED``) occupies one
    of its character positions.  This is deliberately fail-closed without
    treating ordinary non-ASCII scientific text as a verdict.
    """

    normalized = unicodedata.normalize("NFKC", str(value)).translate(
        _ASCII_CONFUSABLES
    )
    token: list[str] = []

    def matches_reserved(candidate: list[str]) -> bool:
        if len(candidate) != len(_FORMAL_RESERVED_TOKEN):
            return False
        saw_confusable = False
        for character, expected in zip(
            candidate,
            _FORMAL_RESERVED_TOKEN,
            strict=True,
        ):
            decimal_digit = _decimal_digit(character)
            if decimal_digit is not None:
                if expected not in _DIGIT_CONFUSABLE_OPTIONS.get(
                    decimal_digit,
                    frozenset(),
                ):
                    return False
                saw_confusable = True
                continue
            if character.isascii():
                if character.casefold() != expected:
                    return False
                continue
            # Known Greek/Cyrillic/phonetic lookalikes were translated above.
            # Treating every remaining Unicode letter as an arbitrary ASCII
            # letter would turn ordinary Chinese text into SUPPORTED.
            return False
        return saw_confusable

    for character in normalized:
        if _visually_ignorable(character):
            continue
        if character.isalnum():
            token.append(character)
            continue
        if matches_reserved(token):
            return True
        token = []
    return matches_reserved(token)


def _canonical_key(value: Any) -> str:
    """Map JSON-key spelling variants to one policy identity."""

    normalized_key = _normalized_text(value).casefold()
    canonical = re.sub(
        r"[^a-z0-9]+",
        "",
        normalized_key.replace("�", "x"),
    )
    if canonical in _PROTECTED_POLICY_KEYS:
        return canonical
    normalized = unicodedata.normalize("NFKC", str(value)).translate(
        _ASCII_CONFUSABLES
    )
    characters = [
        character
        for character in normalized
        if not _visually_ignorable(character) and character.isalnum()
    ]
    for expected_key in _PROTECTED_POLICY_KEYS:
        if len(characters) != len(expected_key):
            continue
        matched = True
        for character, expected in zip(characters, expected_key, strict=True):
            decimal_digit = _decimal_digit(character)
            if decimal_digit is not None:
                if expected not in _DIGIT_CONFUSABLE_OPTIONS.get(
                    decimal_digit,
                    frozenset(),
                ):
                    matched = False
                    break
                continue
            if character.isascii():
                if character.casefold() != expected:
                    matched = False
                    break
                continue
            # Only explicitly mapped lookalikes may identify protected keys.
            matched = False
            break
        if matched:
            return expected_key
    return canonical


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
            or _contains_confusable_supported_token(item)
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


def candidate_bundle_contains_formal_claim_escape(value: Any) -> bool:
    """Scan every candidate text leaf except explicitly negative claim labels.

    ``workflow_spec.forbidden_claims`` is a declarative deny-list and may name
    reserved formal states verbatim.  It is safe only in that exact, labelled
    location.  All other bundle text—including limitations, generation
    metadata, source pins, allowed claims, and workflow notes—is untrusted and
    must fail closed before the candidate version is persisted or executed.
    """

    if not isinstance(value, dict):
        return contains_formal_claim_escape(value, scan_text_leaves=True)
    policy_view = dict(value)
    workflow_spec = value.get("workflow_spec")
    if isinstance(workflow_spec, dict):
        workflow_policy_view = dict(workflow_spec)
        forbidden_claims = workflow_spec.get("forbidden_claims")
        if isinstance(forbidden_claims, list) and all(
            isinstance(item, str) for item in forbidden_claims
        ):
            workflow_policy_view["forbidden_claims"] = []
        policy_view["workflow_spec"] = workflow_policy_view
    return contains_formal_claim_escape(policy_view, scan_text_leaves=True)


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
    if _contains_confusable_supported_token(raw_text):
        return True
    if any(
        (ord(character) < 0x20 and character not in {"\n", "\r", "\t"})
        or 0x7F <= ord(character) <= 0x9F
        for character in raw_text
    ):
        return True
    duplicate_json_key = False

    def strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate_json_key
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                duplicate_json_key = True
            result[key] = item
        return result

    def reject_nonstandard_json_constant(value: str) -> Any:
        raise ValueError(f"nonstandard JSON constant: {value}")

    try:
        parsed_json = json.loads(
            raw_text,
            object_pairs_hook=strict_json_object,
            parse_constant=reject_nonstandard_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError):
        pass
    else:
        if duplicate_json_key:
            return True
        return contains_formal_claim_escape(
            parsed_json,
            scan_text_leaves=True,
        )
    text = _normalized_text(raw_text)
    boundary_text = _normalized_text(
        raw_text,
        preserve_invisible_boundaries=True,
    )
    for candidate in (text, boundary_text):
        for match in _EVIDENCE_CLASS_RELATION.finditer(candidate):
            tail = candidate[match.end() :].lstrip()
            evidence_class: str | None = None
            remainder = ""
            if tail:
                if tail[0] in {"\"", "'"}:
                    closing_quote = tail.find(tail[0], 1)
                    if closing_quote >= 0:
                        evidence_class = tail[1:closing_quote]
                        remainder = tail[closing_quote + 1 :]
                else:
                    scalar = re.match(r"[^\s,;.!?\)\]\}]+", tail)
                    if scalar is not None:
                        evidence_class = scalar.group(0)
                        remainder = tail[scalar.end() :]
            line_remainder = re.split(r"[\r\n]", remainder, maxsplit=1)[0]
            valid_terminator = (
                re.fullmatch(r"[ \t\f\v,;.!?|\)\]\}]*", line_remainder)
                is not None
            )
            if (
                evidence_class != NON_FORMAL_EVIDENCE_CLASS
                or not valid_terminator
            ):
                return True
    if any(
        pattern.search(candidate) is not None
        for pattern in _FORMAL_TEXT_PATTERNS
        for candidate in (text, boundary_text)
    ):
        return True
    if _SUPPORTED_TOKEN.search(text) is not None:
        return True
    for match in _EVIDENCE_PACK_ID_ASSIGNMENT.finditer(text):
        identifier = match.group("value")
        if _EMPTY_EVIDENCE_PACK_ASSIGNMENT_LINE.fullmatch(identifier) is None:
            return True
    for match in _EVIDENCE_PACK_ASSIGNMENT.finditer(text):
        pack = match.group("value")
        if _EMPTY_EVIDENCE_PACK_ASSIGNMENT_LINE.fullmatch(pack) is None:
            return True
    for match in _EVIDENCE_PACK_ID_PROSE.finditer(boundary_text):
        identifier = match.group("value")
        if _EMPTY_EVIDENCE_PACK_PROSE_LINE.fullmatch(identifier) is None:
            return True
    for match in _EVIDENCE_PACK_PROSE.finditer(boundary_text):
        pack = match.group("value")
        if _EMPTY_EVIDENCE_PACK_PROSE_LINE.fullmatch(pack) is None:
            return True
    return False


__all__ = [
    "DEMO_REPORT_FIELDS_V1",
    "NON_FORMAL_EVIDENCE_CLASS",
    "candidate_bundle_contains_formal_claim_escape",
    "contains_formal_claim_escape",
    "contains_formal_claim_escape_text",
    "demo_report_contract_issue",
]
