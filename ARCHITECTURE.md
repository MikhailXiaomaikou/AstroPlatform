# Astro Research Platform — Architecture

## System Overview

```
                                    Astro Research Platform v0.3.0
                                    61 API Routes | 9 Connectors | 11 Pipeline Nodes

    +-------------------------------------------------------------------------------------------+
    |                                      FRONTEND (React 19)                                   |
    |                           TypeScript + Vite + ReactFlow + Axios                            |
    |                                                                                           |
    |  +-------------+ +-------------+ +-----------+ +--------+ +------+ +---------+ +--------+ |
    |  | DataBrowser | |  Pipeline   | | Workspace | |  ADQL  | | Team | | Billing | |  Chat  | |
    |  |  SearchBar  | |   Canvas    | |  Files    | | Query  | | Mgmt | |  Plans  | |   AI   | |
    |  | ResultsTable| | (ReactFlow) | |  Tags     | | Editor | | Share| | Upgrade | | Claude | |
    |  | FITSPreview | | NodePalette | |  Notes    | |        | |Comment| |  Usage | | Actions| |
    |  |  ImageView  | |  Scheduler  | |  Batch    | |        | |      | |        | |        | |
    |  |  Spectrum   | | VersionHist | |  Export   | |        | |      | |        | |        | |
    |  |  WCS Grid   | |  DiffView   | |  SAMP     | |        | |      | |        | |        | |
    |  |  Blink      | |  ExportBtns | |           | |        | |      | |        | |        | |
    |  +------+------+ +------+------+ +-----+-----+ +---+----+ +--+---+ +---+----+ +---+----+ |
    |         |               |              |            |         |         |           |      |
    +---------+---------------+--------------+------------+---------+---------+-----------+------+
              |               |              |            |         |         |           |
              +-------+-------+------+-------+-----+-----+---------+---------+-----------+
                      |              |              |
                      v              v              v
    +===================================================================================+
    |                            API Client (Axios + JWT)                                |
    |              80+ type-safe functions | Auto auth token injection                  |
    +===================================================================================+
                                         |
                                    HTTP / WebSocket
                                         |
    +===================================================================================+
    |                                   NGINX                                           |
    |                    Reverse proxy | SPA routing | WebSocket proxy                  |
    +===================================================================================+
                                         |
    +====================================|==============================================+
    |                          BACKEND (FastAPI 0.3.0)                                  |
    |                    Python 3.11 | Async | 61 Routes                                |
    |                                                                                   |
    |  +------------------+  +------------------+  +--------------------+               |
    |  |   Middleware      |  |   Security       |  |    Infrastructure  |               |
    |  |  +--------------+ |  |  +-----------+   |  |  +--------------+  |               |
    |  |  | CORS         | |  |  | JWT Auth  |   |  |  | Rate Limit   |  |               |
    |  |  | (env-based)  | |  |  | (bcrypt)  |   |  |  | (slowapi)    |  |               |
    |  |  +--------------+ |  |  +-----------+   |  |  +--------------+  |               |
    |  +------------------+  |  | Stripe Sub |   |  |  | Redis Cache  |  |               |
    |                        |  +-----------+   |  |  | (5min TTL)   |  |               |
    |                        +------------------+  |  +--------------+  |               |
    |                                              +--------------------+               |
    |                                                                                   |
    |  +===========================================================================+   |
    |  |                         API ROUTERS (10)                                   |   |
    |  |                                                                           |   |
    |  |  /api/auth/*          Auth, register, login, subscribe, Stripe webhook    |   |
    |  |  /api/data/*          Search, fetch, FITS header/spectrum/WCS             |   |
    |  |  /api/pipeline/*      Run, templates, versioning, diff, node types        |   |
    |  |  /api/workspace/*     Batch search, tags, notes, export                   |   |
    |  |  /api/scheduler/*     CRUD scheduled pipeline runs (cron)                 |   |
    |  |  /api/team/*          Invite, members, share pipelines/datasets, comments |   |
    |  |  /api/export/*        CSV, VOTable, PDF report export                     |   |
    |  |  /api/integration/*   SAMP, VOTable, ADQL/TAP, Jupyter export             |   |
    |  |  /api/chat/*          AI assistant (Claude) message + action execution     |   |
    |  |  /health/*            Basic + detailed (DB/Redis/MinIO checks)             |   |
    |  |  /ws/pipeline/{id}    WebSocket real-time pipeline progress                |   |
    |  +===========================================================================+   |
    |                                                                                   |
    |  +==================================+  +=====================================+   |
    |  |      DATA CONNECTORS (9)         |  |       PIPELINE ENGINE               |   |
    |  |      (with retry decorator)      |  |  (Celery async + sync fallback)     |   |
    |  |                                  |  |                                     |   |
    |  |  +------+ +------+ +----------+ |  |  +-------------------------------+  |   |
    |  |  | SDSS | | Gaia | | SIMBAD   | |  |  |      DAG Validator            |  |   |
    |  |  +------+ +------+ +----------+ |  |  |  Cycle detection, type check  |  |   |
    |  |  +--------+ +------+ +--------+ |  |  +-------------------------------+  |   |
    |  |  | VizieR | | MAST | |  NED   | |  |                                     |   |
    |  |  +--------+ +------+ +--------+ |  |  +-------------------------------+  |   |
    |  |  +-------+ +---------+ +------+ |  |  |    Topological Sort & Execute  |  |   |
    |  |  | 2MASS | | Chandra | |AllWISE| |  |  |    Redis pub/sub progress     |  |   |
    |  |  +-------+ +---------+ +------+ |  |  +-------------------------------+  |   |
    |  |                                  |  |                                     |   |
    |  |  BaseConnector interface:        |  |  +-------------------------------+  |   |
    |  |    search(q, ra, dec, radius)    |  |  |     PROCESSING NODES (11)     |  |   |
    |  |    fetch(object_id) -> FITS      |  |  |                               |  |   |
    |  |    normalize() -> astropy Table  |  |  |  LoadData    | Denoise        |  |   |
    |  |                                  |  |  |  SpectralFit | CoordTransform |  |   |
    |  |  Retry: 3x exponential backoff   |  |  |  Plot        | Redshift       |  |   |
    |  |  Cache: Redis 5-min TTL          |  |  |  EquivWidth  | SEDFit         |  |   |
    |  +==================================+  |  |  CrossMatch  | PhotCalibrate  |  |   |
    |                                        |  |  ImageStack  |                |  |   |
    |                                        |  +-------------------------------+  |   |
    |                                        +=====================================+   |
    |                                                                                   |
    |  +===========================================================================+   |
    |  |                        DATA MODELS (13 tables)                             |   |
    |  |                   SQLAlchemy 2.0 Async | Alembic migrations                |   |
    |  |                                                                           |   |
    |  |  User              DataFile           PipelineRun        RunResult         |   |
    |  |  PipelineTemplateDB PipelineVersion   DataTag            DataNote          |   |
    |  |  TeamMember         SharedPipeline    PipelineComment    SharedDataset     |   |
    |  |  ScheduledRun                                                             |   |
    |  |                                                                           |   |
    |  |  Portable types: UUIDType (SQLite/PostgreSQL), JSONType (TEXT/JSONB)       |   |
    |  +===========================================================================+   |
    |                                                                                   |
    +===================================|===============================================+
                                        |
              +-------------------------+-------------------------+
              |                         |                         |
              v                         v                         v
    +-------------------+   +--------------------+   +------------------------+
    |    PostgreSQL      |   |      Redis         |   |        MinIO           |
    |   (metadata)       |   |  (cache + pubsub   |   |   (FITS file storage)  |
    |                    |   |   + Celery broker)  |   |                        |
    |  13 tables         |   |  Query cache 5min   |   |  S3-compatible         |
    |  Alembic managed   |   |  WS progress relay  |   |  Local FS fallback     |
    |  SQLite dev mode   |   |  Task queue         |   |                        |
    +-------------------+   +--------------------+   +------------------------+
                                        |
                                        v
                            +--------------------+
                            |   Celery Worker    |
                            |  Pipeline executor |
                            |  + Scheduler Beat  |
                            |  (60s poll cycle)  |
                            +--------------------+


    +===========================================================================================+
    |                              EXTERNAL SERVICES                                             |
    |                                                                                           |
    |  Astronomical Databases:                                                                  |
    |  +------+ +--------+ +--------+ +--------+ +------+ +-----+ +-------+ +---------+ +----+ |
    |  | SDSS | | Gaia   | | SIMBAD | | VizieR | | MAST | | NED | | 2MASS | | Chandra | |WISE| |
    |  | DR18 | | DR3    | |        | |        | | HST  | |     | |       | | CSC2    | |    | |
    |  |      | |        | |        | |        | | JWST | |     | |       | |         | |    | |
    |  +------+ +--------+ +--------+ +--------+ +------+ +-----+ +-------+ +---------+ +----+ |
    |                                                                                           |
    |  Third-party APIs:                                                                        |
    |  +-----------+ +------------------+ +-------------------+                                 |
    |  | Stripe    | | Claude API       | | SAMP Hub          |                                 |
    |  | Payments  | | (AI Assistant)   | | (DS9/TOPCAT/Aladin)|                                 |
    |  +-----------+ +------------------+ +-------------------+                                 |
    |                                                                                           |
    |  TAP Services (ADQL):                                                                     |
    |  +-----------+ +------------------+ +-------------------+                                 |
    |  | Gaia TAP  | | VizieR TAP       | | CADC TAP          |                                 |
    |  +-----------+ +------------------+ +-------------------+                                 |
    +===========================================================================================+
```

## Data Flow

```
    User Request                    Pipeline Execution Flow
    ============                    =======================

    Browser                         1. User builds DAG in ReactFlow canvas
      |                             2. POST /api/pipeline/run
      v                                |
    React App                       3. DAG Validator
      |                                |-- Cycle detection
      v                                |-- Node type validation
    Axios + JWT                        |-- Duplicate ID check
      |                                |
      v                             4. async_mode?
    FastAPI                            |
      |                             YES --> Celery task dispatched
      +-- Rate Limit check              |    (returns run_id immediately)
      |                                 |    Worker executes nodes
      +-- JWT Auth verify               |    in topological order
      |                                 |    Redis pub/sub progress
      +-- Redis Cache check             |    WebSocket relay to frontend
      |                                 |
      +-- Connector query           NO  --> Synchronous execution
      |   (with retry x3)               |    Direct result return
      |                                 |
      +-- Response                   5. Results stored in MinIO
                                     6. Metadata in PostgreSQL
                                     7. Export as CSV/VOTable/PDF


    AI Chat Flow
    =============

    User: "Find bright stars near M31 and estimate their redshifts"
      |
      v
    POST /api/chat/message
      |
      v
    Claude API (claude-sonnet-4-20250514)
      |-- System prompt: platform capabilities + available actions
      |-- User conversation history
      |
      v
    Response with <actions> tags parsed:
      |-- reply: "I'll search for bright stars near M31..."
      |-- actions: [
      |     {action: "search", query: "M31", sources: ["gaia","sdss"], radius: 0.1},
      |     {action: "adql", query: "SELECT ... FROM gaiadr3...", service: "gaia"}
      |   ]
      |
      v
    Frontend renders action cards --> User clicks --> POST /api/chat/execute-action
      |
      v
    Results displayed inline in chat
```

## Subscription Tiers

```
    +---------------------+------------------------+---------------------------+
    |       Solo          |         Lab            |       Institution         |
    |      $29/mo         |       $99/mo           |         Custom            |
    +---------------------+------------------------+---------------------------+
    | 1 user              | 5 users (team)         | Unlimited users           |
    | 5 connectors        | All 9 connectors       | All + custom connectors   |
    | 10 runs/day         | Unlimited runs         | Unlimited + priority      |
    | 5 GB storage        | 50 GB storage          | Unlimited storage         |
    | Basic export        | All exports + SAMP     | All + priority support    |
    | --                  | Team collaboration     | SSO/SAML                  |
    | --                  | Pipeline sharing       | Dedicated infrastructure  |
    | --                  | Comments               | SLA guarantee             |
    +---------------------+------------------------+---------------------------+
```

## Tech Stack Summary

```
    Frontend                    Backend                     Infrastructure
    --------                    -------                     --------------
    React 19.2                  FastAPI 0.109               PostgreSQL
    TypeScript 5.9              Python 3.11                 Redis
    Vite 7.3                    SQLAlchemy 2.0 (async)      MinIO (S3)
    ReactFlow 11.11             Celery 5.3 + Beat           Docker Compose
    Axios                       Alembic (migrations)        GitHub Actions CI
    React Router 7              Pydantic v2                 nginx (reverse proxy)
    React Query v5              astropy + numpy + scipy
                                anthropic SDK
                                slowapi (rate limiting)
                                reportlab (PDF export)

    Testing                     Security
    -------                     --------
    pytest (88 tests)           JWT + bcrypt
    pytest-asyncio              Rate limiting (slowapi)
    vitest (12 tests)           CORS (env-configured)
    100 total tests             Non-root containers
                                Resource limits
                                Env-based secrets
```

## Directory Structure

```
    astro-platform/
    ├── .github/workflows/ci.yml          # CI: pytest + tsc + vitest + ruff
    ├── docker-compose.yml                # 6 services with resource limits
    ├── CLAUDE.md                         # Project documentation
    ├── ARCHITECTURE.md                   # This file
    │
    ├── backend/
    │   ├── Dockerfile                    # Python 3.11-slim, non-root USER
    │   ├── requirements.txt              # 30+ dependencies
    │   ├── alembic.ini                   # Migration config
    │   ├── celery_worker.py              # Celery app + Beat schedule
    │   ├── pytest.ini                    # Test config
    │   │
    │   ├── alembic/
    │   │   ├── env.py                    # Async migration support
    │   │   └── versions/001_initial.py   # 13-table initial migration
    │   │
    │   ├── app/
    │   │   ├── main.py                   # FastAPI app (v0.3.0, 61 routes)
    │   │   ├── auth.py                   # JWT create/verify, password hashing
    │   │   ├── config.py                 # Settings (env-based, secure defaults)
    │   │   ├── cache.py                  # Redis async cache (get/set/key)
    │   │   ├── cors.py                   # CORS origin configuration
    │   │   ├── rate_limit.py             # slowapi limiter
    │   │   ├── storage.py                # MinIO + local FS fallback
    │   │   ├── scheduler_worker.py       # Standalone cron scheduler
    │   │   │
    │   │   ├── api/                      # 10 API routers
    │   │   │   ├── auth.py               #   Auth + Stripe (5 endpoints)
    │   │   │   ├── chat.py               #   AI assistant (2 endpoints)
    │   │   │   ├── data.py               #   Data search + FITS (6 endpoints)
    │   │   │   ├── export.py             #   CSV/VOTable/PDF (3 endpoints)
    │   │   │   ├── health.py             #   Health checks (1 endpoint)
    │   │   │   ├── integration.py        #   SAMP/ADQL/VOTable (7 endpoints)
    │   │   │   ├── pipeline.py           #   Pipeline CRUD + versioning (10 endpoints)
    │   │   │   ├── scheduler.py          #   Scheduled runs (4 endpoints)
    │   │   │   ├── team.py               #   Collaboration (10 endpoints)
    │   │   │   ├── workspace.py          #   File management (8 endpoints)
    │   │   │   └── ws.py                 #   WebSocket relay (1 endpoint)
    │   │   │
    │   │   ├── connectors/               # 9 data source connectors
    │   │   │   ├── base.py               #   BaseConnector + AstroObject
    │   │   │   ├── registry.py           #   Lazy connector registry
    │   │   │   ├── retry.py              #   Exponential backoff decorator
    │   │   │   ├── sdss.py               #   SDSS DR18 (SkyServer API)
    │   │   │   ├── gaia.py               #   Gaia DR3 (astroquery)
    │   │   │   ├── simbad.py             #   SIMBAD (astroquery)
    │   │   │   ├── vizier.py             #   VizieR catalogs (astroquery)
    │   │   │   ├── mast.py               #   MAST HST/JWST (astroquery)
    │   │   │   ├── ned.py                #   NED extragalactic (httpx)
    │   │   │   ├── twomass.py            #   2MASS infrared (VizieR)
    │   │   │   ├── chandra.py            #   Chandra X-ray (CSC2 API)
    │   │   │   └── allwise.py            #   AllWISE infrared (VizieR)
    │   │   │
    │   │   ├── pipeline/
    │   │   │   ├── engine.py             #   DAG executor (sync + Celery async)
    │   │   │   ├── validate.py           #   DAG validation (cycles, types)
    │   │   │   └── nodes/                #   11 processing nodes
    │   │   │       ├── load_data.py      #     Load FITS file
    │   │   │       ├── denoise.py        #     Sigma-clip noise removal
    │   │   │       ├── spectral_fit.py   #     Gaussian/Lorentzian fitting
    │   │   │       ├── coord_transform.py#     ICRS/Galactic/FK5 transforms
    │   │   │       ├── plot.py           #     Matplotlib visualization
    │   │   │       ├── redshift.py       #     Redshift estimation
    │   │   │       ├── equivalent_width.py#    Spectral line EW measurement
    │   │   │       ├── sed_fit.py        #     SED fitting (blackbody/power-law)
    │   │   │       ├── crossmatch.py     #     Catalog cross-matching
    │   │   │       ├── phot_calibrate.py #     Photometric calibration
    │   │   │       └── image_stack.py    #     Image stacking (mean/median/sigma)
    │   │   │
    │   │   └── models/
    │   │       ├── database.py           #   Async engine + session factory
    │   │       └── schemas.py            #   13 SQLAlchemy models
    │   │
    │   └── tests/                        # 88 backend tests
    │       ├── conftest.py               #   Fixtures (in-memory SQLite, test client)
    │       ├── test_api.py               #   API endpoint tests (32 tests)
    │       ├── test_connectors.py        #   Retry decorator tests (9 tests)
    │       ├── test_models.py            #   Model + type tests (23 tests)
    │       └── test_pipeline_nodes.py    #   Node function tests (24 tests)
    │
    └── frontend/
        ├── Dockerfile                    # Node 20 build + nginx-unprivileged
        ├── nginx.conf                    # SPA routing + API/WS proxy
        ├── package.json                  # React 19, TypeScript 5.9, vitest
        ├── vite.config.ts                # Vite + vitest config
        │
        └── src/
            ├── App.tsx                   # Router: 8 routes + NavBar
            ├── App.css                   # 2400+ lines, dark theme
            ├── main.tsx                  # Entry point
            │
            ├── api/
            │   └── client.ts            # 80+ typed API functions
            │
            ├── context/
            │   └── AuthContext.tsx       # Auth state + JWT management
            │
            ├── components/
            │   ├── fits/
            │   │   └── FITSPreview.tsx   # Image viewer (DS9-like) + spectrum + WCS + blink
            │   └── nodes/
            │       ├── PipelineNode.tsx  # ReactFlow node component
            │       └── NodePalette.tsx   # Draggable node palette
            │
            ├── pages/
            │   ├── ADQL/ADQLPage.tsx         # ADQL query editor
            │   ├── Auth/AuthPage.tsx         # Login/register
            │   ├── Billing/BillingPage.tsx   # Subscription management
            │   ├── Chat/ChatPage.tsx         # AI assistant chat UI
            │   ├── DataBrowser/              # Search + results + FITS preview
            │   │   ├── DataBrowser.tsx
            │   │   ├── SearchBar.tsx
            │   │   └── ResultsTable.tsx
            │   ├── Pipeline/
            │   │   └── PipelineCanvas.tsx    # ReactFlow canvas + versioning + scheduler
            │   ├── Team/TeamPage.tsx         # Team management + sharing
            │   └── Workspace/WorkspacePage.tsx # File management + batch
            │
            └── __tests__/
                └── client.test.ts       # 12 frontend tests
```
