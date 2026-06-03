# Research Alpha Evaluator Test Report — 2026-06-01

## Summary

This report records the validation pass for the two commits that moved the
observational-cosmology blind-test workflow toward strict A-class evaluation.

Commits covered:

- `95a7f0b feat(research): surface matrix diagnostics in chat`
- `50693a0 feat(research): add strict alpha evaluator`

The changes were pushed to `origin/main` after local verification.

## What Changed

### Research Matrix / Chat Diagnostics

The first commit improves how Research Mode exposes executed matrix diagnostics
in Chat UI:

- Matrix status map.
- Posterior forest rows.
- Chain diagnostics rows.
- Deterministic chart/fallback chart data for research matrix outputs.
- Chat UI handling for `run_research_matrix` diagnostics.

This addresses the earlier issue where tool results existed but the user could
not see enough process or visual diagnostic detail.

### Strict A-Class Hidden-Answer Evaluator

The second commit adds an offline evaluator:

- `backend/app/services/research_alpha_evaluator.py`
- `backend/tests/test_research_alpha_evaluator.py`
- `docs/COSMOLOGY_PARTIAL_PASS_95_TARGET.md`

The evaluator separates:

- **A**: paper-level agreement with structured hidden-answer expectations and
  current-turn evidence.
- **B**: correct route or precise missing-capability map.
- **C**: honest failure.
- **D**: incomplete method route.
- **E**: severe failure or unsafe output.

Important rule: a hidden record with `pending_full_paper_read` can never be
graded A. It can only support partial-pass scoring until the hidden answer is
converted into structured expectations.

## Tests Run

### Backend Lint

Command:

```bash
cd backend
./.venv/bin/ruff check app/services/research_alpha_evaluator.py tests/test_research_alpha_evaluator.py
```

Result:

```text
All checks passed.
```

### Strict Alpha Evaluator Unit Tests

Command:

```bash
cd backend
./.venv/bin/pytest tests/test_research_alpha_evaluator.py -q --no-cov
```

Result:

```text
7 passed
```

Coverage intent:

- Complete structured hidden answer can score A.
- Pending hidden answer cannot score A.
- Unsupported numeric claims are severe failures.
- Numeric mismatch blocks A.
- Config-only scope gaps cannot score A.
- A requires structured data/method/model/direction expectations, not only a
  matching number.
- Batch summaries surface strict A count, B-or-better count, why-not-A reasons,
  and implementation queue items.

### Backend Research Mode Regression

Command:

```bash
cd backend
./.venv/bin/pytest tests/test_research_alpha_evaluator.py tests/test_research_program.py -q --no-cov
```

Result:

```text
59 passed
```

This confirms the new evaluator did not regress the existing Research Mode /
research matrix tests.

### Frontend Focused Tests

Command:

```bash
cd frontend
npm test -- ResearchProgramPanel ChatPage --run
```

Result:

```text
2 test files passed
45 tests passed
```

Test files:

- `src/__tests__/ResearchProgramPanel.test.tsx`
- `src/__tests__/ChatPage.test.tsx`

### Frontend Build

Command:

```bash
cd frontend
npm run build
```

Result:

```text
Build passed.
```

Non-blocking warnings:

- `HelpPage.tsx` is both dynamically and statically imported, so it cannot be
  split into a separate chunk cleanly.
- `ChatPage` remains a large bundle. This is a performance optimization item,
  not a correctness failure for this change.

### Patch Hygiene

Command:

```bash
git diff --check
```

Result:

```text
Passed.
```

## Push Status

The commits were pushed to GitHub:

```text
8a62678..50693a0  main -> main
```

## Current Assessment

The changes are mature enough for the current stage:

- The strict evaluator is tested independently.
- Research Mode regressions are green.
- Chat UI focused tests are green.
- Frontend build is green.
- No unsupported A-class claim is introduced by the evaluator.

This does **not** mean the platform has achieved A-class scientific performance
across the 50-paper blind set. It means the scoring infrastructure now prevents
B-level partial passes from being mislabeled as A-level paper agreement.

## Remaining Work Toward A-Class

The next bottleneck is not scoring. It is execution capability:

- Complete hidden-answer records for the blind-test papers.
- Add missing executable likelihoods/runners where the evaluator reports
  `execution_ready=False`.
- Add dataset/method/model expectation fields for each hidden paper.
- Improve numerical comparison tolerances per scientific quantity.
- Run the dual-model 50-paper harness again and inspect the new
  `why_not_A` aggregate.

