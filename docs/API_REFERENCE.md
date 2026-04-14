# Standard Astro API Reference

Standard Astro uses FastAPI, which auto-generates interactive API documentation.

## Interactive Documentation

| Interface | URL | Best For |
|-----------|-----|----------|
| **Swagger UI** | `{backend-url}/docs` | Testing endpoints interactively |
| **ReDoc** | `{backend-url}/redoc` | Reading documentation |
| **OpenAPI JSON** | `{backend-url}/openapi.json` | Machine-readable schema |

## Authentication

All `/api/*` endpoints require a JWT token in the `Authorization` header:

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

### AI Assistant

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/message` | Send message (SSE streaming response) |
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
