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
    # 必须按文件名排序拼接
    assert composed.find("Body A.") < composed.find("Body B.")
    assert composed.find("Body B.") < composed.find("Body C.")


def test_assemble_system_prompt_skips_underscore_prefixed() -> None:
    """生产用的 sections/ 目录里 _README.md 是注释, 不该进 prompt."""
    from app.api import prompts as prompts_mod

    files = prompts_mod.list_section_files()
    for name in files:
        assert not name.startswith("_"), (
            f"loader leaked draft file {name!r} into production prompt"
        )


# ── Contract 3: archive_manifest substitution ─────────────────────────


def test_archive_manifest_placeholder_substitution(tmp_path, monkeypatch) -> None:
    """`__ARCHIVE_MANIFEST__` 字面量必须被 archive_manifest 参数替换 —
    跟 chat.py 现有的 SYSTEM_PROMPT.replace("__ARCHIVE_MANIFEST__", ...)
    行为一致."""
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
    """当 archive_manifest=None 时 (默认), placeholder 应保留, 让 caller
    决定是否替换. 用于测试 / 检查未替换的拼接结果."""
    from app.api import prompts as prompts_mod

    fake_sections = tmp_path / "sections"
    fake_sections.mkdir()
    (fake_sections / "01_test.md").write_text("X = __ARCHIVE_MANIFEST__\n")
    monkeypatch.setattr(prompts_mod, "_SECTIONS_DIR", fake_sections)

    composed = prompts_mod.assemble_system_prompt(archive_manifest=None)
    assert "__ARCHIVE_MANIFEST__" in composed


# ── Contract 4: 公共 API surface ─────────────────────────────────────


def test_module_public_surface() -> None:
    """__all__ 必须 export 这两个 helper, 否则下游 type checker 会报
    'undefined attribute'."""
    from app.api import prompts as prompts_mod

    assert "assemble_system_prompt" in prompts_mod.__all__
    assert "list_section_files" in prompts_mod.__all__


# ── Contract 5: production sections/ directory shape ─────────────────


def test_production_sections_dir_exists_and_loader_runs() -> None:
    """生产 sections/ 目录必须存在 (即使现在还空), 让 loader 可以加载.
    随着 Phase 2 后续 commit 逐步把 chat.py SYSTEM_PROMPT 切到这里,
    这个目录会逐步填充."""
    from app.api import prompts as prompts_mod

    assert prompts_mod._SECTIONS_DIR.is_dir()
    # loader 能跑不 raise
    composed = prompts_mod.assemble_system_prompt()
    assert isinstance(composed, str)
    files = prompts_mod.list_section_files()
    assert isinstance(files, list)


def test_chat_module_can_use_loader_without_breaking_existing_keywords() -> None:
    """关键不变量: 即使 loader 已就绪, chat.py 的 SYSTEM_PROMPT 字符串
    必须保持原样, 现有所有 keyword-asserting test 全过. Phase 2 这一
    commit 不切流量, 仅落骨架."""
    from app.api.chat import SYSTEM_PROMPT

    # 旧测试断言的关键词必须全部还在
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
