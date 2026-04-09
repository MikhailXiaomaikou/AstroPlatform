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

### Backend (`backend/app/`)

- `api/` — 17 FastAPI routers (auth, chat, data, arxiv, citations, crossmatch, team, settings, pipeline, etc.)
- `connectors/` — 14 astronomical database connectors (SDSS, Gaia, SIMBAD, MAST, VizieR, NED, 2MASS, Chandra, ALMA, AllWISE, ESO, IRSA, JWST, LAMOST). All extend `BaseConnector` in `base.py` with `search()` and `fetch()` methods
- `pipeline/nodes/` — 12 processing nodes (denoise, spectral_fit, coord_transform, redshift, sed_fit, crossmatch, image_stack, phot_calibrate, plot, plot_interactive, load_data, equivalent_width)
- `models/schemas.py` — 16 SQLAlchemy models. Uses custom `UUIDType` and `JSONType` for SQLite/PostgreSQL portability
- `auth.py` — JWT with bcrypt + Google OAuth. `get_current_user()` (required) and `get_optional_user()` (optional) as FastAPI dependencies

### Frontend (`frontend/src/`)

- `pages/` — 9 page components: DataBrowser, Pipeline, Chat (AI assistant), ADQL, Team, Workspace, Settings, Billing, Auth
- `components/viz/PlotBuilder.tsx` — Publication-quality Plotly charts generated client-side. White paper background, CMU Serif fonts, colorblind-safe palette
- `api/client.ts` — Axios client. Base URL from `VITE_API_URL` env var, falls back to `localhost:8000`. JWT auto-attached via interceptor
- `context/AuthContext.tsx` — Auth state with login/register/setupKeyLogin/logout

### Key Data Flow

1. **Search**: User query → `parse_natural_query()` extracts science criteria (redshift, spectral lines, object type) → routes to SIMBAD TAP `search_by_criteria()` for science queries, or direct `connector.search()` for name/coordinate queries
2. **AI Chat**: Frontend sends messages + API key (from localStorage) → backend calls Claude API → response parsed for `<actions>` tags → actions executable inline (search, ADQL, arXiv extraction, plot, **generate_pipeline**, **modify_pipeline**, **comment_pipeline**)
3. **NaN safety**: Astronomy data often contains masked/NaN values. `_safe_float()` and `_sanitize_extra()` in `data.py` sanitize all connector output before JSON serialization. SIMBAD connector also checks NaN in `_table_to_objects()`
4. **FITS Upload**: Users upload FITS files via `POST /api/data/fits/upload` → stored in `data/fits/uploads/` → browseable via `GET /api/data/fits/browse` → usable as pipeline input
5. **AI Pipeline Generation**: User describes workflow in chat ("denoise then fit lines") → AI returns `generate_pipeline` action with full DAG → saved as template → loadable in Pipeline Editor

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
