"""Public-facing status prose must not drift from the repository state.

Phase 0 (2026-09-02): the project handbook points at the direction review and
the execution plan, and carries the instrument-first rule. Phase 0.3 extends
this file with README / HONESTY_EVIDENCE assertions once the scheduled-suite
status wording is rewritten.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_claude_md_points_at_direction_review_and_execution_plan() -> None:
    handbook = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for pointer in (
        "docs/research/STANDARD_ASTRO_REVIEW_2026-09-02.zh-CN.md",
        "plan/2026-09-02-execution-plan.md",
    ):
        assert pointer in handbook, pointer
        assert (REPO_ROOT / pointer).is_file(), pointer


def test_claude_md_carries_instrument_first_and_measure_first_rules() -> None:
    handbook = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Instrument-first" in handbook
    assert "Measure before engineering behaviour" in handbook
    assert "gh run list --workflow=daily.yml" in handbook
    assert "Weekly Scientific Validation" in handbook
