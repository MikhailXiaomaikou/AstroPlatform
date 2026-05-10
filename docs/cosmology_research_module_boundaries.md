# Cosmology Research Module Boundaries

**Audience**: anyone (human or AI) about to touch any of these 5 files.
**Purpose**: explicit input / output / dependency boundary table so the
files don't drift back into a tangled "paper-something" cluster.

The 5 files in scope (all under `backend/app/services/`):

| File | LOC | One-line role |
|---|---|---|
| `research_program.py` | 1220 | Research-mode planning + evidence graph + fact-check + export |
| `paper_tool_mining.py` | 891 | Single-paper → ToolSpec extraction + ontology + gap matrix |
| `paper_tool_mining_loop.py` | 315 | Multi-paper batch driver around `paper_tool_mining` |
| `paper_candidate_pool.py` | 428 | arXiv candidate discovery (input feed for the loop) |
| `line_relation_paper_warmup.py` | 216 | Static lookup table for non-CII line-family papers |

**Headline conclusion**: there are **no merge candidates**. Each file
has a single responsibility and one-way dependencies. The 1-month
surgery does NOT need to consolidate any of them.

---

## End-to-end data flow

```text
user research question (text)
        ↓
   plan_research_program  ←  research_program.py
        ↓ produces hypotheses / probes / dataset candidates / experiment matrix
   run_research_matrix     ←  research_program.py  →  cosmology_likelihoods.run_likelihood_chain
        ↓ executes registered probes
   build_evidence_graph    ←  research_program.py
        ↓ links claims → tool runs → datasets
   verify_research_facts   ←  research_program.py
        ↓ produces fact-check report
   export_research_report  ←  research_program.py
        ↓ produces audit bundle (citations + reproducibility manifest + bibtex)


domain tag (e.g. "observational_cosmology")
        ↓
   build_paper_mining_candidate_pool  ←  paper_candidate_pool.py
        ↓ arXiv search + dedup + score
   list[paper candidates]
        ↓
   run_paper_tool_mining_loop[_round]  ←  paper_tool_mining_loop.py
        ↓ for batch in batches:
   run_paper_tool_mining_batch         ←  paper_tool_mining.py
        ↓ for paper in batch:
   mine_paper_tools                    ←  paper_tool_mining.py
        ↓ paper text → ToolSpec records
   build_tool_ontology / gap_matrix    ←  paper_tool_mining.py
        ↓
   tool ontology + gap matrix vs platform


bibcode (e.g. for [CII] / CO / Halpha line-relation papers)
        ↓
   warmup_arxiv_ids_for(family)
   is_non_cii_paper_lensed_by_default(bibcode)  ←  line_relation_paper_warmup.py
        ↓ static lookup, no logic
   used by:
     - api/admin_literature.preload_*_caches
     - api/arxiv.py:_normalize_line_measurements (lensing fallback)
     - api/chat.py SYSTEM_PROMPT line-relation idioms (future)
```

The two flows (research_program vs paper_tool_mining) are **completely
disjoint**. They share zero data structures and zero imports between
each other. `line_relation_paper_warmup` is a separate static-data
module that neither flow touches directly.

---

## Per-file detail

### 1. `research_program.py` (1220 LOC)

**Role**: Research-mode = AI plans an experiment, executes registered
cosmology probes, links results back to the user's claims, exports an
audit bundle.

**Public API**:
| Function | Input | Output |
|---|---|---|
| `plan_research_program(question)` | research question text | hypotheses, probes, dataset candidates, model families, experiment matrix, alpha-test protocol |
| `run_research_matrix(matrix, …)` | experiment matrix from `plan_…` | per-cell run results, failure categories |
| `build_evidence_graph(tool_results, final_reply)` | tool_results + final assistant text | nodes + edges connecting claims → results → tools → datasets |
| `verify_research_facts(final_reply, claimable, tool_results)` | final reply + claim spec + tool_results | fact-check report (per-claim status + evidence path) |
| `export_research_report(tool_results, …)` | tool_results | report package (citations + reproducibility manifest + bibtex) |

**Dependencies (non-stdlib)**:
- `app.common.regex.{ARXIV_ID_RE, BIBCODE_RE, DOI_RE}`
- `app.services.cosmology_likelihoods.{build_likelihood_config, get_cosmology_dataset, run_likelihood_chain}` — only outbound functional dependency

**Not used here**: paper_tool_mining*, paper_candidate_pool, line_relation_paper_warmup.

**Boundary vs others**: research_program is about *executing a plan over registered datasets*. It does NOT discover papers, mine ToolSpecs, or maintain line-family lookup tables.

---

### 2. `paper_tool_mining.py` (891 LOC)

**Role**: Take one paper's text + metadata, find method/table/equation
passages, classify them as ToolSpec records (loader / extractor /
likelihood / sampler / fitter / diagnostic / plotter / exporter /
validator), then aggregate ToolSpecs into an ontology + gap matrix
against platform capabilities.

**Public API**:
| Function | Input | Output |
|---|---|---|
| `mine_paper_tools(paper)` | paper dict (id, abstract, sections, tables) | list[ToolSpec] for that paper |
| `run_paper_tool_mining_batch(papers)` | list[paper] | aggregated tool_specs + ontology + gap_matrix |
| `build_tool_ontology(tool_specs)` | mined ToolSpecs | ontology (counts per category, methods, datasets) |
| `build_tool_gap_matrix(tool_specs, current_tools)` | mined ToolSpecs + platform tools | per-capability gap rows |
| `rank_tool_implementation_queue(gap_matrix, max_items)` | gap matrix | top-N implementation candidates |

**Internal**: `PLATFORM_CAPABILITIES` dict statically describes
what Standard Astro already exposes (literature_search, run_adql,
fit_cosmology_mcmc, …). This is *separate from* the
`COSMOLOGY_FOCUS_TOOL_ALLOWLIST` in chat.py (different purpose:
PLATFORM_CAPABILITIES is for gap analysis, the allowlist is for
runtime LLM tool gating).

**Dependencies**: stdlib + Counter/defaultdict only. No project deps.

**Boundary vs others**: paper_tool_mining mines INFRASTRUCTURE
requirements (what tools the paper used), not scientific conclusions.
It runs per-paper. The loop driver (paper_tool_mining_loop) calls it
in batches; paper_candidate_pool feeds the input papers; nothing else
calls it.

---

### 3. `paper_tool_mining_loop.py` (315 LOC)

**Role**: Driver around `paper_tool_mining.run_paper_tool_mining_batch`.
Handles batch sizing, persistent state (already-mined paper IDs), and
optional local-diagnostic bundle output.

**Public API**:
| Function | Input | Output |
|---|---|---|
| `run_paper_tool_mining_loop_round(papers, state, batch_size, …)` | list[paper] + previous state | one round of mining + updated state |
| `run_paper_tool_mining_loop(…)` | same | runs multiple rounds until done |
| `read_loop_state(path)` / `write_loop_state(state, path)` | state JSON file | persistent state across rounds |

**Defaults**: `DEFAULT_BATCH_SIZE = 20`, `DEFAULT_OUTPUT_DIR =
".local/paper_tool_mining"`.

**Dependencies**:
- `app.services.local_diagnostics.sanitize_for_diagnostic`
- `app.services.paper_tool_mining.run_paper_tool_mining_batch` — direct
  call, this is the driver/driven boundary

**Boundary vs others**: thin wrapper. Does NOT mine anything itself,
does NOT discover papers, does NOT touch the research_program flow.
Pure batch + state plumbing.

---

### 4. `paper_candidate_pool.py` (428 LOC)

**Role**: Discover arXiv papers in a domain (default queries cover
observational cosmology + high-redshift galaxies). Async httpx fetch,
text-preview hydration (HTML / PDF), dedup, score.

**Public API**:
| Function | Input | Output |
|---|---|---|
| `build_paper_mining_candidate_pool(domain_tag, max_papers, exclude_ids, …)` (async) | domain + caps + exclusions | list[candidate paper] with arxiv metadata + text preview |

**Defaults**: `DEFAULT_MAX_PAPERS = 60`, hardcoded `DEFAULT_QUERIES`
per domain.

**Dependencies**: stdlib + httpx + ElementTree only. No project deps.

**Boundary vs others**: paper_candidate_pool produces the **input
list** that paper_tool_mining_loop consumes. It does NOT mine, does
NOT call research_program, does NOT touch line_relation_paper_warmup.
The output is opaque dicts; downstream files don't peek into its
internal structure beyond the documented fields.

---

### 5. `line_relation_paper_warmup.py` (216 LOC)

**Role**: Static metadata lookup for non-CII line families (CO 1-0,
Halpha broad, OIII 5007 velocity dispersion, HCN 1-0). Pure data file —
no logic beyond table lookup.

**Public API**:
| Function | Input | Output |
|---|---|---|
| `list_line_families()` | — | tuple[LineFamily] |
| `warmup_arxiv_ids_for(family)` | family literal | tuple[arxiv_id] |
| `lensing_kind_for_non_cii_bibcode(bibcode)` | bibcode | "all_lensed" / "no_lensing" / None |
| `is_non_cii_paper_lensed_by_default(bibcode)` | bibcode | bool |
| `workflow_note_for(family)` | family literal | short SYSTEM_PROMPT note |

**Dependencies**: stdlib only.

**Consumers**: as of 2026-05-10 audit, **none in backend/frontend code**.
Only `tests/test_line_relation_paper_warmup.py` imports the module.

The original draft of this doc claimed `api/admin_literature`,
`api/arxiv.py`, and `api/chat.py` were consumers — that was aspirational,
not actual.  `rg -n "line_relation_paper_warmup|LineFamily" backend/ frontend/`
returns only the module itself, its tests, and the export-list line.

This makes the module a pure non-cosmology dormant-candidate.  Per the
modular-platform strategy (cosmology is the first module, others are
dormant), do NOT delete: it is paid-for capital that should move into
`backend/app/prompts/modules/_dormant_high_z_galaxy/` or similar during
Stage 2 M1 (modular surgery).  Leave the module + its tests in place
until M1 routes them.

**Boundary vs others**: completely independent. Sister file to
`cii_paper_metadata.py` which serves the same role for [CII]
specifically. **Possible future consolidation**: merge
`cii_paper_metadata` + `line_relation_paper_warmup` into a single
`line_family_metadata.py` keyed by `LineFamily`. Not in scope for
this 1-week first cut; flag for the 1-month surgery if the line-family
list grows beyond ~6.

---

## What this means for the 1-month surgery

The original 1-week plan flagged these 5 files as a "potential
consolidation cluster". After this inventory, that flag is wrong:

| File | Merge candidate? |
|---|---|
| research_program.py | **No.** Different concern (planning + evidence) from all 4 paper-* files. |
| paper_tool_mining.py | **No.** Pure unit (single-paper → ToolSpec). Loop driver is correctly factored out. |
| paper_tool_mining_loop.py | **No.** Driver. Distinct from the unit it drives. |
| paper_candidate_pool.py | **No.** Input feed. Distinct from the consumer. |
| line_relation_paper_warmup.py | **Maybe** — could merge with `cii_paper_metadata.py` into one `line_family_metadata.py` if the line-family list grows. Not urgent. |

**1-month surgery should NOT touch this cluster** unless:
1. The line-family count grows past ~6 (then merge #5 with `cii_paper_metadata.py`).
2. A new cosmology probe needs research-mode integration — which would
   live inside `research_program.py`, not as a 6th file.

Anything else (new paper-mining domain, new ToolSpec category, new
discovery query) lives **inside** the existing 5 files; do not split
out a 6th.

---

## How to keep this doc honest

If you (human or AI) add a new file or significantly change a public
API in one of the 5 listed here, update this doc in the **same commit**.
A drifted boundary doc is worse than no boundary doc.

Stats:
- Last updated: 2026-05-08 (Action 5 of cosmology-focus 1-week first cut)
- Live LOC: see `scripts/stats.sh`
