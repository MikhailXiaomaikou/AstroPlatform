# Production Cutover Checklist

This is the P0 release gate for moving the legacy Render deployment to the
target topology in `render.yaml`. It is deliberately separate from deployment
automation: completing code and CI does not authorize paid-resource creation,
secret rotation, data migration, downtime, or traffic switching.

## 1. Record the cutover decision

Choose and approve exactly one path:

- **In-place adoption** — rename or adapt the Blueprint to the existing service
  names. Lower duplicate-resource cost, but a maintenance window and a tested
  rollback are required.
- **Parallel stack** — provision the `standard-astro-*` target alongside the
  legacy stack, validate it, then switch the public frontend/API URLs. Safer
  traffic rollback, but incurs duplicate paid resources and requires a final
  consistent data synchronization.

Record the approved budget, target region, database/storage capacity and plans,
maintenance window, operator, target URLs, expected commit, rollback owner, and
maximum tolerated data loss. Provider defaults are not a region or capacity
decision. If any is missing, stop.

## 2. Freeze and inventory the legacy deployment

- Export current service names, plans, regions, environment-variable names,
  custom domains, deploy commit, database connection target, and file paths.
- Confirm `/health`, `/health/ready`, and `/health/deep` behavior. A legacy
  `404`/`503` is evidence that the target contract is not yet adopted, not a
  reason to weaken the new checks.
- Inventory the live database schema and whether `alembic_version` exists.
- Inventory durable files, public/shared records, encrypted BYOK rows, evidence
  key IDs, scheduled jobs, and in-flight research jobs.
- Pause schema changes and document how writes will be stopped for the final
  database/files consistency point.

## 3. Escrow recovery material

- Put stable `JWT_SECRET`, `FERNET_KEY`, independent `EVIDENCE_SIGNING_KEY`,
  and its stable `EVIDENCE_SIGNING_KEY_ID` in an external secret manager;
  retain the evidence verification keyring and any legacy evidence JWT key.
- Seed every `sync: false` Blueprint variable before sync. Never replace an
  existing Fernet key merely because the Blueprint asks for one.
- Create a checksummed portable backup and provider-native database recovery
  point. Copy the portable bundle off the protected service disk.
- Prove `scripts/ops/restore.sh` against a new isolated database and storage
  path that does not yet exist. Supply and record the manifest commit, Fernet
  key ID, evidence-signing key ID, exact Alembic revision, elapsed time, and
  validation evidence. A failed isolated restore is discarded, not reused.

## 4. Prove schema adoption on a clone

- Restore or clone the live database; never experiment on production.
- If it is unversioned, compare the complete schema and constraints before any
  stamp. `alembic stamp` records history and does not repair drift.
- Run `alembic check`, the migration suite, and representative row checks.
- Write a bridge migration for any drift. Do not use a blind production stamp.

## 5. Validate the target stack before traffic

- Confirm PostgreSQL 16, persistent Redis with `noeviction`, S3-compatible
  shared object storage, the `/app/data` mount, backend, worker, beat, and
  frontend are all connected to the intended target resources.
- Confirm `SANDBOX_BACKEND=disabled`, `RATE_LIMIT_ENABLED=true`,
  `SHARED_DEEPSEEK_API_KEY_ENABLED=false`, `TRUSTED_PROXY_MODE=none`,
  cosmology-only focus, and transient alert ingestion disabled.
- Run one authenticated BYOK chat. Confirm an anonymous/no-BYOK request cannot
  use a platform-funded key. If a later separately approved release enables
  shared inference, first complete the budget, Redis, and forged-XFF gates in
  `DEPLOYMENT.md` and add a paid-path smoke test for that release.
- Run a real worker-path smoke job that writes a research-job row, uses shared
  object storage if applicable, emits progress, and reaches a terminal state.
- Confirm deep health reports the expected commit for the backend, every
  worker, and every active tick-coupled Beat lease. Confirm the Beat schedule
  contains only the approved cosmology-safe jobs.
- Verify login, encrypted-key read, session history, FITS checksum download,
  one historical evidence record, and one new evidence record.

## 6. Take the consistency point and switch

- Enter the approved maintenance/write-freeze window.
- Wait for or explicitly terminate in-flight jobs according to the recorded
  policy; do not silently orphan them.
- Take the final database backup and file/object synchronization from the same
  consistency window.
- Apply migrations with the backend as the only schema writer. Start worker and
  beat only after their exact-head gates pass. Their release identity must
  match the backend; a pong from an older worker is not acceptable.
- Run:

  ```bash
  cd backend
  EXPECTED_COMMIT=<full-render-git-sha> \
    ./venv/bin/python scripts/verify_deployment.py https://<target-backend>
  ```

- Switch all API/frontend/worker/beat database and storage references together.
  Do not leave background services on the old database.

## 7. Observe, roll back, and close

- Monitor authentication errors, quota `429`/`503`, database/Redis latency,
  worker queue depth, failed/stale jobs, object-store checksum failures,
  evidence validation, model cost, and error rate through the maintenance
  window and the agreed observation period.
- Roll back traffic if either readiness endpoint fails, commit identities
  differ, schema/storage checks fail, or scientific evidence validation
  regresses. Prefer switching to preserved old resources or a validated PITR
  clone; do not run an untested production downgrade.
- Keep old resources read-only until the observation period and restore sample
  pass. Then obtain explicit approval before deleting paid resources.
- Record actual downtime, RPO/RTO, backup ID, commit, schema revision, smoke
  results, incidents, and follow-up owners.
