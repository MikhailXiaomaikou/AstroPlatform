"""Local-only AI assistant diagnostic bundles.

Bundles are meant for reproducing assistant failures on the developer's
machine.  They are deliberately filesystem-local, gitignored, and sanitized:
no API keys, cookies, bearer tokens, or long raw payloads should be written.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|bearer|cookie|password|secret|token|jwt|session)",
    re.I,
)
DEFAULT_MAX_STRING = 2000
DEFAULT_MAX_LIST = 50


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def sanitize_for_diagnostic(
    value: Any,
    *,
    max_string: int = DEFAULT_MAX_STRING,
    max_list: int = DEFAULT_MAX_LIST,
    _depth: int = 0,
) -> Any:
    """Return a JSON-safe, redacted copy suitable for local debug storage."""
    if _depth > 8:
        return "[depth-limit]"
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if SENSITIVE_KEY_RE.search(key_str):
                clean[key_str] = "[REDACTED]"
            else:
                clean[key_str] = sanitize_for_diagnostic(
                    item,
                    max_string=max_string,
                    max_list=max_list,
                    _depth=_depth + 1,
                )
        return clean
    if isinstance(value, list):
        items = [
            sanitize_for_diagnostic(
                item,
                max_string=max_string,
                max_list=max_list,
                _depth=_depth + 1,
            )
            for item in value[:max_list]
        ]
        if len(value) > max_list:
            items.append(f"[truncated {len(value) - max_list} items]")
        return items
    if isinstance(value, tuple):
        return sanitize_for_diagnostic(list(value), max_string=max_string, max_list=max_list, _depth=_depth)
    if isinstance(value, str):
        if len(value) > max_string:
            return value[:max_string] + f"... [truncated {len(value) - max_string} chars]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def build_diagnostic_bundle(
    *,
    prompt: str,
    model_config: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    final_reply: str = "",
    validator_results: dict[str, Any] | None = None,
    frontend_summary: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Build a sanitized local replay bundle."""
    raw_tool_results = tool_results or []
    return {
        "schema_version": 1,
        "bundle_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "prompt": sanitize_for_diagnostic(prompt),
        "model_config": sanitize_for_diagnostic(model_config or {}),
        "tool_timeline": sanitize_for_diagnostic(raw_tool_results),
        "tool_count": len(raw_tool_results),
        "final_reply": sanitize_for_diagnostic(final_reply),
        "validator_results": sanitize_for_diagnostic(validator_results or {}),
        "frontend_summary": sanitize_for_diagnostic(frontend_summary or {}),
        "notes": sanitize_for_diagnostic(notes),
        "local_only": True,
        "upload_policy": "never_upload_automatically",
    }


def write_diagnostic_bundle(bundle: dict[str, Any], output_dir: str | Path | None = None) -> Path:
    """Write a bundle under a gitignored local diagnostics directory."""
    root = Path(output_dir) if output_dir is not None else _repo_root() / ".local" / "diagnostics"
    root.mkdir(parents=True, exist_ok=True)
    bundle_id = str(bundle.get("bundle_id") or uuid.uuid4())
    path = root / f"{bundle_id}.json"
    path.write_text(json.dumps(sanitize_for_diagnostic(bundle), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def replay_diagnostic_bundle(path: str | Path) -> dict[str, Any]:
    """Replay local validator decisions from a diagnostic bundle.

    This does not call external archives or LLMs.  It re-runs the local
    provenance/narrative validators against captured tool outputs.
    """
    from app.services.claim_validator import (
        provenance_citation_violations,
        unsupported_literature_narrative_violations,
        validate_claims,
    )

    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    reply = str(bundle.get("final_reply") or "")
    tool_results = bundle.get("tool_timeline") or []
    numeric = validate_claims(reply, tool_results)
    citation = provenance_citation_violations(reply, tool_results)
    unsupported = unsupported_literature_narrative_violations(reply, tool_results)
    return {
        "bundle_id": bundle.get("bundle_id"),
        "numeric_ok": numeric.ok,
        "uncited_numeric_claims": [claim.__dict__ for claim in numeric.uncited],
        "citation_violations": [violation.__dict__ for violation in citation],
        "unsupported_narrative_violations": [violation.__dict__ for violation in unsupported],
        "would_withhold": bool(numeric.uncited or unsupported),
    }
