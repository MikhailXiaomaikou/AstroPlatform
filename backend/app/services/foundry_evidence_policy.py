"""Shared fail-closed evidence policy for non-formal Foundry output."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from typing import Any

from app.services.uts39_ascii_confusables import UTS39_ASCII_TRANSLATION


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
        if character == "i":
            alternatives.update({"l", "|"})
        elif character == "l":
            # UTS #39 maps ASCII ``|`` to ``l``. The generated table omits
            # ASCII sources so delimiters keep their normal meaning, but a
            # pipe in the exact position of a protected-key ``l`` must still
            # be recognized.
            alternatives.update({"i", "|"})
        character_pattern = (
            f"[{re.escape(character + ''.join(sorted(alternatives)))}]"
        )
        if character == "m":
            character_pattern = rf"(?:{character_pattern}|rn)"
        elif character == "w":
            character_pattern = rf"(?:{character_pattern}|vv)"
        parts.append(character_pattern)
    return r"[^a-z0-9�]*".join(parts)


_ASCII_FIELD_START = r"(?<![a-z0-9_�])"
_ASCII_FIELD_END = r"(?![a-z0-9_�])"
_ASCII_VALUE_START = r"(?<![a-z0-9])"
_ASCII_VALUE_END = r"(?![a-z0-9])"
_SUPPORTED_TOKEN = re.compile(
    rf"{_ASCII_VALUE_START}{_confusable_text_key_pattern('supported')}"
    rf"{_ASCII_VALUE_END}",
    re.IGNORECASE,
)
_FORMAL_RESERVED_TOKEN = "supported"
_BOOLEAN_POLICY_KEYS = frozenset(
    {
        "claimeligible",
        "evidencepackallowed",
        "publicationready",
    }
)
_VERDICT_POLICY_KEYS = frozenset({"scientificverdict", "status"})
_PACK_POLICY_KEYS = frozenset(
    {
        "evidencepack",
        "evidencepackid",
        "evidencepackidentifier",
        "evidencepackref",
        "evidencepackreference",
        "evidencepackuuid",
        "evidencepacksid",
        "evidencepacksidentifier",
        "evidencepacksref",
        "evidencepacksreference",
        "evidencepacksuuid",
        "formalevidencepack",
        "formalevidencepackid",
        "formalevidencepackidentifier",
        "formalevidencepackref",
        "formalevidencepackreference",
        "formalevidencepackuuid",
        "formalevidencepacksid",
        "formalevidencepacksidentifier",
        "formalevidencepacksref",
        "formalevidencepacksreference",
        "formalevidencepacksuuid",
    }
)
_PROTECTED_POLICY_KEYS = frozenset(
    {*_BOOLEAN_POLICY_KEYS, *_VERDICT_POLICY_KEYS, *_PACK_POLICY_KEYS, "evidenceclass"}
)
_PROTECTED_TEXT_MARKER = re.compile(
    rf"{_ASCII_FIELD_START}(?:"
    + "|".join(
        rf"(?P<{key}>{_confusable_text_key_pattern(key)})"
        for key in sorted(_PROTECTED_POLICY_KEYS, key=len, reverse=True)
    )
    + rf"){_ASCII_FIELD_END}",
    re.IGNORECASE,
)
_SAFE_RELATION_WORDS = frozenset(
    {"assigned", "equal", "equals", "is", "set", "to", "was"}
)
_SAFE_PACK_PLACEHOLDERS = frozenset(
    {
        ("absent",),
        ("empty",),
        ("false",),
        ("intentionally", "unavailable"),
        ("none",),
        ("not", "assigned"),
        ("not", "available"),
        ("null",),
        ("unavailable",),
    }
)
_SAFE_EMPTY_PACK_ASSIGNMENT = re.compile(
    r"\s*[:=]\s*(?:\"\"|'')?\s*\|?\s*"
)
_ASSIGNMENT_WORD_RELATION = (
    r"(?:is|was)(?:[-_\s]+(?:equal(?:s)?(?:[-_\s]+to)?|set[-_\s]+to))?|"
    r"equal(?:s)?(?:[-_\s]+to)?|assigned(?:[-_\s]+to)?|"
    r"set(?:[-_\s]+to)?|becomes|"
    r"is(?:equal(?:s)?(?:to)?|setto)|"
    r"was(?:equal(?:s)?(?:to)?|setto)|"
    r"equal(?:s)?to|assignedto|setto"
)
_ASSIGNMENT_RELATION_COMPACTS = frozenset(
    {
        "assigned",
        "assignedto",
        "becomes",
        "equal",
        "equals",
        "equalsto",
        "equalto",
        "is",
        "isequal",
        "isequals",
        "isequalsto",
        "isequalto",
        "issetto",
        "set",
        "setto",
        "was",
        "wasequal",
        "wasequals",
        "wasequalsto",
        "wasequalto",
        "wassetto",
    }
)
_FORMAL_EVIDENCE_CLASS_COMPACTS = frozenset(
    {
        "aready",
        "formal",
        "formalevidence",
        "formalregistry",
        "isformalevidence",
        "modeladequacy",
        "notnonformaldemo",
        "publicationreadycandidate",
        "ready",
        "registered",
        "registeredevidence",
        "registeredresult",
        "supported",
    }
)
_OPEN_ASSIGNMENT_SUFFIX = re.compile(
    rf"\s*(?:[:=|]+|[-=]+>|{_ASSIGNMENT_WORD_RELATION})\s*",
    re.IGNORECASE,
)
_LEADING_ASSIGNMENT_RELATION = re.compile(
    rf"\s*(?:[:=|]+|[-=]+>|"
    rf"(?:{_ASSIGNMENT_WORD_RELATION})(?=$|[^a-z0-9_]))\s*",
    re.IGNORECASE,
)
_REVERSE_ASSIGNMENT = re.compile(
    r"\s*(?P<value>.+?)\s*"
    r"(?:(?P<symbol>[!<>=:~¬|-]+)|(?P<words>"
    rf"{_ASSIGNMENT_WORD_RELATION}))\s*",
    re.IGNORECASE,
)
_SAFE_FIELD_DOCUMENTATION = re.compile(
    r"\s*(?:field\s+is\s+reserved(?:\s+for\s+formal\s+runs)?|"
    r"issue\s+is\s+tracked|[-.]like\s+(?:documentation|fields))"
    r"[.!?]*\s*",
    re.IGNORECASE,
)
_SAFE_NEGATIVE_PROSE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:this|the)\s+(?:candidate|run|result)\s+is\s+not\s+"
        r"publication\s+ready[.!?]*",
        r"this\s+result\s+is\s+not\s+claim\s+eligible[.!?]*",
        r"the\s+evidence\s+class\s+is\s+not\s+formal[.!?]*",
        r"(?:this|the)\s+candidate\s+(?:does\s+not|cannot)\s+"
        r"(?:create|generate)\s+an?\s+"
        r"evidence\s+pack[.!?]*",
        r"no\s+(?:formal\s+)?evidence\s+pack\s+(?:is|was)\s+"
        r"(?:created|generated)(?:\s+for\s+this\s+non-formal\s+demo)?[.!?]*",
        r"evidence\s+pack\s+(?:creation|generation)\s+is\s+disabled[.!?]*",
    )
)
_DETACHED_LOG_LINE = re.compile(
    r"\s*(?:trace|debug|info|notice|warn(?:ing)?|error|critical|"
    r"metadata|demo\s+result|output\s+policy)\s*:",
    re.IGNORECASE,
)
_LOG_PREFIX_LABELS = (
    "trace",
    "debug",
    "info",
    "notice",
    "warn",
    "warning",
    "error",
    "critical",
    "metadata",
    "demo result",
    "output policy",
)
_WRAPPED_LOG_PREFIX_GAP = r"[ \t\r\n]*"


def _wrapped_log_prefix_pattern(label: str) -> str:
    """Build a renderer-wrap pattern for one known log label."""

    compact = "".join(label.split())
    return _WRAPPED_LOG_PREFIX_GAP.join(re.escape(character) for character in compact)


_WRAPPED_LOG_PREFIX_UNIT = (
    r"(?:"
    + "|".join(_wrapped_log_prefix_pattern(label) for label in _LOG_PREFIX_LABELS)
    + r")"
    + _WRAPPED_LOG_PREFIX_GAP
    + r":[ \t]*"
)
_WRAPPED_LOG_PREFIX = re.compile(
    r"(?<![^\r\n])[ \t]*"
    + _WRAPPED_LOG_PREFIX_UNIT
    + r"(?:"
    + _WRAPPED_LOG_PREFIX_GAP
    + _WRAPPED_LOG_PREFIX_UNIT
    + r")*",
    re.IGNORECASE | re.MULTILINE,
)
_NONSPACE = re.compile(r"\S")
_DIGIT_LETTER_CONFUSABLES = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "2": "z",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "8": "b",
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
        "İ": "I",
        "ı": "i",
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


def _uts39_compatibility_skeleton(value: str, *, prefer_decimal: bool) -> str:
    """Return the marked UTS/compatibility view for non-ASCII text."""

    # UTS #39 applies its confusable mapping after canonical decomposition.
    # Do that before compatibility normalization: NFKD first would rewrite
    # some official source characters (for example long-s or lunate sigma)
    # before the pinned table could see them. The compatibility fallback is
    # still needed for entries intentionally omitted from the compact table.
    skeleton_parts: list[str] = []
    for source_character in value:
        source_parts: list[str] = []
        for character in unicodedata.normalize("NFD", source_character):
            try:
                decimal_digit = str(unicodedata.decimal(character))
            except (TypeError, ValueError):
                decimal_digit = None
            if prefer_decimal and decimal_digit is not None:
                # Decimal digits carry a stronger numeric meaning than a
                # visual UTS mapping (for example Arabic-Indic zero otherwise
                # maps to a period). Keep both views at the policy boundary.
                source_parts.append(decimal_digit)
            elif character in {"ǀ", "∣", "⎮"}:
                # These characters can visually act either as a field
                # separator or as the letter l. The key matcher accepts ``|``
                # specifically at an expected l slot.
                source_parts.append("|")
            elif character == "〇":
                # Do not let ideographic zero turn the unsafe identifier
                # ``n〇ne`` into the accepted absence word ``none``.
                source_parts.append("0")
            else:
                source_parts.append(
                    UTS39_ASCII_TRANSLATION.get(ord(character), character)
                )
        source_skeleton = "".join(source_parts)
        marker_view = unicodedata.normalize("NFKD", source_skeleton).translate(
            _ASCII_CONFUSABLES
        )
        skeleton_parts.append(source_skeleton)
        if (
            not source_character.isascii()
            and any(
                "0" <= character <= "9"
                or "A" <= character <= "Z"
                or "a" <= character <= "z"
                for character in marker_view
            )
        ):
            # A skeleton is valid for recognizing protected keys and the
            # reserved verdict, but it must never make a non-ASCII identifier
            # look like an allowed ASCII absence placeholder.  Determine this
            # from the source's actual final compatibility view rather than
            # ``source_character.isalnum()``: a newer UTS source may still be
            # unassigned on the enforcing Python runtime, while a compatibility
            # fallback can independently decompose a symbol into ASCII.
            skeleton_parts.append("¤")
    return unicodedata.normalize("NFKD", "".join(skeleton_parts)).translate(
        _ASCII_CONFUSABLES
    )


def _normalized_text(
    value: Any,
    *,
    preserve_invisible_boundaries: bool = False,
    prefer_decimal: bool = True,
) -> str:
    """Normalize compatibility forms while preserving visible value content."""

    raw_value = str(value)
    normalized = (
        raw_value
        if raw_value.isascii()
        else _uts39_compatibility_skeleton(
            raw_value,
            prefer_decimal=prefer_decimal,
        )
    )
    shadow: list[str] = []
    for character in normalized:
        if character == "¤":
            shadow.append(character)
            continue
        if character in {"\u0337", "\u0338"}:
            # Preserve negation semantics from decomposed ≠/≢/∉ forms rather
            # than dropping the combining solidus overlay as decoration.
            shadow.append("!")
            continue
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
                "COLON" in unicode_name
                or "RATIO" in unicode_name
                or "PROPORTION" in unicode_name
            ):
                shadow.append(":")
            elif (
                category == "Pd"
                or "MINUS SIGN" in unicode_name
            ):
                shadow.append("-")
            elif "BULLET" in unicode_name or "MIDDLE DOT" in unicode_name:
                shadow.append("-")
            elif "RIGHT" in unicode_name and "ARROW" in unicode_name:
                shadow.append("->")
            elif unicode_name in {"BROKEN BAR", "DIVIDES"}:
                shadow.append("|")
            elif character in {"ǀ", "⎮"}:
                shadow.append("|")
            elif (
                "NOT" not in unicode_name
                and (
                    "IDENTICAL TO" in unicode_name
                    or "EQUIVALENT TO" in unicode_name
                )
            ):
                shadow.append("=")
            elif (
                "VERTICAL" in unicode_name
                and (
                    "LINE" in unicode_name
                    or "BAR" in unicode_name
                    or "BOX DRAWINGS" in unicode_name
                )
            ):
                shadow.append("|")
            elif (
                (skeleton := character.translate(UTS39_ASCII_TRANSLATION))
                != character
                and skeleton.isascii()
            ):
                shadow.append(skeleton)
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

    NFKC, the pinned UTS #39 ASCII skeleton, and decimal normalization handle
    actual compatibility/homoglyph forms.  This final positional pass covers
    common digit substitutions such as ``SUPP0RTED`` without treating an
    arbitrary scientific character (for example Ω) as an ASCII wildcard.
    """

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
            return False
        return saw_confusable

    for normalized in {
        _normalized_text(value),
        _normalized_text(value, prefer_decimal=False),
    }:
        token: list[str] = []
        for character in normalized:
            if _visually_ignorable(character):
                continue
            if character.isalnum():
                token.append(character)
                continue
            if matches_reserved(token):
                return True
            token = []
        if matches_reserved(token):
            return True
    return False


def _canonical_key(value: Any) -> str:
    """Map JSON-key spelling variants to one policy identity."""

    normalized_keys = {
        _normalized_text(value).casefold().replace("�", "x"),
        _normalized_text(value, prefer_decimal=False).casefold().replace("�", "x"),
    }
    canonical_variants = {
        candidate
        for normalized_key in normalized_keys
        for candidate in (
            re.sub(r"[^a-z0-9]+", "", normalized_key),
            re.sub(r"[^a-z0-9]+", "", normalized_key.replace("|", "l")),
            re.sub(r"[^a-z0-9]+", "", normalized_key.replace("|", "i")),
        )
    }
    for canonical_variant in canonical_variants:
        if canonical_variant in _PROTECTED_POLICY_KEYS:
            return canonical_variant
        collapsed = canonical_variant.replace("rn", "m").replace("vv", "w")
        if collapsed in _PROTECTED_POLICY_KEYS:
            return collapsed
    canonical = min(canonical_variants, key=len)
    for normalized in normalized_keys:
        characters = [
            character
            for character in normalized
            if character.isalnum() or character == "x"
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
                    actual = character.casefold()
                    if actual != expected and {actual, expected} != {"i", "l"}:
                        matched = False
                        break
                    continue
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

    pending: list[tuple[Any, bool]] = [(value, False)]
    active_containers: set[int] = set()
    finished_containers: set[int] = set()
    while pending:
        current, exiting = pending.pop()
        if isinstance(current, (bytes, str)):
            if scan_text_leaves and contains_formal_claim_escape_text(current):
                return True
            continue
        if isinstance(current, (list, tuple)):
            identity = id(current)
            if exiting:
                active_containers.discard(identity)
                finished_containers.add(identity)
                continue
            if identity in finished_containers:
                continue
            if identity in active_containers:
                return True
            active_containers.add(identity)
            pending.append((current, True))
            pending.extend((item, False) for item in current)
            continue
        if not isinstance(current, dict):
            continue
        identity = id(current)
        if exiting:
            active_containers.discard(identity)
            finished_containers.add(identity)
            continue
        if identity in finished_containers:
            continue
        if identity in active_containers:
            return True
        active_containers.add(identity)
        pending.append((current, True))
        for raw_key, item in current.items():
            if scan_text_leaves and contains_formal_claim_escape_text(str(raw_key)):
                return True
            key = _canonical_key(raw_key)
            if key in _BOOLEAN_POLICY_KEYS and item is not False:
                return True
            if key in _VERDICT_POLICY_KEYS:
                normalized_items = {
                    _normalized_text(item),
                    _normalized_text(item, prefer_decimal=False),
                }
                if _contains_confusable_supported_token(item) or any(
                    normalized_item.strip().casefold() == "supported"
                    or _SUPPORTED_TOKEN.search(normalized_item) is not None
                    for normalized_item in normalized_items
                ):
                    return True
            if key == "evidenceclass" and item != NON_FORMAL_EVIDENCE_CLASS:
                return True
            if key in _PACK_POLICY_KEYS and not (
                item is None
                or item is False
                or (isinstance(item, str) and item == "")
            ):
                return True
            pending.append((item, False))
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


def _protected_label_is_explicitly_safe(
    key: str,
    suffix: str,
    *,
    allow_documentation: bool = True,
    allow_empty: bool = True,
) -> bool:
    """Allow only closed non-formal values after a protected text label."""

    if key in _VERDICT_POLICY_KEYS:
        # The global reserved-token gate handles SUPPORTED (including its UTS
        # #39 skeleton and digit lookalikes).  Other status prose is not a
        # formal-evidence escape by itself.
        return True
    if not suffix.strip():
        return allow_empty
    if (
        allow_documentation
        and _SAFE_FIELD_DOCUMENTATION.fullmatch(suffix) is not None
    ):
        return True
    relation_surface = suffix.casefold().replace("->", "").replace("=>", "")
    if (
        re.fullmatch(
            r"[a-z0-9_\s:=\-|\(\)\[\]\{\},.;'\"?]*",
            relation_surface,
        )
        is None
    ):
        return False
    token_view = relation_surface.replace(
        NON_FORMAL_EVIDENCE_CLASS.casefold(),
        "nonformaldemo",
    )
    tokens = re.findall(r"[a-z0-9]+", token_view)
    while tokens and tokens[0] in _SAFE_RELATION_WORDS:
        tokens.pop(0)
    if key in _BOOLEAN_POLICY_KEYS:
        return "".join(tokens) == "false"
    if key == "evidenceclass":
        return "".join(tokens) == "nonformaldemo"
    if key in _PACK_POLICY_KEYS:
        if not tokens:
            return _SAFE_EMPTY_PACK_ASSIGNMENT.fullmatch(suffix) is not None
        return tuple(tokens) in _SAFE_PACK_PLACEHOLDERS
    return False


def _reverse_scalar_is_unsafe(key: str, value: str) -> bool:
    """Recognize a compact value in a reverse-layout text record."""

    compact_value = value.strip()
    if re.fullmatch(r"\S+", compact_value) is None:
        return False
    if key in _BOOLEAN_POLICY_KEYS:
        return compact_value.casefold() in {
            "1",
            "enabled",
            "on",
            "true",
            "yes",
        }
    if key == "evidenceclass":
        compact_class = re.sub(r"[^a-z0-9]+", "", compact_value.casefold())
        return any(
            marker in compact_class
            for marker in (
                "adequacy",
                "evidence",
                "formal",
                "model",
                "ready",
                "registry",
                "supported",
            )
        ) and compact_class != "nonformaldemo"
    if key in _PACK_POLICY_KEYS:
        if _protected_label_is_explicitly_safe(
            key,
            compact_value,
            allow_documentation=False,
            allow_empty=False,
        ):
            return False
        return (
            compact_value.casefold().startswith("pack")
            or any(not character.isascii() for character in compact_value)
            or re.search(r"[0-9./-]", compact_value) is not None
        )
    return False


def _implicit_reverse_value_is_unsafe(key: str, value: str) -> bool:
    """Recognize a bounded value immediately before a wrapped bare label."""

    if _protected_label_is_explicitly_safe(
        key,
        value,
        allow_documentation=False,
        allow_empty=False,
    ):
        return False
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    compact_value = "".join(tokens)
    compact_variants = {
        compact_value,
        compact_value.translate(_DIGIT_LETTER_CONFUSABLES),
    }
    if key in _BOOLEAN_POLICY_KEYS:
        return bool(
            compact_variants
            & {"1", "enabled", "notfalse", "on", "true", "yes"}
        ) or ("!" in value and compact_value == "false")
    if key == "evidenceclass":
        formal_prefixes = {
            "aready",
            "formalevidence",
            "formalregistry",
            "isformalevidence",
            "modeladequacy",
            "publicationreadycandidate",
            "registeredevidence",
            "registeredresult",
        }
        return bool(
            compact_variants
            & _FORMAL_EVIDENCE_CLASS_COMPACTS
        ) or any(
            variant.startswith(prefix) and variant != prefix
            for variant in compact_variants
            for prefix in formal_prefixes
        )
    if key in _PACK_POLICY_KEYS:
        # An implicit reverse Evidence Pack value has no safe open namespace:
        # every non-empty, non-placeholder identifier remains non-formal.
        return bool(tokens) or bool(value.strip())
    return False


def _direct_policy_scalar_is_unsafe(key: str, value: str) -> bool:
    """Recognize a standalone formal scalar without treating prose as an ID."""

    unwrapped = value.strip().strip("()[]{}.,;\"'").strip()
    if key in _PACK_POLICY_KEYS:
        # Pack identifiers are deliberately broad, but a complete sentence
        # after a negative disclaimer or closed value is a new record rather
        # than an implicit identifier.  Require the compact ID-like surface
        # used by reverse records here.
        if re.search(r"\s", unwrapped) is None:
            return _reverse_scalar_is_unsafe(key, unwrapped)
        words = re.findall(r"[a-z0-9]+", unwrapped.casefold())
        if words and words[0] == "artifact":
            return True
        if len(words) > 1 and not (
            all(len(word) == 1 for word in words)
        ):
            return False
        compact_surface = re.sub(r"\s+", "", unwrapped)
        return _reverse_scalar_is_unsafe(key, compact_surface)
    return _implicit_reverse_value_is_unsafe(key, unwrapped)


def _policy_scalar_compacts(key: str) -> frozenset[str]:
    """Return the finite closed scalar vocabulary for incremental parsing."""

    if key in _BOOLEAN_POLICY_KEYS:
        return frozenset({"1", "enabled", "false", "notfalse", "on", "true", "yes"})
    if key == "evidenceclass":
        return frozenset({"nonformaldemo", *_FORMAL_EVIDENCE_CLASS_COMPACTS})
    if key in _PACK_POLICY_KEYS:
        return frozenset(
            {
                "absent",
                "empty",
                "false",
                "intentionallyunavailable",
                "none",
                "notassigned",
                "notavailable",
                "null",
                "unavailable",
            }
        )
    return frozenset()


def _policy_fragment_may_extend(
    key: str,
    value: str,
    *,
    direction: str,
) -> bool:
    """Distinguish an incomplete policy atom from unrelated prose."""

    stripped = value.strip()
    compact = "".join(re.findall(r"[a-z0-9]+", stripped.casefold()))
    compact_variants = {
        compact,
    }
    if key == "evidenceclass":
        compact_variants.add(compact.translate(_DIGIT_LETTER_CONFUSABLES))
    candidates = _policy_scalar_compacts(key) | _ASSIGNMENT_RELATION_COMPACTS
    if not compact:
        symbol_view = re.sub(r"[\r\n]+", "", stripped)
        return bool(symbol_view) and re.fullmatch(
            r"[:=|!<>&~¬_\-]+",
            symbol_view,
        ) is not None
    if direction == "right":
        if compact_variants & _ASSIGNMENT_RELATION_COMPACTS:
            return True
        if any(
            candidate.startswith(variant) and candidate != variant
            for candidate in candidates
            for variant in compact_variants
        ):
            return True
    elif direction == "left":
        if compact_variants & _ASSIGNMENT_RELATION_COMPACTS:
            return True
        if any(
            candidate.endswith(variant) and candidate != variant
            for candidate in candidates
            for variant in compact_variants
        ):
            return True
    else:  # pragma: no cover - internal programming error
        raise ValueError(f"unsupported policy-fragment direction: {direction}")
    # A compact pack ID may be renderer-split even when it is not one of the
    # absence placeholders.  Do not extend complete prose containing spaces.
    pack_view = re.sub(r"[\r\n]+", "", stripped.casefold())
    return key in _PACK_POLICY_KEYS and re.fullmatch(
        r"[a-z0-9_.\-/]+",
        pack_view,
    ) is not None


def _forward_policy_tail_state(key: str, value: str) -> bool | None:
    """Classify a forward assignment-chain fragment.

    ``True`` is formal/unsafe, ``False`` is an explicitly closed non-formal
    value, and ``None`` means that more fragments are required or that the
    text is not an assignment-chain fragment.
    """

    views = {
        re.sub(r"[\r\n]+", " ", value).strip(),
        re.sub(r"[\r\n]+", "", value).strip(),
    }
    if any(
        _protected_label_is_explicitly_safe(
            key,
            view,
            allow_documentation=False,
            allow_empty=False,
        )
        for view in views
        if view and _OPEN_ASSIGNMENT_SUFFIX.fullmatch(view) is None
    ):
        return False
    saw_safe = False
    for view in views:
        if not view:
            continue
        symbolic_relation = re.match(r"\s*([!<>=:~¬|&-]+)\s*", view)
        if symbolic_relation is not None:
            scalar = view[symbolic_relation.end() :].strip()
            if not scalar:
                continue
            if symbolic_relation.group(1) not in {
                ":",
                "=",
                ":=",
                "==",
                "->",
                "=>",
                "|",
            }:
                return True
        relation = _LEADING_ASSIGNMENT_RELATION.match(view)
        if relation is not None:
            scalar = view[relation.end() :].strip()
            if not scalar:
                continue
            if _protected_label_is_explicitly_safe(
                key,
                view,
                allow_documentation=False,
                allow_empty=False,
            ):
                saw_safe = True
                continue
            if _safe_value_may_extend_right(key, scalar):
                continue
            # An explicit relation binds every non-safe value to the field.
            return True
        if _protected_label_is_explicitly_safe(
            key,
            view,
            allow_documentation=False,
            allow_empty=False,
        ):
            saw_safe = True
            continue
        if _direct_policy_scalar_is_unsafe(key, view):
            return True
    return False if saw_safe else None


def _reverse_policy_tail_state(key: str, value: str) -> bool | None:
    """Classify one reverse assignment-chain fragment nearest the field."""

    compact_view = re.sub(r"[\r\n]+", "", value).strip()
    compact_reverse = _REVERSE_ASSIGNMENT.fullmatch(compact_view)
    if compact_reverse is not None:
        compact_symbol = compact_reverse.group("symbol")
        if (
            compact_symbol in {None, ":", "=", ":=", "==", "->", "=>", "|"}
            and _protected_label_is_explicitly_safe(
                key,
                compact_reverse.group("value"),
                allow_documentation=False,
                allow_empty=False,
            )
        ):
            return False

    views = {
        re.sub(r"[\r\n]+", " ", value).strip(),
        re.sub(r"[\r\n]+", "", value).strip(),
    }
    saw_safe = False
    for view in views:
        if not view:
            continue
        if _OPEN_ASSIGNMENT_SUFFIX.fullmatch(view) is not None:
            continue
        if _relation_may_extend_left(view):
            continue
        reverse = _REVERSE_ASSIGNMENT.fullmatch(view)
        if reverse is not None:
            symbol = reverse.group("symbol")
            if symbol is not None and symbol not in {
                ":",
                "=",
                ":=",
                "==",
                "->",
                "=>",
                "|",
            }:
                return True
            scalar = reverse.group("value")
            if _protected_label_is_explicitly_safe(
                key,
                scalar,
                allow_documentation=False,
                allow_empty=False,
            ):
                saw_safe = True
                continue
            if _safe_value_may_extend_left(key, scalar):
                continue
            return True
        if _protected_label_is_explicitly_safe(
            key,
            view,
            allow_documentation=False,
            allow_empty=False,
        ):
            saw_safe = True
            continue
        if _direct_policy_scalar_is_unsafe(key, view):
            return True
    return False if saw_safe else None


def _safe_value_may_extend_left(key: str, value: str) -> bool:
    """Return whether a partial reverse value may be a wrapped safe suffix."""

    compact_value = "".join(re.findall(r"[a-z0-9]+", value.casefold()))
    if not compact_value:
        return False
    if key in _BOOLEAN_POLICY_KEYS:
        safe_values = {"false"}
    elif key == "evidenceclass":
        safe_values = {"nonformaldemo"}
    elif key in _PACK_POLICY_KEYS:
        safe_values = {
            "absent",
            "empty",
            "false",
            "intentionallyunavailable",
            "none",
            "notassigned",
            "notavailable",
            "null",
            "unavailable",
        }
    else:
        return False
    return any(
        safe_value.endswith(compact_value) and safe_value != compact_value
        for safe_value in safe_values
    )


def _safe_value_may_extend_right(key: str, value: str) -> bool:
    """Return whether a partial forward value may be a wrapped safe prefix."""

    compact_value = "".join(re.findall(r"[a-z0-9]+", value.casefold()))
    if not compact_value:
        return False
    if key in _BOOLEAN_POLICY_KEYS:
        safe_values = {"false"}
    elif key == "evidenceclass":
        safe_values = {"nonformaldemo"}
    elif key in _PACK_POLICY_KEYS:
        safe_values = {
            "absent",
            "empty",
            "false",
            "intentionallyunavailable",
            "none",
            "notassigned",
            "notavailable",
            "null",
            "unavailable",
        }
    else:
        return False
    return any(
        safe_value.startswith(compact_value) and safe_value != compact_value
        for safe_value in safe_values
    )


def _relation_may_extend_left(value: str) -> bool:
    """Return whether text can be the wrapped suffix of a word relation."""

    compact_value = "".join(re.findall(r"[a-z0-9]+", value.casefold()))
    if not compact_value:
        return False
    return any(
        relation.endswith(compact_value) and relation != compact_value
        for relation in _ASSIGNMENT_RELATION_COMPACTS
    )


def _protected_prefix_has_reverse_value(
    key: str,
    prefix: str,
    *,
    has_forward_surface: bool = False,
) -> bool:
    """Reject positive values written before a protected field label."""

    reverse = _REVERSE_ASSIGNMENT.fullmatch(prefix)
    if reverse is None:
        return _reverse_scalar_is_unsafe(key, prefix)
    symbol = reverse.group("symbol")
    if symbol == ":" and has_forward_surface:
        # ``INFO: publication_ready=false`` is an ordinary labelled log line,
        # but a known positive scalar such as ``true:`` is still a reverse
        # assignment even when a safe forward value follows it.
        if _reverse_scalar_is_unsafe(key, reverse.group("value")):
            return True
        return False
    if symbol is not None and symbol not in {
        ":",
        "=",
        ":=",
        "==",
        "->",
        "=>",
        "|",
    }:
        return True
    return not _protected_label_is_explicitly_safe(
        key,
        reverse.group("value"),
        allow_documentation=False,
        allow_empty=False,
    )


def _protected_line_labels_are_safe(line: str) -> bool:
    """Validate protected labels and values that share one logical line."""

    previous: tuple[int, str] | None = None
    matches = list(_PROTECTED_TEXT_MARKER.finditer(line))
    for index, match in enumerate(matches):
        key = str(match.lastgroup)
        segment_start = previous[0] if previous is not None else 0
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        if _protected_prefix_has_reverse_value(
            key,
            line[segment_start : match.start()],
            has_forward_surface=bool(line[match.end() : next_start].strip()),
        ):
            return False
        if previous is not None:
            end, previous_key = previous
            if previous_key not in _VERDICT_POLICY_KEYS:
                suffix = line[end : match.start()]
                if not suffix.strip() or not _protected_label_is_explicitly_safe(
                    previous_key,
                    suffix,
                ):
                    return False
        previous = (match.end(), key)
    if previous is not None:
        end, key = previous
        if key not in _VERDICT_POLICY_KEYS:
            if not _protected_label_is_explicitly_safe(key, line[end:]):
                return False
    return True


def _protected_text_labels_are_safe(text: str) -> bool:
    """Return whether every recognized protected label uses a safe value."""

    # A renderer can wrap the log-level token itself (``IN\nFO:``).  Remove
    # only exact log labels and their internal renderer wraps so the
    # payload joins the same policy-record stream as an ordinary ``INFO:``
    # payload.  Keeping those internal blank lines would split a safe wrapped
    # value such as ``u\nIN\nFO: navailable`` into a different record.  The
    # direct-prefix pass below handles the unsplit form; requiring a newline
    # here prevents needless recursion.
    wrapped_log_prefix = False

    def strip_wrapped_log_prefix(match: re.Match[str]) -> str:
        nonlocal wrapped_log_prefix
        value = match.group()
        if "\n" not in value and "\r" not in value:
            return value
        wrapped_log_prefix = True
        return ""

    text_without_wrapped_log_prefix = _WRAPPED_LOG_PREFIX.sub(
        strip_wrapped_log_prefix,
        text,
    )
    if wrapped_log_prefix:
        return _protected_text_labels_are_safe(text_without_wrapped_log_prefix)

    def log_payload_is_policy_fragment(value: str) -> bool:
        if _PROTECTED_TEXT_MARKER.search(value) is not None:
            return True
        if re.search(r"[!<>=:~¬|&-]", value) is not None:
            return True
        compact_payload = "".join(
            re.findall(r"[a-z0-9]+", value.casefold())
        )
        if compact_payload and any(
            compact_payload in protected_key or protected_key in compact_payload
            for protected_key in _PROTECTED_POLICY_KEYS
        ):
            return True
        if compact_payload and any(
            compact_payload in policy_atom
            for policy_atom in (
                _ASSIGNMENT_RELATION_COMPACTS
                | _policy_scalar_compacts("publicationready")
                | _policy_scalar_compacts("evidenceclass")
                | _policy_scalar_compacts("evidencepackid")
            )
        ):
            return True
        for scalar in (
            _policy_scalar_compacts("publicationready")
            | _policy_scalar_compacts("evidenceclass")
        ):
            if not compact_payload.endswith(scalar):
                continue
            relation_fragment = compact_payload[: -len(scalar)]
            if relation_fragment and any(
                relation.endswith(relation_fragment)
                for relation in _ASSIGNMENT_RELATION_COMPACTS
            ):
                return True
        for policy_key in (
            "publicationready",
            "evidenceclass",
            "evidencepackid",
        ):
            if (
                _forward_policy_tail_state(policy_key, value) is not None
                or _reverse_policy_tail_state(policy_key, value) is not None
            ):
                return True
            if policy_key not in _PACK_POLICY_KEYS and (
                _policy_fragment_may_extend(
                    policy_key,
                    value,
                    direction="right",
                )
                or _policy_fragment_may_extend(
                    policy_key,
                    value,
                    direction="left",
                )
            ):
                return True
            if policy_key in _PACK_POLICY_KEYS and (
                _safe_value_may_extend_right(policy_key, value)
                or _safe_value_may_extend_left(policy_key, value)
                or _direct_policy_scalar_is_unsafe(policy_key, value)
            ):
                return True
        return False

    # Log level prefixes are renderer metadata, not scientific content.  Strip
    # every repeated prefix while preserving line breaks, then parse the
    # payload exactly like ordinary output.  This keeps ``INFO: f`` + ``alse``
    # symmetric with ``INFO: true`` and also handles a prefix inserted inside a
    # protected field name.
    stripped_log_lines: list[str] = []
    stripped_log_prefix = False
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        ending = raw_line[len(line) :]
        line_had_log_prefix = False
        while True:
            log_prefix = _DETACHED_LOG_LINE.match(line)
            if log_prefix is None:
                break
            line = line[log_prefix.end() :].lstrip()
            stripped_log_prefix = True
            line_had_log_prefix = True
        if (
            line_had_log_prefix
            and line.strip()
            and not log_payload_is_policy_fragment(line)
        ):
            # Preserve a hard logical-record boundary without retaining an
            # unrelated log payload that could look like a pack identifier.
            line = "issue is tracked."
        stripped_log_lines.append(f"{line}{ending}")
    if stripped_log_prefix:
        return _protected_text_labels_are_safe("".join(stripped_log_lines))

    def is_safe_negative_prose(value: str) -> bool:
        return any(
            pattern.fullmatch(value.strip()) is not None
            for pattern in _SAFE_NEGATIVE_PROSE
        )

    def candidate_views(value: str) -> tuple[str, ...]:
        # A line break can be ordinary layout whitespace or can split a token.
        # Validate both interpretations.  The allowlisted negative sentences
        # are the only case where one exact interpretation is sufficient.
        views = {
            re.sub(r"[\r\n]+", " ", value).strip(),
            re.sub(r"[\r\n]+", "", value).strip(),
        }
        return tuple(view for view in views if view)

    def candidate_is_safe(value: str) -> bool:
        views = candidate_views(value)
        if any(is_safe_negative_prose(view) for view in views):
            return True
        # A renderer-added line break may replace layout whitespace or split a
        # token.  A view is accepted only when it still parses to an explicitly
        # closed value; unrecognized suffix/prefix text remains fail-closed.
        return any(_protected_line_labels_are_safe(view) for view in views)

    # Allow an exact non-formal disclaimer regardless of where a renderer
    # wrapped it.  Extra text (including a following assignment) prevents the
    # full match and is inspected below.
    if any(is_safe_negative_prose(view) for view in candidate_views(text)):
        return True

    text_markers = list(_PROTECTED_TEXT_MARKER.finditer(text))

    line_records: list[tuple[int, int, str]] = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        line_records.append((cursor, cursor + len(line), line))
        cursor += len(raw_line)
    if cursor < len(text) or not line_records:
        line_records.append((cursor, len(text), text[cursor:]))
    line_starts = [start for start, _, _ in line_records]
    nonempty_line_indices = [
        index
        for index, (_, _, line) in enumerate(line_records)
        if line.strip()
    ]
    def mapped_negative_spans(*, remove_newlines: bool) -> set[tuple[int, int]]:
        view_chars: list[str] = []
        source_offsets: list[int] = []
        for source_offset, character in enumerate(text):
            if character in {"\r", "\n"}:
                if remove_newlines:
                    continue
                character = " "
            view_chars.append(character)
            source_offsets.append(source_offset)
        view = "".join(view_chars)
        spans: set[tuple[int, int]] = set()
        for pattern in _SAFE_NEGATIVE_PROSE:
            for negative_match in pattern.finditer(view):
                if negative_match.start() == negative_match.end():
                    continue
                spans.add(
                    (
                        source_offsets[negative_match.start()],
                        source_offsets[negative_match.end() - 1] + 1,
                    )
                )
        return spans

    # Evaluate both renderer interpretations so a disclaimer remains safe when
    # a line break replaces layout whitespace or splits a word.  Offsets are
    # mapped back to the original text before physical-line masking.
    safe_negative_spans = sorted(
        mapped_negative_spans(remove_newlines=False)
        | mapped_negative_spans(remove_newlines=True)
    )
    safe_negative_starts = [start for start, _ in safe_negative_spans]

    if safe_negative_spans:
        rewritten_parts: list[str] = []
        rewritten_cursor = 0
        for negative_start, negative_end in sorted(
            safe_negative_spans,
            key=lambda span: (span[0], -(span[1] - span[0])),
        ):
            if negative_start < rewritten_cursor:
                continue
            negative_markers = list(
                _PROTECTED_TEXT_MARKER.finditer(
                    text,
                    negative_start,
                    negative_end,
                )
            )
            if not negative_markers:
                continue
            replacements: list[str] = []
            for negative_marker in negative_markers:
                negative_key = str(negative_marker.lastgroup)
                if negative_key in _BOOLEAN_POLICY_KEYS:
                    replacements.append(f"{negative_marker.group()}=false")
                elif negative_key == "evidenceclass":
                    replacements.append("evidence_class=NON_FORMAL_DEMO")
                elif negative_key in _PACK_POLICY_KEYS:
                    replacements.append("Evidence Pack ID=unavailable")
            if not replacements:
                continue
            rewritten_parts.append(text[rewritten_cursor:negative_start])
            rewritten_parts.append(" ".join(dict.fromkeys(replacements)))
            rewritten_cursor = negative_end
        if rewritten_cursor:
            rewritten_parts.append(text[rewritten_cursor:])
            return _protected_text_labels_are_safe("".join(rewritten_parts))

    for line_index, (line_start, line_end, line) in enumerate(line_records):
        if not line.strip():
            continue
        masked_line = list(line)
        span_offset = bisect_right(safe_negative_starts, line_end) - 1
        while span_offset >= 0:
            span_start, span_end = safe_negative_spans[span_offset]
            if span_end <= line_start:
                break
            overlap_start = max(line_start, span_start) - line_start
            overlap_end = min(line_end, span_end) - line_start
            if overlap_start < overlap_end:
                masked_line[overlap_start:overlap_end] = " " * (
                    overlap_end - overlap_start
                )
            span_offset -= 1
        policy_line = "".join(masked_line)
        if not policy_line.strip():
            continue
        line_matches = list(_PROTECTED_TEXT_MARKER.finditer(policy_line))
        if not _protected_line_labels_are_safe(policy_line):
            following_offset = bisect_right(nonempty_line_indices, line_index)
            preceding_offset = bisect_left(nonempty_line_indices, line_index)
            if line_matches and (
                following_offset < len(nonempty_line_indices)
                or preceding_offset > 0
            ):
                # A renderer may have split either the relation or the closed
                # value.  Defer this line and validate its bounded, coalesced
                # record in the marker pass.
                continue
            return False

    # Physical-line validation is complete when there is no cross-line
    # layout to reconstruct.  This also keeps dense one-line logs strictly
    # linear instead of slicing their full prefix/suffix once per marker.
    if not safe_negative_spans and "\n" not in text and "\r" not in text:
        return True

    # Parse outward from every recognized marker.  A candidate absorbs only a
    # fixed-size neighborhood in either direction: value / relation / label or
    # label / relation / value.  This closes wrapped layouts without joining
    # unrelated logs or rescanning the whole text for each marker.
    for match in text_markers:
        negative_span_offset = bisect_right(
            safe_negative_starts,
            match.start(),
        ) - 1
        if negative_span_offset >= 0:
            negative_start, negative_end = safe_negative_spans[
                negative_span_offset
            ]
            if negative_start <= match.start() and match.end() <= negative_end:
                trailing = _NONSPACE.search(text, negative_end)
                if (
                    trailing is not None
                    and _LEADING_ASSIGNMENT_RELATION.match(
                        text,
                        trailing.start(),
                    )
                    is not None
                ):
                    return False
                if trailing is not None:
                    trailing_start = trailing.start()
                    if (
                        _DETACHED_LOG_LINE.match(text, trailing_start) is None
                        and text[trailing_start] not in {"\r", "\n"}
                    ):
                        trailing_end_candidates = [
                            position
                            for position in (
                                text.find("\n", trailing_start),
                                text.find("\r", trailing_start),
                            )
                            if position >= 0
                        ]
                        trailing_end = (
                            min(trailing_end_candidates)
                            if trailing_end_candidates
                            else len(text)
                        )
                        if _direct_policy_scalar_is_unsafe(
                            str(match.lastgroup),
                            text[trailing_start:trailing_end],
                        ):
                            return False
                continue
        key = str(match.lastgroup)
        if key in _VERDICT_POLICY_KEYS:
            # The reserved SUPPORTED token is rejected before this parser and
            # every other status value is non-formal.  Avoid reconstructing
            # open status logs once per marker.
            continue
        start_line_index = bisect_right(line_starts, match.start()) - 1
        end_line_index = bisect_right(
            line_starts,
            max(match.start(), match.end() - 1),
        ) - 1
        record_start = line_records[start_line_index][0]
        record_end = line_records[end_line_index][1]
        base_record = text[record_start:record_end]
        prefix = text[record_start : match.start()]
        suffix = text[match.end() : record_end]
        base_is_safe = candidate_is_safe(base_record)

        following_offset = bisect_right(nonempty_line_indices, end_line_index)
        forward_parts: list[str] = []
        forward_closed = False
        if suffix.strip():
            suffix_state = _forward_policy_tail_state(key, suffix)
            if suffix_state is True:
                return False
            if suffix_state is False:
                forward_closed = True
            elif _policy_fragment_may_extend(
                key,
                suffix,
                direction="right",
            ):
                forward_parts.append(suffix)
            elif not base_is_safe:
                return False

        # Stream the right-hand logical record.  Only incomplete relation/value
        # atoms are accumulated, so ordinary multi-line output stops in O(1)
        # per marker instead of rebuilding every growing prefix.
        for following_position in range(
            following_offset,
            len(nonempty_line_indices),
        ):
            following_line_index = nonempty_line_indices[following_position]
            following_line = line_records[following_line_index][2]
            if (
                _SAFE_FIELD_DOCUMENTATION.fullmatch(following_line.strip())
                is not None
                or is_safe_negative_prose(following_line)
                or _PROTECTED_TEXT_MARKER.search(following_line) is not None
            ):
                break
            if re.fullmatch(r"_+", following_line.strip()) is not None:
                continue
            forward_candidate = "\n".join([*forward_parts, following_line])
            forward_state = _forward_policy_tail_state(key, forward_candidate)
            if forward_state is True:
                return False
            if forward_state is False:
                forward_closed = True
                forward_parts.clear()
                continue
            if (
                key in _PACK_POLICY_KEYS
                and not forward_parts
                and re.fullmatch(
                    r"[a-z0-9_.\-/]+",
                    following_line.strip().casefold(),
                )
                is not None
                and not _safe_value_may_extend_right(key, following_line)
            ):
                return False
            if _policy_fragment_may_extend(
                key,
                forward_candidate,
                direction="right",
            ):
                forward_parts.append(following_line)
                continue
            if key in _PACK_POLICY_KEYS and not forward_closed:
                # A bare Evidence Pack field followed by non-placeholder text
                # is an identifier even when it contains ordinary spaces.
                return False
            break

        preceding_offset = bisect_left(
            nonempty_line_indices,
            start_line_index,
        )
        reverse_parts: list[str] = []
        if prefix.strip():
            prefix_state = _reverse_policy_tail_state(key, prefix)
            prefix_value = prefix.strip().rstrip(":").strip()
            prefix_is_layout = (
                prefix.rstrip().endswith(":")
                and not _direct_policy_scalar_is_unsafe(key, prefix_value)
            )
            if prefix_state is True and not prefix_is_layout:
                return False
            if (
                prefix_state is None
                and _policy_fragment_may_extend(
                    key,
                    prefix,
                    direction="left",
                )
            ):
                reverse_parts.append(prefix)

        # Stream the left-hand assignment chain from the marker outward.  A
        # closed safe value does not hide an earlier formal shadow.
        for preceding_position in range(preceding_offset - 1, -1, -1):
            preceding_line_index = nonempty_line_indices[preceding_position]
            preceding_line = line_records[preceding_line_index][2]
            if (
                _SAFE_FIELD_DOCUMENTATION.fullmatch(preceding_line.strip())
                is not None
                or is_safe_negative_prose(preceding_line)
                or _PROTECTED_TEXT_MARKER.search(preceding_line) is not None
            ):
                break
            if re.fullmatch(r"_+", preceding_line.strip()) is not None:
                continue
            reverse_candidate = "\n".join(
                [preceding_line, *reverse_parts]
            )
            reverse_state = _reverse_policy_tail_state(key, reverse_candidate)
            if reverse_state is True:
                return False
            if reverse_state is False:
                reverse_parts.clear()
                continue
            if _policy_fragment_may_extend(
                key,
                reverse_candidate,
                direction="left",
            ):
                reverse_parts.insert(0, preceding_line)
                continue
            break
    return True


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
        or character in {"\u2028", "\u2029"}
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
    boundary_texts = {
        _normalized_text(raw_text, preserve_invisible_boundaries=True)
    }
    if not raw_text.isascii():
        boundary_texts.add(
            _normalized_text(
                raw_text,
                preserve_invisible_boundaries=True,
                prefer_decimal=False,
            )
        )
    if any(_SUPPORTED_TOKEN.search(text) is not None for text in boundary_texts):
        return True
    if any(not _protected_text_labels_are_safe(text) for text in boundary_texts):
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
