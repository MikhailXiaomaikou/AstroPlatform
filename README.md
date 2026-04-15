# Standard Astro

**AI-native professional astronomy research platform** — data discovery, spectral/photometric analysis, statistical inference, visual pipelines, team collaboration, and publication export in one unified web interface.

Built with React 19 + FastAPI + **52 AI tools** + **35 pipeline nodes** + **23 data archive connectors** + **literature-cited workflow guidance** across 16 astronomy research domains.

## Core Workflows

| Module | Description |
|--------|-------------|
| **Data Browser** | Query 23 astronomical archives from one place. Inspect merged results, preview FITS headers/spectra/images, cross-match catalogs, and fetch files into your workspace. |
| **AI Assistant** | 52-tool research agent that auto-selects the right data source, writes ADQL, analyzes spectra, fits isochrones/transits/RV orbits, computes SFR, runs Python, builds pipelines, reviews literature, and drafts papers. Now with object-class-specific workflows (open clusters / globular clusters / RR Lyrae / Cepheids / EB / galaxies / X-ray sources / pulsars / white dwarfs / ...). |
| **Pipeline Studio** | Visual DAG editor with 35 node types spanning CCD reduction, spectroscopy, photometry, time-domain analysis, image processing, and Bayesian inference. |
| **ADQL Query** | Multi-service TAP editor with syntax highlighting, template library, and federated queries across Gaia DR3, SIMBAD, VizieR, CADC, and NED — with automatic retry on timeout (reducing cone radius). |
| **Workspace** | Persistent file storage for FITS, VOTable, and analysis results. Batch search, saved searches, and data export. |
| **Team** | Real-time collaboration via WebSocket: shared pipelines/datasets, presence tracking, live comments, and session forking. |
| **Observations** | Transient alert dashboard (ZTF/TNS) with spectroscopic classification, anomaly detection, and follow-up recommendations. |

## Scientific Capabilities

### Data Access (23 connectors + VO protocols)

**Optical/NIR spectroscopy and photometry**
- SDSS, Gaia DR3, SIMBAD, VizieR, LAMOST DR9, DESI EDR, Pan-STARRS, 2MASS

**Space observatories**
- MAST (HST, Kepler, TESS), JWST, ESO (VLT, MUSE, VISTA), IRSA, XMM-Newton, Chandra

**Multi-wavelength**
- NED, AllWISE, ALMA

**Radio**
- NVSS, FIRST (spectral index + luminosity analysis)

**Specialized / domain-specific** (new)
- **JPL Horizons** — solar system ephemerides (via `astroquery.jplhorizons`)
- **ATNF Pulsar Catalogue** — 3,400+ radio pulsars with P, Ṗ, DM, YMW16/NE2001 distances (via `psrqpy`)
- **SPARC** — 175 disk-galaxy rotation curves from Lelli+ 2016 AJ 152, 157
- **FRBSTATS** — CHIME/FRB public catalogue of fast radio bursts

**VO Standards:** SIA v2, SSA, federated TAP, SAMP bidirectional, VOTable import/export, registry discovery (pyvo)

### Gaia DR3 Specialized Tables (AI knows about them)

The AI assistant can select the right Gaia table for the job:

| Table | Use case |
|---|---|
| `gaiadr3.gaia_source` | General: positions, parallax, photometry, gspphot |
| `gaiadr3.vari_rrlyrae` | RR Lyrae periods, types (RRab/RRc), amplitudes |
| `gaiadr3.vari_cepheid` | Classical / Type II / anomalous Cepheids |
| `gaiadr3.vari_eclipsing_binary` | EB periods and morphology |
| `gaiadr3.vari_long_period_variable` | Mira / SR variables |
| `gaiadr3.vari_summary` | Generic variability indicators |
| `gaiadr3.nss_two_body_orbit` | Spectroscopic/astrometric binary orbit solutions |
| `gaiadr3.binary_masses` | Resolved/unresolved binary mass solutions |
| `gaiadr3.galaxy_candidates` | Extended-source classification |
| `gaiadr3.qso_candidates` | QSO candidates with Gaia astrometry |
| `gaiadr3.astrophysical_parameters` | Full GSP-Phot/GSP-Spec/MSC parameter set |

### Object-Class Workflows (system prompt guidance)

The AI assistant has explicit, literature-cited workflows for:

- **Open clusters** (young / intermediate, < 2 Gyr, < 2 kpc) — NGC 1647, Pleiades, Hyades
- **Globular clusters** (old, > 5 Gyr, > 5 kpc) — M53, M13, 47 Tuc
- **RR Lyrae variables** — Oosterhoff classification, Muraveva+ 2018 P-L-Z relation
- **Cepheids** — Ripepi+ 2019 Leavitt law (classical + Type II)
- **Eclipsing binaries** — `vari_eclipsing_binary` + NSS orbital solutions
- **Galaxies** — SFR calibrations from Kennicutt & Evans 2012 ARA&A Table 1 (7 bands)
- **Galaxy rotation curves** — SPARC database + NFW/Burkert/Einasto halo models
- **Galaxy morphology** — Sersic profiles via `galfit`/`statmorph`
- **AGN** — BPT classification, CIGALE SED fitting, Shen+ 2011 QSO catalog
- **X-ray sources** — Sherpa spectral fitting (phabs*powerlaw, phabs*apec, phabs*(diskbb+powerlaw))
- **Pulsars** — ATNF catalogue + Lorimer & Kramer 2004 derived quantities
- **White dwarfs** — Bédard+ 2020 Montreal cooling tables
- **Brown dwarfs** — Kirkpatrick 2005 L/T/Y classification
- **IFU spectroscopy** — Voronoi binning + pPXF (Cappellari 2017)
- **Galactic streams** — GD-1, Sagittarius, Gaia-Enceladus analysis
- **Solar system** — JPL Horizons ephemerides, MPC designations
- **Stellar atmospheres** — ATLAS9 / MARCS / PHOENIX + pysme / iSpec

### Spectral Analysis (specutils + NIST)

- Line identification against 90-line NIST catalog (UV through NIR)
- Gaussian / Lorentzian / Voigt profile fitting via specutils
- Equivalent width measurement, continuum normalization
- Velocity dispersion via cross-correlation (log-wavelength CCF)
- Heliocentric/barycentric velocity correction
- Auto flux calibration (3 standard star reference tables)
- IFU datacube support: spaxel/aperture extraction, Voronoi binning, 2D velocity maps, emission line ratio maps (BPT diagnostics)

### Photometry (photutils)

- PSF photometry (DAOStarFinder + IntegratedGaussianPRF)
- Multi-aperture photometry with local background subtraction
- Source extraction and deblending (photutils segmentation)
- **Auto zero-point** determination via Gaia/SDSS cross-match
- Extinction correction with IRSA dust map E(B-V) auto-lookup (SFD 1998 / Schlafly 2011)
- PSF matching across bands
- Galaxy Sersic profile fits via `statmorph` (Rodriguez-Gomez+ 2019)

### Photometric Redshifts

- 30 parametric SED templates (E through starburst, AGN, post-starburst, LIRG/ULIRG)
- Calzetti 2000 dust attenuation with E(B-V) grid search
- Madau 1995 IGM absorption for z > 0.1
- Emission line contributions scaled by UV luminosity
- Bayesian magnitude prior (simplified from Benitez 2000)
- Full P(z) output with 68% confidence intervals
- Standard quality metrics: sigma_MAD, NMAD, outlier fraction

### Isochrone Fitting (PARSEC CMD 3.9 + turnoff fallback)

- Real PARSEC 1.2S isochrones (Bressan+ 2012) via CMD 3.9 API
- Auto-extract `bp_rp` + `abs_mag` from last Gaia query (no manual prep)
- Auto-estimate distance modulus from median parallax
- 4-D grid search over age × metallicity × DM × A_V
- **Fallback**: PARSEC-calibrated turnoff-magnitude → log(age) lookup table
  (calibrated against Pleiades, NGC 1647, Hyades, NGC 752, M67, NGC 188)
- Gaia DR3 extinction coefficients from Wang & Chen 2019 (A_G/A_V = 0.789)
- Empirical +0.3 mag binary bias correction (physically motivated)
- Validated to ±15% over 70 Myr to 7 Gyr age range

### Stellar Atmospheres and Synthetic Spectra

- Framework: `pysme` (Piskunov & Valenti 2017) or `ispec` (Blanco-Cuaresma+ 2014)
- Grids: Castelli & Kurucz 2003 (ATLAS9), Gustafsson+ 2008 (MARCS), Husser+ 2013 (PHOENIX)
- Line list: VALD3 (Ryabchikova+ 2015)
- NLTE corrections: Mashonkina+ 2011, Amarsi+ 2020
- Solar abundance reference: Asplund+ 2009 / 2021

### Statistical Inference (dynesty + ArviZ)

- MCMC sampling (emcee)
- Nested sampling for Bayesian evidence (dynesty, ultranest)
- Bayes factor computation with Jeffreys scale interpretation
- Chain diagnostics: R-hat, ESS, MCSE via ArviZ (updated for new arviz API)
- Posterior predictive checks
- Model comparison tables (AIC/BIC/WAIC/LOO)
- Monte Carlo error propagation and bootstrap resampling

### Time-Domain Astronomy (batman + celerite2)

- Lomb-Scargle periodogram + BLS transit search
- GP detrending via celerite2 (Matern32, SHO, rotation kernels)
- Transit model fitting via batman (Rp/Rs, a/Rs, inclination)
- **RV orbit fitting** via radvel (Fulton+ 2018) with auto-period init from Lomb-Scargle
- Stellar flare detection with amplitude/duration/energy measurement
- Phase folding with interactive period adjustment
- Variable star classification

### Galaxy Dynamics (new — galpy)

- Rotation curve decomposition: `V_obs² = V_gas² + ϒ_disk × V_disk² + ϒ_bulge × V_bulge² + V_halo²`
- Halo models: NFW (Navarro+ 1996), Burkert (1995), Einasto (1965)
- Freeman 1970 exponential disk
- MCMC/nested-sampling fits with emcee/dynesty
- SPARC database access via VizieR catalog J/AJ/152/157

### X-Ray Spectral Analysis (new — Sherpa)

- Sherpa 4.18 + standard XSPEC-style models (phabs, apec, powerlaw, diskbb)
- Galactic absorption column from HI4PI (HI4PI Collab 2016)
- Wilms+ 2000 abundances for tbabs
- C-stat (Cash 1979) for low-count Poisson data
- 90% confidence intervals via `conf` method

### Star Formation Rate (new — K&E 2012)

Literature-cited calibrations from Kennicutt & Evans 2012 ARA&A 50, 531 Table 1:

| Band | log C | L units |
|------|------|---------|
| H-α | 41.27 | erg/s |
| FUV (1500 Å) | 43.35 | νL_ν erg/s |
| NUV (2300 Å) | 43.17 | νL_ν erg/s |
| TIR (8-1000 μm) | 43.41 | erg/s |
| 24 μm | 42.69 | νL_ν erg/s |
| 70 μm | 43.23 | νL_ν erg/s |
| 1.4 GHz | 28.20 | L_ν erg/s/Hz |

Dust correction via Balmer decrement (Osterbrock 1989) or UV slope β (Meurer+ 1999). Kroupa IMF, 0.1–100 M⊙.

### Pulsar Derived Quantities (new)

From Lorimer & Kramer 2004 "Handbook of Pulsar Astronomy":
- Characteristic age: τ_c = P / (2·Ṗ)
- Surface dipole B: B_s ≈ 3.2×10¹⁹ √(P·Ṗ) Gauss
- Spin-down luminosity: Ė = 4π²·I·Ṗ/P³ (I = 10⁴⁵ g cm²)

Validated against Crab pulsar (τ_c = 1253 yr, B_s = 3.8×10¹² G — matches literature).

### White Dwarf Cooling Ages (new)

13-point log-log interpolation table from **Bédard+ 2020 ApJ 901, 93 Montreal cooling models** (DA, 0.6 M⊙).
M_G → T_eff conversion from Tremblay+ 2019. Supports DA/DB atmospheres and mass scaling.

### Image Processing (reproject + photutils)

- Full CCD reduction pipeline: bias, dark, flat, CR rejection, WCS
- Cosmic ray removal (astroscrappy / LACosmic)
- Astrometric solution via astrometry.net
- Image reprojection and mosaicking (reproject)
- PSF matching with Gaussian kernels
- Source deblending with morphological parameters
- WCS-aware cutout extraction

### Transient Science

- ZTF + TNS + Lasair + GCN alert ingestion
- Photometric classification (Random Forest, 8 classes)
- **Spectroscopic classification** (template matching: SN Ia/II/Ib-c/TDE/AGN/Nova)
- Host galaxy identification via SIMBAD
- Light curve feature extraction and follow-up recommendations

### Literature & Citations

- NASA ADS search with citation-count ranking
- arXiv full-text extraction
- **Citation network graph** construction (references + cited-by)
- AI-powered bibliography synthesis
- BibTeX export, LaTeX paper generation (AASTeX/MNRAS/A&A)

### Reproducibility & Provenance

- Automatic provenance recording per pipeline node (IVOA ProvDM)
- Environment snapshots (pinned pip versions, Python version, platform)
- DOI-ready metadata generation (DataCite compatible)
- Reproducibility package export (DAG + params + environment + instructions)
- Jupyter notebook export (pipeline / chat / search workflows)

## AI Assistant — 52 Tools

The AI assistant can invoke any platform capability. All tools are literature-cited where applicable.

| Category | Tools |
|----------|-------|
| **Search** | search_objects, run_adql (with auto timeout retry), get_object_info, get_object_dossier, batch_object_search, query_vo_service |
| **Spectroscopy** | analyze_spectrum, analyze_spectrum_pro, classify_transient_spectrum |
| **Photometry** | extract_photometry, extract_sources, estimate_photo_z, estimate_photo_z_pro |
| **Galaxy analysis** (NEW) | **compute_galaxy_sfr** (K&E 2012), **fit_sersic_morphology** (statmorph) |
| **Time-Domain** | search_lightcurve, gp_detrend_lightcurve, fit_transit_model, detect_stellar_flares, transit_search_bls |
| **RV orbits** (NEW) | **fit_rv_orbit** (radvel + auto period init) |
| **X-ray** (NEW) | **x_ray_spectral_fit** (Sherpa) |
| **Pulsars** (NEW) | **pulsar_derived_quantities** (Lorimer & Kramer 2004) |
| **Clusters** | fit_isochrone (PARSEC + turnoff fallback), auto-extract from Gaia |
| **Image** | reduce_ccd_image, solve_astrometry, process_image |
| **Statistics** | validate_analysis, sensitivity_analysis |
| **Pipeline** | generate_pipeline, run_pipeline, modify_pipeline |
| **Literature** | search_literature, read_arxiv_paper, literature_review |
| **Transients** | query_transients, classify_transient |
| **Radio** | radio_analysis (spectral index / luminosity / crossmatch) |
| **Code** | run_python (sandbox: numpy/scipy/astropy/specutils/dynesty/dask + 25+ new packages; see below) |
| **Collaboration** | share_with_team, invite_team_member |
| **Export** | export_results, workspace_export, read_fits_header, get_provenance |
| **Research** | generate_paper_draft, generate_proposal, research_workflow, analyze_cross_wavelength, crossmatch_catalogs, get_followup_recommendation, get_last_search_results |

### Python Sandbox (run_python tool)

Available libraries now include:

**Core scientific stack** (existing): numpy, scipy, astropy, specutils, photutils, reproject, dask, pandas, matplotlib, sklearn, emcee, corner, dynesty, ultranest, arviz, celerite2, batman, pyvo

**Added in the knowledge-base expansion:**
- **Sherpa** (Doe+ 2007) — X-ray spectral fitting
- **radvel** (Fulton+ 2018) — exoplanet RV orbits
- **thejoker** (Price-Whelan+ 2017) — sparse binary RV sampling
- **galpy** (Bovy 2015) — galactic dynamics, NFW/Burkert halos
- **pysme** (Piskunov & Valenti 2017) — stellar parameter fitting
- **statmorph** (Rodriguez-Gomez+ 2019) — galaxy morphology
- **vorbin** (Cappellari & Copin 2003) — Voronoi 2D binning
- **ppxf** (Cappellari 2017) — kinematic fitting from absorption lines
- **astroquery** (Ginsburg+ 2019) — IVOA database wrappers (JPL, MPC, VizieR, SIMBAD, NED, Gaia, MAST, IRSA)
- **dustmaps** (Green 2018) — SFD, Bayestar, Planck dust maps
- **healpy** (Górski+ 2005) — HEALPix spherical data
- **pint** (Luo+ 2021) — pulsar timing
- **psrqpy** (Pitkin 2018) — ATNF pulsar catalogue interface
- **lenstronomy** (Birrer & Amara 2018) — strong gravitational lensing
- **MulensModel** (Poleski & Yee 2019) — microlensing
- **treecorr** (Jarvis 2015) — 2-point correlation functions
- **yt** (Turk+ 2011) — N-body / hydrodynamic simulation post-processing
- **scikit-image** — image restoration (Lucy-Richardson deconvolution)
- **warnings** (stdlib) — now permitted

## Data Quality Guardrails

The AI assistant has explicit guardrails against common pitfalls:

1. **Gaia GSP-Phot warnings**: The convenience columns `teff_gspphot`, `mh_gspphot`, `ag_gspphot`, `ebpminrp_gspphot` are model fits with systematic biases for:
   - Distant objects (>5 kpc)
   - Low metallicity ([Fe/H] < -1.5)
   - Crowded fields (globular cluster cores)
   - Faint stars (G > 18)
   - Hot stars (T_eff > 8000 K)

   → Routed to SIMBAD literature values, LAMOST/APOGEE spectra, or SFD dust maps instead.

2. **Extinction routing for low-E(B-V) targets**: For |b|>20° / d<500pc / globular clusters, the AI **must** use `lookup_ebv_irsa` (SFD 1998) instead of `ag_gspphot` which over-estimates A_V by 5-6× for low-extinction targets. Benchmark values hardcoded into the prompt:
   - Pleiades A_V = 0.12, M53 A_V = 0.06, Hyades A_V = 0.03

3. **Distance estimation hierarchy** (by distance range):
   - < 100 pc → Gaia parallax direct
   - 100 pc – 3 kpc → Lindegren+ 2021 zero-point + Bailer-Jones geometric distance when σ_plx/plx > 10%
   - 3 – 30 kpc → Standard candles (RR Lyrae P-L, Cepheid P-L, red clump, TRGB)
   - > 30 kpc → Extragalactic distance ladder (Cepheids, SN Ia, SBF, Tully-Fisher)
   - cosmological → astropy.cosmology FlatLambdaCDM

4. **No simulated/synthetic data**: The system prompt forbids the AI from silently falling back to mock data when queries fail. It must say "could not retrieve X from Y" and propose alternatives.

5. **Blue straggler selection**: The AI has explicit BSS criteria (brighter AND bluer than MSTO but within physical envelope), avoiding the common mistake of requiring BP-RP<0 which misses most BSS.

6. **Variable star periods**: For RR Lyrae / Cepheid / EB analysis, the AI always queries the dedicated `gaiadr3.vari_*` tables for published periods instead of re-deriving from photometry.

7. **Empty AI response fallback**: If the language model returns zero text, the chat loop synthesizes a minimal summary from executed tool results so the user never sees a blank AI bubble.

## Physics Formulas — Literature Audit Status

All astronomy formulas in the codebase have been audited against published references. Each formula is cited in the code comments (author + year + journal + page). Notable entries:

| Formula | Reference | Location |
|---|---|---|
| Distance modulus | Hipparcos/Gaia standard | `astro_analysis.py` |
| CCM89 extinction curve | Cardelli, Clayton & Mathis 1989 ApJ 345, 245 | `astro_analysis.py` |
| Calzetti 2000 attenuation | Calzetti+ 2000 PASP 112, 1547 | `photo_z_pro.py` |
| Madau 1995 IGM | Madau 1995 ApJ 441, 18 | `photo_z_pro.py` |
| Gaia extinction coefficients (A_G/A_V=0.789) | Wang & Chen 2019 ApJ 877, 116 | `astro_analysis.py` |
| PARSEC isochrones | Bressan+ 2012 MNRAS 427, 127 | `parsec_fetcher.py` |
| PARSEC turnoff lookup table | Bressan+ 2012 (calibrated) | `ai_tools.py` |
| SFR calibrations (7 bands) | Kennicutt & Evans 2012 ARA&A 50, 531 | `ai_tools.py` |
| Pulsar τ_c, B_s, Ė | Lorimer & Kramer 2004 handbook | `ai_tools.py` |
| RR Lyrae P-L-Z | Muraveva+ 2018 MNRAS 481, 1195 | system prompt |
| Cepheid Leavitt law | Ripepi+ 2019 A&A 625, A14 | system prompt |
| NFW halo profile | Navarro, Frenk & White 1996 ApJ 462, 563 | system prompt |
| Mass function (binary) | Hilditch 2001 Eq 2.53 | `ai_tools.py` |
| Stetson K variability index | Stetson 1996 PASP 108, 851 | `transient_classifier.py` |
| WD Montreal cooling | Bédard+ 2020 ApJ 901, 93 | `astro_analysis.py` |
| BSS selection | Rain+ 2021 A&A 650, A67 | system prompt |
| Kennicutt & Evans 2012 SFR | ARA&A 50, 531 Table 1 | system prompt + tool |

The audit specifically removed LLM-hallucinated values (e.g. the old "Casagrande & VandenBerg 2018" mis-attribution of 0.836 was from Jordi+ 2010; the `M_V = M_G + 0.2` bolometric correction had the wrong sign).

## Tech Stack

| Layer | Stack |
|-------|-------|
| Frontend | React 19, TypeScript strict, Vite, React Router, React Flow, Plotly, Aladin Lite |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2, SSE streaming |
| AI | Claude (default), OpenAI, DeepSeek, local model backends |
| Astronomy core | astropy, astroquery, specutils, photutils, reproject, emcee, dynesty, ArviZ, batman, celerite2, lightkurve, pyvo, sep, dust_extinction, dask |
| Astronomy extended (expanded sandbox) | sherpa, radvel, thejoker, galpy, pysme, statmorph, vorbin, ppxf, dustmaps, healpy, pint, psrqpy, lenstronomy, MulensModel, treecorr, yt |
| Background | Celery + Redis (optional, sync fallback) |
| Storage | PostgreSQL (prod) / SQLite (dev), local filesystem for FITS |
| Auth | JWT + bcrypt + Google OAuth, Fernet-encrypted API keys |
| Deployment | Render blueprint (render.yaml), Docker Compose |
| i18n | 4 languages (English, Chinese, French, Spanish) |
| Testing | 750 tests (697 passing backend pytest + 29 E2E integration + frontend vitest) |

## Repository Layout

```text
backend/
  app/
    ai/                 Routed inference + orchestrator + specialist agent prompts
    analysis/           CCD reduction and image-analysis helpers
    api/                28 FastAPI routers (auth, chat, data, pipeline, export, provenance, ...)
    connectors/         23 archive adapters (+4 new: jpl, atnf_pulsar, sparc, frbstats)
    middleware/         Request tracking + correlation ID middleware
    models/             SQLAlchemy models (20+ tables) + DB bootstrap
    pipeline/
      nodes/            35 pipeline node types
      engine.py         DAG executor with caching + provenance recording
    services/           30 service modules (AI tools, spectral analysis, photo-z, Bayesian,
                        time-domain, image processing, provenance, transient, literature, ...)
  data/
    line_catalogs/      NIST spectral line catalog (90 lines)
  tests/                697 backend tests + 29 E2E tests
  .venv/                Python environment with 19 newly-installed astronomy packages

frontend/
  src/
    api/                Typed API client (axios + SSE streaming)
    components/
      viz/              SpectrumViewer, LightCurveViewer, ImageCutoutViewer,
                        MCMCDiagnostics, PlotBuilder, AladinViewer, ProvenanceGraph
      nodes/            Pipeline node palette + parameter editor (35 types)
      collab/           PresenceBar (real-time team presence)
      fits/             FITS browser + preview (multi-HDU)
      chat/             MarkdownText (GFM tables, strikethrough, code blocks)
    pages/              DataBrowser, Pipeline, Chat, ADQL, Workspace, Team,
                        Account, Observations, Auth, Landing, Help, SharedSession
    i18n/               Translation keys × 4 languages
    __tests__/          Frontend test files
```

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+
- Redis (optional, for Celery async pipelines)

### Local Development

```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional: install the expanded astronomy package set
pip install sherpa radvel thejoker galpy pysme statmorph vorbin ppxf \
            astroquery dustmaps healpy pint psrqpy lenstronomy MulensModel \
            treecorr yt scikit-image
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

Frontend: `http://localhost:5173` | Backend: `http://localhost:8000`

## Environment Variables

### Required (production)

```bash
DATABASE_URL=postgresql+asyncpg://...
JWT_SECRET=<random-hex-32>
CORS_ORIGINS=https://your-frontend.example
```

### AI Backends

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...           # optional
DEEPSEEK_API_KEY=...            # optional
```

### Optional Services

```bash
ADS_API_KEY=...                  # NASA ADS citation search
REDIS_URL=redis://...            # caching + Celery
PIPELINE_MODE=celery             # async pipeline execution
GOOGLE_CLIENT_ID=...             # Google OAuth
GOOGLE_CLIENT_SECRET=...
ASTROMETRY_API_KEY=...           # astrometry.net WCS solving
FERNET_KEY=...                   # API key encryption (auto-generated if not set)
ADMIN_SECRET=...                 # admin endpoint access
DOCKER_IMAGE_DIGEST=...          # reproducibility tracking
```

### Frontend

```bash
VITE_API_URL=https://your-backend.example
VITE_GOOGLE_CLIENT_ID=...
```

## Testing

```bash
# Backend (697 pytest + 29 E2E)
cd backend && .venv/bin/python -m pytest tests/ -q --no-cov

# E2E smoke tests
cd backend && .venv/bin/python -m pytest tests/test_e2e_full.py \
    -m "integration and not network" -q --no-cov

# Frontend
cd frontend && npm test -- --run

# Build check
cd frontend && npm run build
```

## Deployment

### Render (recommended)

`render.yaml` defines the full infrastructure:
- FastAPI web service + Celery worker + Celery beat
- React static frontend with SPA rewrites
- PostgreSQL + Redis

### Docker Compose

```bash
docker compose up -d
```

## Documentation

- Architecture: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Quick start: [docs/QUICKSTART.md](./docs/QUICKSTART.md)
- API reference: [docs/API_REFERENCE.md](./docs/API_REFERENCE.md)
- Deployment: [DEPLOY_OPENCLAW.md](./DEPLOY_OPENCLAW.md)
- Development notes: [CLAUDE.md](./CLAUDE.md)

## License

Released under the MIT License. See [LICENSE](./LICENSE) for details.

Contributions, issues, and pull requests are welcome.
