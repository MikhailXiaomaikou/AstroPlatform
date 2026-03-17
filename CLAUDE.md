# Astro Research Platform

## Project Overview
A SaaS platform for professional astronomers that unifies data ingestion from major astronomical databases and provides a visual pipeline editor for data processing workflows.

**Subscription model**: Solo ($29/mo), Lab ($99/mo, 5 seats), Institution (custom)

## Tech Stack
- **Frontend**: React + TypeScript + ReactFlow (pipeline canvas) + Vite
- **Backend**: Python 3.11 + FastAPI + Celery + Redis
- **Database**: PostgreSQL (metadata) + MinIO (FITS file storage)
- **Astronomy**: astropy, numpy, scipy, specutils
- **Auth**: JWT + Stripe (subscriptions)
- **Deploy**: Docker Compose (dev) → AWS ECS (prod)

## Repository Structure
```
astro-platform/
├── frontend/                  # React app
│   ├── src/
│   │   ├── pages/
│   │   │   ├── DataBrowser/   # Search & preview astronomical data
│   │   │   └── Pipeline/      # Drag-and-drop workflow canvas
│   │   ├── components/
│   │   │   ├── nodes/         # ReactFlow pipeline nodes
│   │   │   └── fits/          # FITS file viewer
│   │   └── api/               # API client (axios)
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI entry point
│   │   ├── api/
│   │   │   ├── data.py        # /api/data/* endpoints
│   │   │   └── pipeline.py    # /api/pipeline/* endpoints
│   │   ├── connectors/        # Data source connectors
│   │   │   ├── base.py        # Abstract base connector
│   │   │   ├── sdss.py        # SDSS DR18 via SkyServer API
│   │   │   ├── gaia.py        # Gaia DR3 via astroquery
│   │   │   └── simbad.py      # SIMBAD via astroquery
│   │   ├── pipeline/
│   │   │   ├── engine.py      # Celery task executor
│   │   │   └── nodes/         # Built-in processing nodes
│   │   │       ├── denoise.py
│   │   │       ├── spectral_fit.py
│   │   │       ├── coord_transform.py
│   │   │       └── plot.py
│   │   ├── models/            # SQLAlchemy ORM models
│   │   └── storage.py         # MinIO FITS file management
│   ├── requirements.txt
│   └── celery_worker.py
│
├── docker-compose.yml
└── CLAUDE.md                  # This file
```

## Core Concepts

### Data Connectors
Each connector implements `BaseConnector`:
```python
class BaseConnector:
    async def search(self, query: str, ra: float, dec: float, radius: float) -> list[AstroObject]
    async def fetch(self, object_id: str) -> FITSFile
    def normalize(self, raw_data) -> StandardizedData  # Always converts to astropy Table
```

### Pipeline Nodes
Each node is a Celery task + ReactFlow node definition:
```python
# Backend: pure function, receives/returns standardized data
@celery_app.task
def denoise_node(input_data: dict, params: dict) -> dict:
    ...

# Frontend: ReactFlow node with input/output handles
# Nodes connect via handles; data flows left→right
```

### Pipeline Execution Flow
1. User builds pipeline in canvas (ReactFlow graph = JSON DAG)
2. POST /api/pipeline/run → serialize DAG to JSON
3. Backend topologically sorts nodes
4. Each node runs as Celery task, chained in order
5. Results stored in MinIO, metadata in PostgreSQL
6. WebSocket pushes progress updates to frontend

## API Endpoints

### Data API
```
GET  /api/data/search?q={name_or_coords}&sources=sdss,gaia,simbad
GET  /api/data/{source}/{object_id}          # Fetch & store FITS
GET  /api/data/workspace                     # List user's data files
```

### Pipeline API
```
POST /api/pipeline/run        # Body: { dag: {...}, input_data_id: str }
GET  /api/pipeline/{run_id}   # Status + results
GET  /api/pipeline/templates  # List built-in templates
POST /api/pipeline/save       # Save pipeline as template
```

## Data Model (PostgreSQL)
```sql
users          (id, email, subscription_tier, stripe_customer_id)
data_files     (id, user_id, source, object_id, fits_path, metadata jsonb)
pipeline_runs  (id, user_id, dag jsonb, status, created_at, completed_at)
run_results    (id, run_id, node_id, output_path, logs)
```

## Built-in Pipeline Nodes (MVP)
| Node | Input | Output | Key Params |
|------|-------|--------|------------|
| LoadData | data_file_id | spectrum/image | - |
| Denoise | spectrum | spectrum | sigma_clip threshold |
| SpectralFit | spectrum | fit_result | model (gaussian/lorentzian) |
| CoordTransform | coords | coords | from_frame, to_frame |
| Plot | any | PNG/HTML | plot_type (spectrum/image/scatter) |

## Environment Variables
```
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
MINIO_ENDPOINT=...
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...
STRIPE_SECRET_KEY=...
JWT_SECRET=...
```

## Development Setup
```bash
# Start all services
docker-compose up -d

# Backend (with hot reload)
cd backend && uvicorn app.main:app --reload

# Frontend
cd frontend && npm run dev

# Celery worker
cd backend && celery -A celery_worker worker --loglevel=info
```

## Implementation Priority
1. **Phase 1** – Backend connectors (SDSS + Gaia) + search API
2. **Phase 2** – Frontend data browser + FITS preview
3. **Phase 3** – Pipeline engine (Celery) + 5 built-in nodes
4. **Phase 4** – Frontend canvas (ReactFlow) wired to backend
5. **Phase 5** – Auth + Stripe subscriptions + deploy
