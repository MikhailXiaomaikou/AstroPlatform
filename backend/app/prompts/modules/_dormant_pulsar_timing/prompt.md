# _dormant_pulsar_timing Module Prompt

**Status (M1 Phase 2, 2026-05-18)**: file scaffolded, content to be
filled in M1 Phase 4 by extracting non-cosmology sections from
backend/app/api/chat.py SYSTEM_PROMPT (lines 1080-1377 region).

**Currently DORMANT** — not loaded under ASTRO_RESEARCH_FOCUS=cosmology.

Module: **Pulsar Timing**

## Activation procedure (M5+ or strategy change)

1. `mv _dormant_pulsar_timing/ pulsar_timing/`
2. In `manifest.yaml`: `status: dormant` → `status: active`
3. Add `pulsar_timing` to the `ASTRO_RESEARCH_FOCUS` enum
4. In `chat.py`: add an if-branch in `load_active_modules()` for `focus == "pulsar_timing"`
