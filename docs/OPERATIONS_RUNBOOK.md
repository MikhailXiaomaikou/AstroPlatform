# Standard Astro Production Operations Runbook

This runbook is the operational contract for the Render deployment and the
production-like Docker Compose stack. It covers release migrations, readiness,
backup, recovery, and rollback. It intentionally contains no credentials.

## 1. Production topology

The Render Blueprint provisions:

- `standard-astro-backend`: FastAPI, with a persistent disk mounted at
  `/app/data`;
- `standard-astro-celery-worker`: Celery task execution;
- `standard-astro-celery-beat`: scheduled-task dispatch;
- `standard-astro-redis`: persistent Render Key Value broker, configured with
  `noeviction`;
- `standard-astro-frontend`: static application;
- `standard-astro-db`: paid Render PostgreSQL.
- external S3-compatible storage: shared FITS/research objects for backend and
  worker. The bucket is supplied through secrets and is not created by Render.

The worker, scheduler, Key Value instance, and persistent disk are paid Render
resources. Review the workspace estimate before syncing the Blueprint.
Render disks are single-instance and disable zero-downtime instance swaps; plan
a short backend interruption during deploys until gate-event/cache state moves
to shared external storage and the disk can be removed.

Before syncing an existing Blueprint, seed stable `JWT_SECRET`, `FERNET_KEY`,
independent `EVIDENCE_SIGNING_KEY`, and the matching stable
`EVIDENCE_SIGNING_KEY_ID` values from an external secret manager. Also set
`S3_BUCKET`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` on the
backend, plus `S3_ENDPOINT_URL` for R2/MinIO, and pre-seed
`EVIDENCE_VERIFICATION_KEYS` as `{}` when first introducing the evidence-key
configuration. Replace it with the retained-key map before a rotation. Render
ignores new `sync: false` values during an update; if required values are not
pre-seeded, the deliberate
`STORAGE_BACKEND=s3` startup guard will stop the backend. Worker values reference
the backend variables so both processes address the same bucket. Docker Compose
uses local storage and shares the named FITS volume between backend and worker.
Do not rotate JWT or Fernet merely to adopt the Blueprint: JWT rotation ends
existing sessions, while Fernet rotation without a data migration makes stored
BYOK records unreadable.

## 2. Release and migration procedure

Production schema changes have exactly one writer: Alembic.

1. CI creates an empty PostgreSQL 16 database and runs `alembic upgrade head`,
   `alembic check`, the readiness schema probe, and a backup/restore round trip.
   A second disposable PostgreSQL 16 database reconstructs the complete
   13-table revision-002 `VARCHAR(36)` schema, upgrades valid representative
   rows through the current head, and proves that a deliberately late dirty
   UUID rolls every earlier type conversion back without changing data,
   constraints, tables, or revision. CI also builds the backend and frontend
   images from source-pinned base-image digests.
2. Render waits for required GitHub checks because every deployable service uses
   `autoDeployTrigger: checksPass`.
3. The backend image runs `alembic upgrade head` as its
   `preDeployCommand`. A migration failure prevents the new release from
   starting.
4. Worker and beat independently run `scripts/wait_for_schema_head.py` before
   `exec celery`. They are read-only schema consumers and wait up to 15 minutes
   for the backend's migration; they never race new task code against the old
   schema or become additional migration writers.
5. Render admits the backend only after `/health/ready` confirms database
   connectivity and the image's exact Alembic head within its five-second
   deadline. Deployment acceptance is not complete until `/health/deep` also
   confirms durable storage, Redis, and at least one Celery worker.

Never add `create_all`, `ALTER TABLE`, or `Table.create` to production startup.
Every production schema change needs a reviewed Alembic revision.

### Existing unversioned database adoption

Older development-style databases can have application tables but no
`alembic_version` table. Do not point automatic pre-deploy migration at such a
database: revision `001_initial` will correctly try to create existing tables
and fail.

Use this one-time, fail-closed adoption process:

1. Create a Render PITR recovery database or an isolated logical-backup restore.
2. Confirm the clone has no version record:

   ```bash
   psql "$DATABASE_URL" -c 'select * from alembic_version'
   ```

3. Reconstruct the candidate matching revision in a second empty PostgreSQL 16
   database. Compare the complete schemas before any stamp: tables, columns,
   types, defaults, nullability, primary/foreign/unique constraints, indexes,
   enums, and required extensions must all match. Comparing table names alone
   is not sufficient.
4. Only when that structural comparison is exact, stamp the disposable clone
   to the matching revision, then run `alembic check`, the intended upgrade,
   migration tests, and representative-row checks. Stamping records history;
   it does not repair schema drift.
5. If any structural difference exists, stop and write a reviewed bridge
   migration with a real PostgreSQL 16 rollback drill. Do not stamp either the
   clone or production to hide the difference.
6. Only after the clone proves the exact adoption and upgrade path, take a new
   production backup, obtain the required production approval, stamp production
   once to the proven matching revision, and deploy. Verify `/health/deep`
   immediately.

## 3. Readiness contract

`GET /health` is process liveness. `GET /health/ready` is Render's bounded
traffic-readiness gate: it runs only the database and exact-schema checks in
parallel and fails closed before Render's five-second limit. `GET /health/deep`
is the full post-deploy infrastructure check; it is deliberately not called
every few seconds because its storage round trip writes an object and its
Redis/Celery checks are heavier.

| Component | Ready condition | Failure action |
|---|---|---|
| `db` | `SELECT 1` succeeds | Block deployment |
| `schema` | DB revisions exactly equal all image Alembic heads | Block deployment; migrate, never stamp blindly |
| `storage` | expected mount fsyncs and configured local/S3 storage passes a checksum-verified round trip | Block deployment; inspect disk or bucket credentials/policy |
| `broker` | Redis ping succeeds when `PIPELINE_MODE=celery` | Block deployment |
| `celery_worker` | at least one control-ping reply and every responding node identity carries the backend commit | Block deployment; inspect worker image/command, logs, and broker |
| `celery_beat` | every active per-instance Redis lease was renewed by a successful scheduler tick and carries the backend commit | Block deployment; inspect every beat image, schedule, and broker |
| `ai_backend` | server key or per-request BYOK is available | Informational; BYOK-only remains ready |

Both unauthenticated endpoints deliberately return short labels only. Detailed
exceptions remain in structured server logs. A release is operationally
accepted only when both `/health/ready` and `/health/deep` pass for the same
known reported commit. Run the repository verifier with the exact expected
release identity:

```bash
cd backend
EXPECTED_COMMIT=<full-render-git-sha> \
  ./venv/bin/python scripts/verify_deployment.py https://<target-backend>
```

### Long-job delivery and reconciliation

Celery acknowledges long scientific work only after completion
(`task_acks_late=true`), rejects work back to Redis when a worker process is
lost, and reserves one message per worker child (`worker_prefetch_multiplier=1`).
The Redis visibility timeout is longer than the hard task limit, and stale-job
reconciliation is longer than both. Render and Compose currently use:

| Control | Seconds |
|---|---:|
| soft task limit | 43,200 |
| hard task limit | 45,000 |
| Redis visibility timeout | 46,800 |
| stale job threshold | 50,400 |
| Render worker graceful shutdown | 300 |

The Starter worker uses `--concurrency=1` to avoid two scientific child
processes competing for 512 MB. Raising concurrency requires a measured memory
and queue-latency run on the target instance type.

Changing these values out of order fails worker startup. On worker startup and
every five minutes thereafter, `queued`/`running` rows older than the stale
threshold become durable `failed / stale_job_reconciled` records and are mirrored
to the hot KV. This prevents an orphan from remaining `running` forever; it does
not pretend that a killed computation completed.

Research-job lifecycle writes are not telemetry. They receive three bounded,
exponentially backed-off PostgreSQL attempts. Initial queue persistence must
succeed before dispatch. An exhausted write is logged at critical level and
surfaced as `durable_persistence_failed`; such a result is not scientifically
claimable and must be retried after database health is restored.

Scheduled pipeline dispatch follows the same visibility rule. If Celery rejects
the message after a `PipelineRun` row is created, the scheduler commits that row
as terminal `failed / celery_dispatch_failed` with `completed_at` set and moves
the schedule to its next cadence; it never leaves a run permanently `pending`.

## 4. Backup policy and objectives

Render continuously backs up paid PostgreSQL for PITR. The recovery window is
workspace-dependent. Render persistent disks receive automatic daily snapshots.
See the current Render documentation for
[PostgreSQL recovery](https://render.com/docs/postgresql-backups) and
[disk snapshots](https://render.com/docs/disks).

Operational targets:

| Asset | Primary protection | Target RPO | Target RTO |
|---|---|---:|---:|
| PostgreSQL | Render PITR plus weekly portable export | 1 hour | 2 hours |
| S3 research objects | bucket versioning plus provider replication/lifecycle policy | 24 hours | 4 hours |
| `/app/data` events/cache | Render daily disk snapshot plus weekly portable export | 24 hours | 4 hours |
| JWT/Fernet/evidence-signing secrets | external secret manager, versioned key IDs | manual change only | 1 hour |
| Whole service | DB recovery + disk recovery + redeploy | 24 hours | 4 hours |

RPO/RTO are targets, not claims of successful recovery. Run a recovery exercise
at least quarterly and record actual data loss and elapsed time.

### Portable backup

The backend image includes PostgreSQL client tools. From a trusted shell with
access to both the database and storage mount:

```bash
export DATABASE_URL='postgresql://...'
export STORAGE_DIR=/app/data
export BACKUP_ROOT=/tmp/astro-portable-backups
export FERNET_KEY_ID='fernet-prod-2026-01'  # identifier only, never the key
export EVIDENCE_SIGNING_KEY_ID='evidence-prod-v1'  # identifier only
backend/scripts/ops/backup.sh  # repo-root shell
# scripts/ops/backup.sh        # inside the backend/Render image
```

The script writes one `standard-astro-*.tar.gz` containing:

- `database.dump` from `pg_dump --format=custom`;
- optional local `storage.tar.gz` (it does not export an S3 bucket);
- `manifest.json` with SHA-256 hashes, Alembic revision, commit, Fernet key ID,
  and evidence-signing key ID.

The backup refuses an unknown/non-SHA commit or an absent/`unrecorded` key ID.
Those identifiers are recovery material: they make the required code checkout
and externally escrowed secrets unambiguous without placing any secret value in
the bundle. It also requires the database Alembic revision to equal the current
code heads before `pg_dump`, then reads it again after the dump and discards the
bundle if it changed. This prevents a concurrent migration from producing a
manifest that describes a different schema snapshot; it does not replace the
write freeze required to keep database and object storage mutually consistent.

The bundle contains no database URL, JWT secret, Fernet key, API key, or other
credential. Copy it off the Render disk after creation; a backup stored only on
the protected disk is not an independent backup. `BACKUP_RETENTION_DAYS=N`
optionally removes older local bundles after a successful new bundle.

Enable bucket versioning and a provider-side retention/replication policy for
S3/R2. Periodically perform a provider-native object export or replication test;
the local backup script is not a substitute for object-store protection.

Store the actual Fernet/JWT/evidence-signing values and retired evidence
verification keyring in an external secret manager. Database recovery without
the matching Fernet key leaves encrypted BYOK fields unreadable; recovery
without the evidence keyring makes historical paper evidence unverifiable.

### Evidence-key rotation

Evidence keys are rotated additively, never by replacing the only verifier:

1. Copy the current `EVIDENCE_SIGNING_KEY_ID` and secret into the JSON
   `EVIDENCE_VERIFICATION_KEYS` map in the external secret manager.
2. Generate a new independent `EVIDENCE_SIGNING_KEY` and a new immutable id.
3. Deploy the new current key/id and the expanded keyring to backend, worker,
   and beat together.
4. Verify one historical public paper and create/validate one new signed tool
   record. Roll back the deployment if either fails.
5. Retain old keys for at least as long as any evidence-bearing paper or session
   is retained. Do not remove a key merely because JWT tokens using a similar
   date have expired.

Schema-v1 records have no key id and were signed with the then-current
`JWT_SECRET`. Before rotating JWT, preserve the old JWT secret in
`EVIDENCE_VERIFICATION_KEYS` under a descriptive id such as
`legacy-jwt-2026-07`; the verifier tries retained keys only for those legacy
records. Never place secret values in the backup manifest.

## 5. Restore procedure

Prefer Render PITR for database incidents because it has a smaller RPO and
creates an isolated recovery instance. Validate the recovery database before
changing `DATABASE_URL` for backend, worker, and beat.

For a portable bundle, check out the manifest's exact commit, restore into a
new PostgreSQL database with no non-system objects, and choose a storage path
that does not yet exist:

```bash
export DATABASE_URL='postgresql://.../astro_recovery'
export RESTORE_STORAGE_DIR=/srv/astro-recovery-data
export RESTORE_CONFIRM='restore:standard-astro-YYYYMMDDTHHMMSSZ-COMMIT'
export RESTORE_EXPECTED_COMMIT='<full-40-character-manifest-commit>'
export RESTORE_FERNET_KEY_ID='fernet-prod-2026-01'
export RESTORE_EVIDENCE_SIGNING_KEY_ID='evidence-prod-v1'
backend/scripts/ops/restore.sh /secure/standard-astro-YYYYMMDDTHHMMSSZ-COMMIT.tar.gz
# Inside the backend image, use scripts/ops/restore.sh instead.
```

The restore script rejects unsafe archive paths, verifies every manifest hash,
requires the manifest commit and both key identifiers to match the operator's
explicit expectations, and requires the running checkout/image to be that same
commit. It rejects non-system schemas and namespace objects plus publications,
subscriptions, event triggers, non-default extensions, FDW objects, large
objects, default ACLs, and other database-level user objects; there is no
non-empty override. PostgreSQL is restored in one transaction, then the exact
Alembic revision and `alembic check` must pass. An older bundle therefore has
to be validated with its corresponding code revision before any forward
migration.

Storage is extracted only after database validation, into a temporary sibling
directory, then published with an atomic no-replace rename (`renameat2` on
Linux, `renamex_np` on macOS). An unsupported host fails closed; there is no
overlay mode. A database validation failure can still leave the isolated
recovery database modified; discard that database and start again rather than
reusing it. There is no unchecked-success mode.

After restore:

1. Set `DATABASE_URL` to the recovery database in an isolated backend.
2. Run `alembic current`, `alembic check`, and `/health/deep`.
3. Verify login, one encrypted BYOK record, one chat/session record, one FITS
   download by checksum, one historical public paper signed by a retired key,
   and one newly generated evidence record signed by the current key id.
4. Switch all production services together; do not leave worker/beat connected
   to the old database.
5. Preserve the old resources until the recovery has passed a full smoke test.

## 6. Rollback and incident rules

- Prefer forward-fix migrations. An application rollback is safe only when the
  new schema remains backward compatible with the old image.
- Freeze writes and snapshot the target before a rollback. If the target has
  accepted any unique writes or object versions, keep both target and rollback
  destination read-only until those changes are enumerated, idempotently
  reconciled, and checksum/row validated. Switch API, frontend, worker, beat,
  database, and storage together, then require database and release-owner
  approval before restoring writes. A normal 30-minute cutover budget does not
  authorize dropping data during a longer incident recovery.
- Do not run Alembic downgrade against production before testing it on a PITR
  clone. For destructive mistakes, restore to a new PITR database instead.
- If `/health/deep` reports `revision_mismatch`, stop deploys and compare
  `alembic current` with `alembic heads`.
- If it reports a missing or revision-mismatched worker/Beat lease, inspect the
  worker identities, every Beat scheduler tick and Redis; do not make the
  checks optional to force readiness green.
- If it reports a missing persistent mount, do not redirect writes to the
  ephemeral container filesystem.
- Record incident start time, affected commit/schema revision, recovery point,
  actual RPO/RTO, validation evidence, and follow-up owner.

## 7. Docker Compose verification

```bash
export JWT_SECRET="$(openssl rand -hex 32)"
export FERNET_KEY="$(openssl rand -hex 32)"
export EVIDENCE_SIGNING_KEY="$(openssl rand -hex 32)"
export EVIDENCE_SIGNING_KEY_ID="evidence-compose-v1"
docker compose up --build -d
docker compose ps
curl --fail http://localhost:8000/health/deep
```

The `openssl` commands above are for the first start of a disposable verification
stack. If the named volumes will be reused, generate the three secrets once,
assign the evidence key ID once, store them outside the repository, and reuse
the same values on every start.
Changing `FERNET_KEY` makes stored BYOK records unreadable; changing the evidence
key without the additive rotation procedure above makes historical evidence
unverifiable. Never commit these values to the repository.

The one-shot `migrate` service must exit `0`; backend and worker both wait for
it. Redis AOF, PostgreSQL, runtime data, and FITS data use named volumes. The
frontend waits for backend readiness instead of mere process start. Its Docker
build explicitly injects `VITE_API_URL=http://localhost:8000`; inspect the built
bundle if changing frontend build arguments so a Compose smoke test can never
silently send test credentials or jobs to the hosted Render API. Compose Redis
sets `maxmemory=192mb` inside its 256 MB container and `noeviction`, leaving
headroom while making queue saturation fail producers instead of invoking the
kernel OOM killer first. Because Compose exposes uvicorn directly rather than
through Render's trusted proxy, it also pins `TRUSTED_PROXY_MODE=none` so a
client cannot choose its own rate-limit/audit identity with forwarded headers.
