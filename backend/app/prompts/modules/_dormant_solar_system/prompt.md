# _dormant_solar_system Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_solar_system/ solar_system/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `solar_system` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "solar_system"`

---

## Solar system objects
For asteroids, comets, TNOs:

1. Ephemeris: JPL Horizons (Giorgini+ 1996 BAAS 28, 1158) —
   authoritative solar system ephemeris, access via `astroquery.jplhorizons`.
2. Minor Planet Center (MPC): IAU official designation and orbit database.
3. H-G magnitude system (asteroid absolute magnitude):
   Bowell+ 1989 in "Asteroids II", Univ. Arizona Press —
     H = V(α) + 2.5 log10[(1-G) Φ_1(α) + G Φ_2(α)]
   where α is phase angle, G is slope parameter (default 0.15).
4. Proper vs osculating orbital elements: osculating from Horizons, proper
   from AstDyS (Knežević & Milani 2003) for dynamical family membership.
5. NEO collision probability: Öpik 1951 Proc. Royal Irish Academy 54A, 165
   (modern formulations in Morbidelli+ 2002 Icarus 158, 329).

