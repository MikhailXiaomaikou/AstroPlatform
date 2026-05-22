# Refactor in progress — DO NOT block

This file tracks isolated, work-in-progress refactors that other agents
should NOT compete with. Edit this file before starting any other large
refactor that touches the same files.

---

## Active refactors

(none — the ai_tools.py split landed on 2026-05-21; see Completed refactors below.)

If you start another large refactor that touches multiple files / multiple agents, add a new "Active refactor: <name>" subsection here with owner, branch, worktree path, status, and a coordination contract similar to the completed entry below.

---

## Completed refactors

### Completed: `ai_tools.py` split (Phase 1)

- **Merged on** 2026-05-21 as commit `d17ca07` (`refactor(api): package large API and tool modules`).
- **Owner:** Claude (Anthropic Opus 4.7), started 2026-05-03.
- **Outcome:** The previously 9000+ line single-file `backend/app/services/ai_tools.py` is now the package `backend/app/services/ai_tools/`, with `__init__.py` re-exporting `TOOLS`, `execute_tool`, and the helper symbols so every `from app.services.ai_tools import X` call site continues to resolve unchanged. The followup commit `7e603a2` (`fix(ci): restore tool readiness and source mapping checks`) re-greened the CI tool-readiness + source-mapping assertions against the new layout.
- **Subsequent moves on top of the split:** `ai_tools_solar_system.py` (Solar System M0) and `ai_tools_exoplanet.py` (Exoplanet M0) were added alongside the package and are imported by `ai_tools/__init__.py` so the focus gate still sees one unified `TOOLS` list.
- **Coordination contract is no longer in force.** Structural edits to the package are unblocked; bug-fix-shaped changes can land directly on `main` like any other service module.
