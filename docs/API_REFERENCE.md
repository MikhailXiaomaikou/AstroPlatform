# Standard Astro API Reference

Standard Astro uses FastAPI, which auto-generates interactive API documentation.

## Interactive Documentation

| Interface | URL | Best For |
|-----------|-----|----------|
| **Swagger UI** | `{backend-url}/docs` | Testing endpoints interactively |
| **ReDoc** | `{backend-url}/redoc` | Reading documentation |
| **OpenAPI JSON** | `{backend-url}/openapi.json` | Machine-readable schema |

## Authentication

Authenticated account, workspace, session, and team endpoints require a JWT token in the `Authorization` header. Chat and some data-search endpoints also accept optional auth for beta/local usage:

```
Authorization: Bearer <your-jwt-token>
```

Obtain a token via:
- `POST /api/auth/login` -- email + password login
- `POST /api/auth/setup-key-login` -- beta setup key login
- `POST /api/auth/google` -- Google OAuth login

## Key Endpoints

### Health & Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service status + version |
| GET | `/health/stats` | Uptime, request counts, error rate, top endpoints |
| GET | `/health/detailed` | External service probe results (SIMBAD, Gaia, VizieR) |
| GET | `/metrics` | Prometheus text metrics, including provenance-v2 connector and citation counters |
| GET | `/api/inference/stats` | AI model usage statistics (tokens, latency, cost) |
| GET | `/api/inference/health` | AI backend connection status |

### Data Access

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/data/search` | Multi-source astronomical search |
| GET | `/api/data/workspace` | List workspace files |
| POST | `/api/data/fits/upload` | Upload FITS file (returns type detection) |
| GET | `/api/data/fits/header/{path}` | Read FITS headers + HDU structure |
| POST | `/api/integration/adql/query` | Execute ADQL on TAP services |
| POST | `/api/integration/votable/upload` | Upload + convert VOTable to FITS |

The source registry currently exposes 23 connector keys. The active provenance-v2 sources are `vizier`, `gaia`, `simbad`, `ned`, `2mass`, and `alma`; the other 17 keys return an `UNAVAILABLE` maintenance payload instead of executing legacy connector code. ALMA is active for Science Archive observation metadata only, not derived line luminosity or FWHM measurements. Direct SDSS SQL (`run_sdss_sql`) is gated the same way until it emits independent `archive_version` provenance. The solar-system (`jpl`/`mpc`) and exoplanet (`nasa_exoplanet_archive`) connectors — which backed the `fetch_horizons_ephemeris`, `query_mpc_orbit`, and exoplanet-archive tools — were extracted to the sibling standard-astro-verticals repo on 2026-06-03, so they are no longer registered here. `ASTRO_RESEARCH_FOCUS` now fails closed to cosmology for any value other than `all` (see DEPLOYMENT.md), so `solar_system` / `exoplanet` no longer surface a distinct allowlist in this repo.

### AI Assistant

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/message` | Send message (SSE streaming response) |
| POST | `/api/chat/message/stream` | Streaming chat endpoint used by the frontend agent loop |
| GET | `/api/chat/ai_backend_status` | Reports whether server-side or browser-provided AI backends are available |
| GET | `/api/chat/sessions` | List chat sessions |
| GET | `/api/chat/session/{id}` | Get session messages |

### Pipeline

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/pipeline/run` | Execute a pipeline DAG |
| POST | `/api/pipeline/batch-run` | Batch execute on multiple inputs (up to 200) |
| GET | `/api/pipeline/run/{id}` | Get run status + results |
| GET | `/api/pipeline/templates` | List saved pipeline templates |

### Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/export/run/{id}/notebook` | Export as Jupyter notebook |
| GET | `/api/export/run/{id}/csv` | Export as CSV |
| GET | `/api/export/run/{id}/votable` | Export as VOTable |
| GET | `/api/export/run/{id}/fits` | Export as FITS |
| GET | `/api/export/run/{id}/publication-package` | All formats in one response |

### Citations & Literature

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/citations/ads?object=M31` | Search NASA ADS by object |
| GET | `/api/citations/search?q=...` | Free-text literature search |
| GET | `/api/citations/bibtex/{bibcode}` | Export BibTeX |

### Provenance

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/provenance/{id}/lineage` | Data lineage graph |
| GET | `/api/provenance/{id}/export/ivoa` | IVOA ProvDM XML |
| GET | `/api/provenance/{id}/doi-metadata` | DataCite DOI metadata |
| GET | `/api/provenance/{id}/requirements.txt` | Pinned environment |

Tool results also carry inline provenance. The backward-compatible top-level fields (`reproducibility`, `data_origin`, `analysis_status`, `source_urls`, `archive_ids`, `warnings`) remain, and provenance-v2 adds a nested `provenance` object with `datasets`, `field_bibcodes`, `coverage`, and copied reproducibility metadata. Generated papers and the frontend acknowledgement button read from this nested object.

For literature-derived measurement workflows, `search_literature` is paper/abstract-level evidence only. Measurement claims such as line-luminosity/FWHM slopes, intercepts, intrinsic scatter, and correlation values require extracted table rows or a publication-ready fit result in the same tool turn. Built-in cosmology presets likewise do not make their manifest bibcodes globally citeable; the relevant cosmology or fit tool must return the preset provenance in the current turn.

### Team & Collaboration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/team/members` | List team members |
| POST | `/api/team/invite` | Invite member |
| POST | `/api/team/share/pipeline` | Share pipeline |
| POST | `/api/team/share/dataset` | Share dataset |

### VO Interoperability

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/integration/samp/status` | SAMP hub connection status |
| POST | `/api/integration/samp/send` | Send data to DS9/TOPCAT via SAMP |
| POST | `/api/integration/samp/subscribe` | Subscribe to SAMP messages |

## Rate Limits

| Endpoint Group | Limit |
|----------------|-------|
| Authentication | 3-10/minute |
| Data search | 30/minute |
| ADQL queries | 20/minute |
| Chat (AI) | 15/minute |
| Pipeline runs | 5/minute |
| General API | 100/minute |

Daily quotas apply per subscription tier (solo/lab/institution).

## Error Responses

All errors follow the format:
```json
{
  "detail": "Human-readable error message"
}
```

Common status codes: 400 (bad request), 401 (unauthorized), 403 (forbidden), 404 (not found), 429 (rate limited), 500 (server error).
