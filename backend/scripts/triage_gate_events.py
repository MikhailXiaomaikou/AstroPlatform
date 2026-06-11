"""Aggregate validation-gate events for false-positive triage.

Reads gate events from either/both:
  --jsonl <path>     the JSONL sink written by app/observability/gate_events.py
                     (default: the settings default backend/data/gate_events.jsonl)
  --results <dir>    a blind-test results directory — scans case_*.json artifacts'
                     "events" arrays for type=="gate_event" records

Prints counts by (gate, action, reason), the most frequent trigger phrases
(claim labels / violation match_text), and with --verbose one line per event
with draft/final previews. Always exits 0 — this is a triage report, not a CI
gate. Typical loop: run the blind suite or collect a day of local traffic,
then eyeball which gates intervene on turns that LOOK healthy (tools ran,
action=downgraded_summary) — those are the false-positive candidates.

Usage (from backend/):
    venv/bin/python scripts/triage_gate_events.py
    venv/bin/python scripts/triage_gate_events.py --results scripts/blind_test_cosmology_m0/results_20260611T120000 --verbose
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _default_jsonl() -> str:
    sys.path.insert(0, str(_BACKEND_ROOT))
    try:
        from app.config import settings

        return str(getattr(settings, "gate_events_jsonl_path", "") or "")
    except Exception:
        return str(_BACKEND_ROOT / "data" / "gate_events.jsonl")


def _load_jsonl(path: Path) -> list[dict]:
    events: list[dict] = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "gate_event":
            events.append(obj)
    return events


def _load_results_dir(results_dir: Path) -> list[dict]:
    events: list[dict] = []
    for case_file in sorted(results_dir.glob("case_*.json")):
        try:
            record = json.loads(case_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        for evt in record.get("events") or []:
            if isinstance(evt, dict) and evt.get("type") == "gate_event":
                evt = dict(evt)
                evt.setdefault("_case_id", record.get("case_id"))
                events.append(evt)
    return events


def _trigger_phrases(evt: dict) -> list[str]:
    phrases: list[str] = []
    details = evt.get("details") or {}
    for claim in details.get("claims") or []:
        if isinstance(claim, dict):
            phrases.append(str(claim.get("raw") or claim.get("label") or ""))
    for violation in details.get("violations") or []:
        if isinstance(violation, dict):
            phrases.append(str(violation.get("match_text") or violation.get("kind") or ""))
    return [p for p in phrases if p]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", type=str, default=None,
                    help="gate-event JSONL path (default: settings.gate_events_jsonl_path)")
    ap.add_argument("--results", type=str, default=None,
                    help="blind-test results dir to scan for case_*.json gate events")
    ap.add_argument("--verbose", action="store_true",
                    help="print one line per event with previews")
    ap.add_argument("--top", type=int, default=10, help="top-N trigger phrases")
    args = ap.parse_args()

    events: list[dict] = []
    jsonl_path = Path(args.jsonl) if args.jsonl else Path(_default_jsonl() or "")
    if str(jsonl_path):
        loaded = _load_jsonl(jsonl_path)
        if loaded:
            print(f"[jsonl] {len(loaded)} event(s) from {jsonl_path}")
        events.extend(loaded)
    if args.results:
        loaded = _load_results_dir(Path(args.results))
        print(f"[results] {len(loaded)} event(s) from {args.results}")
        events.extend(loaded)

    if not events:
        print("No gate events found. (Either the gates never intervened — good — "
              "or the sinks are empty/not configured.)")
        return 0

    print(f"\n== {len(events)} gate event(s) ==\n")
    by_key = Counter((e.get("gate"), e.get("action"), e.get("reason") or "-") for e in events)
    width = max(len(f"{g}/{a}/{r}") for (g, a, r) in by_key)
    for (gate, action, reason), n in by_key.most_common():
        print(f"  {f'{gate}/{action}/{reason}':<{width}}  {n}")

    phrase_counter: Counter = Counter()
    for evt in events:
        phrase_counter.update(_trigger_phrases(evt))
    if phrase_counter:
        print(f"\n== top {args.top} trigger phrases ==\n")
        for phrase, n in phrase_counter.most_common(args.top):
            print(f"  {n:>3}x  {phrase[:120]}")

    if args.verbose:
        print("\n== events ==\n")
        for evt in events:
            case = f" case={evt.get('_case_id')}" if evt.get("_case_id") else ""
            print(f"- [{evt.get('ts')}] {evt.get('gate')}/{evt.get('action')}"
                  f"/{evt.get('reason') or '-'} agent={evt.get('agent')}"
                  f" tools={evt.get('tools_run')}{case}")
            if evt.get("draft_preview"):
                print(f"    draft: {str(evt['draft_preview'])[:200]!r}")
            if evt.get("final_preview"):
                print(f"    final: {str(evt['final_preview'])[:200]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
