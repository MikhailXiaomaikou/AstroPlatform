# CLAUDE.md

Shared handbook for Claude Code, Codex, Cursor, Aider, and other coding agents.
The root `AGENTS.md` delegates here so agent instructions do not drift.

## Project Contract

- Standard Astro is a **cosmology-only research alpha workbench**.
- The goal is controlled, auditable research over registered datasets,
  likelihoods, evidence graphs, fact checks, and exports.
- It is not a general "reproduce any paper" machine.
- Unsupported scientific claims must become capability gaps, not guesses.
- Local development + GitHub Actions are primary. Render deploy is a side
  effect unless the user explicitly asks about deployment.

## Source Of Truth

- Public positioning: `README.md`
- Architecture: `ARCHITECTURE.md`
- Source mapping: `docs/SOURCE_MAPPING.md`
- Blind-test target: `docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md`
- Blind-test protocol: `docs/BLIND_RESEARCH_TESTING_LOG.md`
- Backlog: `plan/cosmology-completion-backlog.md`
- Provenance v2 guide: `plan/provenance-v2-upgrade-plan.md`

Prefer current code and the most specific scientific test over stale prose.
Update stale docs rather than preserving folklore.

## Communication With User

- Act like a PhD-level astronomer explaining to an early-undergraduate user.
- Define first-use jargon and state physical meaning before formulas.
- Be direct when the user's idea is wrong or risky.
- Unknown means unknown. Unsupported means unsupported.
- Avoid "泼冷水"; use "先说结论", "诚实提醒", or "容我直言".

## Commands

Frontend, from `frontend/`:

```bash
npm run lint
npm run test
npm run build
```

Backend, from `backend/`:

```bash
./venv/bin/ruff check app tests
./venv/bin/pytest tests -q --no-cov
```

Use `.venv/` instead of `venv/` if that is the environment present.
`--no-cov` skips the coverage floor configured in `backend/pytest.ini`;
CI still enforces it, so a full run before commit should drop the flag.

Science checks, from `backend/`:

```bash
./venv/bin/python scripts/benchmarks/run_cosmology_benchmarks.py
./venv/bin/python scripts/audit_registry.py
./venv/bin/python scripts/audit_citation_pool.py
bash scripts/daily_blind.sh --module cosmology --case A2,A3
```

Run focused tests first. Broaden when touching shared validators, auth, chat,
tool schemas, prompts, registries, runners, or frontend result rendering.

## Editing Rules

- Use `rg` / `rg --files` first.
- Codex only: use `apply_patch` for manual edits. Other agents use their
  native edit tools.
- Never revert user changes unless explicitly asked.
- Never commit secrets, API keys, hidden-answer records, or `.local/`
  diagnostics.
- Do not edit vendored packages such as `backend/packages/code/CAMB` unless the
  task is explicitly about that vendored code.
- Prompt content lives under `backend/app/prompts/`, not inline in
  `backend/app/api/chat.py`.
- If a tool schema changes, update the manifest, dispatcher, tests, and
  frontend result cards.
- If a dataset or likelihood changes, update source mapping docs and run
  registry / benchmark audits.

## TypeScript Rules

- Strict TypeScript is intentional. Do not weaken `tsconfig`.
- Use `import type` for type-only imports.
- Remove unused imports, variables, and parameters.
- `npm run build` is the final frontend gate.

## Non-Negotiable Scientific Guardrails

Do not relax these to make a demo pass:

- `CONFIG_READY`, abstracts, old chat context, and user assumptions do not
  support posterior, fit, significance, or tension numbers.
- Strong claims need current-turn evidence:
  claim -> result -> tool run -> dataset/table -> citation/source URL.
- Fake tool transcripts and self-supplied `tool_results` must not ground claims.
- Literature search supports context and citations only. Measurement claims need
  extracted table rows or publication-ready tool output.
- Synthetic `run_python` output cannot be used as observations.
- Clean successful runs need specificity coverage so anti-fabrication gates do
  not falsely block them.
- Low-ESS, failed, exploratory, or config-only cells must remain visible but not
  claimable.
- Overlapping cosmology datasets must respect `do_not_combine_with`.

### Named regression invariants — DO NOT relax

These are enforced by tests and blind cases, but the tests themselves are
load-bearing: do not weaken a forbid string, delete a case, or shrink a
blacklist "while updating the matching test".

- Blind-suite anti-fabrication defenses
  (`backend/scripts/blind_test_cosmology_m0/cases.yaml`) must stay strict:
  B1 inline-rows blocked, B2 fake-bibcode replaced, B3 fake-tool-transcript
  never grounds a claim, B4 self-supplied export evidence stays unverified,
  B5 a rejected number stays unverified across turns, C1 zero-data
  hard-blocked, C2 abstention, D1 `suspicious_author_year` provenance
  violation. Groups B/C are hard CI gates. Group F is the SPECIFICITY side
  (clean runs must NOT be falsely blocked — the 9f2667e bug class); its
  `hard: true` cases gate CI too.
- `claim_validator._CITATION_KEYS_BLACKLIST` subtree-skips citation-string
  keys (bibcode/DOI/arXiv-id/...) so scattered digits in identifiers never
  enter the claimable numeric universe. Do not remove the regression case
  `numeric_in_bibcode_string_not_in_universe` in
  `tests/_red_team_cases/numeric_claims.yaml` or shrink the blacklist.
- `app/services/cosmology.py` `PRESETS["planck18"]["astropy_alias"]` MUST
  be `None`. Aliasing to astropy's built-in Planck18 silently swaps the
  cited CMB-only column (H0=67.36) for the +BAO best fit (H0=67.66) — the
  exact cross-release value mixing the module promises to prevent (bug
  commit `45383ac`). Pinned by
  `tests/test_astro_fundamentals.py::test_planck18_preset_matches_cited_cmb_only_values`
  and the `planck18_preset_matches_cited` benchmark.

## Guardrail Files

- Claim validation: `backend/app/services/claim_validator.py`
- Result banners/provenance: `backend/app/services/result_provenance.py`
- Synthetic detector: `backend/app/services/synthetic_code_detector.py`
- Chat reply gate: `backend/app/api/chat.py`
- Research planning/evaluator:
  `backend/app/services/research_program.py`,
  `backend/app/services/research_alpha_evaluator.py`
- Cosmology likelihoods/runners:
  `backend/app/services/cosmology_likelihoods.py` and related services

## Current Architecture Pointers

- Backend entrypoint: `backend/app/main.py`
- Chat loop: `backend/app/api/chat.py`
- Tool dispatcher: `backend/app/services/ai_tools/` (package; `__init__.py`
  re-exports `TOOLS` and `execute_tool`)
- Prompt loader: `backend/app/services/prompt_loader.py`
- Frontend chat: `frontend/src/pages/Chat/ChatPage.tsx`
- Chat components: `frontend/src/components/chat/`

For full structure and live counts, read `ARCHITECTURE.md` and run
`scripts/stats.sh`.

## Change-Type Test Rules

- Frontend UI: lint, focused Vitest, build.
- Backend service: ruff plus focused pytest; all backend tests for shared
  validators, auth, chat, tools, or runners.
- Cosmology data / likelihood: focused tests, benchmarks, registry audit,
  citation audit.
- Prompt / guardrail: red-team corpus, blind-test subset, and at least one clean
  specificity case.
- Docs only: `git diff --check`, unless docs include executable commands or
  generated counts.

## Local / Deployment Notes

- Production secrets belong in environment variables only.
- Local no-auth and local Codex/OpenAI CLI modes are development-only.
- Render auto-deploy can lag behind `main`; local verification comes first.
- Local diagnostics under `.local/` are ignored and should not be uploaded
  unless explicitly requested.

## Shared Agent Note

Codex should read root `AGENTS.md`, which points back here. This file is the
single shared rule source.
