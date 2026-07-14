# Standard Astro

> An auditable AI workbench for observational cosmology.

**Research alpha · Cosmology only**

Standard Astro turns research questions into registered data, likelihood,
fitting, and validation workflows. Numerical claims must trace to
current-turn tool results, versioned data, provenance, and citations.
Unsupported requests fail with an explicit capability gap instead of a guessed
answer.

See [Honesty Evidence](./docs/HONESTY_EVIDENCE.md) for known limitations and
anti-fabrication tests.

## Quick start

Requires **Python 3.11** and **Node.js 20+**.

```bash
# Terminal 1: backend
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install --require-hashes -r requirements.lock
uvicorn app.main:app --reload --port 8000
```

From the repository root in a second terminal:

```bash
# Terminal 2: frontend
cd frontend
npm ci
npm run dev
```

Open [the app](http://localhost:5173/chat),
[health check](http://localhost:8000/health), or
[API docs](http://localhost:8000/docs).

Local development uses SQLite and needs no `.env`. Add an Anthropic, OpenAI,
or DeepSeek key under **Account**, or use an authenticated local Claude Code or
Codex CLI bridge. Secure setup details are in the
[Quick Start](./docs/QUICKSTART.md) and [Deployment Guide](./DEPLOYMENT.md).

## Capabilities

- Versioned BAO, SN, CMB, H(z), and fσ8 workflows
- Archive, ADQL, literature, and table-extraction tools
- Model fitting with visible convergence and evidence status
- Reproducible drafts, citations, notebooks, figures, and evidence bundles
- Fail-closed provenance for supported astronomical archives

## Scientific contract

- Configuration, abstracts, prior chat, and user assumptions are not numerical
  evidence.
- Synthetic output cannot be presented as observation.
- Unconverged, low-ESS, exploratory, or incomplete results cannot support
  strong claims.
- Dataset overlap and `do_not_combine_with` restrictions are enforced.
- The current DESI `w0wa` work does not produce significance, Bayes-factor,
  ΛCDM-exclusion, or discovery claims.

### Current DESI `w0wa` status

The preregistered target is the DESI 2024 VI Table 3
`DESI+CMB+PantheonPlus` parameter intervals. The status is **`WITHHELD`**
(`A_ready_count=0`, `strict_A_count=0`). The formal chains and six adequacy
runs are incomplete. Because the published targets were disclosed before
execution, this is not analyst-blind; the local grader cannot waive that
condition, and completed chains alone cannot grant A-ready or A status.

See the [A-readiness protocol](./docs/DESI_W0WA_A_READINESS_PROTOCOL.md) and
[historical proxy record](./backend/scripts/cobaya/README_full_cmb_reproduction.md).

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

Main entrypoints are `backend/app/main.py`, `frontend/src/main.tsx`, and
`render.yaml`. Start with [Architecture](./ARCHITECTURE.md),
[Source Mapping](./docs/SOURCE_MAPPING.md),
[Scientific Rigor](./docs/SCIENTIFIC_RIGOR_REMEDIATION.md),
[Operations](./docs/OPERATIONS_RUNBOOK.md), and [Security](./SECURITY.md).

## Data and license

Outputs must retain release-specific citations and acknowledgements. Standard
Astro is not affiliated with the archives, institutions, or model providers it
integrates. No `LICENSE` file is currently published; do not assume
redistribution or contribution-merge rights.
