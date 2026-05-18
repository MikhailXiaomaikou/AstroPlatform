# _dormant_stellar Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_stellar/ stellar/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `stellar` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "stellar"`

---

## Stellar atmosphere models and synthetic spectra
For precision stellar parameters (Teff, log g, [Fe/H], v sin i) from high-res spectra:

1. Analysis framework: pysme (Piskunov & Valenti 2017 A&A 597, A16) or
   iSpec (Blanco-Cuaresma+ 2014 A&A 569, A111) — do NOT implement synthesis from scratch.
2. Model atmosphere grids (choose based on stellar type):
   - Castelli & Kurucz 2003 (ATLAS9): F/G/K dwarfs and giants, 3500-50000 K, standard
     for solar-type and warmer stars. IAU Symp 210, A20.
   - MARCS: Gustafsson+ 2008 A&A 486, 951 — 2500-8000 K, preferred for cool giants/dwarfs
   - BT-Settl/PHOENIX: Husser+ 2013 A&A 553, A6 — M/L dwarfs, including dust clouds
3. Line lists: VALD3 (Ryabchikova+ 2015 Phys. Scr. 90, 054005) is the standard
   atomic+molecular database for optical spectroscopy.
4. NLTE corrections (important for low-gravity giants, hot stars, low-metallicity):
   - Fe I/II: Mashonkina+ 2011, A&A 528, A87
   - Multi-element grids: Amarsi+ 2020 A&A 642, A62
5. Solar reference abundances:
   - Photospheric: Asplund, Grevesse, Sauval & Scott 2009 ARA&A 47, 481
   - Updated: Asplund, Amarsi & Grevesse 2021 A&A 653, A141
   - Meteoritic: Lodders 2021 Space Science Reviews 217, 44



## Stellar initial mass function (IMF)
Three standard IMF parametrizations — cite explicitly:

- Salpeter 1955 ApJ 121, 161: single power law dN/dm ∝ m^(-α) with α = 2.35.
  Valid for 0.4 < m/M_sun < 10 only; overestimates low-mass stars.
- Kroupa 2001 MNRAS 322, 231: broken power law
    α₁ = 0.3 (0.01 ≤ m/M_sun < 0.08)
    α₂ = 1.3 (0.08 ≤ m/M_sun < 0.5)
    α₃ = 2.3 (0.5 ≤ m/M_sun)
- Chabrier 2003 PASP 115, 763: lognormal for m < 1 M_sun + Salpeter above
    ξ(log m) ∝ exp[-(log m - log 0.22)^2 / (2 × 0.57^2)] for m < 1
    ξ(log m) ∝ m^(-1.3) for m ≥ 1
  This is the most commonly used IMF in current extragalactic work.

For cluster mass function fitting, use PARSEC/MIST mass-luminosity relation
+ MCMC fit to the observed CMD star counts.



## White dwarf cooling ages
For WD cooling age estimation:

1. Montreal cooling models (Bédard, Bergeron, Brassard & Fontaine 2020 ApJ 901, 93) —
   download grids from http://www.astro.umontreal.ca/~bergeron/CoolingModels/
   Do NOT refit the cooling curves.
2. Photometric WD identification from Gaia:
   Gentile Fusillo+ 2019 MNRAS 482, 4570 (Gaia DR2 catalog of 260k WD candidates),
   updated by Gentile Fusillo+ 2021 for DR3.
3. Hydrogen-atmosphere (DA) vs helium-atmosphere (DB) classification via
   Balmer vs He I lines in optical spectra.
4. Cooling age formula: WD luminosity ∝ t^(-7/5) asymptotically (Mestel 1952);
   for accurate ages use Bédard+ 2020 tables, not the analytic formula.
5. For mass-radius: Fontaine, Brassard & Bergeron 2001 PASP 113, 409 tables.
6. Luminosity function: the 1/V_max method (Schmidt 1968 ApJ 151, 393) is
   the standard way to correct for distance-limited completeness. Each
   WD contributes 1/V_max to its magnitude bin where V_max is the volume
   within which that star would have been detected given the survey
   magnitude limit: V_max = (4π/3) × d_max³ with d_max = 10^((m_lim − M)/5 + 1) pc.
   Without this correction the derived space density will be biased LOW by
   a factor of ~10-30 because faint WDs are over-represented in volume-
   limited samples at small distances. Typical solar-neighborhood WD density
   from Harris+ 2006 AJ 131, 571 and Limoges+ 2015 ApJS 219, 19 is
   (4.5-5.0) × 10⁻³ pc⁻³ total for M_G 10-16 — any result an order of
   magnitude below this suggests missing V_max correction.



## Brown dwarf (substellar) classification
For L, T, Y dwarf identification and characterization:

1. Spectral classification scheme: Kirkpatrick 2005 ARA&A 43, 195 (review).
   Primary defining features:
   - L dwarfs: VO/TiO absorption, metal hydrides (FeH, CrH)
   - T dwarfs: deep CH4 bands in H and K
   - Y dwarfs: NH3 absorption, Teff < ~500 K
2. 2MASS J-K_s color-spectral-type relation:
   Burgasser 2007 ApJ 659, 655 — polynomial fits for L0-T8
3. Gravity-sensitive indices (for young, low-gravity objects):
   Allers & Liu 2013 ApJ 772, 79 — VO index, FeH index, K I equivalent width
4. Spectral indices (literal definitions):
   - H2O index (Burgasser+ 2006): flux ratio at 1.14/1.165 μm, 1.48/1.23 μm
   - CH4 index: flux ratio at 1.56/1.66 μm
5. For LT/Y evolutionary models: Saumon & Marley 2008 ApJ 689, 1327.



## Galactic streams and substructure
For identifying stellar streams in Gaia DR3:

1. Known major streams:
   - GD-1: Grillmair & Dionatos 2006 ApJL 643, L17 — thin cold stream from SDSS
   - Sagittarius: Ibata, Gilmore & Irwin 1994 Nature 370, 194; mapped extensively
     by Majewski+ 2003 ApJ 599, 1082 with 2MASS M giants.
   - Palomar 5 tidal tails: Odenkirchen+ 2001 ApJL 548, L165
2. In situ accretion remnants:
   - Gaia-Enceladus/Sausage: Helmi+ 2018 Nature 563, 85; Belokurov+ 2018 MNRAS 478, 611
     Identified via retrograde metal-poor halo stars with high eccentricity.
   - Sequoia: Myeong+ 2019 MNRAS 488, 1235
3. Analysis in action-angle space:
   Helmi & de Zeeuw 2000 MNRAS 319, 657 — integrals of motion (E, L_z, L_⊥)
   for identifying common-origin groups.
4. Use galpy.actionAngle for computing actions in Milky Way potentials.

