# Standard Astro

[English](./README.md) | [简体中文](./README.zh-CN.md)

> **An auditable AI workbench for observational cosmology.**

**Research alpha · Cosmology only**

Standard Astro helps researchers plan, run, inspect, and document
observational-cosmology work. The model proposes structured actions; the
backend executes registered datasets, likelihoods, fits, and evidence checks.

> This is not a general “reproduce any paper” system. Unsupported requests must
> become explicit capability gaps, never guessed results.

See [Honesty Evidence](./docs/HONESTY_EVIDENCE.md) for known failures,
limitations, and anti-fabrication evidence.

## How it works

```text
Research question
        ↓
Model proposes tool calls
        ↓
Backend runs data, likelihoods, and validation
        ↓
Result + provenance + citations
```

Strong numerical claims must trace to a **current-turn** tool result, dataset,
and citation. Missing evidence produces a gap report—not model-memory filler.

## Quick start

Prerequisites: **Python 3.11** and **Node.js 20+**.

```bash
# Terminal 1: backend
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install --require-hashes -r requirements.lock
uvicorn app.main:app --reload --port 8000
```

In a second terminal, return to the repository root and run:

```bash
# Terminal 2: frontend
cd frontend
npm ci
npm run dev
```

Open:

- App: [http://localhost:5173/chat](http://localhost:5173/chat)
- Health: [http://localhost:8000/health](http://localhost:8000/health)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

Local development defaults to SQLite and needs no `.env`. Add an Anthropic,
OpenAI, or DeepSeek key under **Account**, or enable an authenticated local
Claude Code / Codex CLI bridge. See the
[Product Quick Start](./docs/QUICKSTART.md) and
[Deployment](./DEPLOYMENT.md) for secure configuration.

Good first questions:

- `List the executable cosmology datasets and their execution modes.`
- `Build a DESI DR2 BAO + BBN likelihood and explain every data source.`
- `Compute the Planck 2018 theory CMB TT power spectrum.`

Always inspect the result's data version, provenance, citations, and
acknowledgement—not only the prose answer.

## Capabilities

- Research chat, archive queries, ADQL, literature search, and table extraction
- Versioned BAO, SN, CMB, H(z), and fσ8 workflows
- Model fitting with visible convergence and evidence status
- Paper-to-tool capability mining
- Reproducible drafts, BibTeX, notebooks, figures, and evidence bundles
- Optional local `/bot` control surface for a personal research pipeline

Six provenance-v2 archive connectors are live: VizieR, Gaia DR3, SIMBAD, NED,
2MASS, and ALMA observation metadata. Other connectors fail closed until
versioned provenance is available.

## Scientific boundaries

- Configuration, abstracts, old chat context, and user assumptions are not
  evidence for posterior, fit, significance, or tension numbers.
- Literature supports context and citations; measurements require extracted
  rows or publication-ready tool output.
- Synthetic Python output cannot be presented as observation.
- Low-ESS, unconverged, exploratory, or configuration-only results remain
  visible but cannot support strong claims.
- Overlapping datasets must respect `do_not_combine_with`.
- No Wilks p-value, Gaussian-equivalent significance, Bayes-factor preference,
  ΛCDM exclusion, or dynamic-dark-energy discovery claim is produced for the
  current DESI `w0wa` task.

### Current DESI `w0wa` milestone

The current target is a preregistered reproduction of the four DESI 2024 VI
Table 3 `DESI+CMB+PantheonPlus` intervals using:

```text
preflight → generate → run → analyze → grade
```

The status is **`WITHHELD`**: `A_ready_count=0` and `strict_A_count=0`.
Formal chains and all six model-adequacy requirements have not completed.
Target values were exposed in the implementation request, and the frozen
protocol-adjudication registry is empty, so the local grader cannot waive the
failed analyst-blinding condition. Completing software tests or chains alone
does not grant A-ready or A status.

See the [A-readiness protocol](./docs/DESI_W0WA_A_READINESS_PROTOCOL.md) and
[historical preliminary proxy record](./backend/scripts/cobaya/README_full_cmb_reproduction.md).

## Project map

| Path | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI backend entrypoint |
| `frontend/src/main.tsx` | React frontend entrypoint |
| `frontend/src/App.tsx` | Routes and application shell |
| `frontend/src/pages/Chat/ChatPage.tsx` | Primary research surface |
| `backend/app/services/ai_tools/` | Registered tools and dispatch |
| `render.yaml` | Reference production topology |

Stack: React 19, strict TypeScript, Vite, FastAPI, Pydantic v2, PostgreSQL,
Redis/Celery, astropy, emcee, dynesty, Cobaya, CAMB, and ArviZ.

## Development

```bash
# Backend
cd backend
./venv/bin/ruff check app tests
./venv/bin/pytest tests -q

# Frontend
cd ../frontend
npm run lint
npm run test
npm run build
```

Scientific-data, likelihood, and anti-fabrication changes also require the
relevant benchmarks, registry audits, and blind tests. See
[Contributing](./CONTRIBUTING.md) and the
[Agent Handbook](./CLAUDE.md).

## Documentation

- [Product Quick Start](./docs/QUICKSTART.md)
- [Architecture](./ARCHITECTURE.md)
- [Source Mapping](./docs/SOURCE_MAPPING.md)
- [Scientific-rigor Ledger](./docs/SCIENTIFIC_RIGOR_REMEDIATION.md)
- [API Reference](./docs/API_REFERENCE.md)
- [Operations and Recovery](./docs/OPERATIONS_RUNBOOK.md)
- [Security](./SECURITY.md) · [Privacy](./PRIVACY.md) ·
  [References](./docs/REFERENCES.md)

## Data and license

Research outputs must retain release-specific citations and required
acknowledgements. Standard Astro is not affiliated with the archives,
institutions, or model providers it integrates.

Licensing is not yet finalized, and the repository currently publishes no
`LICENSE` file. Do not assume redistribution or third-party
contribution-merge rights.
