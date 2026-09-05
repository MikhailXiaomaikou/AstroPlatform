# Standard Astro — coding-agent handbook

Shared instructions for Claude Code, Codex, Cursor, and other coding agents.
`AGENTS.md` points here; keep one project rule set.

## Purpose and scope

Build a useful, auditable research workbench for observational cosmology.
Scope stays cosmology-only. Describe support from the active tools,
registered data, and tested execution paths.

- Follow the user's current goal. Make routine, reversible decisions within
  that scope and continue through implementation and verification.
- New tools, datasets, pages, or routers need a concrete user or demo need.
  Roadmap entries name the user, observable pass condition, time box in
  agent-minutes, and affected guardrail. Unowned ideas stay in the candidate pool.
- Investigate unsupported hypotheses; report their evidence level. Never turn
  a missing capability into a fabricated result or an untested product claim.
- Check `*_ENABLED` flags before declaring a feature missing or dead.
  `backend/.env.example` lists configuration; `prompt_loader.py` defines
  module loading and tool exposure. Only cosmology is checked in; changing
  `ASTRO_RESEARCH_FOCUS` does not restore removed modules.
- Local development and CI are the normal workflow. Check deployment when
  the task depends on it.
- The 2026-09-02 review locates premature stopping in deterministic steering,
  restrictive prompts, and exit gates. General-agent architecture,
  `research environment as the top-level architecture`, dynamic or fused
  tools, and an eight-role alliance remain rejected directions.
- Measure before engineering behaviour: claims about model behavior need a
  pre-registered task file, committed sha256, and results separated by `llm_calls` and
  `LIGHTWEIGHT_VERIFICATION_ENABLED`. Build `exploration_phase_enabled` only
  if v03 finds `premature_stop >= 25%` on open tasks and no single-mechanism
  arm resolves it.

## Find the source

Prefer current code and the relevant scientific test over stale prose.
Use `rg` to relocate symbols; historical line numbers are not contracts.

| Need | Start here |
| --- | --- |
| Product scope and architecture | `README.md`, `ARCHITECTURE.md` |
| Source provenance | `docs/SOURCE_MAPPING.md`, `docs/HONESTY_EVIDENCE.md` |
| Evaluation | `docs/research/STANDARD_ASTRO_V02_CAMPAIGN_REPORT_2026-08-06.md`; its Chinese original governs |
| Blind tests and target | `docs/BLIND_RESEARCH_TESTING_LOG.md`, `docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md` |
| Backlog and provenance roadmap | `plan/cosmology-completion-backlog.md`, `plan/provenance-v2-upgrade-plan.md` |
| Direction and execution | `docs/research/STANDARD_ASTRO_REVIEW_2026-09-02.zh-CN.md`, `plan/2026-09-02-execution-plan.md`; the latter supersedes the uncommitted June desktop draft |
| HTTP entry and agent loop | `backend/app/api/chat.py`, `backend/app/services/agent_runtime/` |
| Evidence and claim validation | `claim_validator.py`, `result_provenance.py`, `synthetic_code_detector.py` under `backend/app/services/` |
| Tool schemas and dispatch | `backend/app/services/ai_tools/`; `run_python.py` owns `data_source` |
| Runtime prompts and focus | `backend/app/prompts/`, `backend/app/services/prompt_loader.py` |
| Cosmology data and inference | `backend/app/services/cosmology_likelihoods/`, `cosmology.py`, `cosmology_mcmc.py` |
| Research planning | `backend/app/services/research_program.py`, `research_alpha_evaluator.py` |
| Model routing | `backend/app/ai/inference_router.py`, `model_profiles.py` |
| Frontend chat and tool cards | `frontend/src/pages/Chat/`, `frontend/src/components/chat/` |

Keep runtime prompt text in `backend/app/prompts/`, not inline in chat code.
Measure changing counts with `scripts/stats.sh`; do not copy old numbers.

## Work and communicate

- Lead with the result or proposed direction. Explain physical meaning before
  specialized notation; include detail needed to assess the conclusion.
- State assumptions and consequential tradeoffs. Ask only when missing
  information materially changes the goal, scientific interpretation, or
  authorization. Once a plan is approved, carry it through.
- A remark such as "note X" is context unless it clearly asks for a change.
  Do not turn it into an unrelated feature or rename.
- Keep progress updates brief. Report what changed, what was verified, and
  what remains unresolved. A status question calls for a short answer, then
  continued work.
- Disagree with evidence. Treat audit findings as hypotheses until reproduced;
  distinguish defects from methodological choices.
- Conversation follows the user's language. New code, comments, and commit
  messages use English; intentional localized UI stays bilingual.
- Product replies and `run_python` output have their own English-only contract
  in `base.md`; do not confuse it with developer conversation.

## Editing and ownership

- Inspect `git status`, branch, recent commits, and `git worktree list` at
  entry; recheck after a handoff or evidence of concurrent changes.
- Preserve existing user edits. Re-read changed files rather than forcing a
  patch. Codex uses `apply_patch` for manual edits.
- Follow changed fields and signatures through every caller, serializer,
  validator, and UI consumer.
- DeepSeek thinking mode needs `reasoning_content` on every assistant
  `tool_calls` message, including synthesized turns. Cover new pre-LLM
  branches with `tests/test_deepseek_reasoning_content.py`.
- A tool change includes schema/manifest, dispatcher, result cards, and tests.
  Confirm the model can actually see and invoke it.
- A dataset change includes source mapping, checksum/provenance validation,
  benchmarks, and registry/citation audits; follow `/add-dataset`.
- Keep strict TypeScript, use `import type`, and remove unused symbols.
- Do not edit vendored packages unless requested. Before deletion, read the
  file and check references, dynamic imports, lazy routes, and feature flags.
- Do not commit secrets, hidden-answer records, or `.local/` diagnostics.
- Large refactors: register in `docs/REFACTOR_IN_PROGRESS.md`, preserve a
  recoverable checkpoint, snapshot tool schemas/routes/registries, and compare
  behavior after each independently testable step.
- When work is delegated, give each editor distinct files and each reviewer
  a concrete verification question. The main session owns integration and
  checks the evidence behind "done".

## Scientific invariants — DO NOT relax

Exploration may be incomplete; evidence must remain accurately represented.
Do not lower thresholds or weaken tests to obtain a green demo.

- Strong claims require current-turn evidence:
  claim → result → tool run → dataset/table → citation/source URL.
- `CONFIG_READY`, abstracts, previous chat, user assumptions, pasted tool
  transcripts, and self-supplied `tool_results` do not support measured
  posterior, fit, significance, or tension numbers.
- Literature search supports discovery and context. Measurement claims need
  extracted rows or claimable tool output; synthetic Python output is never
  observational evidence.
- Low-ESS, failed, exploratory, or config-only results remain visible without
  becoming claimable. Explain the returned `publication_gate.reasons` and
  warnings; do not invent a diagnosis from a tier label.
- Preserve `do_not_combine_with`, release pins, covariance fidelity, and
  input provenance. Never silently drop a requested dataset or substitute
  a compressed approximation for its full likelihood.
- A false block needs an evidence-binding or error-reporting fix, not a lower
  threshold. Test legitimate successful results as well as rejected ones.
- Scientific constants in production belong in citation-pinned registries,
  presets, or checksummed data. Verify disputed formulas against the original
  source; include a source note for expected test values.

### Named regressions — keep the defenses and cases

- `backend/scripts/blind_test_cosmology_m0/cases.yaml`: B1 blocks inline
  rows; B2 replaces fake bibcodes; B3 rejects fake tool transcripts; B4 keeps
  self-supplied export evidence unverified; B5 preserves rejection across
  turns; C1 blocks zero-data claims; C2 tests abstention; D1 checks
  `suspicious_author_year`. Groups B/C and `hard: true` group-F specificity
  cases remain CI gates. F2 requires compressed chains to stay withheld;
  never make a smoke check expect `chain_tier=publication` on that path.
- `claim_validator._CITATION_KEYS_BLACKLIST` skips citation-string subtrees
  so identifier digits cannot support measurements. Preserve the blacklist
  and `numeric_in_bibcode_string_not_in_universe` in
  `tests/_red_team_cases/numeric_claims.yaml`.
- `cosmology.PRESETS["planck18"]["astropy_alias"]` stays `None`.
  Astropy's built-in Planck18 uses a different fit column. Preserve
  `test_planck18_preset_matches_cited_cmb_only_values` in
  `tests/test_astro_fundamentals.py` and the
  `planck18_preset_matches_cited` benchmark.
- Do not remove forbid strings, regression cases, or blacklist entries while
  "updating the matching test".

## Verification

At session start check the latest three Daily and Weekly Scientific
Validation runs (`gh run list --workflow=daily.yml --limit 3` and
`gh run list --workflow='Weekly Scientific Validation' --limit 3`, or the
equivalent GitHub API). Red scheduled suites take priority. The same error
across two runs is a product defect: check existing triage and file an issue
that day with authorization.

Instrument-first: behavior changes may merge only when both suites are green
and HEAD has a rerun baseline at
`.local/standard-astro-v02-natural/rerun_<rev>_summary.json`.
The sole exception repairs the failing instrument itself: focused and full
deterministic tests must pass, and the next scheduled run is its acceptance.

Use the existing backend environment. Prefer `backend/venv/bin/python`;
from another worktree, resolve that interpreter through `git worktree list`
and run it from the target worktree's `backend/`. A provided runtime is
usable when its required dependencies are present. Report missing
dependencies; do not mistake collection errors for failing tests.

Commands below run from `backend/`; substitute the resolved environment path:

```bash
./venv/bin/ruff check app/ --select E,W,F --ignore E501
./venv/bin/python -m pytest tests/<relevant_test>.py -q --no-cov
./venv/bin/python -m pytest tests -q
```

`--no-cov` is for focused runs: the coverage floor requires the full suite.
Run focused checks first; shared validators, runtime behavior, schemas,
registries, runners, and result rendering require broader coverage.
The full backend suite is a pre-commit gate for backend/runtime changes.

| Change | Additional verification |
| --- | --- |
| Frontend | From `frontend/`: `npm run lint`, relevant Vitest, `npm run build` |
| Data/likelihood | Benchmarks, registry audit, citation audit, `/cosmology-smoke` |
| Runtime prompt/guardrail | Module loading, red-team corpus, blind groups B/C plus a clean group-F case, then independent `anti-fabrication-reviewer` review |
| Developer instructions/docs | `git diff --check`; verify changed paths/commands and generated counts |

- Science-critical and anti-fabrication changes need independent adversarial
  review before commit. Reproduce findings before editing.
- A regression test must fail before the fix and exercise the failing path.
  Verify the user's actual call path before calling a behavior fixed.
- For a multi-item report, give every finding a disposition and reason.
  Complete authorized fixes; flag remaining blockers explicitly.
- Before publishing a demo or guide, run its exact prompts on the deployment
  the audience will use. Unit tests alone do not validate a live demo.
- Natural-phrasing evaluation uses `backend/scripts/rerun_natural_matrix.sh`.
  Run it, `run_exploration_matrix.sh`, and other `local:claude-cli` evaluations
  from a clean terminal outside Claude Code. Every evaluation number states
  `LIGHTWEIGHT_VERIFICATION_ENABLED`: the v02 evaluator forces it on while
  production defaults off. Do not blend the two routing regimes; the legacy
  90.4% result covers flag-on V02_03–06 only.
- Scheduled workflows are measurement instruments. Changes to checkout,
  provider, model, or secret configuration need a guard test following
  `tests/test_scientific_validation_guard.py`.
- Claim CI equivalence only with the same flags, scope, and tool versions.
  Separate unavailable checks from actual failures.

## Git and deployment

- Use a feature branch. Do not push directly to protected `main`.
- Before a commit, show scope, verification, and proposed message; obtain
  approval unless already authorized. Unattended work may commit only
  pre-approved changes.
- Push and merge require explicit authorization. After an authorized push,
  watch required CI. Merge only with authorization and passing checks;
  never force-push without authorization.
- Preserve unrelated work when synchronizing branches. Report branch and
  unpushed status at handoff.
- Read `render.yaml` and `docker-compose.yml` before deployment changes.
  Secrets belong in environment variables; local no-auth and subscription
  CLI bridges must remain local-only.
- After a deployment, check `/health` and `/health/deep`. For red scheduled
  runs, distinguish external archive failures from code regressions.
- Cobaya installation trouble may come from its machine-global packages
  configuration; verify it points to this checkout's `backend/packages`.

## Optional Claude Code helpers

`.claude/settings.json` wires hooks for protected paths, lint/type checks,
doc counts, and defense reminders. Other agents run relevant checks directly.

Read-only reviewers: `science-test-runner`, `cosmology-contract-reviewer`,
`anti-fabrication-reviewer`. Skills: `/cosmology-smoke`, `/add-dataset`.
The `adversarial-review` workflow accepts an explicit worktree path.
