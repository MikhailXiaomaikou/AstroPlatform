"""Shared fail-closed evidence policy for non-formal Foundry output."""

from __future__ import annotations

import json
import re
import unicodedata
import uuid
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
_OPEN_ASSIGNMENT_SUFFIX = re.compile(
    r"\s*(?:[:=]+|[-=]+>|is|equals?|equal[-_\s]+to|"
    r"assigned(?:[-_\s]+to)?|set[-_\s]+to|is[-_\s]+set[-_\s]+to)\s*",
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
        skeleton_parts.append(source_skeleton)
        if (
            not source_character.isascii()
            and source_character.isalnum()
            and any(character.isascii() and character.isalnum() for character in source_skeleton)
        ):
            # A skeleton is valid for recognizing protected keys and the
            # reserved verdict, but it must never make a non-ASCII identifier
            # look like an allowed ASCII absence placeholder.
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
        return tokens == ["false"]
    if key == "evidenceclass":
        return tokens == ["nonformaldemo"]
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


def _protected_prefix_has_reverse_value(
    key: str,
    prefix: str,
    *,
    has_forward_surface: bool = False,
) -> bool:
    """Reject positive values written before a protected field label."""

    reverse = re.fullmatch(
        r"\s*(?P<value>.+?)\s*"
        r"(?:(?P<symbol>[!<>=:~¬|-]+)|(?P<words>"
        r"is[-_\s]+equal(?:s)?(?:[-_\s]+to)?|"
        r"equal(?:s)?(?:[-_\s]+to)?|"
        r"assigned(?:[-_\s]+to)?|"
        r"is[-_\s]+set[-_\s]+to|"
        r"was[-_\s]+(?:equal(?:s)?|set)(?:[-_\s]+to)?|"
        r"set(?:[-_\s]+to)?|becomes|is|was))\s*",
        prefix,
        re.IGNORECASE,
    )
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

    source_lines = [
        line
        for line in (text.splitlines() or [text])
        if line.strip()
        and not any(
            pattern.fullmatch(line.strip()) for pattern in _SAFE_NEGATIVE_PROSE
        )
    ]
    lines: list[str] = []
    index = 0
    while index < len(source_lines):
        line = source_lines[index]
        matches = list(_PROTECTED_TEXT_MARKER.finditer(line))
        if matches and index + 1 < len(source_lines):
            last_match = matches[-1]
            suffix = line[last_match.end() :]
            if _OPEN_ASSIGNMENT_SUFFIX.fullmatch(suffix) is not None:
                lines.append(f"{line} {source_lines[index + 1]}")
                index += 2
                continue
        lines.append(line)
        index += 1
    if any(not _protected_line_labels_are_safe(line) for line in lines):
        return False

    # A rendered field name may itself be wrapped across lines. Inspect only
    # the logical record slices whose recognized marker actually crosses a
    # newline, so unrelated adjacent log lines are not merged.
    for match in _PROTECTED_TEXT_MARKER.finditer(text):
        if "\n" not in match.group() and "\r" not in match.group():
            continue
        record_start = max(text.rfind("\n", 0, match.start()), text.rfind("\r", 0, match.start())) + 1
        newline_positions = [
            position
            for position in (
                text.find("\n", match.end()),
                text.find("\r", match.end()),
            )
            if position >= 0
        ]
        record_end = min(newline_positions) if newline_positions else len(text)
        logical_record = re.sub(r"[\r\n]+", "", text[record_start:record_end])
        if not _protected_line_labels_are_safe(logical_record):
            return False

    # Check the two unambiguous cross-line record layouts without treating an
    # unrelated sentence on the preceding line as a reverse assignment.
    for previous_line, current_line in zip(lines, lines[1:], strict=False):
        if _PROTECTED_TEXT_MARKER.search(current_line) is not None:
            for joiner in (" ", ""):
                if not _protected_line_labels_are_safe(
                    f"{previous_line}{joiner}{current_line}"
                ):
                    return False
        previous_matches = list(_PROTECTED_TEXT_MARKER.finditer(previous_line))
        if previous_matches:
            previous_match = previous_matches[-1]
            previous_key = str(previous_match.lastgroup)
            if (
                previous_key not in _VERDICT_POLICY_KEYS
                and not previous_line[previous_match.end() :].strip()
                and not _protected_label_is_explicitly_safe(
                    previous_key,
                    current_line,
                    allow_documentation=False,
                    allow_empty=False,
                )
            ):
                return False

        current_matches = list(_PROTECTED_TEXT_MARKER.finditer(current_line))
        if len(current_matches) != 1:
            continue
        current_match = current_matches[0]
        if (
            current_line[: current_match.start()].strip()
            or current_line[current_match.end() :].strip()
        ):
            continue
        compact_value = previous_line.strip()
        if (
            len(compact_value) <= 256
            and re.fullmatch(r"\S+", compact_value) is not None
            and not _protected_label_is_explicitly_safe(
                str(current_match.lastgroup),
                compact_value,
                allow_documentation=False,
                allow_empty=False,
            )
        ):
            return False
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
