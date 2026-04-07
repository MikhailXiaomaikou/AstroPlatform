# Astro Platform

An AI-native research environment for professional astronomers. Search 14 astronomical databases simultaneously, analyze spectra with AI, build processing pipelines visually, and export publication-ready results — all from a single interface.

**Live Demo:** https://astro-frontend-tyfr.onrender.com  
**API:** https://astro-backend-h4x1.onrender.com

---

## What It Does

### AI Research Agent

The AI assistant is not a chatbot — it's a research agent that autonomously calls tools, inspects results, and plans next steps. Ask a question in natural language, and it will:

- Search databases, filter results, cross-match catalogs
- Write and execute ADQL queries with correct column names and data completeness rules
- Analyze uploaded spectra (classify objects, identify emission/absorption lines, estimate redshift)
- Read arXiv papers and cite specific findings
- Generate data processing pipelines
- Chain multiple steps together (up to 5 rounds of automatic tool use)

**9 tools available:** `search_objects`, `run_adql`, `get_object_info`, `analyze_spectrum`, `generate_pipeline`, `search_literature`, `get_last_search_results`, `run_pipeline`, `read_arxiv_paper`

**Example interaction:**
```
User: "Find Seyfert galaxies with z > 0.1 and check what X-ray data exists
       for the brightest one"

Agent automatically:
  1. search_objects → finds 100 Seyfert galaxies
  2. run_adql → filters by redshift and sorts by magnitude
  3. get_object_info → checks Chandra survey coverage for the brightest
  4. search_literature → finds relevant papers
  → Returns a comprehensive answer citing specific papers
```

### Multi-Database Search

Search 14 astronomical databases concurrently from a single search bar:

| Database | Data | Operator |
|----------|------|----------|
| SIMBAD | Object identification, classification, redshift | CDS, Strasbourg |
| Gaia DR3 | 1.8 billion stars: positions, parallax, proper motion | ESA |
| SDSS DR18 | Optical photometry + spectroscopic redshift | Sloan |
| VizieR | Published catalog tables | CDS |
| MAST | HST, JWST archival observations | STScI |
| NED | Extragalactic objects, distances | NASA/IPAC |
| 2MASS | Near-infrared J/H/K survey | NASA/IPAC |
| ALMA | Millimeter/submillimeter | ESO |
| Chandra | X-ray observations | NASA |
| AllWISE | Wide-field infrared survey | NASA/IPAC |
| ESO | VLT, ALMA archives | ESO |
| IRSA | Infrared data archive | NASA/IPAC |
| JWST | James Webb observations | STScI |
| LAMOST | Low-resolution spectra | NAOC |

Results from different sources are automatically cross-matched and deduplicated by position (3 arcsecond matching radius with cos(dec) correction).

### Object Detail Panel

Click any object name in search results to open a slide-out panel showing:

- **Overview:** Type, redshift, spectral type, morphology, parallax, proper motion
- **Cross-identifications:** All known names from SIMBAD (e.g., M 77 has 97 identifiers)
- **Survey coverage:** Which databases have data for this object
- **Literature:** Top papers from NASA ADS with abstracts
- **Save to bookmarks** with project-based organization

### AI Spectrum Analysis

Upload a FITS file or fetch one from a database, then click "Analyze with AI":

1. **Automated analysis** (Python, no AI needed):
   - Peak detection (emission + absorption)
   - Continuum shape classification
   - Redshift estimation (4 methods: peak matching, cross-correlation, chi-squared grid, multi-method vote)
   - Spectral line identification against 17 known lines

2. **AI interpretation** (Claude):
   - Object classification with confidence level
   - Line identification verification
   - Special feature detection (broad lines, P Cygni profiles, BAL)
   - Suggested next analysis steps

3. **Visual overlay:** Identified lines annotated directly on the spectrum plot (emission in green, absorption in blue)

### Pipeline Editor

Visual DAG editor for building data processing workflows:

**12 processing nodes:**
- LoadData, Denoise, SpectralFit, RedshiftEstimate, EquivalentWidth
- SEDFit (4 models: blackbody, power-law, modified blackbody, composite)
- CoordTransform, CrossMatch, PhotCalibrate, ImageStack
- Plot, InteractivePlot

**Features:**
- Drag-and-drop from node palette
- Smooth-step curved connections with 30px snap radius
- Grid snapping (15px)
- Auto-save to localStorage (survives page navigation)
- Template save/load with version history and diff
- Cron-based scheduling (via Celery Beat)
- AI can generate pipelines from natural language descriptions

### FITS File Viewer

- **1D Spectrum:** SVG-based viewer with drag-zoom, spectral line annotations, continuum marking, column selection
- **2D Image:** Canvas-based with 5 stretch modes (linear, log, sqrt, asinh, power), 5 colormaps (grayscale, heat, cool, viridis, plasma), contour overlay, WCS coordinate grid, blink comparison, region selection with photometry statistics

### Export

- **CSV / VOTable** from search results
- **Jupyter Notebook** with embedded astropy Table + sky distribution plot
- **AI analysis Markdown report**
- **Pipeline results** as CSV, VOTable, PDF, or HTML

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, TypeScript 5.9 (strict), Vite, ReactFlow, Plotly.js |
| Backend | FastAPI, Python 3.11+, SQLAlchemy (async), Pydantic v2 |
| Database | SQLite (dev) / PostgreSQL (prod), auto-migration on startup |
| Queue | Celery + Redis (optional, graceful fallback to sync mode) |
| AI | Anthropic Claude API (tool_use with agent loop) |
| Astronomy | astropy, astroquery, scipy, numpy |
| Auth | JWT + bcrypt + Google OAuth |
| Deployment | Render.com (render.yaml), Docker Compose |

---

## Setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- (Optional) Redis for caching and async pipelines

### Local Development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

### Environment Variables

**Backend (required for production):**
```
ENV=production
DATABASE_URL=postgresql://...
JWT_SECRET=<random-hex-32>
CORS_ORIGINS=https://your-frontend.com
```

**Backend (optional):**
```
ANTHROPIC_API_KEY=sk-ant-...       # AI assistant (users can also set their own)
ADS_API_KEY=...                    # NASA ADS paper search
REDIS_URL=redis://...              # Caching + async pipelines (supports rediss:// TLS)
PIPELINE_MODE=celery               # "sync" (default) or "celery"
GOOGLE_CLIENT_ID=...               # Google OAuth login
MAX_UPLOAD_SIZE=104857600           # FITS upload limit (default 100MB)
```

**Frontend:**
```
VITE_API_URL=https://your-backend.com
VITE_GOOGLE_CLIENT_ID=...
```

### Deploy to Render

Push to `main` branch, then in Render Dashboard:
1. **New > Blueprint** → select this repo → `render.yaml` creates all 6 services automatically
2. Set `ANTHROPIC_API_KEY` on the backend service
3. (Optional) Set `GOOGLE_CLIENT_ID` on both backend and frontend

Services created by `render.yaml`:
- `astro-backend` (web) — FastAPI
- `astro-celery-worker` (worker) — Pipeline executor
- `astro-celery-beat` (worker) — Scheduler
- `astro-frontend` (static) — React SPA
- `astro-redis` (redis) — Cache + queue
- `astro-db` (postgres) — Database

### Docker Compose (self-hosted)

```bash
docker compose up -d
```

Creates: PostgreSQL, Redis, MinIO, Backend, Celery Worker, Frontend (Nginx).

---

## API Reference

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/data/search` | Multi-database object search |
| `POST` | `/api/data/advanced-search` | Structured science criteria search |
| `GET` | `/api/data/object-detail` | Aggregated object info + cross-IDs |
| `POST` | `/api/data/fits/upload` | Upload FITS file |
| `POST` | `/api/data/fits/analyze` | AI spectrum analysis |
| `POST` | `/api/data/batch-lookup` | Batch SIMBAD lookup (50 max) |
| `GET` | `/api/data/fits-header` | FITS header inspection |
| `GET` | `/api/data/fits-spectrum` | Extract spectrum data |

### AI Chat

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat/message` | Send message to AI agent (multi-turn tool use) |
| `POST` | `/api/chat/execute-action` | Execute a suggested action |
| `POST` | `/api/chat/sessions/save` | Save chat session |
| `GET` | `/api/chat/sessions` | List saved sessions |
| `GET` | `/api/chat/sessions/{id}` | Load a session |

### Pipeline

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/pipeline/run` | Execute pipeline DAG |
| `GET` | `/api/pipeline/templates` | List pipeline templates |
| `POST` | `/api/pipeline/save` | Save template |
| `POST` | `/api/pipeline/templates/{id}/versions` | Save version |

### ADQL

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/integration/adql/query` | Execute ADQL on Gaia/SIMBAD/VizieR/CADC |
| `GET` | `/api/integration/adql/services` | List available TAP services |

### Export

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/export/report/markdown` | AI analysis → Markdown report |
| `POST` | `/api/export/notebook/from-search` | Search results → Jupyter notebook |
| `GET` | `/api/export/run/{id}/csv` | Pipeline results → CSV |

### Monitoring

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Basic health check |
| `GET` | `/health/detailed` | Database, Redis, storage status |
| `GET` | `/health/stats` | Request counts, error rates, top endpoints |

---

## Architecture

```
Frontend (React SPA)
  ├── Data Browser ── search + results table + object detail panel
  ├── Pipeline ────── ReactFlow DAG editor + node params
  ├── AI Chat ─────── agent loop + tool results + session history
  ├── ADQL ─────────── query editor + per-service presets
  ├── Workspace ────── FITS file manager + batch targets
  └── Settings ─────── API keys + billing + team

Backend (FastAPI)
  ├── api/ ──────── 17 routers
  ├── connectors/ ── 14 database connectors (BaseConnector interface)
  ├── pipeline/ ──── engine (topological sort + DAG execution)
  │   └── nodes/ ─── 12 processing nodes
  ├── services/ ──── spectrum_analyzer, ai_tools
  ├── models/ ────── SQLAlchemy ORM (18 tables)
  └── search/ ────── natural language query parser

Infrastructure
  ├── PostgreSQL ─── primary database (auto-migration on startup)
  ├── Redis ──────── cache + Celery broker + WebSocket pub/sub
  └── Celery ─────── async pipeline execution + scheduled runs
```

---

## Key Technical Decisions

**NaN Safety:** Astronomical data is riddled with masked/NaN values. Every path from connector to API response passes through `_safe_float()` which converts NaN/Inf to None for JSON safety.

**Lazy Loading:** Connectors and pipeline nodes use lazy initialization to avoid import overhead at startup.

**Database Portability:** Custom `UUIDType` and `JSONType` handle SQLite (dev) and PostgreSQL (prod) transparently. Auto-migration adds new columns on startup.

**Connection Pool:** `pool_pre_ping=True` detects dead PostgreSQL connections. `pool_recycle=300` refreshes every 5 minutes.

**AI Agent Loop:** Claude calls tools via native `tool_use` API (not string-based `<actions>` tags). Backend executes tools, returns results, Claude decides next step. Max 5 iterations. Tool results truncated to valid JSON if >8KB.

**Redshift Estimation:** 4 methods available: peak matching, cross-correlation, chi-squared template fitting, and multi-method voting (runs all 3 and picks highest confidence).

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Ensure `npm run build` passes (strict TypeScript)
4. Ensure `python -m pytest tests/` passes
5. Submit a pull request

### TypeScript Rules (enforced)

- `strict: true`, `noUnusedLocals: true`, `noUnusedParameters: true`
- `verbatimModuleSyntax: true` — use `import type { Foo }` for type-only imports
- Build is `tsc -b && vite build` — TypeScript errors block the build

---

## License

MIT

---

## Citation

If you use Astro Platform in your research, please cite:

```bibtex
@software{astroplatform,
  title={Astro Platform: An AI-Native Research Environment for Multi-wavelength Astronomical Data Exploration},
  author={Chen, Kexuan},
  year={2026},
  url={https://github.com/MikhailXiaomaikou/AstroPlatform}
}
```
