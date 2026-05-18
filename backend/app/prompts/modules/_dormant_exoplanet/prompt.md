# _dormant_exoplanet Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_exoplanet/ exoplanet/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `exoplanet` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "exoplanet"`

---

## Radial velocity orbit fitting
For Keplerian orbit fits to radial velocity curves (exoplanets, binary stars):

1. For exoplanets (often well-sampled): use radvel (Fulton+ 2018 PASP 130, 044504).
2. For sparse-sampling binary stars: use the-joker (Price-Whelan+ 2017 ApJ 837, 20) —
   rejection sampling over (P, e, omega, K, M_0) handles multi-modal posteriors.
3. Standard 5 Keplerian parameters: P (period), K (semi-amplitude),
   t_p (time of periastron), e (eccentricity), omega (argument of periastron).
4. Mass function (Hilditch 2001 "An Introduction to Close Binary Stars" Eq. 2.53):
     f(m) = (M_2 sin i)^3 / (M_1 + M_2)^2 = P K^3 (1-e^2)^(3/2) / (2π G)
5. For N-planet systems: radvel supports simultaneous fits; report MAP and MCMC posteriors.
6. Always report jitter (σ_jit) as a free parameter alongside K to capture
   instrumental and stellar activity noise.

