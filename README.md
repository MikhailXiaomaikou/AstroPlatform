# Standard Astro

**AI-native professional astronomy research platform** — data discovery, spectral/photometric analysis, statistical inference, visual pipelines, team collaboration, and publication export in one unified web interface.

Built with React 19 + FastAPI + 46 AI tools + 35 pipeline nodes + 19 data archive connectors.

## Core Workflows

| Module | Description |
|--------|-------------|
| **Data Browser** | Query 19 astronomical archives from one place. Inspect merged results, preview FITS headers/spectra/images, cross-match catalogs, and fetch files into your workspace. |
| **AI Assistant** | 46-tool research agent that can search catalogs, write ADQL, analyze spectra, fit transits, estimate photo-z, run Python code, build pipelines, review literature, and draft papers. |
| **Pipeline Studio** | Visual DAG editor with 35 node types spanning CCD reduction, spectroscopy, photometry, time-domain analysis, image processing, and Bayesian inference. |
| **ADQL Query** | Multi-service TAP editor with syntax highlighting, template library, and federated queries across Gaia, SIMBAD, VizieR, CADC, and NED. |
| **Workspace** | Persistent file storage for FITS, VOTable, and analysis results. Batch search, saved searches, and data export. |
| **Team** | Real-time collaboration via WebSocket: shared pipelines/datasets, presence tracking, live comments, and session forking. |
| **Observations** | Transient alert dashboard (ZTF/TNS) with spectroscopic classification, anomaly detection, and follow-up recommendations. |

## Scientific Capabilities

### Data Access (19 connectors + VO protocols)
- **Optical/NIR:** SDSS, Gaia DR3, SIMBAD, VizieR, LAMOST, DESI
- **Space:** MAST (HST/Kepler/TESS), JWST, ESO, IRSA
- **Multi-wavelength:** NED, 2MASS, AllWISE, Chandra, ALMA
- **Radio:** NVSS, FIRST (spectral index + luminosity analysis)
- **VO Standards:** SIA v2, SSA, federated TAP, SAMP bidirectional, VOTable import/export, registry discovery (pyvo)

### Spectral Analysis (specutils + NIST)
- Line identification against 90-line NIST catalog (UV through NIR)
- Gaussian / Lorentzian / Voigt profile fitting via specutils
- Equivalent width measurement, continuum normalization
- Velocity dispersion via cross-correlation (log-wavelength CCF)
- Heliocentric/barycentric velocity correction
- Auto flux calibration (3 standard star reference tables)
- Telluric absorption correction (simplified atmospheric model)
- IFU datacube support: spaxel/aperture extraction, Voronoi binning, 2D velocity maps, emission line ratio maps (BPT diagnostics)

### Photometry (photutils)
- PSF photometry (DAOStarFinder + IntegratedGaussianPRF)
- Multi-aperture photometry with local background subtraction
- Source extraction and deblending (photutils segmentation)
- **Auto zero-point** determination via Gaia/SDSS cross-match
- Extinction correction with IRSA dust map E(B-V) auto-lookup
- PSF matching across bands

### Photometric Redshifts
- 30 parametric SED templates (E through starburst, AGN, post-starburst, LIRG/ULIRG)
- Calzetti dust attenuation with E(B-V) grid search
- Madau IGM absorption for z > 0.1
- Emission line contributions scaled by UV luminosity
- Bayesian magnitude prior (Benitez 2000)
- Full P(z) output with 68% confidence intervals
- Standard quality metrics: sigma_MAD, NMAD, outlier fraction

### Statistical Inference (dynesty + ArviZ)
- MCMC sampling (emcee)
- Nested sampling for Bayesian evidence (dynesty, ultranest)
- Bayes factor computation with Jeffreys scale interpretation
- Chain diagnostics: R-hat, ESS, MCSE via ArviZ
- Posterior predictive checks
- Model comparison tables (AIC/BIC/WAIC/LOO)
- Monte Carlo error propagation and bootstrap resampling

### Time-Domain Astronomy (batman + celerite2)
- Lomb-Scargle periodogram + BLS transit search
- GP detrending via celerite2 (Matern32, SHO, rotation kernels)
- Transit model fitting via batman (Rp/Rs, a/Rs, inclination)
- Stellar flare detection with amplitude/duration/energy measurement
- Phase folding with interactive period adjustment
- Variable star classification

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

## AI Assistant — 46 Tools

The AI assistant can invoke any platform capability:

| Category | Tools |
|----------|-------|
| **Search** | search_objects, run_adql, get_object_info, get_object_dossier, batch_object_search, query_vo_service |
| **Spectroscopy** | analyze_spectrum, analyze_spectrum_pro, classify_transient_spectrum |
| **Photometry** | extract_photometry, extract_sources, estimate_photo_z, estimate_photo_z_pro |
| **Time-Domain** | search_lightcurve, gp_detrend_lightcurve, fit_transit_model, detect_stellar_flares, transit_search_bls |
| **Image** | reduce_ccd_image, solve_astrometry, process_image |
| **Statistics** | validate_analysis, sensitivity_analysis, fit_isochrone |
| **Pipeline** | generate_pipeline, run_pipeline, modify_pipeline |
| **Literature** | search_literature, read_arxiv_paper, literature_review |
| **Transients** | query_transients, classify_transient |
| **Radio** | radio_analysis (spectral index / luminosity / crossmatch) |
| **Code** | run_python (sandboxed with numpy/scipy/astropy/specutils/dynesty/dask) |
| **Collaboration** | share_with_team, invite_team_member |
| **Export** | export_results, workspace_export, read_fits_header, get_provenance |
| **Other** | get_last_search_results, generate_paper_draft, generate_proposal, research_workflow, analyze_cross_wavelength, crossmatch_catalogs, get_followup_recommendation |

## Tech Stack

| Layer | Stack |
|-------|-------|
| Frontend | React 19, TypeScript strict, Vite, React Router, React Flow, Plotly, Aladin Lite |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2, SSE streaming |
| AI | Claude (default), OpenAI, DeepSeek, local model backends |
| Astronomy | astropy, astroquery, specutils, photutils, reproject, emcee, dynesty, ArviZ, batman, celerite2, lightkurve, pyvo, sep, dust_extinction, dask |
| Background | Celery + Redis (optional, sync fallback) |
| Storage | PostgreSQL (prod) / SQLite (dev), local filesystem for FITS |
| Auth | JWT + bcrypt + Google OAuth, Fernet-encrypted API keys |
| Deployment | Render blueprint (render.yaml), Docker Compose |
| i18n | 4 languages (English, Chinese, French, Spanish), 264 keys |
| Testing | 741 tests (623 backend pytest + 118 frontend vitest) |

## Repository Layout

```text
backend/
  app/
    ai/                 Routed inference + orchestrator + specialist agent prompts
    analysis/           CCD reduction and image-analysis helpers
    api/                29 FastAPI routers (auth, chat, data, pipeline, export, provenance, ...)
    connectors/         19 archive adapters + radio analysis + VO services
    middleware/         Request tracking + correlation ID middleware
    models/             SQLAlchemy models (20+ tables) + DB bootstrap
    pipeline/
      nodes/            35 pipeline node types
      engine.py         DAG executor with caching + provenance recording
    services/           31 service modules (AI tools, spectral analysis, photo-z, Bayesian,
                        time-domain, image processing, provenance, transient, literature, ...)
  data/
    line_catalogs/      NIST spectral line catalog (90 lines)
  tests/                623 backend tests

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
    i18n/               264 translation keys x 4 languages
    __tests__/          118 frontend tests (9 test files)
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
# Backend (623 tests)
cd backend && .venv/bin/python -m pytest tests/ -q

# Frontend (118 tests)
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
- Deployment: [DEPLOY_OPENCLAW.md](./DEPLOY_OPENCLAW.md)
- Development: [CLAUDE.md](./CLAUDE.md)

## License

This project is proprietary software. All rights reserved.
