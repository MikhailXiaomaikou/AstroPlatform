# Standard Astro Deployment Guide

Current production deploys use the Render blueprint in `render.yaml`.

## Production Services

| Service | Type | Purpose |
|---|---|---|
| `standard-astro-backend` | Web service | FastAPI API server |
| `standard-astro-frontend` | Static site | Vite SPA with rewrite-to-index routing |
| `standard-astro-celery-worker` | Worker | Heavy pipeline execution |
| `standard-astro-celery-beat` | Worker | Scheduled jobs |
| `standard-astro-redis` | Redis | Celery queue, pub/sub, cache |
| `standard-astro-db` | PostgreSQL | Primary database |

Live URLs used by the current docs:

- Backend: `https://astro-backend-h4x1.onrender.com`
- Frontend: `https://astro-frontend-tyfr.onrender.com`

Pushes to `main` trigger Render auto-deploys.

## Backend Environment

Set these on `standard-astro-backend`:

```bash
ENV=production
DATABASE_URL=postgresql://...          # Render internal DB URL; backend converts to asyncpg
JWT_SECRET=<random-hex-32>
CORS_ORIGINS=https://astro-frontend-tyfr.onrender.com,http://localhost:5173
RATE_LIMIT_ENABLED=false               # optional for beta deployments
PORT=8000
```

Optional production variables:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ADS_API_KEY=...
REDIS_URL=redis://...
PIPELINE_MODE=celery
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
ADMIN_SECRET=<random-hex-32>
PROVENANCE_VALIDATOR_HARDBLOCK=false
ASTRO_RESEARCH_FOCUS=cosmology          # cosmology | solar_system | exoplanet | all
```

`PROVENANCE_VALIDATOR_HARDBLOCK` defaults to warning mode. When set to `true`, citation violations from the provenance-v2 validator block replies.

`ASTRO_RESEARCH_FOCUS` selects which active research module the process serves. See [Research module focus](#research-module-focus) below — the default `cosmology` is what `render.yaml` ships, and changing this env var is the only correct way to flip the process between cosmology / solar-system / exoplanet workflows.

## Frontend Environment

Set these on `standard-astro-frontend`:

```bash
VITE_API_URL=https://astro-backend-h4x1.onrender.com
VITE_GOOGLE_CLIENT_ID=...
```

## Verification

After deploy:

```bash
curl https://astro-backend-h4x1.onrender.com/health
curl https://astro-backend-h4x1.onrender.com/metrics
```

Manual smoke checks:

1. Open the frontend and confirm the backend wake-up banner clears.
2. Search an active source such as SIMBAD or Gaia DR3.
3. Try a gated source such as Chandra or SDSS and confirm the UI shows Maintenance / `UNAVAILABLE`, not a generic error.
4. In AI Assistant, run a tool-backed query and confirm Data Sources and Copy Acknowledgement appear when provenance is present.

## Research module focus

Standard Astro now ships three active research modules — `cosmology`, `solar_system`, and `exoplanet` — gated at runtime by the `ASTRO_RESEARCH_FOCUS` environment variable. The focus selects (1) which `modules/<focus>/prompt.md` is appended to the SYSTEM_PROMPT and (2) which manifest tool allowlist `_filter_tools_by_research_focus` sends to the LLM. Non-focus tools are physically invisible to the model.

Supported values:

| Value | Active prompt + tool surface | Active provenance-v2 connectors surfaced |
|---|---|---|
| `cosmology` (default) | `modules/cosmology` (35 tools) — BAO / SN / CMB / lensing likelihood workflow, literature-table / line-relation workflow, dataset registry, compressed-likelihood runner | VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA observation metadata |
| `solar_system` | `modules/solar_system` (12 tools) — MPC + JPL Horizons + JPL SBDB + Sentry-II + DAMIT lookups; H–G / Afρ / NEATM / Öpik formulas; Bus-DeMeo / Carvano taxonomy | The cosmology set + JPL Horizons (`jpl`) + IAU MPC (`mpc`) |
| `exoplanet` | `modules/exoplanet` (9 tools) — NASA Exoplanet Archive + Confirmed Planets + TESS / TIC v8 queries; trapezoidal transit fit (with batman/pytransit recommended downstream); equilibrium-temperature / depth / density helpers; `fit_rv_orbit` carried over from the original dormant manifest | The cosmology set + NASA Exoplanet Archive (`nasa_exoplanet_archive`); TESS / TIC v8 reached via MAST through `lightkurve` even while the generic `mast` key remains gated |
| `all` (or any unrecognised value) | All modules loaded, no L1 tool filtering applied | All active v2 connectors |

Authoritative source-of-truth: `backend/app/connectors/availability.py` `V2_AVAILABLE_CONNECTORS` (and the mirror in `backend/app/services/source_mapping.py`, enforced by `backend/tests/test_source_mapping.py`). Human-facing status: [docs/SOURCE_MAPPING.md](./docs/SOURCE_MAPPING.md).

Multi-focus deployment patterns:

1. **Single-focus process (recommended for prod).** Set `ASTRO_RESEARCH_FOCUS=cosmology` (or `solar_system` / `exoplanet`) on `standard-astro-backend`. The default for the shipping `render.yaml` is `cosmology`. Flip the env var in the Render dashboard and trigger a manual deploy to switch focus.
2. **Multi-focus deployment (one backend per focus).** Run three copies of `standard-astro-backend` behind different subdomains / paths, each pinned to a different `ASTRO_RESEARCH_FOCUS`. Share the same `standard-astro-db` and `standard-astro-redis` so chat sessions stay portable; route the frontend at the chosen focus per user / per workspace. The frontend itself does not yet expose a focus switcher — switching focuses requires the user to land on the appropriate backend URL.
3. **Unified `all` deployment (development only).** Setting `ASTRO_RESEARCH_FOCUS=all` (or any value outside `_FOCUS_GATED_VALUES`) disables the L1 hard gate. Useful for local cross-module testing; do not use in production because the LLM sees the full 91-tool catalog plus all module prompts and quickly exhausts its context budget.

The focus literal must be one of `cosmology`, `solar_system`, `exoplanet`, or `all`. Anything else is treated as `all` for the prompt assembler but as a no-op for the tool gate — that combination is intentionally weird so misspelled env values get caught quickly in dev rather than silently shipping the wrong manifest.

## Provenance-v2 Startup Guard

Backend startup calls the fallback-registry freshness check. If any registry entry is stale, startup raises `Provenance registry freshness check failed` and logs `provenance_registry_freshness_blocker`. Fix `backend/app/services/provenance_v2/fallback_registry.yaml` before redeploying.

## Troubleshooting

### Backend build fails on scientific wheels

The backend Docker image installs system libraries for the scientific stack. If a package starts building from source unexpectedly, check `backend/requirements.txt` pins and Render build logs.

### CORS errors

Make sure `CORS_ORIGINS` contains the exact frontend origin, including scheme. The current Render frontend is `https://astro-frontend-tyfr.onrender.com`.

### Free-tier cold starts

Render free-tier services sleep after idle time. The frontend retries one 502/503/504 response after 5 seconds and displays the backend wake-up banner.

### Gated connector confusion

The provenance-v2 rollout intentionally gates non-v2 sources. This is expected until each connector has an M3-style provenance upgrade and is added to `V2_AVAILABLE_CONNECTORS`.
