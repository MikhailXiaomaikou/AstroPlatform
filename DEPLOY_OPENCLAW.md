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
```

`PROVENANCE_VALIDATOR_HARDBLOCK` defaults to warning mode. When set to `true`, citation violations from the provenance-v2 validator block replies.

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
