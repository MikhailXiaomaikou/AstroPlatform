#!/usr/bin/env python3
"""CLI for the canonical Foundry tracked-source manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--allow-staged-changes", action="store_true")
    args = parser.parse_args()

    backend_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(backend_root))
    from app.services.foundry_source_tree import (  # noqa: PLC0415
        assert_clean_checkout,
        git_commit,
        tracked_source_tree_hash,
    )

    assert_clean_checkout(
        args.repo, allow_staged_changes=args.allow_staged_changes
    )
    digest, manifest = tracked_source_tree_hash(args.repo)
    if args.manifest:
        Path(args.manifest).write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "schema": manifest["schema"],
                "git_commit": git_commit(args.repo),
                "source_tree_sha256": digest,
                "tracked_file_count": len(manifest["entries"]),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
