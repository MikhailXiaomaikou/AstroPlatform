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
