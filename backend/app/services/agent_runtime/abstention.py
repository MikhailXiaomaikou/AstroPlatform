"""Abstention-tag parsing/rendering, truncation detection, reply scrubbing.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _parse_actions(text: str) -> list[dict]:
    """Extract action JSON from <actions>...</actions> tags."""
    actions = []
    import re

    matches = re.findall(r"<actions>(.*?)</actions>", text, re.DOTALL)
    for match in matches:
        try:
            parsed = json.loads(match.strip())
            if isinstance(parsed, list):
                actions.extend(parsed)
            else:
                actions.append(parsed)
        except json.JSONDecodeError:
            # Try line-by-line
            for line in match.strip().split("\n"):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        actions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return actions


def _strip_actions_from_reply(text: str) -> str:
    """Remove <actions> blocks from the user-facing reply."""
    import re

    return re.sub(r"<actions>.*?</actions>", "", text, flags=re.DOTALL).strip()


# F2.3: <tools_returned_nothing/> structured abstention parser.
# The model's entire reply is supposed to be a single self-closing XML tag
# when tools had no data.  We parse permissively: attribute order doesn't
# matter, quotes can be " or ', and whitespace may surround the tag.
_ABSTENTION_RE = __import__("re").compile(
    r"""^\s*<(?P<tag>tools_returned_nothing|toolsreturnednothing)
        (?P<attrs>[^>]*)
        /?\s*>\s*(?:</(?P=tag)>\s*)?$""",
    __import__("re").VERBOSE | __import__("re").DOTALL,
)
_ATTR_RE = __import__("re").compile(
    r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""",
)


# PART AC C3 — substrings whose presence in run_python code indicates
# the AI is reading REAL data from a platform cache helper or astropy /
# astroquery / lightkurve-shaped reader. The G3.2 SYNTHETIC tainter
# uses this to grant a reverse-direction exemption: "you didn't
# declare data_source as a real source, but the code body proves you
# actually read real cached data, so we won't paint the result red".
#
# Keep this in sync with the data_source contract validator's
# Mirrors the `_REAL_DATA_READERS` concept in synthetic_code_detector.py —
# both mean "the code is genuinely reading real data".
# PART AD: these names must appear as ACTUAL AST calls / imports, not as
# substrings. The old `token in code` scan could be spoofed by writing
# `get_cached_results(` inside a comment or string literal to dodge G3.2.
_REAL_CACHE_READER_CALL_NAMES = frozenset({
    "get_cached_results", "get_search_results", "get_adql_results",
    "get_adql_result_sets", "get_latest_adql_result",
    "load_fits", "load_votable", "load_csv",
    "search_lightcurve", "download_and_clean_lightcurve", "transit_search",
    "read_csv", "read_parquet",
})
_REAL_CACHE_READER_CALL_CHAINS = frozenset({
    "Table.read", "fits.open", "astropy.io.fits.open",
})
_REAL_CACHE_READER_MODULES = frozenset({"lightkurve", "astroquery"})


def _run_python_code_reads_real_cache(code: str) -> bool:
    """PART AD (hardened PART AC C3): True when run_python code REALLY reads
    real data via an actual call / import.

    Reverse-direction exemption for the G3.2 SYNTHETIC tainter. The old
    implementation used a substring scan, so `get_cached_results(` in a
    comment or string literal would spoof the exemption. We now parse the
    AST and require the reader to be a genuine Call / Import node.
    """
    if not isinstance(code, str) or not code:
        return False
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False

    def _chain(node: ast.AST) -> str:
        parts: list[str] = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            chain = _chain(node.func)
            if not chain:
                continue
            if chain.rsplit(".", 1)[-1] in _REAL_CACHE_READER_CALL_NAMES:
                return True
            if chain in _REAL_CACHE_READER_CALL_CHAINS:
                return True
            if chain.split(".", 1)[0] in _REAL_CACHE_READER_MODULES and "." in chain:
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in _REAL_CACHE_READER_MODULES:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".", 1)[0] in _REAL_CACHE_READER_MODULES:
                return True
    return False


# `=` catches an unfinished equation / assignment ("H0 =" with no value);
# a finished equation never ends on the operator itself.
_TRUNCATED_TRAILING_PUNCT = {":", ",", "—", "–", "(", "[", "{", "="}
# `-` (ASCII hyphen) is intentionally NOT in this set — many sentences
# legitimately end with it (e.g. "M-class star") — and we don't want
# false-positive truncations on those. Em-dash and en-dash are kept
# because reply text ending on those is a strong mid-sentence signal.

_TRUNCATED_TRAILING_CONNECTIVES = {
    "or", "and", "the", "to", "of", "in", "on", "by", "for", "with",
    "a", "an", "as", "at", "but", "if", "is", "are", "was", "were",
    "where", "while", "from", "than", "then", "vs",
}


def _reply_looks_truncated(reply: str) -> bool:
    """PART AC C2 — text-shape detection of mid-sentence termination.

    Catches the M3 / R2.6 / R2.10 silent-truncation regression where
    the provider returns stop_reason="stop" / "end_turn" but the prose
    is obviously cut off (e.g. ends on a colon). Used as a fallback
    signal in the main truncation gate when stop_reason looks clean.

    Returns True when the reply's last meaningful character / word
    indicates an incomplete sentence. Returns False for already-clean
    endings (sentence-final punctuation, abstention tags, code fences).
    """
    if not reply:
        return False
    s = reply.rstrip()
    if not s:
        return False
    # Already an honest abstention tag — that's a complete reply by
    # design, not a truncation.
    if "<tools_returned_nothing" in s or "</tools_returned_nothing>" in s:
        return False
    # Inside an unclosed Markdown fence — let the caller see the raw
    # text rather than trigger a regen.
    if s.count("```") % 2 == 1:
        return False

    last_char = s[-1]
    if last_char in _TRUNCATED_TRAILING_PUNCT:
        return True
    # Sentence-final punctuation, closing quote/bracket, ellipsis →
    # the reply has a real ending.
    if last_char in {".", "!", "?", "…", '"', "'", "”", "’", ")", "]", "}", ";"}:
        return False

    # Trailing connective word (e.g. "...the", "...or") suggests the
    # next clause was cut. Strip Markdown emphasis / inline-code chars
    # and look at the final whitespace-delimited token.
    last_line = s.split("\n")[-1].rstrip("*_`>").strip()
    if not last_line:
        return False
    parts = last_line.split()
    if not parts:
        return False
    last_word = parts[-1].lower().strip(".,;:!?)]}\"'`*_~—–")
    if last_word in _TRUNCATED_TRAILING_CONNECTIVES:
        return True
    return False


def _parse_abstention_tag(reply: str) -> dict | None:
    """Return attrs dict if reply is a single <tools_returned_nothing/> tag,
    else None.  Tolerates a trailing newline or surrounding whitespace."""
    if not reply:
        return None
    reply_l = reply.lower()
    if "tools_returned_nothing" not in reply_l and "toolsreturnednothing" not in reply_l:
        return None
    m = _ABSTENTION_RE.match(reply.strip())
    if not m:
        return None
    attrs_raw = m.group("attrs") or ""
    attrs: dict = {}
    for match in _ATTR_RE.finditer(attrs_raw):
        key = _normalize_abstention_attr_key(match.group(1))
        val = match.group(2) if match.group(2) is not None else match.group(3) or ""
        attrs[key] = val.strip()
    return attrs


def _normalize_abstention_attr_key(key: str) -> str:
    """Normalize known malformed abstention attribute spellings.

    The prompt requires snake_case, but production traces occasionally
    contain variants such as `failedtools` or `suggestednext_step`.  Keep
    this recovery narrow so the UI can render a friendly card without
    treating arbitrary XML as valid.
    """
    compact = key.replace("-", "_").replace(" ", "_").lower()
    no_underscore = compact.replace("_", "")
    aliases = {
        "failedtools": "failed_tools",
        "failedtool": "failed_tools",
        "emptytools": "empty_tools",
        "emptytool": "empty_tools",
        "suggestednextstep": "suggested_next_step",
        "nextstep": "suggested_next_step",
        "reason": "rationale",
        "rationale": "rationale",
    }
    return aliases.get(no_underscore, compact)


def _classify_abstention_reason(all_tool_results: list[dict]) -> str:
    """Was this an empty-tools turn, a failed-tools turn, or a mix?"""
    statuses: list[str] = []
    for entry in all_tool_results or []:
        inner = entry.get("result") if isinstance(entry.get("result"), dict) else entry
        st = inner.get("__tool_status__") or inner.get("analysis_status")
        if isinstance(st, str):
            statuses.append(st.upper())
    has_empty = any(s == "EMPTY" for s in statuses)
    has_failed = any(s in ("FAILED", "UNAVAILABLE") for s in statuses)
    if has_empty and has_failed:
        return "mixed"
    if has_empty:
        return "empty"
    if has_failed:
        return "failed"
    return "no_tools"


def _sequence_or_mapping_is_empty(value: Any) -> bool:
    return isinstance(value, (list, tuple, dict, set)) and len(value) == 0


def _is_failed_or_empty_data_fetch(result: Any) -> bool:
    """Return True when a data-fetch result has no citeable payload.

    This intentionally includes soft failures such as timeouts and retry
    budget exhaustion.  They should not disable the tool immediately, but
    they must suppress later synthetic Python substitutions in the same
    turn.
    """
    if not isinstance(result, dict):
        return False
    status_tokens: list[str] = []
    for key in ("analysis_status", "__tool_status__", "status", "data_origin"):
        value = result.get(key)
        if isinstance(value, str):
            status_tokens.append(value.strip().upper())

    err_str = str(result.get("error") or "").lower()
    err_class = str(result.get("error_class") or "").lower()
    message_to_model = str(result.get("__message_to_model__") or "").lower()

    if any(s in {"EMPTY", "FAILED", "UNAVAILABLE"} for s in status_tokens):
        return True
    if result.get("success") is False or bool(result.get("error")):
        return True
    if result.get("row_count") == 0 or result.get("found") == 0:
        return True
    if "timeout" in err_str or "timed out" in err_str or "timeout" in err_class:
        return True
    if "retry budget" in err_str or "retry budget" in message_to_model:
        return True

    for key in ("data", "results", "rows"):
        if key in result and _sequence_or_mapping_is_empty(result.get(key)):
            return True
    return False


def _user_requested_synthetic_demo(messages: list[dict] | None) -> bool:
    text_parts: list[str] = []
    for message in messages or []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
    text = " ".join(text_parts).lower()
    return any(
        keyword in text
        for keyword in (
            "demonstrate",
            "demo",
            "example",
            "synthetic",
            "mock",
            "toy model",
            "show me how",
            "tutorial",
            "演示",
            "示例",
        )
    )


def _abstention_attrs_without_numeric_claims(attrs: dict) -> tuple[dict, list[str]]:
    """R2 hard line for the abstention path (audit 2026-07-03).

    The <tools_returned_nothing/> card path skips the claim validator by
    design, but the tag's `rationale` / `suggested_next_step` (and any other
    attribute) are MODEL-authored free text — a fabricated number there would
    ship under the '✓ Honest reply' banner on a zero-data turn (Pleiades
    F1.1 class: rationale="...the distance is 136.2 pc...").  Drop every
    attribute that contains a numeric claim; the platform-authored card prose
    renders without it.  extract_claims is deterministic (no LLM call).

    Returns (scrubbed copy, dropped attribute names).
    """
    from app.services.claim_validator import extract_claims

    clean: dict = {}
    dropped: list[str] = []
    for key, value in (attrs or {}).items():
        if isinstance(value, str) and value.strip() and extract_claims(value):
            dropped.append(key)
            continue
        clean[key] = value
    return clean, dropped


def _render_abstention_card(attrs: dict, reason: str) -> str:
    """F2.3: canonical Markdown card rendered from the abstention tag.
    The model does NOT write this prose — we do, so we control the
    quality and tone.
    """
    # Enforce the docstring at the choke point: attribute text is
    # model-authored, so numeric claims must never reach the card
    # (audit 2026-07-03; callers may pass raw parsed attrs).
    attrs, numeric_attrs_dropped = _abstention_attrs_without_numeric_claims(attrs)
    failed = (attrs.get("failed_tools") or "").strip()
    empty = (attrs.get("empty_tools") or "").strip()
    rationale = (attrs.get("rationale") or "").strip()
    next_step = (attrs.get("suggested_next_step") or "").strip()

    header_map = {
        "empty": "✓ Honest reply — tools returned no data",
        "failed": "✓ Honest reply — tools failed to run",
        "mixed": "✓ Honest reply — tools returned no data and some failed",
        "no_tools": "✓ Honest reply — no claims to make",
    }
    header = header_map.get(reason, header_map["no_tools"])

    lines = [f"**{header}**", ""]
    if failed:
        lines.append(f"**Failed tools:** `{failed}`")
    if empty:
        lines.append(f"**Empty tools:** `{empty}`")
    if failed or empty:
        lines.append("")
    if rationale:
        lines.append(f"_{rationale}_")
        lines.append("")
    if next_step:
        lines.append(f"**Suggested next step:** {next_step}")
    if not rationale and not next_step:
        lines.append(
            "No numerical claims are made because no tool produced data "
            "this turn.  Please rephrase your question, provide target "
            "values explicitly, or try the suggested next step above."
        )
    if numeric_attrs_dropped:
        lines.append("")
        lines.append(
            "_Model-supplied details were withheld: they contained numeric "
            "claims that no tool produced this turn._"
        )
    return "\n".join(lines)


def _sanitize_tools_returned_nothing(reply: str) -> str:
    import re

    text = str(reply or "")
    if "<tools_returned_nothing" not in text and "<toolsreturnednothing" not in text:
        return text

    failed = ""
    rationale = ""
    next_step = ""
    for attr, target in (
        ("failed_tools", "failed"),
        ("failedtools", "failed"),
        ("rationale", "rationale"),
        ("suggested_next_step", "next"),
        ("suggestednext_step", "next"),
        ("suggestednextstep", "next"),
    ):
        match = re.search(attr + r"=[\"']([^\"']*)[\"']", text, re.I)
        if not match:
            continue
        if target == "failed":
            failed = match.group(1)
        elif target == "rationale":
            rationale = match.group(1)
        elif target == "next":
            next_step = match.group(1)

    def _user_safe_detail(value: str) -> str:
        safe = str(value or "")
        replacements = {
            "extract_literature_tables": "table extraction",
            "extractliteraturetables": "table extraction",
            "search_literature": "literature search",
            "searchliterature": "literature search",
            "read_arxiv_paper": "paper reader",
            "readarxivpaper": "paper reader",
            "fit_line_lfr": "line-relation fitting",
            "fitlinelfr": "line-relation fitting",
            "line_measurements": "line measurements",
            "linemeasurements": "line measurements",
            "run_python": "analysis-code execution",
            "runpython": "analysis-code execution",
        }
        for needle, replacement in replacements.items():
            safe = re.sub(re.escape(needle), replacement, safe, flags=re.I)
        safe = re.sub(r"\bfailedtools\b", "failed steps", safe, flags=re.I)
        safe = re.sub(r"\bemptytools\b", "empty steps", safe, flags=re.I)
        safe = re.sub(r"\bsuggestednext_?step\b", "suggested next step", safe, flags=re.I)
        safe = re.sub(r"</?tools_?returned_?nothing[^>]*>", "", safe, flags=re.I)
        return re.sub(r"\s+", " ", safe).strip()

    failed = _user_safe_detail(failed)
    rationale = _user_safe_detail(rationale)
    next_step = _user_safe_detail(next_step)

    parts = [
        "I could not complete the requested analysis with the data steps that succeeded this turn."
    ]
    if rationale:
        parts.append(rationale)
    if failed:
        parts.append(f"Unavailable or failed data step(s): {failed}.")
    if next_step:
        parts.append(f"Suggested next step: {next_step}")
    parts.append(
        "I am not reporting unsupported numerical conclusions because the required tool-backed data were not available."
    )
    return "\n\n".join(parts)
