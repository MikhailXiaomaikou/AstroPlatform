#!/usr/bin/env python3
"""Local maintenance inbox CLI.

This script is intentionally local-only: it never edits source files, pushes,
deploys, uploads diagnostics, or creates remote issues.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_backend_path() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_dir))


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_report(args: argparse.Namespace) -> int:
    _bootstrap_backend_path()
    from app.services.maintenance import (
        build_maintenance_report,
        render_markdown_report,
        write_maintenance_report,
    )

    report = build_maintenance_report(
        diagnostics_dir=args.diagnostics_dir,
        diagnostic_limit=args.limit,
    )
    paths = None
    if args.write:
        paths = write_maintenance_report(report, args.output_dir)
    if args.json:
        payload = {"report": report, "written": paths}
        _print_json(payload)
    else:
        print(render_markdown_report(report))
        if paths:
            print(f"Written JSON: {paths['json']}")
            print(f"Written Markdown: {paths['markdown']}")
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    _bootstrap_backend_path()
    from app.services.maintenance import acquire_lock, read_lock, release_lock

    if args.action == "status":
        _print_json(read_lock())
        return 0
    if args.action == "acquire":
        result = acquire_lock(owner=args.owner, force=args.force)
        _print_json(result)
        return 0 if result.get("acquired") else 1
    if args.action == "release":
        result = release_lock(owner=args.owner, force=args.force)
        _print_json(result)
        return 0 if result.get("released") else 1
    raise AssertionError(args.action)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Standard Astro maintenance inbox")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Collect local maintenance evidence and render a report")
    report.add_argument("--diagnostics-dir", help="Directory containing local diagnostic JSON bundles")
    report.add_argument("--limit", type=int, default=10, help="Maximum diagnostic bundles to inspect")
    report.add_argument("--output-dir", help="Where to write reports when --write is set")
    report.add_argument("--write", action="store_true", help="Write JSON + Markdown under .local/maintenance")
    report.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    report.set_defaults(func=cmd_report)

    lock = sub.add_parser("lock", help="Coordinate local maintenance agents")
    lock.add_argument("action", choices=("status", "acquire", "release"))
    lock.add_argument("--owner", default="codex", help="Owner name for acquire/release")
    lock.add_argument("--force", action="store_true", help="Override stale/mismatched locks")
    lock.set_defaults(func=cmd_lock)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
