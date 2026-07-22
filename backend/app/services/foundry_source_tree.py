"""Canonical source-tree receipts shared by Draft, Validation, and Formal CI.

The hash is independent of tar metadata and filesystem mtimes.  It covers the
exact Git index: sorted UTF-8 path, Git mode, SHA-256 of blob bytes, and byte
length.  Draft CI stages a checked patch before hashing; Formal CI hashes a
clean checkout of the approved commit with the same function.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


SOURCE_MANIFEST_SCHEMA = "standard_astro_tracked_source_manifest_v1"
_ALLOWED_MODES = frozenset({"100644", "100755"})


class FoundrySourceTreeError(ValueError):
    """The checkout cannot produce a safe, canonical source receipt."""


def _repository_git_dir(repo: Path) -> Path:
    marker = repo / ".git"
    if marker.is_dir() and not marker.is_symlink():
        return marker.resolve()
    if not marker.is_file() or marker.is_symlink():
        raise FoundrySourceTreeError("source_git_metadata_invalid")
    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise FoundrySourceTreeError("source_git_metadata_invalid") from exc
    prefix = "gitdir: "
    if not raw.startswith(prefix) or "\n" in raw:
        raise FoundrySourceTreeError("source_git_metadata_invalid")
    git_dir = Path(raw[len(prefix) :])
    if not git_dir.is_absolute():
        git_dir = marker.parent / git_dir
    git_dir = git_dir.resolve()
    if not git_dir.is_dir():
        raise FoundrySourceTreeError("source_git_metadata_invalid")
    return git_dir


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    git_dir = _repository_git_dir(repo)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    try:
        completed = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                f"--git-dir={git_dir}",
                f"--work-tree={repo}",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.untrackedCache=false",
                "-c",
                "core.ignoreStat=false",
                *args,
            ],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            cwd=repo,
            env=environment,
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
    manifest = tracked_source_manifest(repo)
    _assert_worktree_matches_index(repo, manifest)
    _assert_index_flags_safe(repo, manifest)
    # Pin untracked-file visibility instead of trusting the repository-local
    # ``status.showUntrackedFiles`` setting.  Otherwise a checkout can look
    # clean while untracked Python source remains outside the source receipt.
    status = _git(
        repo,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
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


def _assert_index_flags_safe(repo: Path, manifest: dict[str, Any]) -> None:
    raw = _git(repo, "ls-files", "-v", "-z")
    paths: set[str] = set()
    for record in (item for item in raw.split(b"\0") if item):
        if len(record) < 3 or record[1:2] != b" ":
            raise FoundrySourceTreeError("source_index_flags_invalid")
        # ``H`` is the ordinary cached-entry marker.  Lower-case tags indicate
        # assume-unchanged and ``S`` indicates skip-worktree; both can hide
        # modified runtime bytes from status and must fail closed.
        if record[:1] != b"H":
            raise FoundrySourceTreeError("source_index_flags_unsafe")
        try:
            path = record[2:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FoundrySourceTreeError("source_index_flags_invalid") from exc
        if path in paths:
            raise FoundrySourceTreeError("source_index_flags_invalid")
        paths.add(path)
    expected_paths = {str(item["path"]) for item in manifest["entries"]}
    if paths != expected_paths:
        raise FoundrySourceTreeError("source_index_flags_invalid")


def _assert_worktree_matches_index(repo: Path, manifest: dict[str, Any]) -> None:
    for entry in manifest["entries"]:
        path = repo / str(entry["path"])
        mode = str(entry["mode"])
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise FoundrySourceTreeError("source_worktree_mismatch")
            content = path.read_bytes()
            if os.name != "nt" and bool(metadata.st_mode & 0o111) != (
                mode == "100755"
            ):
                raise FoundrySourceTreeError("source_worktree_mismatch")
        except FoundrySourceTreeError:
            raise
        except OSError as exc:
            raise FoundrySourceTreeError("source_worktree_mismatch") from exc
        if (
            len(content) != entry["bytes"]
            or hashlib.sha256(content).hexdigest() != entry["sha256"]
        ):
            raise FoundrySourceTreeError("source_worktree_mismatch")


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
