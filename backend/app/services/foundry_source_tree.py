"""Canonical source-tree receipts shared by Draft, Validation, and Formal CI.

The hash is independent of tar metadata and filesystem mtimes.  It covers the
exact Git index: sorted UTF-8 path, Git mode, SHA-256 of blob bytes, and byte
length.  Draft CI stages a checked patch before hashing; Formal CI hashes a
clean checkout of the approved commit with the same function.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


SOURCE_MANIFEST_SCHEMA = "standard_astro_tracked_source_manifest_v1"
_ALLOWED_MODES = frozenset({"100644", "100755", "120000"})


class FoundrySourceTreeError(ValueError):
    """The checkout cannot produce a safe, canonical source receipt."""


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FoundrySourceTreeError("source_git_unavailable") from exc
    if completed.returncode != 0:
        raise FoundrySourceTreeError("source_git_command_failed")
    return completed.stdout


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def git_commit(repo_root: str | Path) -> str:
    repo = Path(repo_root).resolve()
    value = _git(repo, "rev-parse", "HEAD").decode("ascii").strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise FoundrySourceTreeError("source_commit_invalid")
    return value


def assert_clean_checkout(
    repo_root: str | Path, *, allow_staged_changes: bool = False
) -> None:
    repo = Path(repo_root).resolve()
    status = _git(repo, "status", "--porcelain=v1", "-z")
    if not status:
        return
    if allow_staged_changes:
        records = [record for record in status.split(b"\0") if record]
        # A Draft patch must be fully staged.  Worktree-only changes (second
        # status column) and untracked files would escape the index manifest.
        if all(
            len(record) >= 4
            and record[:2] != b"??"
            and record[1:2] == b" "
            for record in records
        ):
            return
    raise FoundrySourceTreeError("source_checkout_not_clean")


def tracked_source_manifest(repo_root: str | Path) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    raw = _git(repo, "ls-files", "--stage", "-z")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in (item for item in raw.split(b"\0") if item):
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode_bytes, oid_bytes, stage_bytes = metadata.split(b" ", 2)
            mode = mode_bytes.decode("ascii")
            oid = oid_bytes.decode("ascii")
            stage = stage_bytes.decode("ascii")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise FoundrySourceTreeError("source_index_entry_invalid") from exc
        if stage != "0" or mode not in _ALLOWED_MODES:
            raise FoundrySourceTreeError("source_index_mode_unsupported")
        if (
            not path
            or path.startswith("/")
            or "\x00" in path
            or any(part in {"", ".", "..", ".git"} for part in path.split("/"))
            or path in seen
        ):
            raise FoundrySourceTreeError("source_index_path_invalid")
        blob = _git(repo, "cat-file", "blob", oid)
        entries.append(
            {
                "path": path,
                "mode": mode,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
            }
        )
        seen.add(path)
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    return {"schema": SOURCE_MANIFEST_SCHEMA, "entries": entries}


def tracked_source_tree_hash(repo_root: str | Path) -> tuple[str, dict[str, Any]]:
    manifest = tracked_source_manifest(repo_root)
    return hashlib.sha256(_canonical(manifest)).hexdigest(), manifest


__all__ = [
    "FoundrySourceTreeError",
    "SOURCE_MANIFEST_SCHEMA",
    "assert_clean_checkout",
    "git_commit",
    "tracked_source_manifest",
    "tracked_source_tree_hash",
]
