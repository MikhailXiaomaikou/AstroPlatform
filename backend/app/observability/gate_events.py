"""Structured validation-gate events — the false-positive measurement layer.

Every time a reply gate in chat._run_agent_loop intervenes (hard block,
regeneration, or downgrade to a deterministic tool-grounded summary), one
structured event is built here and (a) appended to a local JSONL file and
(b) emitted through the SSE on_event stream. The JSONL is the durable triage
sink for local/dev runs and blind-test artifacts; on Render there is NO
persistent disk, so production JSONL is ephemeral — the durable production
signal is the gate_event_total{gate,action} counter. Deep triage (which gate
fired, on what phrase, with what draft) is expected to happen on local runs
and on blind-test case_<id>.json artifacts, which capture the SSE events.

The two sinks do NOT carry the same text (2026-09-03 review, BLOCKER). The
event a gate builds quotes the pre-gate draft, which is exactly the text the
honesty gates just refused to publish. The local JSONL sink keeps that draft
in full: it is gitignored (`data/gate_events.jsonl`), it never leaves the
machine, and reading the observed evidence verbatim is the only way to
diagnose a false kill — the 9f2667e class of bug. The copy that leaves the
process — on_event -> chat.py SSE -> the browser, and on_event -> the blind
runner's `events` list -> `case_<id>.json` on disk — must instead go through
`redact_event_for_wire` so a withheld value does not exit on the same
connection the reply was gated for.

Why this exists: the anchor-gate false positive fixed in commit 9f2667e
(honest "Planck18 (H0=67.36)" declarations hard-blocked as anchor laundering)
survived undetected precisely because gate decisions left no structured
trace — fabrication_stats is function-local and discarded. This module makes
every intervention measurable so false positives can be found and counted.

Stdlib-only, mirroring metrics.py's dependency discipline.
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GATE_EVENT_TYPE = "gate_event"

_PREVIEW_CHARS = 800
_DETAIL_CHARS = 300

_jsonl_lock = threading.Lock()


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


def _clip_details(node: Any, limit: int = _DETAIL_CHARS) -> Any:
    if isinstance(node, dict):
        return {k: _clip_details(v, limit) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_clip_details(v, limit) for v in node]
    return _clip(node, limit)


def claims_to_dicts(claims: Any) -> list[dict]:
    """Serialize claim_validator.Claim objects (label/value/raw) defensively."""
    out: list[dict] = []
    for claim in claims or []:
        try:
            out.append({
                "label": str(getattr(claim, "label", "")),
                "value": getattr(claim, "value", None),
                "raw": _clip(str(getattr(claim, "raw", "")), _DETAIL_CHARS),
            })
        except Exception:
            continue
    return out


def violations_to_dicts(violations: Any) -> list[dict]:
    """Serialize claim_validator.CitationViolation objects defensively."""
    out: list[dict] = []
    for violation in violations or []:
        try:
            out.append({
                "kind": str(getattr(violation, "kind", "")),
                "match_text": _clip(str(getattr(violation, "match_text", "")), _DETAIL_CHARS),
                "line_number": getattr(violation, "line_number", None),
            })
        except Exception:
            continue
    return out


def build_gate_event(
    *,
    gate: str,
    action: str,
    reason: str = "",
    agent: str = "",
    details: dict | None = None,
    tools_run: list[str] | None = None,
    universe_size: int | None = None,
    regenerations: int = 0,
    draft: str = "",
    final: str = "",
    chat_session_id: str | None = None,
    python_session_id: str | None = None,
) -> dict:
    """One structured record of a gate intervention.

    action vocabulary: "blocked" (hard block banner), "regenerated" /
    "regenerated_clean" (LLM rewrite accepted), "downgraded_summary"
    (deterministic tool-grounded summary replaced the prose),
    "annotated_limited" (original prose kept, violation footer appended),
    "synthesized" (empty model reply filled by a deterministic summary).
    """
    return {
        "type": GATE_EVENT_TYPE,
        "schema_version": 1,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gate": gate,
        "action": action,
        "reason": reason,
        "agent": agent,
        "details": _clip_details(details or {}),
        "tools_run": sorted(tools_run or []),
        "universe_size": universe_size,
        "regenerations": regenerations,
        "draft_preview": _clip(str(draft or ""), _PREVIEW_CHARS),
        "final_preview": _clip(str(final or ""), _PREVIEW_CHARS),
        "chat_session_id": chat_session_id,
        "python_session_id": python_session_id,
    }


_WIRE_TEXT_FIELDS = ("draft_preview", "final_preview")
_WIRE_REDACTION_FAILED = "[withheld: redaction unavailable]"
_WIRE_REDACTED_NUMBER = "[withheld]"


# Keys whose sibling text carries the claim a numeric leaf belongs to.  A
# bare "123.456" has no parameter label, no pasted source and no withheld
# statistic in it, so the redactor cannot decide it on its own and returned
# it unchanged while the sibling `raw` was blanked (Codex review
# 2026-09-03).  The number is decided WITH its context instead.
_CLAIM_CONTEXT_KEYS = ("raw", "text", "snippet", "sentence", "claim", "label")
# Keys under which a numeric leaf IS a claim value (claims_to_dicts writes
# ``value``).  Only these leaves go through the redactor: a count, a line
# number or an iteration budget elsewhere in the event is not a claim, and
# ``details.value_count = 1`` shipped as "[withheld]" whenever a withheld
# statistic happened to be 1.0 (review thread e0fKr, 2026-09-04).
_CLAIM_VALUE_KEYS = ("value", "values", "number", "numbers")


def _redact_tree(
    node: Any,
    redact: Callable[[str], tuple[str, int]],
    context: str = "",
    path: tuple[str, ...] = (),
) -> Any:
    """``path`` is the chain of dictionary keys above ``node``; a list does
    not add to it, so a leaf in ``values: [...]`` still sits under ``values``."""
    if isinstance(node, str):
        return redact(node)[0]
    if isinstance(node, dict):
        # A claim record's own text is the context for its numeric leaves.
        own = " ".join(
            str(node[key]) for key in _CLAIM_CONTEXT_KEYS
            if isinstance(node.get(key), str)
        )
        nested = f"{context} {own}".strip() if own else context
        return {
            k: _redact_tree(v, redact, nested, (*path, str(k)))
            for k, v in node.items()
        }
    if isinstance(node, (list, tuple)):
        return [_redact_tree(v, redact, context, path) for v in node]
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if not any(key in _CLAIM_VALUE_KEYS for key in path):
            # Not a claim value: a count, a line number, an iteration budget.
            return node
        # A blocking gate reports the offending claim as BOTH a raw snippet
        # and a parsed number (claim_validator.Claim.value), so redacting only
        # the strings would ship the withheld value as a JSON float one key
        # over. Round-trip the number through the same redactor: it reports a
        # hit on a bare numeric token, and a leaf it does not touch is left as
        # the original number, not stringified.
        rendered = repr(node)
        if redact(rendered)[0] != rendered:
            return _WIRE_REDACTED_NUMBER
        if context:
            # Decide the number inside its own claim text: "H0 = 123.456"
            # carries the label the bare leaf lacks.
            probe = f"{context} {rendered}"
            if redact(probe)[0] != probe:
                return _WIRE_REDACTED_NUMBER
        return node
    return node


def redact_event_for_wire(
    event: dict,
    redact: Callable[[str], tuple[str, int]],
) -> dict:
    """Copy of ``event`` with the model's prose stripped of withheld values.

    Feed this to on_event; feed the untouched ``event`` to
    ``append_gate_event_jsonl``.  ``draft_preview`` quotes the reply as the
    model wrote it, before any gate ran, so on a turn where a gate withheld
    an exploratory posterior or an untrusted pasted value, that value is
    still sitting in the event.  The local JSONL sink needs it (false-kill
    triage means reading the observed evidence); every consumer outside this
    process must not have it.

    ``redact`` is injected rather than imported so this module keeps its
    stdlib-only discipline and stays free of the honesty gates' signature:
    the redactor needs the turn's ``messages`` and tool results, which are
    agent-loop state, not observability state.  The loop passes a closure
    over ``honesty.redact_gated_values``, which blanks only spans whose token
    value the gates actually reported as a hit — years, arXiv ids and request
    parameters survive byte-for-byte, so a redacted preview is still readable.

    Redaction covers ``details`` too, not only the two preview fields: a
    blocking gate serializes ``claim_validator.Claim`` objects into
    ``details["claims"]``, and each carries both a ``raw`` snippet lifted
    from the same draft and a parsed numeric ``value``.  Both are redacted;
    ``gate``/``action``/``reason``/ids are routing metadata and are left
    alone so the event still triages.

    Fails closed: if the redactor raises, the prose is dropped entirely
    rather than shipped raw.
    """
    wire = dict(event)
    try:
        for field in _WIRE_TEXT_FIELDS:
            if wire.get(field):
                wire[field] = redact(str(wire[field]))[0]
        if wire.get("details"):
            wire["details"] = _redact_tree(wire["details"], redact)
    except Exception as exc:
        logger.debug("gate-event wire redaction failed, dropping prose: %s", exc)
        for field in _WIRE_TEXT_FIELDS:
            if wire.get(field):
                wire[field] = _WIRE_REDACTION_FAILED
        if wire.get("details"):
            wire["details"] = {"redaction_failed": True}
    return wire


def append_gate_event_jsonl(event: dict) -> None:
    """Append one event line to the configured JSONL sink. Never raises."""
    try:
        from app.config import settings

        raw_path = str(getattr(settings, "gate_events_jsonl_path", "") or "").strip()
        if not raw_path:
            return
        path = Path(raw_path)
        with _jsonl_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        logger.debug("gate-event JSONL append failed: %s", exc)
