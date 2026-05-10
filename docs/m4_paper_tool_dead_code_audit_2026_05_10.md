# M4 Paper-Tool Dead-Code Audit (2026-05-10)

**Outcome**: no code deleted.  Three findings reverse the original
"M4 = delete `write_loop_state`" plan.

## Scope

Audit the five files under `backend/app/services/`:
- `paper_tool_mining_loop.py`
- `paper_tool_mining.py`
- `paper_candidate_pool.py`
- `research_program.py`
- `line_relation_paper_warmup.py`

For every public function (no leading underscore), check whether any
backend or frontend code (excluding the file itself and its sibling
`paper_tool_*` files) calls it.

## Method

```bash
rg -n "^def [a-z][a-z_]+|^async def [a-z][a-z_]+" "backend/app/services/$f"
# then for each function:
rg -l "\\b$fn\\b" backend/ frontend/ | grep -v "<the 5 files>"
```

## Findings

### Finding 1. `write_loop_state` is NOT a 0-caller (Phase 1 Explore was wrong)

The plan said `write_loop_state` had 0 callers.  Actual call chain:

```
ai_tools.py:1531           tool spec  run_paper_tool_mining_loop
ai_tools.py:8526           _exec_run_paper_tool_mining_loop
   ↓
paper_tool_mining_loop.py:119   run_paper_tool_mining_loop
   ↓ (loops over)
paper_tool_mining_loop.py:25    run_paper_tool_mining_loop_round
   ↓ (when write_local_bundle=True at lines 68 / 115)
paper_tool_mining_loop.py:287   _write_loop_bundle
   ↓
paper_tool_mining_loop.py:298   write_loop_state(state, root / "state.json")
```

`write_loop_state` is a real public API — it persists the multi-round
mining loop state between `_exec_run_paper_tool_mining_loop` calls so a
chat user can resume mining without re-reading already-mined papers.

**Action**: do nothing.  Do NOT delete.

### Finding 2. `line_relation_paper_warmup.py` has 0 backend callers

All five public functions (`list_line_families`, `warmup_arxiv_ids_for`,
`lensing_kind_for_non_cii_bibcode`, `is_non_cii_paper_lensed_by_default`,
`workflow_note_for`) are imported only from
`tests/test_line_relation_paper_warmup.py`.

The previous boundaries doc (`docs/cosmology_research_module_boundaries.md`)
claimed `api/admin_literature` / `api/arxiv.py` / `api/chat.py` consumed
this module.  That was aspirational, not actual.  The boundaries doc
has been corrected.

**Action**: do NOT delete.  Per the modular-platform strategy
(2026-05-10 plan revision), non-cosmology code is preserved as dormant
capital, not deleted.  This module + its 216 LOC and tests will move
into a dormant module folder during Stage 2 M1 (e.g.
`backend/app/prompts/modules/_dormant_high_z_galaxy/` or whichever
module owns the line-family workflows).

### Finding 3. The other public functions are all live

| Function | External callers (count) | Live? |
|---|---|---|
| `mine_paper_tools` | 4 (chat, ai_tools, result_provenance, tests) | ✓ |
| `run_paper_tool_mining_batch` | 4 | ✓ |
| `build_tool_ontology` | 4 | ✓ |
| `build_tool_gap_matrix` | 6 | ✓ |
| `rank_tool_implementation_queue` | 4 | ✓ |
| `build_paper_mining_candidate_pool` | 4 | ✓ |
| `plan_research_program` | 5 (chat.py, ai_tools.py, result_provenance, ChatPage.tsx, tests) | ✓ |
| `run_research_matrix` | 5 | ✓ |
| `build_evidence_graph` | 5 | ✓ |
| `verify_research_facts` | 5 | ✓ |
| `export_research_report` | 4 | ✓ |
| `run_paper_tool_mining_loop_round` | 2 (loop.py internal + tests) | ✓ |
| `run_paper_tool_mining_loop` | 2 (ai_tools.py + tests) | ✓ |
| `read_loop_state` | 1 (test_research_program.py) | ✓ (test contract) |
| `write_loop_state` | 1 (`_write_loop_bundle` internal → ai_tools.py LLM tool) | ✓ |

## Implications

1. **Plan file's Phase 1 inventory line** (`paper_tool: write_loop_state 0 caller (dead)`) is wrong.  When Stage 2 M1 begins, do not rely on Phase 1's caller counts — re-grep.

2. **Stage 2 M1 work remains valuable**: the `line_relation_paper_warmup`
   module IS dormant capital that the modular surgery will route into a
   dormant module folder.  But that is a M1 task (with the `_dormant_`
   file structure), not a Day-0 deletion.

3. **No commit modifies `backend/app/services/`** as part of M4.
   This audit's only outputs are:
   - This file (audit record)
   - `docs/cosmology_research_module_boundaries.md` (consumer-list correction)
