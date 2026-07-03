# Standard Astro Deployment Guide

Current production deploys use the Render blueprint in `render.yaml`.

## Production Services

Currently deployed on Render: **3 services + 1 database** (`render.yaml`'s header comment is the source of truth).

| Service | Type | Purpose |
|---|---|---|
| `standard-astro-backend` | Web service | FastAPI API server |
| `standard-astro-frontend` | Static site | Vite SPA with rewrite-to-index routing |
| `standard-astro-db` | PostgreSQL | Primary database |

**Not deployed** (kept as commented templates at the bottom of `render.yaml`): `standard-astro-celery-worker`, `standard-astro-celery-beat`, `standard-astro-redis`. `PIPELINE_MODE` stays `celery` by code default but no worker is deployed, so heavy pipeline DAG runs (BayesianFit / ImageStack / etc.) return `503` until a Celery worker is brought up. The cosmology chat path does not go through Celery and is unaffected.

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
ASTRO_RESEARCH_FOCUS=cosmology          # cosmology | all  (any other value fails closed to cosmology)
```

`PROVENANCE_VALIDATOR_HARDBLOCK` defaults to warning mode. When set to `true`, citation violations from the provenance-v2 validator block replies.

`ASTRO_RESEARCH_FOCUS` selects which active research module the process serves. This repository is cosmology-only, so `cosmology` (the `render.yaml` default) is the only active focus. See [Research module focus](#research-module-focus) below.

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

This repository is **cosmology-only**. `ASTRO_RESEARCH_FOCUS` still exists as the runtime gate, but the only active module is `cosmology` (the solar-system and exoplanet verticals were extracted to the sibling `standard-astro-verticals` repo on 2026-06-03). The focus selects (1) which `modules/<focus>/prompt.md` is appended to the SYSTEM_PROMPT and (2) which manifest tool allowlist `_filter_tools_by_research_focus` sends to the LLM. Non-focus tools are physically invisible to the model.

Supported values:

| Value | Active prompt + tool surface | Active provenance-v2 connectors surfaced |
|---|---|---|
| `cosmology` (default) | `modules/cosmology` (57 tools incl. shared core/infrastructure) — BAO / SN / CMB / lensing likelihood workflow, literature-table / line-relation workflow, dataset registry, compressed-likelihood runner | VizieR, Gaia DR3, SIMBAD, NED, 2MASS, ALMA observation metadata |
| `all` | All modules loaded, no L1 tool filtering (exposes the full 77-tool catalog incl. retained dormant tools) | All active v2 connectors |
| anything else (empty / typo / stale `solar_system` pin) | **Fails closed** to the `cosmology` allowlist | The cosmology connector set |

Authoritative source-of-truth: `backend/app/connectors/availability.py` `V2_AVAILABLE_CONNECTORS` (and the mirror in `backend/app/services/source_mapping.py`, enforced by `backend/tests/test_source_mapping.py`). Human-facing status: [docs/SOURCE_MAPPING.md](./docs/SOURCE_MAPPING.md).

Deployment patterns:

1. **Single-focus process (recommended for prod).** Leave `ASTRO_RESEARCH_FOCUS=cosmology` (the `render.yaml` default) on `standard-astro-backend`.
2. **Unified `all` deployment (development only).** Setting `ASTRO_RESEARCH_FOCUS=all` disables the L1 hard gate. Useful for local cross-module testing of retained dormant tools; do not use in production because the LLM sees the full 77-tool catalog plus all module prompts and quickly exhausts its context budget.

Any focus literal other than `all` (including a stale `solar_system` / `exoplanet` pin left over from before the extraction) **fails closed** to the cosmology allowlist — it never silently exposes the full tool surface under a cosmology-only prompt.

## Provenance-v2 Startup Guard

Backend startup (`lifespan` in `backend/app/main.py`) runs the fallback-registry
freshness check before anything else, in **every** environment — production and
local dev alike; there is no bypass flag, and none should be added. It loads
`backend/app/services/provenance_v2/fallback_registry.yaml` and requires every
service entry's `metadata.last_verified` date to be within 180 days of today
(a missing or unparseable registry also fails).

On failure the process refuses to start:

- each stale entry is logged as
  `provenance_registry_freshness_blocker <service>: registry entry is N days old`
- startup raises
  `RuntimeError: Provenance registry freshness check failed: <stale entries>`

Refresh procedure (the sanctioned fix):

1. For each stale service entry in `fallback_registry.yaml`, re-verify its
   provenance metadata against the archive itself: `credits_page_url` /
   `reference_url` still resolve, the `acknowledgement_template` wording still
   matches what the archive requests, and `ivoid` / `article` are still current.
2. Update that entry's `metadata.last_verified` to the date you actually
   re-verified it.
3. From `backend/`, run the focused test:
   `./venv/bin/pytest tests/test_provenance_registry_loader.py -q --no-cov`,
   then commit.

Do **not** blind-bump `last_verified` without re-checking — the date is a claim
that the fallback provenance was verified on that day, and bumping it without
verification is exactly the drift this gate exists to prevent.

## Troubleshooting

### Backend build fails on scientific wheels

The backend Docker image installs system libraries for the scientific stack. If a package starts building from source unexpectedly, check `backend/requirements.txt` pins and Render build logs.

### CORS errors

Make sure `CORS_ORIGINS` contains the exact frontend origin, including scheme. The current Render frontend is `https://astro-frontend-tyfr.onrender.com`.

### Free-tier cold starts

Render free-tier services sleep after idle time. The frontend retries one 502/503/504 response after 5 seconds and displays the backend wake-up banner.

### Gated connector confusion

The provenance-v2 rollout intentionally gates non-v2 sources. This is expected until each connector has an M3-style provenance upgrade and is added to `V2_AVAILABLE_CONNECTORS`.
