"""Composable SYSTEM_PROMPT loader.

Phase 2 of PART AI follow-up: the SYSTEM_PROMPT in chat.py is currently
a 111 KB / 1848-line module-level triple-quoted string with 59
sections. That monolith is hard to review (a single-line edit pulls the
whole file's diff in code review) and has saturated review attention to
the point where new SYSTEM_PROMPT sections rarely get tested for
interaction with existing rules.

This package provides a loader that assembles SYSTEM_PROMPT from a set
of small Markdown files in `sections/`. Each file owns one logical
group (safety contracts, ADQL idioms, cluster workflows, etc.).

**This commit lands the loader infrastructure but does NOT switch the
production SYSTEM_PROMPT to use it.** The monolith in chat.py remains
the source of truth. Per-section migration is a follow-up where each
section move is a small, reviewable diff that preserves every
keyword-asserting test verbatim.

Migration discipline:
  1. Pick one section header, e.g. `## ZERO-FABRICATION CONTRACT`.
  2. Cut its full body into `sections/06_zero_fabrication.md`.
  3. Replace the body in chat.py SYSTEM_PROMPT with the loader's
     assembled string (or, simpler, leave chat.py monolith unchanged
     until ALL sections are migrated, then swap).
  4. Run the keyword-asserting test suites
     (test_admin_literature, test_h_regression, test_system_prompt_helpers,
     test_m6_methodology_validator, test_research_focus_gating). The
     keyword `"ZERO-FABRICATION CONTRACT"` must still resolve via
     `from app.api.chat import SYSTEM_PROMPT`.
"""

from __future__ import annotations

from pathlib import Path

_SECTIONS_DIR = Path(__file__).parent / "sections"


def _read_section_files() -> list[tuple[str, str]]:
    """Return [(filename, body), ...] sorted by filename.

    Filenames must follow `NN_topic.md` convention so lexicographic
    sort matches intended assembly order. Files starting with `_` are
    treated as drafts/scratch and skipped.
    """
    if not _SECTIONS_DIR.is_dir():
        return []
    entries: list[tuple[str, str]] = []
    for path in sorted(_SECTIONS_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        entries.append((path.name, body))
    return entries


def assemble_system_prompt(*, archive_manifest: str | None = None) -> str:
    """Compose the full SYSTEM_PROMPT from per-section Markdown files.

    Args:
        archive_manifest: optional replacement for the literal
            `__ARCHIVE_MANIFEST__` placeholder used by chat.py for
            archive version pins. Pass None to leave the placeholder
            untouched (useful for inspection / tests).

    Returns:
        Concatenated section bodies separated by a blank line. If the
        sections directory is empty, returns an empty string — caller
        should fall back to the legacy monolith in that case.
    """
    parts = [body.rstrip() for _, body in _read_section_files()]
    if not parts:
        return ""
    composed = "\n\n".join(parts) + "\n"
    if archive_manifest is not None:
        composed = composed.replace("__ARCHIVE_MANIFEST__", archive_manifest)
    return composed


def list_section_files() -> list[str]:
    """Return registered section filenames in load order.

    Used by tests + maintenance reporting to confirm migration progress.
    """
    return [name for name, _ in _read_section_files()]


__all__ = [
    "assemble_system_prompt",
    "list_section_files",
]
