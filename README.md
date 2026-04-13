# Standard Astro

Standard Astro is an AI-native astronomy workbench for data discovery, analysis, pipelines, collaboration, and publication export. It combines a React frontend with a FastAPI backend, a routed AI layer, multi-archive search, ADQL/TAP access, FITS handling, visual pipelines, research memory, and paper-generation workflows.

## Product Overview

Standard Astro is organized around six core workflows:

- `Data Browser`: query 15 astronomy archives from one place, inspect merged results, preview FITS headers/spectra/WCS, and fetch files into your workspace.
- `AI Assistant`: run a tool-using astronomy assistant that can search catalogs, write ADQL, execute Python, validate analyses, draft papers, and export reports.
- `Pipeline Studio`: build DAG-based analysis pipelines visually, including catalog query nodes, workspace import nodes, classic spectroscopy nodes, and CCD reduction nodes.
- `Workspace`: keep fetched or exported files available across modules so Data Browser, Chat, and Pipeline can operate on the same assets.
- `Research History + Collaboration`: save/fork/share sessions, create snapshots, comment on shared sessions, and opt into persistent research memory.
- `Alerts + Anomalies`: inspect transient alerts, follow-up recommendations, and anomaly exploration tools from the same application shell.

## Current Capabilities

### Data access

- 15 integrated sources: `sdss`, `gaia`, `simbad`, `vizier`, `mast`, `ned`, `2mass`, `chandra`, `allwise`, `alma`, `eso`, `irsa`, `jwst`, `lamost`, `desi`
- ADQL/TAP support through Gaia, SIMBAD, VizieR, and CADC services
- Object-detail aggregation, dossier generation, and cross-wavelength follow-up helpers
- FITS upload, preview, download, and AI-assisted spectrum interpretation

### AI + analysis

- 26 AI tools registered in the backend tool layer
- Routed inference layer with `claude`, `openai`, `deepseek`, and `local` backends
- Specialist-agent prompts for data, analysis, literature, observation, and visualization tasks
- Python sandbox with astronomy helper functions preloaded
- Analysis validation before manuscript generation
- Session-to-paper export for `AASTeX`, `MNRAS`, and `A&A`-style workflows

### Pipelines

- 22 pipeline node types
- Catalog-query and workspace-import entry nodes
- Spectral and photometric processing nodes
- CCD reduction nodes: bias subtraction, dark correction, flat-fielding, cosmic-ray rejection, astrometric solve, and source extraction
- Sync execution plus Celery-backed async execution when Redis/worker services are available

### Collaboration and memory

- Saved chat sessions with share links and access levels
- Session forking, comments, snapshots, restore, and snapshot diff
- Research profile and history page
- Opt-in persistent memory with lightweight session embeddings and profile summaries

## Tech Stack

| Layer | Stack |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, React Router, React Flow, Plotly |
| Backend | FastAPI, SQLAlchemy async, Pydantic v2 |
| AI | Anthropic by default, routed via internal inference router with OpenAI/DeepSeek/local backends available |
| Astronomy | astropy, astroquery, scipy, numpy, pandas, matplotlib |
| Background execution | Celery + Redis optional, sync fallback available |
| Storage | PostgreSQL or SQLite for metadata, object storage/local file backend for FITS and exports |
| Auth | Username/password JWT auth, optional Google login |
| Deployment | Render blueprint, Docker Compose, or custom FastAPI/React deployment |

## Repository Layout

```text
backend/
  app/
    ai/                 Routed inference + orchestrator + specialist agent prompts
    analysis/           CCD reduction and image-analysis helpers
    api/                FastAPI routers by product domain
    connectors/         Archive adapters for astronomy data sources
    middleware/         Tracking and request middleware
    models/             SQLAlchemy models and DB bootstrap
    pipeline/           DAG engine, validators, and node registry
    services/           AI tools, analysis helpers, paper generation, memory, alerts
  tests/                Backend pytest suite

frontend/
  src/
    api/                Typed client wrappers for backend endpoints
    components/         Reusable UI pieces, node editors, viewers
    context/            Auth context
    hooks/              Tracking and other shared hooks
    pages/              Data Browser, Chat, Pipeline, Workspace, ADQL, Research, Shared Session, Alerts
```

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+
- Redis is optional unless you want Celery-backed async execution

### Run locally

```bash
# backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# frontend
cd frontend
npm install
npm run dev
```

Frontend default dev URL: `http://localhost:5173`  
Backend default dev URL: `http://localhost:8000`

## Environment Variables

### Core backend

```bash
DATABASE_URL=postgresql+asyncpg://...
JWT_SECRET=replace-me
CORS_ORIGINS=https://your-frontend.example
```

### AI backends

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
LOCAL_MODEL_ENABLED=1
LOCAL_MODEL_BASE_URL=http://localhost:8000/v1
```

### Optional services

```bash
ADS_API_KEY=...
REDIS_URL=redis://...
PIPELINE_MODE=celery
GOOGLE_CLIENT_ID=...
ASTROMETRY_API_KEY=...
PUBLIC_APP_URL=https://app.standardastro.com
ADMIN_USERNAMES=your_admin_username
```

### Frontend

```bash
VITE_API_URL=https://your-backend.example
VITE_GOOGLE_CLIENT_ID=...
```

## Testing

### Backend

From the repository root:

```bash
./backend/.venv/bin/python -m pytest -q
```

The root `pytest.ini` excludes `integration` and `slow` tests by default.

### Frontend

```bash
cd frontend
npm run test -- --run
npm run build
```

## Deployment

### Render

`render.yaml` is kept in the repo for blueprint deployment. The standard setup is:

- FastAPI web service
- React static frontend
- PostgreSQL
- Redis
- Celery worker
- Celery beat

### Docker Compose

```bash
docker compose up -d
```

Use Docker Compose when you want the whole stack locally, including Redis and the worker path.

## Documentation

- Architecture detail: [ARCHITECTURE.md](./ARCHITECTURE.md)
- Deployment notes: [DEPLOY_OPENCLAW.md](./DEPLOY_OPENCLAW.md)
- Internal coding guidance: [CLAUDE.md](./CLAUDE.md)
