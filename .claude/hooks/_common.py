"""Shared helpers for the .claude/hooks PostToolUse check hooks.

Hooks run as `python3 .claude/hooks/<name>.py` with cwd = repo root; Python
puts the script's own directory on sys.path, so `from _common import ...`
works without packaging.

Delivery contract (the reason feedback() exits 2): PostToolUse stderr
reaches the model only on exit 2; on exit 0 it lands in the transcript and
the model never sees it.
"""
from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def read_hook_input() -> dict:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def edited_path(data: dict) -> Path | None:
    """Resolved absolute path of the edited file, or None."""
    file_path = (data.get("tool_input") or {}).get("file_path") or ""
    if not file_path:
        return None
    return Path(file_path).resolve()


def under(path: Path, root: Path) -> bool:
    """Case-insensitive containment check — the repo lives on case-insensitive
    APFS, where a wrong-case path still edits the real file but would fail an
    exact-case is_relative_to()."""
    import os

    p = str(path).lower().rstrip(os.sep)
    r = str(root).lower().rstrip(os.sep)
    return p == r or p.startswith(r + os.sep)


def feedback(message: str) -> int:
    """Report to the model: stderr + exit 2 (see module docstring)."""
    print(message, file=sys.stderr)
    return 2


def _stamp_file(key: str) -> Path:
    digest = hashlib.sha256(f"{REPO_ROOT}|{key}".encode()).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"claude-hook-{digest}"


def recently(key: str, seconds: float) -> bool:
    """True if stamp(key) was called within the last `seconds`."""
    try:
        return time.time() - _stamp_file(key).stat().st_mtime < seconds
    except OSError:
        return False


def stamp(key: str) -> None:
    try:
        _stamp_file(key).touch()
    except OSError:
        pass


def notice_debounced(key: str, seconds: float) -> bool:
    """True if this notice already fired within `seconds`; else stamp now and return False."""
    if recently(key, seconds):
        return True
    stamp(key)
    return False


def read_state(key: str) -> str | None:
    try:
        return _stamp_file(key).read_text().strip()
    except OSError:
        return None


def write_state(key: str, value: str) -> None:
    try:
        _stamp_file(key).write_text(value)
    except OSError:
        pass
