---
name: focus-switch
description: Switch ASTRO_RESEARCH_FOCUS between cosmology / solar_system / exoplanet / all, verify the resulting tool-allowlist count, and print the SYSTEM_PROMPT size. Use when the user says "switch to <module> focus", "test the X module", or wants to verify a focus gate after editing a manifest.
---

# ASTRO_RESEARCH_FOCUS switcher + verifier

astro-platform's focus gate hides tools per module via `backend/app/prompts/modules/<focus>/manifest.yaml`. Switching is one env var, but you should verify the allowlist resolves to the expected count and the SYSTEM_PROMPT assembles without import errors.

## Expected tool counts (as of 2026-05-25)

| Focus | Allowed tools |
|---|---|
| `cosmology` | 54 |
| `solar_system` | 30 (18 core/overlap + 12 module) |
| `exoplanet` | 27 (18 core/overlap + 9 module) |
| `all` | 94 |

If the count drifts unexpectedly, the manifest was edited.

## Usage

The skill takes one argument: the focus name.

```bash
focus=$1  # cosmology / solar_system / exoplanet / all

cd /Users/chenkexuan/Projects/astro-platform/backend && ASTRO_RESEARCH_FOCUS="$focus" ./venv/bin/python3 - <<PY
import os
focus = os.environ["ASTRO_RESEARCH_FOCUS"]
from app.services.prompt_loader import build_allowed_tools, build_system_prompt
allowed = build_allowed_tools(focus)
prompt = build_system_prompt(focus)
print(f"focus={focus}")
print(f"allowed_tool_count={len(allowed)}")
print(f"system_prompt_chars={len(prompt)}")
print(f"system_prompt_tokens_approx={len(prompt) // 4}")
print()
print("allowed tools (sorted):")
for t in sorted(allowed):
    print(f"  {t}")
PY
```

## After running

If the tool count doesn't match the expected table:
1. Look at `backend/app/prompts/modules/<focus>/manifest.yaml` — count the `tools:` entries
2. Look at `backend/app/prompts/core/infrastructure.yaml` — count core tools
3. Sum should match `build_allowed_tools(focus)` output

If `build_system_prompt(focus)` raises, the prompt files have a Jinja-like placeholder that can't be resolved (e.g. `__ARCHIVE_MANIFEST__` resolver in `archive_versions.py` failed). Read the traceback.

## Local backend run with the chosen focus

```bash
cd /Users/chenkexuan/Projects/astro-platform/backend && \
  ASTRO_RESEARCH_FOCUS=<focus> ./venv/bin/python3 -m uvicorn app.main:app \
    --host 127.0.0.1 --port 8000 --reload
```

The backend is single-focus per process — restart to switch.
