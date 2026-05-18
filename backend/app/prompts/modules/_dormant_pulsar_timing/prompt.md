# _dormant_pulsar_timing Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_pulsar_timing/ pulsar_timing/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `pulsar_timing` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "pulsar_timing"`

---

## Pulsar analysis
For pulsar timing and physics:

1. Data source: ATNF Pulsar Catalogue (Manchester, Hobbs, Teoh & Hobbs 2005 AJ 129, 1993),
   current version v1.70+. Access via `psrqpy` Python package or HTTP API.
2. DM → distance: use YMW16 electron density model (Yao, Manchester & Wang 2017 ApJ 835, 29)
   or the older NE2001 (Cordes & Lazio 2002 astro-ph/0207156). YMW16 is the modern default.
3. Derived quantities from P and P-dot (Lorimer & Kramer 2004, "Handbook of Pulsar Astronomy"):
   - Characteristic age: τ_c = P / (2 Ṗ)  (Eq. 3.16)
   - Surface dipole B field: B_s ≈ 3.2 × 10^19 × √(P Ṗ) Gauss  (Eq. 3.18)
     (assumes I = 10^45 g cm^2, R = 10 km, alpha = 90°)
   - Spin-down luminosity: Ė = 4π^2 I Ṗ / P^3, I ≈ 10^45 g cm^2  (Eq. 3.14)
4. For timing residuals and full TOA analysis: use PINT (Luo+ 2021 ApJ 911, 45)
   — NANOGrav's modern Python-based timing package.
5. P-Ṗ diagram classification: radio pulsars, millisecond pulsars, magnetars occupy
   distinct regions (see Lorimer & Kramer 2004 Fig. 1.13).

