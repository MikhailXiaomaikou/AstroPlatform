# Refactor in progress — DO NOT block

This file tracks isolated, work-in-progress refactors that other agents
should NOT compete with. Edit this file before starting any other large
refactor that touches the same files.

---

## Active refactor: `ai_tools.py` split (Phase 1)

**Owner:** Claude (Anthropic Opus 4.7), starting 2026-05-03.
**Branch:** `refactor/ai-tools-split` in a separate git worktree at
`/Users/chenkexuan/Projects/astro-platform-refactor-ai-tools/` (NOT a
sibling clone — same repo, different worktree).
**Status:** WIP, NOT committed to `main`.
**Detailed plan:** `.local/PHASE1_AI_TOOLS_REFACTOR_PLAN.md` (local-only,
read it on the same machine).

### What this refactor does

`backend/app/services/ai_tools.py` is currently 9242 lines containing
75 tool registrations and 57 `_exec_*` implementations. It is split
into a package:

```
backend/app/services/ai_tools/
├── __init__.py       # re-exports all public API; existing imports unchanged
├── dispatcher.py     # execute_tool / _execute_tool_inner / shared helpers
├── cosmology.py      # ~10 cosmology tools
├── line_relations.py # 5 [CII] LFR / extract_literature_tables / etc.
├── archive.py        # ~12 catalog query tools
├── literature.py     # 3 ADS / arXiv tools
├── analysis.py       # ~15 spectroscopy / photometry / time-domain tools
└── workflow.py       # rest (paper draft / proposal / pipelines / VO / FITS)
```

**No public API changes**: every existing `from app.services.ai_tools
import X` continues to work via `__init__.py` re-exports. Tests must
remain green at every step.

### Coordination contract for codex / other agents

While this refactor is in progress on `refactor/ai-tools-split`:

1. **DO NOT** make structural edits (move functions, change module
   layout) to `backend/app/services/ai_tools.py` on `main`. Bug fixes
   that touch a single function body are fine; structural changes
   conflict and require manual rebase.
2. **DO NOT** delete or rename `_exec_*` functions in `ai_tools.py` on
   `main`. The split branch needs all of them present for clean
   per-domain extraction.
3. **DO** keep adding tools / fixing bugs on `main` — the split branch
   will rebase onto `main` before merging.
4. If you need to land an urgent `ai_tools.py` change, commit it on
   `main` and ping in CHANGELOG.md so the split branch can see it.

### When the refactor is "done"

Done = green when:

- Backend `pytest -q tests/` reports the same passing count as the
  pre-refactor baseline (currently 1711+ on `main`).
- Frontend `npm run build && npm run test` still green.
- Every `from app.services.ai_tools import X` in the codebase still
  resolves correctly.
- `ruff check` clean.
- A `.local/PHASE1_AI_TOOLS_REFACTOR_SELFTEST.md` self-audit report
  exists and signs off.

Only after that does the owner decide whether to merge to `main`. The
decision is **not automatic**.

### How to remove this section

When the refactor merges (or is abandoned), strike out this whole
"Active refactor" section and add an entry to the bottom of this file:

```
## Completed: ai_tools.py split (Phase 1)
- Merged on YYYY-MM-DD as commit <hash>.
- OR abandoned on YYYY-MM-DD; reason: ...
```

---

## Completed refactors

(none yet)
