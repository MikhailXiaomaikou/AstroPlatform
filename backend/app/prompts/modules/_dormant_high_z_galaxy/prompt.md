# _dormant_high_z_galaxy Module Prompt

**Status (M1 Phase 2, 2026-05-18)**: file scaffolded, content to be
filled in M1 Phase 4 by extracting non-cosmology sections from
backend/app/api/chat.py SYSTEM_PROMPT (lines 1080-1377 region).

**Currently DORMANT** — not loaded under ASTRO_RESEARCH_FOCUS=cosmology.

Module: **High-z Galaxy (line family lookup helper, no LLM tools)**

## Activation procedure (M5+ or strategy change)

1. `mv _dormant_high_z_galaxy/ high_z_galaxy/`
2. In `manifest.yaml`: `status: dormant` → `status: active`
3. Add `high_z_galaxy` to the `ASTRO_RESEARCH_FOCUS` enum
4. In `chat.py`: add an if-branch in `load_active_modules()` for `focus == "high_z_galaxy"`
