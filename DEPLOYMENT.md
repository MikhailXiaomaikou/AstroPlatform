# Standard Astro Deployment Guide

`render.yaml` defines the **target** production topology. As observed on
2026-07-13, the current Render services (`astro-backend-h4x1` and
`astro-frontend-tyfr`) predate this Blueprint and do not yet have its database
migrations, persistent storage, Redis, or Celery workers. Do not sync the
Blueprint directly: Render matches resources by exact name, so doing so would
create a second stack instead of safely adopting the legacy services.

## Production Services

The target production Blueprint defines **5 services + 1 database**
(`render.yaml` is the source of truth for that target).

| Service | Type | Purpose |
|---|---|---|
| `standard-astro-backend` | Web service | FastAPI API server |
| `standard-astro-frontend` | Static site | Vite SPA with rewrite-to-index routing |
| `standard-astro-db` | PostgreSQL | Primary database |
| `standard-astro-redis` | Render Key Value | Persistent Celery broker and shared cache |
| `standard-astro-celery-worker` | Background worker | Heavy pipeline/task execution |
| `standard-astro-celery-beat` | Background worker | Scheduled-task dispatch |

Backend and worker share an S3-compatible object store for uploaded FITS and
research artifacts. The backend also has a persistent `/app/data` disk for
gate-event logs, local caches, and export staging. Before syncing an existing
Blueprint, set `S3_BUCKET`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` on the
backend, plus `S3_ENDPOINT_URL` for R2/MinIO; missing required values fail
startup closed. On that existing backend also seed `JWT_SECRET`, `FERNET_KEY`,
the independent `EVIDENCE_SIGNING_KEY`, and its stable
`EVIDENCE_SIGNING_KEY_ID` from an external secret manager, and initialize
`EVIDENCE_VERIFICATION_KEYS` to `{}` before the first sync. These values are
deliberately `sync: false`: automatically generated values cannot be reliably
escrowed and recovered. Render ignores newly added `sync: false` variables
during Blueprint updates. See
[`docs/OPERATIONS_RUNBOOK.md`](./docs/OPERATIONS_RUNBOOK.md) for this boundary,
backup policy, and recovery steps.

Live URLs used by the current docs:

- Backend: `https://astro-backend-h4x1.onrender.com`
- Frontend: `https://astro-frontend-tyfr.onrender.com`

Before adoption, inventory and back up the existing database and files, test
Alembic adoption against a clone, populate all `sync: false` variables, then
choose either exact-name in-place adoption or a verified new-stack cutover.
The choice affects cost, downtime, URLs, rollback, and data synchronization and
therefore requires explicit operator approval. Follow
[`docs/PRODUCTION_CUTOVER_CHECKLIST.md`](./docs/PRODUCTION_CUTOVER_CHECKLIST.md);
do not treat a green CI run as authorization to provision paid resources.

Target-topology pushes to `main` deploy only after linked CI checks pass. Before
the backend starts, Render runs `alembic upgrade head`; migration failure blocks
the deploy.
Render deploys workers independently, so worker and beat run the read-only
`scripts/wait_for_schema_head.py` gate before Celery starts. They poll until the
database exactly matches the Alembic head shipped in their image and exit after
15 minutes instead of running new task code against an old schema. Worker node
names carry `RENDER_GIT_COMMIT`. Each Beat instance renews its own short-lived
Redis release lease only after a successful scheduler tick; deep health
requires every active lease to match the backend commit. It therefore rejects
an old/mixed or stalled background release even when its schema revision is
unchanged.

## Backend Environment

Set these on `standard-astro-backend`:

```bash
ENV=production
DATABASE_URL=postgresql://...          # Render internal DB URL; backend converts to asyncpg
JWT_SECRET=<random-hex-32>
FERNET_KEY=<random-secret-stored-outside-the-database>
DELETION_TOMBSTONE_KEY=<stable-hmac-secret-stored-outside-the-database>
DELETION_TOMBSTONE_KEY_ID=deletion-prod-v1
DELETION_TOMBSTONE_VERIFICATION_KEYS={}
EVIDENCE_SIGNING_KEY=<independent-random-secret-at-least-32-bytes>
EVIDENCE_SIGNING_KEY_ID=evidence-prod-v1
# JSON map of retired key ids to secrets; normally empty until a rotation:
EVIDENCE_VERIFICATION_KEYS={}
CORS_ORIGINS=https://<target-frontend>
RATE_LIMIT_ENABLED=true
SHARED_DEEPSEEK_API_KEY_ENABLED=false # public chat is BYOK-only
SIGNUP_MODE=invite_only               # hosted alpha; local dev may use public
CLAIM_AUDIT_ENABLED=false             # dark until P0/P1 release gates pass
CLAIM_AUDIT_EXECUTION_MODE=celery
CLAIM_AUDIT_REGISTERED_TIMEOUT_SECONDS=1800
CLAIM_AUDIT_WORKER_LEASE_SECONDS=120  # redelivery takeover window
CLAIM_AUDIT_HEARTBEAT_SECONDS=30      # must stay below half the lease
CLAIM_AUDIT_MAX_ACTIVE_PER_USER=2    # durable per-account queued/running cap
ARTIFACT_CLEANUP_GRACE_SECONDS=86400 # commit-ACK/orphan safety window
PRODUCT_ANALYTICS_RETENTION_DAYS=30
PRIVACY_OPERATOR_NAME=<real-operator-name>
PRIVACY_CONTACT=<monitored-privacy-contact>
PRIVACY_JURISDICTION=<actual-hosting-and-operator-jurisdiction>
TRUSTED_PROXY_MODE=none               # verify the target proxy before trusting headers
REDIS_URL=redis://...                   # Blueprint injects Render Key Value URL
PIPELINE_MODE=celery
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://...             # empty only for AWS's default endpoint
S3_BUCKET=...
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
STORAGE_REQUIRE_INTEGRITY=true          # fail closed if SHA-256 metadata is missing
PORT=8000
```

The S3/R2 credential is also part of the account-deletion boundary. In
addition to ordinary read/write access, it must be able to inspect bucket
versioning, enumerate exact-key versions, and delete both versions and delete
markers. For AWS IAM this means bucket-level `s3:GetBucketVersioning` and
`s3:ListBucketVersions`, plus object-level `s3:GetObject`, `s3:PutObject`,
`s3:DeleteObject`, and `s3:DeleteObjectVersion` on the configured bucket/prefix
(use the equivalent permissions for R2/MinIO). Object Lock, MFA Delete, or a
provider policy that prevents version deletion makes the deletion gate fail
closed. Prove these permissions with a disposable versioned object before
traffic; a normal current-version delete is not sufficient.

These are **target settings**, not evidence that the current hosted services
already run this release. Keep `CLAIM_AUDIT_ENABLED=false` through migration,
restore testing, the P0 observation window, and the required Daily stability
gate. Enabling it is a later release decision, not a routine configuration
sync.

`SIGNUP_MODE=invite_only` applies to the hosted research alpha. The
development example remains `public` so a new local checkout is usable without
an invitation service. `SHARED_DEEPSEEK_API_KEY_ENABLED=false` is independent:
an invitation permits account creation but does not permit use of an
operator-funded model key.

Product analytics are opt-in per account. The event collector accepts only
scrubbed product metadata and Celery Beat schedules an hourly purge using
`PRODUCT_ANALYTICS_RETENTION_DAYS=30`. Monitor both Beat and the purge task;
setting an environment variable does not prove that expired rows were deleted.
Claim text, prompts, paper titles, URL/DOI identifiers, tool arguments, raw
errors, and scientific values are forbidden from analytics. See
[`PRIVACY.md`](./PRIVACY.md) for the bilingual user-facing framework.

Tool, pipeline, and large research-job uploads first create a durable
`artifact_cleanup_queue` discovery row. The owner/reference ledger and removal
of that row commit together; a five-minute Beat task only removes an unclaimed
object after `ARTIFACT_CLEANUP_GRACE_SECONDS` and rechecks every trusted
reference first. Keep the default 24-hour window unless the longest measured
upload plus database-recovery window proves a different value safe. Monitor
queue age, retries, and `last_error_class`; do not manually clear rows merely
to make the queue look healthy.

Account deletion writes a signed external tombstone at a stable, key-independent
object path before disabling the account and dispatching asynchronous erasure.
`DELETION_TOMBSTONE_KEY` and its id must be the same on every process and must
survive a database restore. Before rotation, move the retired id/secret into
`DELETION_TOMBSTONE_VERIFICATION_KEYS`; never remove a retired key while a
legacy v1 tombstone or restorable backup may still depend on it. Production
startup fails when the current key or key id is absent.
The returned 30-day `backup_expiry` is a retention target, not a cloud backup
control. Configure and test database snapshot and object-version expiry at the
provider, and retain each external tombstone until no older backup can restore
that account.

The three `PRIVACY_*` values must describe the real instance. Every production
process requires them at startup, regardless of the Claim Audit feature flag,
and `/privacy` publishes them to users. That validation is not legal review. Publish actual subprocessors, infrastructure
log retention, backup retention, and user-request procedures separately.

`EVIDENCE_SIGNING_KEY` is deliberately independent from `JWT_SECRET` and is
required in production. New server tool-evidence records carry
`EVIDENCE_SIGNING_KEY_ID`. Before rotating the current evidence key, retain the
old id/secret in `EVIDENCE_VERIFICATION_KEYS`, for example
`{"evidence-prod-v1":"<old-secret>"}`, then deploy the new key and id to all
services together. Pre-key-id evidence was signed with the JWT secret; before a
JWT rotation, retain that old JWT value in the same map under a descriptive id
such as `legacy-jwt-2026-07`. Unknown key ids and keyless new-schema records fail
closed.

Long-job worker defaults (all values are seconds):

```bash
CELERY_TASK_SOFT_TIME_LIMIT_SECONDS=43200  # 12 h graceful failure window
CELERY_TASK_TIME_LIMIT_SECONDS=45000       # 12.5 h hard process limit
CELERY_VISIBILITY_TIMEOUT_SECONDS=46800    # exceeds hard limit
RESEARCH_JOB_STALE_SECONDS=50400           # exceeds visibility timeout
JOB_PERSIST_MAX_ATTEMPTS=3
JOB_PERSIST_RETRY_BASE_SECONDS=0.2
```

Startup rejects non-positive values or unsafe ordering. Celery uses late ACK,
`reject_on_worker_lost`, and prefetch `1`; a worker crash therefore returns an
unfinished message to Redis instead of acknowledging it early. The worker
reconciles stale `queued`/`running` database rows at startup and Celery Beat
repeats the reconciliation every five minutes. A lifecycle write is retried
with bounded backoff; if it still cannot reach PostgreSQL the hot job becomes a
visible `durable_persistence_failed` terminal state and no queued job is
dispatched before its initial database row is durable.

Optional production variables:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ADS_API_KEY=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
ADMIN_SECRET=<random-hex-32>
PROVENANCE_VALIDATOR_HARDBLOCK=true          # default; set false only for an emergency warn-only downgrade
ASTRO_RESEARCH_FOCUS=cosmology          # cosmology | all  (any other value fails closed to cosmology)
```

`PROVENANCE_VALIDATOR_HARDBLOCK` defaults to hard-block mode. Leave it unset or set it to `true` so provenance-v2 citation violations block replies. Set it to `false` only as a temporary emergency downgrade to warning-only behavior.

The reference production Blueprint makes **public chat BYOK-only**:
`SHARED_DEEPSEEK_API_KEY_ENABLED=false`, and it does not declare a platform
DeepSeek secret. Do not add a paid platform key merely to make an anonymous
smoke test pass. Enabling shared inference is a separate operating decision
that requires an approved budget, a global cost ceiling, per-user and per-IP
abuse controls, a real-Redis failure/concurrency test, and verified client-IP
behavior on the target proxy. The existing 50-per-UTC-day anonymous/starter
counter is a defense in depth, not a global budget boundary. When enabled in
production it requires Redis and returns `503` instead of using a per-process
memory fallback. BYOK and a configured local backend are exempt, and an invalid
BYOK is never allowed to fall back to a platform-funded provider.
The separately declared Anthropic secret is available only to operator-owned
flows that do not pass an explicit per-user provider-key map; it does not turn
public chat into a shared-key path and still needs its own budget approval.

Before upgrading a legacy local-volume deployment to integrity-required reads,
inventory then backfill its existing objects from `backend/`:

```bash
venv/bin/python scripts/backfill_storage_hashes.py
venv/bin/python scripts/backfill_storage_hashes.py --apply
```

Review the dry-run list before applying. New writes always create and verify
their digest automatically; S3 production objects carry the digest as object
metadata and are checked on every read.

`ASTRO_RESEARCH_FOCUS` selects which active research module the process serves. This repository is cosmology-only, so `cosmology` (the `render.yaml` default) is the only active focus. See [Research module focus](#research-module-focus) below.

`TRUSTED_PROXY_MODE` controls which client IP the backend believes for per-IP
rate limiting and comment audit logs (`backend/app/rate_limit.py`). Forwarded
headers are attacker-controlled unless a trusted reverse proxy overwrites them.
The Blueprint and every environment default use `none`: trust only the socket
peer and ignore forwarded headers. `render` reads the first
`X-Forwarded-For` item, matching Render's published staff description, but the
platform's formal web-service documentation does not currently guarantee an
ordering contract. Before selecting it, deploy a diagnostic that sends forged
left and right XFF values through the real target service and prove which item
Render replaces; keep platform-funded inference disabled until that check
passes. See [Render's XFF behavior report](https://feedback.render.com/features/p/send-the-correct-xforwardedfor).
Use `1`..`N` only for a separately documented append-only proxy chain, or
`cloudflare` only when Cloudflare actually fronts the service
(`CF-Connecting-IP` is honored in that mode alone). Unrecognized values fail
closed to `none`.

Production CORS trusts only explicit origins. Keep `CORS_ORIGINS` to the exact
target frontend origin. The legacy downloaded `file://` admin page uses the
opaque `null` origin and is not enabled in production unless an operator
explicitly appends `,null`; prefer the same-origin `/admin` page.

## Frontend Environment

Set these on `standard-astro-frontend` (the Blueprint derives the API URL from
the backend's `RENDER_EXTERNAL_URL` automatically):

```bash
VITE_API_URL=https://<target-backend>.onrender.com
VITE_GOOGLE_CLIENT_ID=...
```

Render's static build receives the hosted `VITE_API_URL` above. Production
builds fail when it is absent; there is no hosted-backend fallback. The frontend
Dockerfile defaults to `http://localhost:8000`, and Docker Compose repeats that
value as an explicit build argument. Do not remove the Compose override: without
it a local production-style browser test can silently exercise the hosted API.

## Verification

After deploy:

```bash
cd backend
EXPECTED_COMMIT=<full-render-git-sha> \
  ./venv/bin/python scripts/verify_deployment.py https://<target-backend>
curl https://astro-backend-h4x1.onrender.com/metrics
```

`/health` is liveness only. Render's bounded traffic gate is `/health/ready`.
The post-deploy acceptance check `/health/deep` fails unless the database
is at the image's exact Alembic head, `/app/data` is a real fsync-writable mount,
the configured object store passes a checksum-verified round trip, Redis answers,
at least one Celery worker replies, every replying worker identifies the same
commit as the backend, and every active tick-coupled Beat lease identifies that
commit. It must not be weakened to work around an infrastructure failure.

Manual smoke checks:

1. Open the frontend and confirm the backend wake-up banner clears.
2. Search an active source such as SIMBAD or Gaia DR3.
3. Try a gated source such as Chandra or SDSS and confirm the UI shows Maintenance / `UNAVAILABLE`, not a generic error.
4. In AI Assistant, run a tool-backed query and confirm Data Sources and Copy Acknowledgement appear when provenance is present.

## Database migrations

Production schema changes are Alembic-only. Do not add runtime `create_all`,
`ALTER TABLE`, or `Table.create` calls to application startup. The Render
`preDeployCommand` and Docker Compose `migrate` service both run:

```bash
cd backend
alembic upgrade head
alembic check
```

An older database with application tables but no `alembic_version` table needs
the one-time clone-and-validate adoption procedure in
[`docs/OPERATIONS_RUNBOOK.md`](./docs/OPERATIONS_RUNBOOK.md). Never stamp an
unverified production schema merely to make deployment green.

The Render worker and beat are not additional schema writers. Their command is
`python scripts/wait_for_schema_head.py && exec celery ...`; it is a read-only
release barrier necessitated by Render's independent service deploys. Keep this
gate on both services whenever the Blueprint changes.

## Backup and recovery

Paid Render PostgreSQL provides PITR, the backend disk receives daily snapshots,
and the S3-compatible bucket must have versioning enabled. Portable,
checksummed logical/database + local-filesystem bundles are created and restored
with:

```bash
backend/scripts/ops/backup.sh
backend/scripts/ops/restore.sh /secure/path/standard-astro-....tar.gz
```

The scripts never include JWT, Fernet, API, or database credentials. RPO/RTO,
explicit restore confirmation, secret-key recovery, quarterly drills, and the
full incident procedure are defined in
[`docs/OPERATIONS_RUNBOOK.md`](./docs/OPERATIONS_RUNBOOK.md).

Versioning is for recovery, not an excuse to retain a deleted account. The
provider lifecycle must preserve ordinary recovery versions for the published
backup window while the deletion worker retains permission to erase every
version of an account-owned key immediately. Record and test both rules; a
bucket lifecycle screenshot alone does not prove account erasure.

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

The backend Docker image installs system libraries for the scientific stack and installs Python packages from the hash-locked `backend/requirements.lock`. If a package starts building from source unexpectedly, compare that lock with `backend/requirements.txt` and inspect the Render build logs.

`run_python` is deliberately unavailable in production. The bundled
in-process and subprocess implementations limit crashes and resource use but
share the application host, filesystem, and process trust domain; filtered
Python builtins are not a security boundary. Production startup rejects
`SANDBOX_BACKEND=inprocess` and `SANDBOX_BACKEND=subprocess`. Re-enable dynamic
code only after adding an external runner with no application secrets, no
tenant mounts, an immutable image, network egress policy, and per-job CPU,
memory, process, and wall-clock limits.

### CORS errors

Make sure `CORS_ORIGINS` contains the exact frontend origin, including scheme. The current Render frontend is `https://astro-frontend-tyfr.onrender.com`.

### Free-tier cold starts

If a preview uses free-tier services, they can sleep after idle time. The
production backend, worker, persistent Key Value broker, and disk use paid plans
because background workers and durable queue/storage semantics are required.

### Deep health reports schema / broker / worker / storage failure

- `schema`: compare `alembic current` with `alembic heads`; run migrations, do
  not stamp blindly.
- `broker`: inspect the Render Key Value service and `REDIS_URL` binding.
- `celery_worker`: inspect the worker deploy/logs and run
  `celery -A celery_worker inspect ping` from a trusted shell. A
  `revision_mismatch` means an old worker is still consuming from the queue.
- `celery_beat`: inspect every Beat deploy/log, Redis connectivity, scheduler
  ticks, lease age, and commit. A stopped old instance remains deliberately
  blocking until its 120-second lease expires.
- `schema gate timed out`: the backend migration did not reach the image head;
  inspect the backend pre-deploy log. Do not bypass the worker/beat wait command.
- `stale_job_reconciled`: a queued/running record exceeded the configured
  lifecycle ceiling after worker/process loss. Inspect the original worker log,
  then retry only from the owner-scoped Jobs UI/API.
- `durable_persistence_failed`: PostgreSQL rejected a critical lifecycle update
  after bounded retries. The result is deliberately non-claimable; restore DB
  health before retrying the job.
- `storage`: verify the `/app/data` disk attachment and free space. Never fall
  back to the ephemeral filesystem in production.

### Gated connector confusion

The provenance-v2 rollout intentionally gates non-v2 sources. This is expected until each connector has an M3-style provenance upgrade and is added to `V2_AVAILABLE_CONNECTORS`.
