# CLAUDE.md

Shared handbook for Claude Code, Codex, Cursor, Aider, and other coding agents.
The root `AGENTS.md` delegates here so agent instructions do not drift.
Last full refresh: 2026-09-02 (paths and commands checked against the tree).

## Project Contract

- Standard Astro is a **cosmology-only research alpha workbench**:
  controlled, auditable research over registered datasets, likelihoods,
  evidence graphs, fact checks, and exports. It is not a general
  "reproduce any paper" machine.
- North star for direction decisions: serve real observational
  cosmologists; the differentiator is provable non-fabrication and
  provenance, not fitting power. "Put it in front of a real user" is
  always one of the candidate next steps.
- Unsupported scientific claims must become capability gaps, not guesses.
- Cosmology-only is a focus gate, not a deletion: `ASTRO_RESEARCH_FOCUS`
  (default `cosmology`; anything but `all` fails closed to it) selects the
  prompt module and tool allowlist in
  `backend/app/services/prompt_loader.py`. The other verticals' prompt
  modules moved to `standard-astro-verticals` (2026-06-03); their tool
  implementations remain in `backend/app/services/`, invisible to the LLM
  and kept on purpose — not dead code, and not re-enabled without the
  growth gate.
- Growth gate (2026-08-11): before adding any router, page, tool, or
  dataset, name the real user or scheduled demo that needs it; otherwise
  it goes to the backlog candidate pool, not into the tree.
- Many surfaces sit behind `*_ENABLED` flags (all listed in
  `backend/.env.example`); check the flag before declaring a route dead or
  a feature missing.
- Local development + GitHub Actions are primary; Render deploy is a side
  effect unless the user explicitly asks about deployment.

## Source Of Truth

- Public positioning: `README.md`
- Architecture and live counts: `ARCHITECTURE.md` + `scripts/stats.sh`
- Source mapping: `docs/SOURCE_MAPPING.md`
- Latest honest evaluation numbers and framing:
  `docs/research/STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.md` (the
  `.zh-CN.md` original governs). Rerun the natural-phrasing matrix with
  `backend/scripts/rerun_natural_matrix.sh` from a clean terminal, never
  inside a Claude Code session (leaked `CLAUDE*` variables break the CLI
  bridge).
- Public honesty evidence, including open gaps: `docs/HONESTY_EVIDENCE.md`
- Blind-test target: `docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md`
- Blind-test protocol: `docs/BLIND_RESEARCH_TESTING_LOG.md`
- Backlog: `plan/cosmology-completion-backlog.md`
- Provenance v2 guide: `plan/provenance-v2-upgrade-plan.md`
- Large-refactor coordination: `docs/REFACTOR_IN_PROGRESS.md` — register
  there before starting one.

Prefer current code and the most specific scientific test over stale prose.
Update stale docs rather than preserving folklore.

## Where Things Live

Pre-2026-07-03 notes citing line numbers or single-file paths for
`cosmology_likelihoods.py`, `ai_tools/__init__.py`, `chat.py`, or
`ChatPage.tsx` are stale — those were split; re-locate with `rg`.

- Entrypoint / router mounting: `backend/app/main.py` (seven zero-caller
  routers stay unmounted unless `ZERO_CALLER_ROUTERS_ENABLED=1`, PR #54).
- Chat HTTP entry: `backend/app/api/chat.py`; the agent loop, honesty
  gate, abstention parser, and the `validate_claims` call sites:
  `backend/app/services/agent_runtime/` (`loop.py`, `honesty.py`,
  `abstention.py`).
- Honesty gates: `backend/app/services/claim_validator.py`,
  `result_provenance.py`, `synthetic_code_detector.py`.
- Tool dispatcher: `backend/app/services/ai_tools/` (domain modules;
  `__init__.py` re-exports `TOOLS` / `execute_tool`; `run_python.py`
  owns the `data_source` contract).
- Prompt loader / focus gate: `backend/app/services/prompt_loader.py`;
  prompt text under `backend/app/prompts/` (only `modules/cosmology/` is
  checked in) — never inline it in `chat.py` or the runtime.
- Cosmology likelihoods: `backend/app/services/cosmology_likelihoods/`
  (`registry.py`; `bao.py` / `sn.py` / `cc.py` / `rsd.py` / `cmb.py`;
  `verification.py`; `runners.py`; `sampling.py`). Presets in
  `cosmology.py`; MCMC in `cosmology_mcmc.py`.
- Research planning/evaluator: `backend/app/services/research_program.py`,
  `research_alpha_evaluator.py`.
- Model backends: `backend/app/ai/inference_router.py` +
  `model_profiles.py` (Anthropic, DeepSeek, or the local CLI bridges under
  Local / Deployment Notes); the daily blind run
  (`.github/workflows/daily.yml`) takes `provider` = auto | anthropic |
  deepseek.
- Frontend chat: entry `frontend/src/pages/Chat/ChatPage.tsx`; tool-result
  routing `frontend/src/pages/Chat/AutoToolResult.tsx`; panels
  `frontend/src/components/chat/`.

## Communication With User

- Act like a PhD-level astronomer explaining to an early-undergraduate
  user: define first-use jargon, state physical meaning before formulas.
- Every technical report leads with a plain-language summary (analogies
  welcome); technical detail after. Do not wait to be asked.
- Put decisions to the user as 2-4 numbered plain-language options with a
  marked recommendation, so a one-word reply ("1", "ok", "推") settles it.
- Separate must-fix defects from judgment calls. Scientific judgment calls
  (dataset version, modeling choice, hard-block vs warn) always go to the
  user as options — never decide them silently.
- While waiting for a go-ahead, state what has and has not been touched
  ("nothing modified yet").
- A bare "?" or "怎么样了" means the previous reply never arrived: restate
  the last conclusion in three lines or less, then continue. Persist major
  conclusions in a report file or commit message.
- Be direct when the user's idea is wrong or risky. When an assumption
  conflicts with evidence, settle it with one real test and report the
  measurement — do not argue from theory or silently comply.
- Unknown means unknown. Unsupported means unsupported.
- Avoid "泼冷水"; use "先说结论", "诚实提醒", or "容我直言".
- Effort estimates in agent terms (minutes, tool calls), never
  engineer-hours.

### Statements are not instructions

- "注意一下 X" is context, not a change request. Without an explicit
  action verb, confirm scope before touching any file (a brand-rename
  "note" once became an unwanted six-file commit).
- Once the user approves a stated plan, execute it without re-asking;
  re-confirm only if scope or facts changed after the approval.
- A request that clearly belongs to another project gets one confirming
  question before any work is invested.

## Environment & Commands

`backend/venv/` is the only supported Python environment; it is untracked
and exists only in the primary checkout
(`/Users/chenkexuan/Projects/astro-platform/backend/venv`) — from another
worktree, call it by absolute path from that worktree's `backend/`.
Always `./venv/bin/python` / `./venv/bin/ruff`; never bare `python` /
`python3` (system python lacks the science deps); never create a new venv.

Frontend, from `frontend/`:

```bash
npm run lint
npm run test
npm run build
```

Backend, from `backend/`:

```bash
./venv/bin/ruff check app/ --select E,W,F --ignore E501   # CI's exact lint
./venv/bin/pytest tests -q --no-cov                        # while iterating
./venv/bin/pytest tests -q                                 # one full run before commit
```

`--no-cov` skips the coverage floor in `backend/pytest.ini`; without it
any small selection exits 1 with a coverage FAIL even when every test
passed — not a test failure. CI enforces the floor, so the one full run
before commit drops the flag. The full suite is slow (~14 min as of
2026-07): run it in the background; gate the commit on its result.

Science checks, from `backend/`:

```bash
./venv/bin/python scripts/benchmarks/run_cosmology_benchmarks.py
./venv/bin/python scripts/audit_registry.py
./venv/bin/python scripts/audit_citation_pool.py
bash scripts/daily_blind.sh --module cosmology --case A2,A3
```

## Verify By Change Type

Run focused tests first. Broaden when touching shared validators, auth,
chat / agent_runtime, tool schemas, prompts, registries, runners, or
frontend result rendering.

| Change | Minimum verification |
| --- | --- |
| Frontend UI | `npm run lint`, focused Vitest, `npm run build` (the final frontend gate) |
| Backend service | ruff + focused pytest (`--no-cov`); the full backend suite for the shared areas above |
| Cosmology data / likelihood | focused tests, benchmarks, registry audit, citation audit, `/cosmology-smoke`; new entries follow `/add-dataset` |
| Prompt / guardrail | red-team corpus (`tests/test_red_team_corpus.py`), blind-test subset (groups B/C plus at least one clean group-F specificity case), then the `anti-fabrication-reviewer` agent |
| Docs only | `git diff --check`; if the doc carries commands or generated counts, run them (`scripts/stats.sh`) |

## Editing Rules

- Use `rg` / `rg --files` first.
- Codex only: use `apply_patch` for manual edits; other agents use their
  native edit tools.
- Never revert user changes unless explicitly asked.
- Never commit secrets, API keys, hidden-answer records, or `.local/`
  diagnostics.
- Do not edit vendored packages such as `backend/packages/code/CAMB`
  unless the task is explicitly about that vendored code.
- Tool schema change → update the manifest, dispatcher, tests, and
  frontend result cards. Dataset or likelihood change → update source
  mapping docs and run the registry / benchmark audits.
- New code, comments, and commit messages are English; only conversation
  follows the user's language (Chinese in code has broken frontend
  rendering and language checks). Exception: the intentional localized UI
  content (`frontend/src/i18n/`, `frontend/src/data/glossary.ts`) stays
  bilingual — never "translate" it away.
- Before deleting an untracked or "obviously dead" file, read it, `rg`
  for references, and present deletions with a reversibility note.
  Frontend dead-code claims must account for dynamic `import()` / lazy
  routes (a scan without them once flagged ChatPage itself as dead);
  backend dead-route claims must account for the `*_ENABLED` flags.
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

## Multi-Agent / Batch Work

- Batch fixes: one agent owns one file (no overlaps), each fix gets an
  independent adversarial verifier, nothing auto-commits.
- A subagent's "done" is a claim. The main session re-runs the full
  suites and snapshot comparisons itself before committing.
- Large refactors: register in `docs/REFACTOR_IN_PROGRESS.md`,
  archive-commit the workspace, dump behavior snapshots (tool schemas,
  API route table, registry) before surgery and byte-compare after, run
  the full suite after every step. This protocol has survived subagents
  dying mid-surgery.
- Fix blockers/majors in batch; triage minors first (highest
  false-positive rate) — fix the clear ones, list the arguable ones for
  the user.
- Handoff prompts must be self-contained: absolute worktree path, entry
  documents, acceptance commands, report format.
- Describe anti-fabrication work in neutral terms (honesty gate, echo
  channel — not offensive-security vocabulary): model safety filters have
  killed review sessions over aggressive wording.

## Claude Code Automation (Claude Code only)

Automatic in Claude Code sessions started from the repo root; Codex,
Cursor, and Aider get none of it and run the equivalents by hand.

- Hooks (`.claude/settings.json` → `.claude/hooks/`): `block-protected-paths`
  denies edits to venv / node_modules / dist and the pinned data dirs;
  `ruff-check` / `tsc-check` run CI's exact lint and type-check after
  each `backend/app` or `frontend/src` edit; `doc-drift-check` echoes
  `scripts/stats.sh` after a `CLAUDE.md` / `ARCHITECTURE.md` edit that
  writes digits; `flag-defense-redline` reminds you to run regression
  tests and the red-team reviewer after touching `claim_validator.py` or
  `synthetic_code_detector.py`.
- Agents (`.claude/agents/`, read-only): `science-test-runner`,
  `cosmology-contract-reviewer`, `anti-fabrication-reviewer`.
- Skills: `/cosmology-smoke` (science anchors), `/add-dataset` (checklist).
- Workflow `adversarial-review` (`.claude/workflows/adversarial-review.js`):
  multi-lens review of a git range with a verifier per finding; pass
  `{repo: "<worktree path>"}` outside the primary checkout.

## Git, Branches, PRs & CI

- `main` is protected (verified 2026-09-02): no direct pushes, linear
  history, and eight required checks on an up-to-date branch —
  `backend-test`, `frontend-test`, `lint`, `frontend-e2e`,
  `container-build`, `migration-and-recovery`, `benchmarks`, `CodeQL`.
- Flow since 2026-07-23: feature branch (often its own worktree) → local
  commits → push only on the user's explicit word → PR → CI green → the
  user says "squash merge" → reset local `main` to `origin/main` and
  watch post-merge CI. Never merge, rebase-push, or force-push on your
  own. Status reports name the branch/worktree and how far it is ahead
  of `origin/main`.
- One logical unit — one dataset, one fix batch — is one commit. Before
  committing: show what changed, the verification results, and the
  proposed message; wait for the user's one-word ok. Unattended runs
  commit pre-approved work locally and report every commit at the next
  check-in.
- Many worktrees share this repo (`git worktree list`): the primary
  checkout `~/Projects/astro-platform` is often parked on a feature
  branch; Codex branches live under `~/Documents/standard astro 5.6/`.
  Re-check `git status` / `git log` / `git worktree list` at session
  start and after any wait — other sessions or the user may have moved
  HEAD. Audit unpushed commits you did not make before building on them.
  If an edit fails because the file changed, re-read — never force.
- After a push, watch CI to green and report; if that reply is dropped,
  lead with CI status next turn. A red daily run: check for external
  service noise (TAP/archive timeouts) and an existing triage report
  before touching code.

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
- Never guess deployment topology or env-var behavior — read `render.yaml`
  (and `docker-compose.yml`) first; guessing once broke the production
  API-key path three rounds in a row.
- Local no-auth mode is development-only. The subscription-CLI backends
  (`local:claude-cli` via `CLAUDE_CLI_ENABLED`, `local:openai-cli` via
  `OPENAI_CLI_ENABLED`; 2026-07-10) are a supported self-hosting feature:
  CLI installed and logged in on the same machine, run as a local-only
  ephemeral bridge with tools/settings/session disabled, never present on
  the hosted deployment.
- Render auto-deploy can lag behind `main`; local verification comes
  first. After a deploy lands, curl `/health` and `/health/deep` — the
  deep check once caught an expired database nobody suspected.
- Local diagnostics under `.local/` are ignored; do not upload them
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
