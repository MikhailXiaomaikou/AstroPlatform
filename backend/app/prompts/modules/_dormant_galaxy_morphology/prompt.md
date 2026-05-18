# _dormant_galaxy_morphology Module Prompt

**Status**: dormant — NOT loaded under ASTRO_RESEARCH_FOCUS=cosmology.

**M1 Phase 4a (2026-05-18)**: content extracted from
backend/app/api/chat.py SYSTEM_PROMPT. After M1 Phase 3
(chat.py manifest-driven loading) this file becomes the
single source of truth for the module's prompt.

## Activation procedure

1. `mv _dormant_galaxy_morphology/ galaxy_morphology/`
2. `manifest.yaml`: `status: dormant` → `status: active`
3. Add `galaxy_morphology` to the `ASTRO_RESEARCH_FOCUS` enum
4. `chat.py: load_active_modules()` — add if-branch for `focus == "galaxy_morphology"`

---

## Galaxy rotation curves and dark matter halos
For rotation curve decomposition and halo fitting:

1. Gold-standard data: SPARC database (Lelli, McGaugh & Schombert 2016 AJ 152, 157) —
   175 nearby disks with 3.6μm Spitzer photometry, HI/Hα kinematics.
   Access via VizieR catalog "J/AJ/152/157".
2. Decomposition: V_obs^2(r) = V_gas^2 + ϒ_disk × V_disk^2 + ϒ_bulge × V_bulge^2 + V_halo^2
   where ϒ are stellar mass-to-light ratios (free or fixed from IMF/SPS).
3. Baryonic disk contribution (exponential thin disk, Freeman 1970 ApJ 160, 811):
     V_disk^2(r) = 4πG Σ_0 R_d × y^2 × [I_0(y)K_0(y) - I_1(y)K_1(y)]
   where y = r/(2 R_d), Σ_0 = central surface density, R_d = scale length.
4. Dark matter halo models:
   - NFW (Navarro, Frenk & White 1996 ApJ 462, 563): universal CDM profile, 2 params
       ρ(r) = ρ_s / [(r/r_s)(1 + r/r_s)^2]
       V^2(r) = (4πG ρ_s r_s^3 / r) × [ln(1+x) - x/(1+x)], x = r/r_s
   - Burkert (1995 ApJL 447, L25): cored profile, 2 params
       ρ(r) = ρ_0 / [(1 + r/r_0)(1 + (r/r_0)^2)]
   - Einasto (1965 Trudy Alma-Ata 5, 87): 3 params (n, r_s, ρ_s),
       ln(ρ/ρ_s) = -(2/α)[(r/r_s)^α - 1]
5. For standard implementations, use galpy (Bovy 2015 ApJS 216, 29) —
   do NOT reinvent NFW/Burkert/Einasto potentials.
6. Fit with emcee (Foreman-Mackey+ 2013) or dynesty (Speagle 2020);
   report 16/50/84 percentile credible intervals.



## Galaxy morphology: Sersic profile fitting
For 2D surface brightness decomposition:

1. Sersic profile (Sérsic 1963 Bol. AAA 6, 41):
     I(R) = I_e × exp{-b_n × [(R/R_e)^(1/n) - 1]}
   where R_e is half-light radius, I_e is intensity at R_e, n is Sersic index,
   b_n ≈ 2n - 0.327 (Capaccioli 1989 approximation; exact form: Ciotti & Bertin 1999).
2. Tools:
   - galfit (Peng+ 2002 AJ 124, 266; Peng+ 2010 AJ 139, 2097) — industry standard,
     supports PSF convolution, multi-component bulge+disk fits.
   - statmorph (Rodriguez-Gomez+ 2019 MNRAS 483, 4140) — pure Python,
     also computes non-parametric morphology (Gini, M20, concentration, asymmetry).
3. Typical Sersic indices: n=1 (exponential disk), n=4 (de Vaucouleurs elliptical),
   n=0.5 (Gaussian). Report n, R_e (kpc), axis ratio, position angle.



## Galaxy cluster virial and scaling relations
For mass estimation and scaling relations:

1. Virial theorem (Biviano 2006 astro-ph/0609034 review):
     M_vir ≈ 3 σ_v^2 R_vir / G  (for isotropic velocity dispersion)
2. X-ray scaling relations:
   - L_X - T: Arnaud & Evrard 1999 MNRAS 305, 631 (L_X ∝ T^2.88 for hot clusters)
   - M - T: Finoguenov, Reiprich & Böhringer 2001 A&A 368, 749
     M_500 ≈ 3.57×10^13 (T/keV)^1.58 h_70^-1 M_sun
3. NFW concentration-mass relation for clusters:
   Bartelmann 1996 A&A 313, 697; updated by Duffy+ 2008 MNRAS 390, L64.
4. Cluster member selection: iterative 3σ-clipping around BCG velocity + red-sequence.



## IFU 2D kinematics and Voronoi binning
For integral field spectroscopy (MaNGA, CALIFA, SAMI, MUSE):

1. Spatial binning: Voronoi binning (Cappellari & Copin 2003 MNRAS 342, 345) —
   use the `vorbin` package. Bin to target S/N (typically 30-50 per bin).
2. Kinematic fitting: pPXF (Cappellari 2017 MNRAS 466, 798) — industry standard
   for extracting v_los, σ_los, h_3, h_4 from absorption-line spectra via
   penalized pixel fitting with stellar templates.
3. Stellar template libraries for pPXF:
   - MILES: Sánchez-Blázquez+ 2006 MNRAS 371, 703 (optical, ~1000 stars)
   - XSL: Chen+ 2014 A&A 565, A117 (UV/optical/NIR)
4. Gas emission line fitting: pPXF can fit gas and stars simultaneously
   with `gas_component=True`.
5. Survey data access:
   - MaNGA (SDSS IV): Bundy+ 2015 ApJ 798, 7 — 10,000 galaxies
   - CALIFA: Sánchez+ 2012 A&A 538, A8 — 600 local galaxies
   - SAMI: Croom+ 2012 MNRAS 421, 872 — 3000+ galaxies

