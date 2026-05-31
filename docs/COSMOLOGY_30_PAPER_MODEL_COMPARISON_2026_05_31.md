# Cosmology 30-Paper Chat UI Model Comparison — 2026-05-31

## Scope

This run compared the same 30 paper-derived observational-cosmology blind prompts through the real local Chat UI using two manually selected model backends:

- DeepSeek V4 Pro (`deepseek:v4-pro`)
- local Codex/OpenAI CLI bridge (`local:openai-cli`)

Each prompt was sent in a fresh Chat UI session. The prompts did not expose paper titles, arXiv IDs, or target conclusions. Raw per-case browser text and JSON diagnostics are stored locally under:

```text
.local/blind-research-tests/round-2026-05-31-30-chat-ui/
```

Those raw artifacts are intentionally ignored by Git.

## Aggregate Results

| Metric | DeepSeek V4 Pro | Codex CLI |
|---|---:|---:|
| Total cases | 30 | 30 |
| Script errors | 0 | 0 |
| Timeouts | 0 | 0 |
| Raw backend error visible | 1 | 0 |
| Reply withheld | 0 | 0 |
| Internal/XML/tool marker leak | 0 | 0 |
| Unsupported numeric-risk flag | 0 | 0 |
| Research/robustness matrix visible | 26 | 29 |
| Fact-check visible | 29 | 30 |
| Honest scope gap visible | 30 | 30 |

## Model Differences

DeepSeek produced one raw backend failure in P02:

```text
All configured AI backends failed: deepseek:
```

This exposed a UI weakness: when no tool cards had streamed yet, the frontend still displayed the raw provider failure string. The fix sanitizes pre-tool backend failures into a user-facing message that does not expose provider internals and does not imply a scientific result.

Codex CLI completed all 30 prompts without raw backend errors after the local CLI bridge was fixed. The key bridge fix was that `local:openai-cli` now actually invokes `OPENAI_CLI_COMMAND=codex` when `OPENAI_CLI_ENABLED=1`; previously the UI could show “OpenAI CLI” while the backend silently fell back to other configured providers.

Codex CLI was more conservative and slower, but slightly more consistent on the tested structure:

- Codex CLI: 29/30 matrix visible, 30/30 fact-check visible.
- DeepSeek: 26/30 matrix visible, 29/30 fact-check visible.

DeepSeek sometimes generated richer agent paths; Codex CLI more often landed directly on the deterministic research-mode summary.

## Issues Found And Fixed

### 1. Local Codex CLI profile did not actually call Codex CLI

Before the fix, choosing `local:openai-cli` in the Chat UI did not guarantee that Codex CLI was used. The backend skipped the local backend unless `LOCAL_MODEL_ENABLED` was set and then fell through to fallback providers.

Fix:

- `LocalBackend` now detects `model_profile.id == "local:openai-cli"` plus `OPENAI_CLI_ENABLED=1`.
- It invokes `OPENAI_CLI_COMMAND` using a JSON bridge.
- The bridge supports `tool_calls`, parses common aliases, and retries once if the CLI claims tools are unavailable.

### 2. Codex CLI bridge prompt under-advertised available tools

The CLI model previously tended to answer as if Standard Astro tools were unavailable.

Fix:

- Tool specs are now serialized into the CLI prompt.
- Paper/table/cosmology workflow tools are prioritized in the prompt.
- The prompt explicitly says the backend will execute requested tool calls.
- Self-blocked responses such as “tools are not available in this backend tool list” are detected and retried with a protocol correction.

### 3. Hubble-tension direct route was too broad

The phrase “Hubble tension” forced `compare_luminosity_distances`, even for extended-model Fisher/covariance geometry requests. This caused P07 Codex to route into an irrelevant line-measurement-cache error path.

Fix:

- The direct `compare_luminosity_distances` route is now reserved for simple Planck-vs-SH0ES style H0 comparisons.
- Prompts mentioning matrix, Fisher, covariance, constant-w, curvature, or extended dark-energy context now fall through to Research Mode instead.

### 4. Frontend exposed raw provider failures

When a provider failed before any tool card streamed, the Chat UI displayed the raw backend failure.

Fix:

- Pre-tool backend failures are sanitized.
- The UI now says the selected AI backend failed before a verified final answer was produced.
- It explicitly states that no tool-grounded scientific conclusion was produced.

### 5. S8 tension comparison missed derived S8 cases

Some compressed likelihood datasets provide direct `S8`; others provide `sigma8` and `Omega_m`, from which S8 must be derived.

Fix:

- Pairwise tension logic now compares direct S8 against derived S8 where covariance support is available.
- Non-comparable cases are surfaced as such instead of being silently omitted.

## Residual Limitations

- The DeepSeek P02 raw backend failure was observed in the pre-fix run. The frontend now sanitizes this class of failure, but a full 30-case DeepSeek rerun after the UI fix has not yet been performed.
- Codex CLI remains slower than DeepSeek in this UI harness.
- The local test harness counts raw tool cards conservatively because collapsed tool cards are not expanded. This affects tool-card counts, not pass/fail classification.
- Research matrices are compressed-likelihood preliminary outputs unless explicitly labeled as full external likelihood runs.

## Validation Run

Commands run after fixes:

```bash
cd backend && ./.venv/bin/ruff check app/api/chat.py app/ai/inference_router.py tests/test_new_features.py
cd backend && ./.venv/bin/pytest tests/test_new_features.py -q --no-cov -k 'local_openai_cli_prompt_prioritizes_paper_workflow_tools or local_openai_cli_detects_backend_tool_list_self_block or CosmologyDirectRouting or local_backend_can_call_openai_cli or local_openai_cli_bridge_accepts_database_tool_aliases or local_openai_cli_retries_self_blocked_tool_refusal'
cd backend && ./.venv/bin/pytest tests/test_claim_validator.py tests/test_cosmology_likelihood_registry.py tests/test_new_features.py -q --no-cov --maxfail=1
cd frontend && npm test -- --run src/__tests__/ChatPage.test.tsx src/__tests__/ResearchStepsCard.test.tsx
cd frontend && npm run lint -- --max-warnings=0
cd frontend && npm run build
git diff --check
```

Observed result:

- Backend targeted local-CLI/direct-routing tests: 7 passed.
- Backend claim/cosmology/new-features regression: 197 passed.
- Frontend ChatPage/ResearchStepsCard tests: 40 passed.
- Frontend lint: passed.
- Frontend build: passed, with existing large-chunk warnings only.
- `git diff --check`: passed.
