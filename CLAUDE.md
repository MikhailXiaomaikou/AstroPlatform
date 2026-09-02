# CLAUDE.md

Shared handbook for Claude Code, Codex, Cursor, Aider, and other coding agents.
The root `AGENTS.md` delegates here so agent instructions do not drift.

## Project Contract

- Standard Astro is a **cosmology-only research alpha workbench**.
- The goal is controlled, auditable research over registered datasets,
  likelihoods, evidence graphs, fact checks, and exports.
- It is not a general "reproduce any paper" machine.
- North star for direction decisions: serve real observational
  cosmologists; the differentiator is provable non-fabrication and
  provenance, not fitting power. "Put it in front of a real user" is
  always one of the candidate next steps.
- Unsupported scientific claims must become capability gaps, not guesses.
- Growth gate (2026-08-11): before adding any new router, page, tool, or
  dataset, name the real user or scheduled demo that needs it. If no one
  can be named it goes to the backlog candidate pool, not into the tree.
- Local development + GitHub Actions are primary. Render deploy is a side
  effect unless the user explicitly asks about deployment.
- Direction review (2026-09-02): the limiter behind "the model does not
  push its analysis" is the deterministic steering layer in
  `backend/app/services/agent_runtime/loop.py`, the prohibition-only
  prompt, and the exit gates — not human review. Scope stays
  cosmology-only. "General research agent", "research environment as the
  top-level architecture", dynamic or fused tools, and an eight-role agent
  alliance were rejected; see the review and the execution plan under
  Source Of Truth. Anything listed there as candidate pool needs a named
  user before it re-enters the tree.
- Instrument-first: no behaviour change merges while the Daily blind suite
  or the Weekly Scientific Validation workflow is red, or while HEAD has
  no rerun baseline
  (`.local/standard-astro-v02-natural/rerun_<rev>_summary.json`). At
  session start run `gh run list --workflow=daily.yml --limit 3` and
  `gh run list --workflow='Weekly Scientific Validation' --limit 3`; a red
  scheduled suite is P0 before any other work, and an identical error
  repeated across two runs is a product defect that gets an issue the
  same day (the 2026-08-11 → 09-01 outage ran 22 times unfiled).
- Measure before engineering behaviour: a claim about model behaviour
  ("stops early", "too cautious") enters the backlog only with a
  pre-registered task file, a committed sha256, and a number stratified
  by `llm_calls` and by `LIGHTWEIGHT_VERIFICATION_ENABLED` state. The
  exploration window (`exploration_phase_enabled`) is built only if the
  v03 experiment reproduces `premature_stop >= 25%` on open tasks and no
  single-mechanism arm closes it.
- Roadmap items carry four fields: who needs it / observable pass
  condition / time box in agent-minutes / which guardrail it touches.
  Items without a named user go to the candidate pool, not the backlog.

## Source Of Truth

- Public positioning: `README.md`
- Architecture: `ARCHITECTURE.md`
- Source mapping: `docs/SOURCE_MAPPING.md`
- Blind-test target: `docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md`
- Blind-test protocol: `docs/BLIND_RESEARCH_TESTING_LOG.md`
- Backlog: `plan/cosmology-completion-backlog.md`
- Provenance v2 guide: `plan/provenance-v2-upgrade-plan.md`
- Direction review (2026-09-02):
  `docs/research/STANDARD_ASTRO_REVIEW_2026-09-02.zh-CN.md`
- Execution plan (2026-09-02): `plan/2026-09-02-execution-plan.md`
  (supersedes the never-committed desktop draft
  `Standard_Astro_Workflow_Optimization_Plan.md` of 2026-06-04)

Prefer current code and the most specific scientific test over stale prose.
Update stale docs rather than preserving folklore.

## Communication With User

- Act like a PhD-level astronomer explaining to an early-undergraduate user.
- Define first-use jargon and state physical meaning before formulas.
- Every technical report leads with a plain-language summary (analogies
  welcome); technical detail comes after. Do not wait to be asked — the
  user has had to ask for this repeatedly across sessions.
- Put decisions to the user as 2-4 numbered plain-language options with a
  marked recommendation, so a one-word reply ("1", "ok", "推") settles it.
- Separate must-fix defects from judgment calls. Scientific judgment calls
  (dataset version, modeling choice, hard-block vs warn) always go to the
  user as options — never decide them silently.
- While waiting for a go-ahead, state explicitly what has and has not been
  touched ("nothing modified yet").
- A bare "?" or "怎么样了" usually means your previous reply never reached
  the user. Restate the last conclusion in three lines or less before
  continuing. Persist major conclusions in a report file or commit message
  so a dropped reply cannot lose them.
- Be direct when the user's idea is wrong or risky. When the user's
  assumption conflicts with evidence, settle it with one real test and
  report the measurement — do not argue from theory or silently comply.
- Unknown means unknown. Unsupported means unsupported.
- Avoid "泼冷水"; use "先说结论", "诚实提醒", or "容我直言".

### Statements are not instructions

- A remark like "注意一下 X" is context, not a change request. Without an
  explicit action verb, confirm scope before touching any file (a
  brand-rename "note" once became an unwanted six-file commit).
- Once the user approves a stated plan, execute it without re-asking;
  re-confirm only if the scope or the facts changed after the approval.
- A request that clearly belongs to another project gets one confirming
  question before any work is invested.

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

`backend/venv/` is the only supported Python environment (the old broken
`.venv/` was verified and deleted 2026-06-03). Always call
`./venv/bin/python` / `./venv/bin/ruff`; never bare `python`/`python3`
(system python lacks the science deps), and never create a new venv.
`--no-cov` skips the coverage floor configured in `backend/pytest.ini`:
without it any small test selection exits 1 with a coverage FAIL even
when every test passed — that is not a test failure. CI still enforces
the floor, so the one full run before commit drops the flag.
The full backend suite is slow (~14 min as of 2026-07): run it in the
background and keep working; gate the commit on its result.

Science checks, from `backend/`:

```bash
./venv/bin/python scripts/benchmarks/run_cosmology_benchmarks.py
./venv/bin/python scripts/audit_registry.py
./venv/bin/python scripts/audit_citation_pool.py
bash scripts/daily_blind.sh --module cosmology --case A2,A3
```

Scheduled-suite status (run at session start, from the repo root):

```bash
gh run list --workflow=daily.yml --limit 3
gh run list --workflow='Weekly Scientific Validation' --limit 3
```

Run focused tests first. Broaden when touching shared validators, auth, chat,
tool schemas, prompts, registries, runners, or frontend result rendering.

## Editing Rules

- Use `rg` / `rg --files` first.
- Evaluation and rerun scripts (`rerun_natural_matrix.sh`,
  `run_exploration_matrix.sh`, anything that uses the `local:claude-cli`
  bridge) run from a clean Terminal, never inside a Claude Code session:
  the bridge exits 1 there and the 2026-08-11 reruns lost half their
  samples to it.
- Every reported evaluation number states its
  `LIGHTWEIGHT_VERIFICATION_ENABLED` state. `evaluate_standard_astro_v02.py`
  forces it on; production default is off; the two are different routing
  regimes and are never blended (the 90.4% figure was measured flag-on,
  on tasks V02_03–06 only).
- DeepSeek thinking-mode profiles need `reasoning_content` on every
  assistant `tool_calls` message, including platform-synthesized turns.
  Any new pre-LLM synthesized branch in `loop.py` must be covered by
  `tests/test_deepseek_reasoning_content.py` (the 2026-08-11 Daily
  outage).
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
- New code, comments, and commit messages are English; only conversation
  follows the user's language (Chinese in code has broken frontend
  rendering and language checks before). Exception: the intentional
  localized UI content (`frontend/src/i18n/` and
  `frontend/src/data/glossary.ts`) stays bilingual — never "translate"
  it away.
- Re-check `git status` / `git log` at session start and after any wait:
  parallel sessions, other agents, or the user may have moved HEAD. Audit
  unpushed commits you did not make before building on them. If an edit
  fails because the file changed since you read it, re-read — never force.
- Before deleting an untracked or "obviously dead" file, read it and `rg`
  for references, and present deletions with a reversibility note.
  Frontend dead-code claims must account for dynamic `import()`/lazy
  routes (a scan without them once flagged ChatPage itself as dead).
- When generalizing or reusing a dataset-specific code path, grep for
  hardcoded dataset names and version strings on that path (a reused DR1
  path once mislabeled DR2 provenance as "DESI DR1").
- When trimming or reorganizing this file, "DO NOT relax" red-line
  clauses may move but must never disappear.

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
- When a validation gate false-kills legitimate input, fix it by echoing
  the observed evidence or improving the error message so the caller can
  self-correct — never by loosening the gate's threshold.

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
  `hard: true` cases gate CI too. F2 pins that a compressed chain is
  withheld (`chain_tier` never reaches `publication` on that path); do not
  "fix" a smoke check by asserting `publication`.
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
  `backend/app/services/cosmology_likelihoods/` and related services

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

## Verification Discipline

Every rule here traces to a real incident in past agent sessions
(2026-04 → 2026-07, Claude and Codex alike):

- Green tests do not mean fixed. Science-critical or anti-fabrication
  changes get an adversarial multi-agent review before commit — such
  reviews have repeatedly found blockers in "clean" fixes.
- Review and audit findings are hypotheses, not conclusions. Reproduce a
  finding before fixing it; reject false positives and pin the refuting
  evidence in a code comment so it is not re-flagged later.
- "Fixed" must be verified on the real call path the user will hit (the
  live local chat route, not only synthetic unit tests — a fix once
  passed on synthetic data while the real data path still crashed). A
  new tool is not done until it is provably exposed end-to-end: run the
  Editing Rules checklist (manifest, dispatcher, tests, frontend cards)
  and confirm the model can actually see and call it. Anything not
  verified is reported as "not verified", never as fixed.
- When changing a field, signature, or contract, `rg` every occurrence
  and fix all code paths. Patching only the first path found has
  repeatedly caused regressions in black-box test rounds.
- A regression test must fail on the pre-fix code and must exercise the
  channel that actually triggered the bug — not a convenient neutral
  fixture that passes either way.
- Multi-item test reports get a per-item disposition table (fix now /
  needs retest / cannot reproduce / deferred + reason); deferring needs
  the user's nod. Never silently fix only a subset.
- Before publishing any demo, guide, or external deliverable, run its
  exact prompts end-to-end on the live deployment; re-run after fixes.
- Physics formula disputes are settled against the original paper, not by
  re-derivation — first re-derivations have been wrong too.
- No uncited magic numbers or ad-hoc astronomical values in production
  code. Citation-pinned registry entries, `PRESETS`, and checksummed
  data dicts are the sanctioned mechanism for such values (do not "fix"
  those); expected values in tests live in fixtures with a source note.
  Volatile counts in docs are measured (`scripts/stats.sh`, the audits)
  and marked with an as-of date, never copied from older prose.
- A local check that claims to mirror CI must use CI's exact flags,
  scope, and tool versions.
- Scheduled workflows are instruments. A change to a workflow file
  (checkout depth, provider, model, secrets) needs a guard test in the
  `tests/test_scientific_validation_guard.py` pattern.

## Multi-Agent / Batch Work

- Batch fixes: one agent owns one file (no overlaps), each fix gets an
  independent adversarial verifier, nothing auto-commits.
- A subagent's "done" is a claim. The main session re-runs the full
  suites and snapshot comparisons itself before committing.
- Large refactors: archive-commit the workspace first; dump behavior
  snapshots (tool schemas, API route table, registry) before surgery and
  byte-compare after; run the full suite after every step. This protocol
  has already survived subagents dying mid-surgery.
- Fix blockers/majors in batch, but triage minors first — they have the
  highest false-positive rate. Fix the clear ones; send arguable ones to
  the user as a list.
- Handoff prompts for other agents or sessions must be self-contained:
  absolute repo path, entry documents, acceptance commands, report format.
- Describe anti-fabrication work in neutral terms in prompts and workflow
  scripts (honesty gate, echo channel — not offensive-security
  vocabulary): model safety filters have killed review sessions over
  aggressive wording.
- A reusable review harness is saved as the `adversarial-review` workflow
  (`.claude/workflows/adversarial-review.js`, Claude Code only).

## Git, Push & CI Policy

- Commits stay local by default. Push only on the user's explicit word.
  Status reports include "local main is ahead of origin by N commits".
  (This supersedes the older 2026-04 habit of pushing every stage.)
- One logical unit — one dataset, one fix batch — is one commit.
- Before committing: show what changed, the verification results, and the
  proposed commit message; wait for the user's one-word ok. Exception:
  unattended runs commit pre-approved work locally per the stated plan
  and report every commit at the next check-in.
- After a push, watch CI to green and report the outcome. If that reply
  gets dropped, lead with the CI status at the start of the next turn.
- A red daily CI run: check first whether the failing job is external
  service noise (TAP/archive timeouts) and whether a triage report
  already tracks it, before touching code — and whether the same error
  string repeats across runs: repetition means product defect, not noise.

## Autonomous / Unattended Sessions

- Allowed: read-only recon, running tests and audits, preparing decision
  options, mechanical steps the user already approved.
- Not allowed: scope decisions, features beyond the approved list, and
  push. An unattended session once shipped an unrequested feature with a
  false security claim; it was reverted wholesale.
- Keep a backlog file and commit finished work locally so interruptions
  (rate limits, session limits) are cheap to resume from.

## Local / Deployment Notes

- Production secrets belong in environment variables only.
- Never guess deployment topology or env-var behavior — read
  `render.yaml` (and `docker-compose.yml`) first (guessing once broke
  the production API-key path three rounds in a row).
- Local no-auth mode is development-only. The subscription-CLI backends
  (`local:claude-cli` via CLAUDE_CLI_ENABLED, `local:openai-cli` via
  OPENAI_CLI_ENABLED) are a supported self-hosting feature (2026-07-10):
  they require the CLI installed and logged in on the same machine, run it
  as a local-only ephemeral bridge (Claude tools/settings/session disabled;
  Codex config/rules ignored in a read-only sandbox), and never
  exist on the hosted deployment.
- Render auto-deploy can lag behind `main`; local verification comes first.
- After a deploy lands, curl `/health` and `/health/deep` — the deep
  check once caught an expired database nobody suspected.
- Local diagnostics under `.local/` are ignored and should not be uploaded
  unless explicitly requested.
- cobaya keeps a machine-global packages path in
  `~/Library/Application Support/cobaya/config.yaml` that can shadow the
  repo checkout. If the cobaya-parity tests suddenly report "has not been
  correctly installed", check that file first — it must point at
  `backend/packages` (a cobaya-install run from a temp dir once left it
  pointing at a deleted scratchpad and 4 parity tests went red).

## Shared Agent Note

Codex should read root `AGENTS.md`, which points back here. This file is the
single shared rule source.
