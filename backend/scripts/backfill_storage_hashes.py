#!/usr/bin/env python3
"""Inventory or backfill SHA-256 sidecars for legacy local research objects.

Run without ``--apply`` first.  Symlinks and special files are rejected rather
than followed.  Hosted S3 objects should be migrated with provider-native copy
tooling that preserves version history; this command is local-storage only.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from app.config import settings
from app.storage import _atomic_write, _sidecar_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=settings.local_storage_dir)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        parser.error(f"storage root does not exist: {root}")

    unhashed: list[tuple[Path, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SystemExit(f"refusing symlink in storage root: {path}")
        if path.is_dir() or path.name.endswith(".sha256"):
            continue
        if not path.is_file():
            raise SystemExit(f"refusing special file in storage root: {path}")
        sidecar = _sidecar_path(path)
        digest = _sha256(path)
        if sidecar.is_file() and sidecar.read_text(encoding="ascii").strip() == digest:
            continue
        unhashed.append((path, digest))

    for path, digest in unhashed:
        relative = path.relative_to(root)
        if args.apply:
            _atomic_write(_sidecar_path(path), f"{digest}\n".encode("ascii"))
            print(f"hashed {relative} {digest}")
        else:
            print(f"needs-hash {relative} {digest}")

    print(f"objects_requiring_backfill={len(unhashed)} apply={args.apply}")
    return 0 if args.apply or not unhashed else 1


if __name__ == "__main__":
    raise SystemExit(main())
