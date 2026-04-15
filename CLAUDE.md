# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Run Commands

```bash
# Frontend (from frontend/)
npm run build          # tsc -b && vite build — MUST pass before pushing
npm run dev            # vite dev server on :5173
npm run test           # vitest run
npm run test:watch     # vitest in watch mode
npm run lint           # eslint

# Backend (from backend/)
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python3 -m pytest tests/                    # all tests
python3 -m pytest tests/test_api.py -k test_search  # single test

# Python syntax check (all files)
python3 -c "import py_compile, glob; [py_compile.compile(f, doraise=True) for f in glob.glob('app/**/*.py', recursive=True)]"
```

## TypeScript Constraints (CRITICAL)

The frontend uses **strict TypeScript** with these enforced rules:
- `strict: true`, `noUnusedLocals: true`, `noUnusedParameters: true`
- `verbatimModuleSyntax: true` — interfaces/types MUST use `import type` syntax
- `erasableSyntaxOnly: true`
- Build is `tsc -b && vite build` — TypeScript errors block the build

Common pitfalls:
- `import { Foo }` for a type → build fails. Use `import type { Foo }` or `import { type Foo }`
- Unused variables after refactoring → build fails. Remove or prefix with `_`
- Unused imports → build fails. Clean up after changes

## Architecture

**Full-stack astronomy research platform**: React SPA (Vite) + FastAPI backend + SQLite (dev) / PostgreSQL (prod).

See [ARCHITECTURE.md](./ARCHITECTURE.md) for the full module breakdown and data flows.

### Backend (`backend/app/`)

- `api/` — **28 FastAPI routers** (auth, chat, data, pipeline, export, paper, sessions, team, research, alerts, anomalies, citations, crossmatch, integration, arxiv, workspace, settings, followup, provenance, visualization, scheduler, isochrones, inference, events, health, ws, ...)
- `connectors/` — **23 astronomical database connectors** (SDSS, Gaia, SIMBAD, VizieR, MAST, NED, 2MASS, Chandra, AllWISE, ALMA, ESO, IRSA, JWST, LAMOST, DESI, Pan-STARRS, XMM, NVSS, FIRST, JPL Horizons, ATNF Pulsar, SPARC, FRBSTATS). All extend `BaseConnector` in `base.py` with `search()` / `fetch()` / `normalize()` methods
- `pipeline/nodes/` — **35 processing nodes** (CCD reduction, spectroscopy, photometry, time-domain, image processing, Bayesian inference, ML clustering, custom scripts, plotting)
- `services/` — 30 service modules: ai_tools (52 tools), astro_analysis, spectral_analysis_pro, photo_z_pro, bayesian_inference, time_domain_pro, image_processing_pro, parsec_fetcher, transient_classifier, literature_engine, memory_service, code_executor, provenance, dossier_generator, vo_services, ...
- `models/schemas.py` — 20+ SQLAlchemy models. Uses custom `UUIDType` and `JSONType` for SQLite/PostgreSQL portability
- `ai/` — Orchestrator + inference router + specialist agent prompts (Claude / OpenAI / DeepSeek routing)
- `auth.py` — JWT with bcrypt + Google OAuth. `get_current_user()` (required) and `get_optional_user()` (optional) as FastAPI dependencies
- `api/chat.py` — SYSTEM_PROMPT is now ~51 KB / ~12.8 K tokens with 16+ literature-cited object-class workflows

### Frontend (`frontend/src/`)

- `pages/` — Main pages: DataBrowser, Pipeline, Chat (AI assistant with persistent sidebar), ADQL, Workspace, Team, Account, Observations, Auth, Landing, Help, SharedSession
- `components/viz/` — SpectrumViewer, LightCurveViewer, ImageCutoutViewer, MCMCDiagnostics, PlotBuilder (Plotly, publication-quality), AladinViewer, ProvenanceGraph
- `components/nodes/` — 35-node palette + parameter editor + validation
- `components/chat/` — Claude-desktop-style MarkdownText, chat sidebar, figure expand modal
- `api/client.ts` — Axios client with SSE streaming support. Base URL from `VITE_API_URL`, JWT auto-attached, `AbortController` on search
- `context/AuthContext.tsx` — Auth state with login/register/setupKeyLogin/logout
- `i18n/index.tsx` — 4 languages (en/zh/fr/es)

### Key Data Flow

1. **Search**: User query → Data Browser → connector.search() across selected sources → concurrent dispatch with per-source timeout → normalize via `_astro_to_result()` → sanitize NaN via `_safe_float()` / `_sanitize_extra()` → cache full results under `"latest"` key
2. **ADQL**: User/AI writes ADQL → `execute_adql_query()` (standalone function, not route-handler-only) → **auto-retry on 408/502/503 with halved cone radius** → full result under `"latest_adql"` cache key, AI sees first 100 rows + note
3. **AI Chat**: Frontend sends messages + `current_session_id` + `python_session_id` → backend builds runtime context (system prompt + specialist agents + filtered tool list) → inference_router calls LLM → `_run_agent_loop` dispatches tool calls concurrently (max 12 iterations) → **empty-reply fallback** synthesizes summary from tool results if model returns blank → SSE stream → auto-save after each turn → auto-title from first user message
4. **NaN safety**: Every path from connector to API response MUST go through `_astro_to_result()` which uses `_safe_float()`. ADQL query results separately handled in `execute_adql_query()` — masked astropy values → None, not NaN
5. **FITS Upload**: `POST /api/data/fits/upload` → `_validate_path()` (uses `relative_to()` not string prefix) → `data/fits/uploads/` → browseable via `GET /api/data/fits/browse` → usable as pipeline input
6. **AI Pipeline Generation**: User describes workflow in chat → AI returns `generate_pipeline` action with full DAG → saved as template → loadable in Pipeline Editor
7. **fit_isochrone**: AI calls with no params → tool auto-extracts `bp_rp`+`abs_mag` from search/ADQL cache → auto-estimates DM from median parallax → 4-D grid search over age/met/DM/A_V → PARSEC CMD 3.9 lookup → falls back to turnoff M_G → log(age) table (Bressan+ 2012 calibrated) on PARSEC timeout

## Critical Patterns

### NaN Handling
SIMBAD/astropy return masked values that become `float('nan')` and break `json.dumps`. Every path from connector to API response MUST go through `_astro_to_result()` which uses `_safe_float()`:
```python
def _safe_float(val):
    if val is None: return None
    if val != val or val == float("inf") or val == float("-inf"): return None
    return val
```

### SIMBAD TAP Queries
The `basic` table has specific columns. Notably does NOT have `flux_B/V/R/I/J/H/K` or `Fe_H_Fe_H` — those are in separate tables. Available: `main_id, ra, dec, otype, otype_txt, rvz_redshift, rvz_radvel, sp_type, morph_type, plx_value, pmra, pmdec, galdim_*`. Object type values need SQL injection prevention via `re.sub(r"[^a-zA-Z0-9*]", "", simbad_type)`.

### API Key Flow (Beta Mode)
Currently no login required. API keys stored in browser `localStorage` as `astro_api_keys` JSON. Frontend sends Anthropic key in `context.api_key` field of chat requests. Backend strips it from the Claude system prompt for security.

### Database Migrations
SQLite `create_all()` does NOT add columns to existing tables. New columns require manual `ALTER TABLE` via:
```python
import sqlite3
db = sqlite3.connect('data/astro.db')
db.execute('ALTER TABLE users ADD COLUMN new_col TEXT')
db.commit()
```

## Deployment

**Production**: Render.com (backend Docker + PostgreSQL) + Render static site (frontend)
- Backend: `https://standard-astro-backend-h4x1.onrender.com`
- Frontend: `https://standard-astro-frontend-tyfr.onrender.com`
- Backend auto-converts `postgresql://` to `postgresql+asyncpg://` in `config.py`
- Render free tier sleeps after 15min — `BackendBanner` component in `App.tsx` shows "waking up" notice
- CORS origins configured in `cors.py` — includes both localhost and Render URLs

Push to `main` branch → Render auto-deploys (may need Manual Deploy for first time).

**Infrastructure as Code**: `render.yaml` defines all services:
- `standard-astro-backend` (web) — FastAPI API server
- `standard-astro-celery-worker` (worker) — Celery pipeline executor, concurrency=2
- `standard-astro-celery-beat` (worker) — Celery Beat scheduler
- `standard-astro-frontend` (static) — Vite-built SPA with SPA rewrites
- `standard-astro-redis` (redis) — Task queue + pub/sub + caching
- `standard-astro-db` (postgres) — Primary database

## Environment Variables

Backend (required for production):
```
ENV=production
DATABASE_URL=postgresql://...    # auto-converted to asyncpg
JWT_SECRET=<random-hex-32>
CORS_ORIGINS=https://your-frontend.com
```

Backend (optional):
```
ANTHROPIC_API_KEY=sk-ant-...     # server-wide default for AI assistant
ADS_API_KEY=...                  # NASA ADS citation search
REDIS_URL=redis://...            # for caching (graceful fallback if unavailable)
                                 # supports rediss:// (TLS) for Upstash
PIPELINE_MODE=celery             # "sync" (default) or "celery" for async execution
MAX_UPLOAD_SIZE=104857600        # max FITS upload size in bytes (default 100MB)
GOOGLE_CLIENT_ID=...             # Google OAuth client ID (from Google Cloud Console)
GOOGLE_CLIENT_SECRET=...         # Google OAuth client secret
```

Frontend:
```
VITE_API_URL=https://your-backend.com   # defaults to http://localhost:8000
VITE_GOOGLE_CLIENT_ID=...               # same Google OAuth client ID as backend
```
