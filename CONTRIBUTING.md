# Contributing to Standard Astro

Standard Astro is a cosmology-only research alpha, so proposed changes must
preserve the distinction between model suggestions and backend-verified
evidence. Source code is licensed under Apache-2.0. Contributions use the
Developer Certificate of Origin described in `docs/DCO.md`.

## Before starting

- Read `CLAUDE.md`, `README.md`, and the relevant architecture/science document.
- Search existing issues and pull requests before beginning a large change.
- For security vulnerabilities, follow `SECURITY.md` instead of opening a
  detailed public issue.
- Sign every commit with `git commit -s`. Only submit material you have the
  right to contribute.
- Treat scientific data separately from source code. Follow
  `docs/DATA_LICENSES.md` and the upstream provider's terms.

## Local setup

Use Python 3.11 and Node 20 or newer:

```bash
cd backend
python3.11 -m venv venv
source venv/bin/activate
pip install --require-hashes -r requirements.lock

cd ../frontend
npm ci
```

Run the backend and frontend as described in `README.md`. Never commit `.env`,
provider credentials, database URLs, unpublished data, or generated local
artifacts.

## Scientific contract

Changes must not turn unavailable or config-only data into executable evidence,
weaken provenance freshness, accept fabricated tool transcripts, or report a
numerical/citation claim that is unsupported by a current backend result.

When adding a dataset, likelihood, connector, or scientific tool, include:

- source and release/version metadata;
- exact claim scope and known limitations;
- deterministic tests, including a fail-closed negative case;
- licensing/redistribution information for any data product;
- provenance and acknowledgement behavior where applicable.

Do not weaken a scientific gate merely to make a test or demonstration pass.
Document a real capability gap instead.

## Code and database changes

- Keep one logical change per pull request and avoid unrelated formatting.
- Add or update tests for every behavior change.
- Production schema changes require an Alembic revision. Application startup
  must not create or alter production tables directly.
- Preserve the cosmology-only runtime allowlist unless a separately reviewed
  repository-scope decision changes it.
- Treat client IPs, user prompts, API keys, audit trails, and unpublished
  research records as sensitive data.

## Validation

Run focused tests while developing, then the relevant broad checks before
requesting review. The normal minimum is:

```bash
cd backend
./venv/bin/ruff check app/ --select E,W,F --ignore E501
./venv/bin/pytest tests -q

cd ../frontend
npm run lint
npm run test
npm run build
```

Also run migration, Docker/Compose, registry, or scientific validation commands
required by `CLAUDE.md` for the files you changed. If an environment-dependent
check cannot be run, state exactly what remains unverified and why.

## Pull-request notes

Explain the user/scientific problem, the chosen boundary, tests run, migration
or deployment impact, data/licensing impact, and any remaining risk. Screenshots
are useful for visible UI changes, but they do not replace tests.
