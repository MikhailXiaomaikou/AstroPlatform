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

- `api/` — **28 FastAPI routers** (auth, chat, data, pipeline, export, paper, sessions, team, research, alerts, anomalies, citations, citation_graph, crossmatch, integration, arxiv, workspace, settings, followup, dossier, provenance, visualization, scheduler, isochrones, inference, events, health, ws)
- `connectors/` — **22 astronomical database connectors** (SDSS, Gaia, SIMBAD, VizieR, MAST, NED, 2MASS/twomass, AllWISE, Chandra, XMM, ALMA, ESO, IRSA, JWST, LAMOST, DESI, Pan-STARRS, JPL Horizons, ATNF Pulsar, SPARC, FRBSTATS, radio [NVSS+FIRST]). All extend `BaseConnector` in `base.py` with `search()` / `fetch()` / `normalize()` methods
- `pipeline/nodes/` — **35 processing nodes** (CCD reduction, spectroscopy, photometry, time-domain, image processing, Bayesian inference, ML clustering, custom scripts, plotting)
- `services/` — 30+ service modules: ai_tools (**55 tools**), astro_analysis, spectral_analysis_pro, photo_z_pro, bayesian_inference, time_domain_pro, image_processing_pro, parsec_fetcher, transient_classifier, literature_engine, memory_service, code_executor, **claim_validator** (zero-fabrication regex gate with ±1% and strict-mode ±0.1% tolerances), **result_provenance** (EMPTY/FAILED status + `__tool_status__` / `__do_not_claim__` / `__message_to_model__` upstream banners + reproducibility envelope), provenance (versioned environment manifest), dossier_generator, vo_services, **connector_cache** (content-addressed, Null/SQLite/Redis, singleflight), **workflow_checkpoint** (resumable multi-step AI workflows), **sandbox/subprocess_backend** (crash-isolated `multiprocessing` spawn + rlimit + killpg + F0 payload-completeness guard)
- `connectors/throttle.py` — Per-connector upstream rate limiter (`asyncio.Semaphore` + stdlib token bucket), per-archive ToS policies
- `connectors/retry.py` — Transient-only retry set + circuit breaker (closed/half-open/open)
- `observability/metrics.py` — Stdlib-only Prometheus registry exposed at `GET /metrics`. Current counters include `fabrication_blocked_total{agent,reason}`, `honest_abstention_total{agent,reason}`, `structured_abstention_emitted_total`, `empty_tool_result_total`, `sandbox_silent_failure_total`, `zero_data_but_claims_total`, plus connector + sanity counters
- `pipeline/nodes/__init__.py` — `NODE_COST` registry; `dag_has_heavy_nodes()` gates `/api/pipeline/run` with `503` when `PIPELINE_MODE != "celery"` and heavy nodes are present
- `models/schemas.py` — 20+ SQLAlchemy models. Uses custom `UUIDType` and `JSONType` for SQLite/PostgreSQL portability
- `ai/` — Orchestrator + inference router + specialist agent prompts (Claude / OpenAI / DeepSeek routing)
- `auth.py` — JWT with bcrypt + Google OAuth. `get_current_user()` (required) and `get_optional_user()` (optional) as FastAPI dependencies
- `api/chat.py` — **SYSTEM_PROMPT is ~57 KB / ~14 K tokens with 46 sections**, including `ZERO-FABRICATION CONTRACT`, `STRUCTURED ABSTENTION`, `ADQL aggregate-function semantics`, and `Cluster / association analysis idioms` at the top; literature-cited workflows for 16+ object classes below. Also defines `_parse_abstention_tag` / `_classify_abstention_reason` / `_render_abstention_card` for the `<tools_returned_nothing/>` structured-abstention flow and `GET /api/chat/ai_backend_status` for the F4 pre-send Send-button gate

### Frontend (`frontend/src/`)

- `pages/` — 18 page directories: DataBrowser, Pipeline, Chat (AI assistant with persistent sidebar + HonestAbstentionCard), ADQL, Workspace, Team, Account, Observations, Auth, Landing, Help, SharedSession, Papers (three-col LaTeX manuscript editor), AlertDashboard, AnomalyExplorer, Billing, ResearchHistory, Settings
- `styles/journal.css` — ~2 k-line Journal-Edition stylesheet (newspaper palette: paper #fbfaf5 / ink #1a1a1a / burgundy accent #7b2d26 / deep blue #2a5d7b / forest green #2e6a4e / plum #6b4a7e). MUST be imported AFTER `App.css` in `App.tsx` so same-specificity overrides win the cascade
- `App.tsx` — Journal-masthead two-row nav (8 tabs: Home / AI Assistant / Browse / ADQL / Pipeline / Sessions / Papers / Account) + chip-style 4-lang switcher + theme toggle + user card. Theme migration key is `astro_theme_v2` (defaults light)
- `components/viz/` — SpectrumViewer, LightCurveViewer (**both auto-upgrade to Plotly `scattergl` when N > 5000**), ImageCutoutViewer, MCMCDiagnostics, PlotBuilder (Plotly, publication-quality; Fit checkbox now shows ✓ / "(not supported)" per chart type), AladinViewer, ProvenanceGraph
- `components/nodes/` — 35-node palette + parameter editor + validation; Journal-palette accent stripes by node family
- `components/pipeline/autoLayout.ts` — Pure-stdlib layered DAG layout via Kahn longest-path; `PipelineCanvas` exposes it as the **Auto Layout** button (no `elkjs` / `dagre`)
- `components/chat/` — Claude-desktop-style MarkdownText, chat sidebar, figure expand modal
- `api/client.ts` — Axios client with SSE streaming support. Base URL from `VITE_API_URL`, JWT auto-attached, `AbortController` on search + chat. `ThinkingEvent` union includes `honest_abstention` variant; `getAIBackendStatus()` feeds the F4 pre-send gate
- `context/AuthContext.tsx` — Auth state with login/register/setupKeyLogin/logout. Logout only on 401/403, not transient errors
- `i18n/index.tsx` — 4 languages (en/zh/fr/es), ~200+ keys including `pipeline.template_open_cluster`

### Key Data Flow

1. **Search**: User query → Data Browser → connector.search() across selected sources → concurrent dispatch with per-source timeout → normalize via `_astro_to_result()` → sanitize NaN via `_safe_float()` / `_sanitize_extra()` → cache full results under `"latest"` key
2. **ADQL**: User/AI writes ADQL → `execute_adql_query()` (standalone function, not route-handler-only) → **auto-retry on 408/502/503 with halved cone radius** → full result under `"latest_adql"` cache key, AI sees first 100 rows + note
3. **AI Chat**: Frontend sends messages + `current_session_id` + `python_session_id` → backend builds runtime context (system prompt + specialist agents + filtered tool list) → inference_router calls LLM → `_run_agent_loop` dispatches tool calls concurrently (max 12 iterations, per-tool deadlines: `fit_isochrone` 180 s / `fit_transit_model` + `transit_search_bls` 120 s / rest 45 s; agent-loop outer 360 s; 12 s SSE heartbeats) → every tool return flows through `result_provenance.normalize_tool_result` which stamps `__tool_status__` banners on EMPTY/FAILED + a reproducibility envelope → final reply goes through abstention parser → claim validator → optional regen → SSE stream → auto-save after each turn → auto-title from first user message
4. **NaN safety**: Every path from connector to API response MUST go through `_astro_to_result()` which uses `_safe_float()`. ADQL query results separately handled in `execute_adql_query()` — masked astropy values → None, not NaN
5. **FITS Upload**: `POST /api/data/fits/upload` → `_validate_path()` (uses `relative_to()` not string prefix) → `data/fits/uploads/` → browseable via `GET /api/data/fits/browse` → usable as pipeline input
6. **AI Pipeline Generation**: User describes workflow in chat → AI returns `generate_pipeline` action with full DAG → saved as template → loadable in Pipeline Editor
7. **fit_isochrone**: AI calls with no params → tool auto-extracts `bp_rp`+`abs_mag` from search/ADQL cache → auto-estimates DM from median parallax → 4-D grid search over age/met/DM/A_V → PARSEC CMD 3.9 lookup → falls back to turnoff M_G → log(age) table (Bressan+ 2012 calibrated) on PARSEC timeout
8. **Cluster workflow (F6)**: Chat prompt like "query Pleiades members" → AI calls `query_gaia_cluster(center_name="Pleiades", radius_deg=2, parallax_center_mas=7.3, pmra_center=..., pmdec_center=..., ruwe_max=1.4)` → backend resolves via `name_resolver.resolve_name` → composes ADQL → dispatches to Gaia TAP → if 0 rows, F2.1 EMPTY banner fires and AI emits `<tools_returned_nothing/>`. For A_V / E(B-V): `get_extinction(ra, dec, band="G")` → `dustmaps.sfd` (or analytic fallback) + Cardelli+1989 band ratios.

## Critical Patterns

### Zero-fabrication architecture (Phase F core — DO NOT regress)

Three layers of defence + one positive incentive, every layer tested:

1. **Upstream banners** (`services/result_provenance.py`): `_is_empty_payload` + `_inject_empty_banner` / `_inject_failed_banner` prepend `{__tool_status__, __do_not_claim__, __message_to_model__, __suggested_next_step__}` at the FRONT of the tool_result dict so the LLM reads them first. `analysis_status` gets a dedicated `EMPTY` value distinct from `FAILED`.
2. **Claim validator** (`services/claim_validator.py`): extracts numeric claims from the reply via regex (redshift, mass, age, `Mean Parallax: X`, `776 stars`, `X ± Y mas`, etc.), harvests the numeric universe from `tool_results` recursively, matches at ±1 % (default) or ±0.1 % (strict mode when universe < 10 entries — F1.3). `is_empty_turn` + `zero_data_but_quantitative` implement the F1.4 hard block.
3. **Structured abstention** (`api/chat.py` `_parse_abstention_tag` + `_classify_abstention_reason` + `_render_abstention_card`): when all tools are EMPTY/FAILED the model emits `<tools_returned_nothing failed_tools="..." empty_tools="..." rationale="..." suggested_next_step="..."/>` as its ENTIRE reply; the backend renders a canonical Markdown card (no prose generation = no fabrication pressure) and emits an SSE `honest_abstention` event.
4. **Positive reward loop**: `honest_abstention_total` + `structured_abstention_emitted_total` counters are emitted on the honest path; `fabrication_blocked_total{reason}` + `fabrication_detected_total{attempt}` + `zero_data_but_claims_total` on the punishment path. The frontend renders honest abstentions as a **celebratory** pale-blue ✓ card (`HonestAbstentionCard`), not a negative "error" bubble.

When adding new tools or modifying the agent loop: preserve these invariants. `tests/test_claim_validator.py`, `tests/test_abstention_parser.py`, `tests/test_result_provenance.py`, and `tests/test_sandbox_crash_paths.py` will fail fast on regressions.

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

**F4 pre-send gate**: ChatPage calls `GET /api/chat/ai_backend_status` on mount; if the server reports zero configured backends AND the browser has no stored keys, the Send button is disabled (`disabled={!input.trim() || loading || !aiBackendReady}`) and a red banner directs the user to `/account`. Prevents the old "type a prompt → hit Send → see cryptic error" UX.

### Sandbox error surfacing (F0)
`run_python` MUST always carry a user-actionable error on failure. Two layers:
- `services/sandbox/subprocess_backend.py` payload-completeness guard — when the child dies mid-serialisation (`parent_conn.recv()` returns `None` / `{}` / non-dict / `success=False` with no error), the backend returns an explicit `SandboxResult(success=False, error="subprocess terminated without result (exit code …)")`. Child also writes breadcrumbs to its stderr so Render logs show whether `conn.send` succeeded.
- `services/ai_tools._exec_run_python` error-field tripwire — any `success=False` path always populates both `error` (concrete message) and `error_class` (one of `sandbox_crash` / `oom` / `timeout` / `name_error` / `import_error` / `syntax_error` / …). `sandbox_silent_failure_total` Prometheus counter fires when the synthesised-error path is taken. The frontend renders `error_class` as a red chip next to the error line.

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
- Backend: `https://astro-backend-h4x1.onrender.com`
- Frontend: `https://astro-frontend-tyfr.onrender.com`
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
PIPELINE_MODE=celery             # "celery" (default) or "sync" for dev/test only.
                                 # DAGs containing heavy nodes (BayesianFit, TransitFit,
                                 # ImageStack, ...) return 503 in sync mode.
SANDBOX_BACKEND=subprocess       # "subprocess" (default, crash-isolated) or "inproc"
CONNECTOR_CACHE_BACKEND=auto     # "auto" (Redis if available, else SQLite), "null", "sqlite", "redis"
MAX_UPLOAD_SIZE=104857600        # max FITS upload size in bytes (default 100MB)
GOOGLE_CLIENT_ID=...             # Google OAuth client ID (from Google Cloud Console)
GOOGLE_CLIENT_SECRET=...         # Google OAuth client secret
```

Frontend:
```
VITE_API_URL=https://your-backend.com   # defaults to http://localhost:8000
VITE_GOOGLE_CLIENT_ID=...               # same Google OAuth client ID as backend
```
