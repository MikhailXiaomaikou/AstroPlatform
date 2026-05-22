"""PART AI Phase 2 — SYSTEM_PROMPT loader infrastructure (no flow switch).

Phase 2 of PART AI follow-up: introduce a per-section Markdown loader
for the 1848-line / 111 KB SYSTEM_PROMPT monolith in chat.py. This
commit lands the loader and tests its contract; the production
SYSTEM_PROMPT continues to use the monolith string until per-section
migration commits move each section into `prompts/sections/*.md`.

Locks 4 contracts:

1. Loader exists, importable, returns "" when sections/ is empty.
2. Files starting with `_` (drafts, README) are skipped by the loader.
3. Files load in lexicographic order — naming convention is `NN_topic.md`.
4. archive_manifest substitution works the same as the monolith does.
"""

from __future__ import annotations




# ── Contract 1: empty sections directory ──────────────────────────────


def test_assemble_system_prompt_returns_empty_when_only_drafts(tmp_path, monkeypatch) -> None:
    """When sections/ has only `_draft_*` files, the loader should
    return empty (caller falls back to the chat.py monolith)."""
    from app.api import prompts as prompts_mod

    fake_sections = tmp_path / "sections"
    fake_sections.mkdir()
    (fake_sections / "_draft_xxx.md").write_text("# draft only\n")
    (fake_sections / "_README.md").write_text("# readme\n")

    monkeypatch.setattr(prompts_mod, "_SECTIONS_DIR", fake_sections)

    assert prompts_mod.assemble_system_prompt() == ""
    assert prompts_mod.list_section_files() == []


# ── Contract 2: real files load + concatenate in order ────────────────


def test_assemble_system_prompt_loads_files_in_lexicographic_order(
    tmp_path, monkeypatch,
) -> None:
    from app.api import prompts as prompts_mod

    fake_sections = tmp_path / "sections"
    fake_sections.mkdir()
    (fake_sections / "10_third.md").write_text("# Third\n\nBody C.\n")
    (fake_sections / "01_first.md").write_text("# First\n\nBody A.\n")
    (fake_sections / "05_second.md").write_text("# Second\n\nBody B.\n")

    monkeypatch.setattr(prompts_mod, "_SECTIONS_DIR", fake_sections)

    composed = prompts_mod.assemble_system_prompt()
    files = prompts_mod.list_section_files()

    assert files == ["01_first.md", "05_second.md", "10_third.md"]
    # must be concatenated in filename sort order
    assert composed.find("Body A.") < composed.find("Body B.")
    assert composed.find("Body B.") < composed.find("Body C.")


def test_assemble_system_prompt_skips_underscore_prefixed() -> None:
    """In the production sections/ directory, _README.md is a comment file and must not be included in the prompt."""
    from app.api import prompts as prompts_mod

    files = prompts_mod.list_section_files()
    for name in files:
        assert not name.startswith("_"), (
            f"loader leaked draft file {name!r} into production prompt"
        )


# ── Contract 3: archive_manifest substitution ─────────────────────────


def test_archive_manifest_placeholder_substitution(tmp_path, monkeypatch) -> None:
    """The `__ARCHIVE_MANIFEST__` literal must be replaced by the archive_manifest parameter —
    consistent with chat.py's existing SYSTEM_PROMPT.replace("__ARCHIVE_MANIFEST__", ...)
    behaviour."""
    from app.api import prompts as prompts_mod

    fake_sections = tmp_path / "sections"
    fake_sections.mkdir()
    (fake_sections / "01_test.md").write_text(
        "Active archive versions: __ARCHIVE_MANIFEST__\n"
    )
    monkeypatch.setattr(prompts_mod, "_SECTIONS_DIR", fake_sections)

    composed = prompts_mod.assemble_system_prompt(
        archive_manifest="gaia=DR3, sdss=DR18, planck=2020"
    )
    assert "__ARCHIVE_MANIFEST__" not in composed
    assert "gaia=DR3, sdss=DR18, planck=2020" in composed


def test_archive_manifest_none_leaves_placeholder(tmp_path, monkeypatch) -> None:
    """When archive_manifest=None (default), the placeholder should be preserved so the
    caller decides whether to substitute. Used for testing / inspecting un-substituted assembled results."""
    from app.api import prompts as prompts_mod

    fake_sections = tmp_path / "sections"
    fake_sections.mkdir()
    (fake_sections / "01_test.md").write_text("X = __ARCHIVE_MANIFEST__\n")
    monkeypatch.setattr(prompts_mod, "_SECTIONS_DIR", fake_sections)

    composed = prompts_mod.assemble_system_prompt(archive_manifest=None)
    assert "__ARCHIVE_MANIFEST__" in composed


# ── Contract 4: public API surface ─────────────────────────────────────


def test_module_public_surface() -> None:
    """__all__ must export these two helpers; otherwise downstream type checkers
    will report 'undefined attribute'."""
    from app.api import prompts as prompts_mod

    assert "assemble_system_prompt" in prompts_mod.__all__
    assert "list_section_files" in prompts_mod.__all__


# ── Contract 5: production sections/ directory shape ─────────────────


def test_production_sections_dir_exists_and_loader_runs() -> None:
    """The production sections/ directory must exist (even if currently empty) so the loader can load.
    As Phase 2 subsequent commits progressively migrate chat.py SYSTEM_PROMPT sections here,
    this directory will be gradually populated."""
    from app.api import prompts as prompts_mod

    assert prompts_mod._SECTIONS_DIR.is_dir()
    # loader must run without raising
    composed = prompts_mod.assemble_system_prompt()
    assert isinstance(composed, str)
    files = prompts_mod.list_section_files()
    assert isinstance(files, list)


def test_chat_module_can_use_loader_without_breaking_existing_keywords() -> None:
    """Key invariant: even with the loader in place, chat.py's SYSTEM_PROMPT string
    must remain unchanged so all existing keyword-asserting tests pass.
    This Phase 2 commit does not switch traffic; it only lands the skeleton."""
    from app.api.chat import SYSTEM_PROMPT

    # all keywords asserted by old tests must still be present
    must_present = [
        "ZERO-FABRICATION CONTRACT",
        "STRUCTURED ABSTENTION",
        "Multi-survey sample composition",
        "Cite-after-extract",
        "EXACT CALL EXAMPLES",
        "0 lensed sources detected",
        "Declare fit orientation and pivot",
    ]
    for keyword in must_present:
        assert keyword in SYSTEM_PROMPT, (
            f"SYSTEM_PROMPT lost {keyword!r} during Phase 2 loader land"
        )
