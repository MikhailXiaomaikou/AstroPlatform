# _dormant_agn Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_agn/ agn/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `agn` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "agn"`

---

## AGN SED decomposition
For separating AGN and host galaxy contributions:

1. Full SED fitting: CIGALE (Boquien+ 2019 A&A 622, A103) — Bayesian SED fitting
   with AGN + stellar + dust components, Python package.
2. QSO composite template: Vanden Berk+ 2001 AJ 122, 549 (SDSS median composite).
3. Dust torus models:
   - Smooth torus: Fritz, Franceschini & Hatziminaoglou 2006 MNRAS 366, 767
   - Clumpy torus: Nenkova+ 2008 ApJ 685, 147 (CLUMPY code)
4. QSO property catalogs:
   - Shen+ 2011 ApJS 194, 45 — SDSS DR7 quasar properties (BH mass, L_bol, etc.)
   - Rakshit+ 2020 ApJS 249, 17 — SDSS DR14 QSO catalog
5. BH mass via single-epoch virial estimator (Vestergaard & Peterson 2006 ApJ 641, 689):
     log(M_BH/M_sun) = a + b log(L_λ / 10^44) + 2 log(FWHM / km/s)
   Line-dependent coefficients: Hβ (a=6.91, b=0.5), Mg II (a=6.86, b=0.5), C IV (a=6.66, b=0.53).

