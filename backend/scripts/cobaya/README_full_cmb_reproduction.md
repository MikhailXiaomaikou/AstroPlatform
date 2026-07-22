# DESI 2024 VI `w0wa` exact-profile reproduction

This directory contains the fail-closed offline workflow for reproducing the
parameter intervals in DESI 2024 VI (`2404.03002v3`), Table 3, for the
`DESI+CMB+PantheonPlus` combination. It does not test or claim that LambdaCDM is
excluded. Run commands below from `backend/`. The target values were exposed in
the implementation request, so this is a known-target preregistered
reproduction, not an analyst-blind or model-blind reproduction.

The exact profile is `w0wa_desi_cmb_pantheonplus_exact.yaml`:

- DESI DR1 Gaussian BAO likelihood;
- Pantheon+ full statistical+systematic covariance and the likelihood's
  source-column `zHD > 0.01` selection (mapped internally by Cobaya to its
  `zcmb` array);
- official PR3 clipy Commander (`TT_clik`), SimAll (`EE_clik`) and plik
  `TTTEEE` likelihoods;
- ACT DR6 + Planck PR4 lensing with `actplanck_baseline`;
- CAMB PPF, one `0.06 eV` massive neutrino, DESI Table 2 priors and the
  `w0 + wa < 0` high-redshift matter-domination condition;
- ACT-recommended CAMB precision (`lmax=4000`, `lens_margin=1250`,
  `lens_potential_accuracy=4`, unit accuracy boosts, `mead2016`).

`w0wa_desi_sn_planck.yaml` and `planck_clikfree_install.yaml` are retained only
as historical CamSpec/native exploratory proxies. They cannot pass exact-profile
preflight and cannot produce A-readiness evidence.

## Environment and data

The offline dependency lock is `w0wa_exact_requirements.txt`. It is deliberately
separate from the web application's requirements and Render deployment. Build
a dedicated Python 3.14.5 exact environment from the committed 52-wheel
closure, retaining the original archives because preflight re-hashes them.
Revision 2 must use new paths: never create, update, remove, or install into the
revision-1 `exact-venv`, `wheels`, `isolated-venv`, `backend/packages`, receipt,
or chain paths. Do not reuse the backend web/test environment either; its extra
packages and startup hooks are outside the frozen scientific closure.

The following setup deliberately fails if any revision-2 destination already
exists (including a symlink). An interrupted attempt must be inspected and
archived before an operator chooses another fresh revision-2 destination; do
not work around the guard by touching revision-1 state:

```bash
set -eu

export EXACT_R2_ROOT=../.local/w0wa-strict-a-readiness
export EXACT_VENV="$EXACT_R2_ROOT/exact-venv-r2"
export EXACT_WHEELHOUSE="$EXACT_R2_ROOT/wheelhouse-r2"
export EXACT_PACKAGES="$EXACT_R2_ROOT/packages-r2"
export EXACT_PRIMARY="$EXACT_R2_ROOT/primary-r2"
export EXACT_PRIMARY_PREFLIGHT="$EXACT_PRIMARY/preflight-r2.json"
export EXACT_PRIMARY_GENERATION="$EXACT_PRIMARY/generation-r2.json"
export EXACT_PRIMARY_ANALYSIS="$EXACT_PRIMARY/analysis-r2.json"
export EXACT_PRIMARY_ADEQUACY="$EXACT_PRIMARY/model-adequacy-r2.json"
export EXACT_HIDDEN_ANSWER="$EXACT_PRIMARY/hidden-answer-r2.json"
export EXACT_PRIMARY_GRADE="$EXACT_PRIMARY/grade-r2.json"

for path in "$EXACT_VENV" "$EXACT_WHEELHOUSE" "$EXACT_PACKAGES" "$EXACT_PRIMARY"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    echo "refusing to reuse revision-2 destination: $path" >&2
    exit 1
  fi
done

/opt/homebrew/bin/python3.14 -m venv "$EXACT_VENV"
mkdir -p "$EXACT_WHEELHOUSE" "$EXACT_PACKAGES" "$EXACT_PRIMARY"
"$EXACT_VENV/bin/pip" download --only-binary=:all: --no-deps \
  -r scripts/cobaya/w0wa_exact_requirements.txt \
  -d "$EXACT_WHEELHOUSE"

/opt/homebrew/bin/python3.14 -I - \
  scripts/cobaya/w0wa_exact_wheel_manifest.json \
  scripts/cobaya/w0wa_exact_requirements.txt \
  "$EXACT_WHEELHOUSE" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
lock_path = Path(sys.argv[2])
wheelhouse = Path(sys.argv[3])
expected_manifest_hash = (
    "sha256:e45ea8e098a3470622cd26cd7ed5061262859a09a6f84f93d97eaf49e56541bc"
)
expected_lock_hash = (
    "sha256:6d40a07a26b021b3cab6de36dbec3df446115718998a25b68abf08bde0a7f833"
)

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()

if sha256(manifest_path) != expected_manifest_hash:
    raise SystemExit("wheel manifest hash does not match revision 2")
if sha256(lock_path) != expected_lock_hash:
    raise SystemExit("dependency lock hash does not match revision 2")
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
records = manifest.get("wheels")
if manifest.get("requirements_sha256") != expected_lock_hash:
    raise SystemExit("wheel manifest dependency-lock binding is invalid")
if not isinstance(records, list) or len(records) != 52:
    raise SystemExit("wheel manifest must contain exactly 52 records")
expected = {record["filename"]: record for record in records}
if len(expected) != 52 or any(Path(name).name != name for name in expected):
    raise SystemExit("wheel manifest filenames are duplicate or unsafe")
observed = {path.name: path for path in wheelhouse.glob("*.whl")}
if set(observed) != set(expected):
    raise SystemExit("wheelhouse filenames do not exactly match the manifest")
for name, record in expected.items():
    wheel = observed[name]
    if wheel.stat().st_size != record["size_bytes"] or sha256(wheel) != record["sha256"]:
        raise SystemExit(f"wheel bytes do not match the manifest: {name}")
print("verified revision-2 wheel manifest and all 52 wheel archives")
PY

"$EXACT_VENV/bin/pip" install --no-index \
  --find-links "$EXACT_WHEELHOUSE" \
  -r scripts/cobaya/w0wa_exact_requirements.txt
"$EXACT_VENV/bin/pip" check
```

Preserve the three official source archives named in
`w0wa_exact_data_manifest.json` under
`../.local/w0wa-strict-a-readiness/`. The Pantheon+ statistical-only variant is
sourced from its frozen collaboration commit, not derived by subtracting
covariances:

```bash
git clone --filter=blob:none --sparse \
  https://github.com/PantheonPlusSH0ES/DataRelease \
  ../.local/w0wa-strict-a-readiness/pantheonplus-data-release
git -C ../.local/w0wa-strict-a-readiness/pantheonplus-data-release \
  sparse-checkout set Pantheon+_Data/4_DISTANCES_AND_COVAR
git -C ../.local/w0wa-strict-a-readiness/pantheonplus-data-release \
  checkout --detach c447f0fea703fcd0fff57de5000947b5ca81286b

"$EXACT_VENV/bin/cobaya-install" scripts/cobaya/w0wa_exact_install.yaml \
  -p "$EXACT_PACKAGES"
```

After that one controlled installation, treat `$EXACT_PACKAGES` as the
read-only revision-2 likelihood/data closure: do not rerun `cobaya-install`,
edit it, or use it as an output directory. The primary and isolated revision-2
environments deliberately read the same hash-verified tree; this avoids a
second multi-gigabyte copy without trusting or modifying revision-1
`backend/packages`. Each environment independently re-hashes the tree during
its own preflight, so any drift fails closed.

Preflight enforces exact versions and hashes every installed file in the full
runtime dependency closure. It also verifies each original wheel archive,
official Planck/ACT/CamSpec source archives, every consumed likelihood
data/code file, the exact YAML, the dependency lock and the pinned native
Python/Open MPI/mpi4py/Accelerate runtime. A non-empty `PYTHONPATH`, an unowned
`.pth`, or a `sitecustomize`/`usercustomize` startup hook fails preflight.
Source-owned `__pycache__` is excluded from the stable fingerprint as derived
cache, while sourceless or otherwise unowned bytecode remains fatal.
Formal and model-adequacy children run as `python -I -m cobaya.run`, and that
isolated import policy is bound into the environment identity.

## Fixed workflow

The only canonical order is:

```text
preflight -> generate -> run -> analyze -> grade
```

Each stage validates and binds the preceding receipt. Config, data, reference,
artifact, Git source-tree or receipt drift closes the next gate. Formal
preflight must run from a globally clean commit descended from frozen base
`f9efb4ac6f7850d4c7739ac038d08beb37ea785e`; it records the Git HEAD/tree and
re-hashes every acceptance-critical source/config file for external checkout.
Branch names are not trusted state: clean descendants on any branch and a
detached HEAD are both accepted.
Before any command, freeze the three-thread environment used by preflight and
every run:

```bash
export OMP_NUM_THREADS=3
export MKL_NUM_THREADS=3
export OPENBLAS_NUM_THREADS=3
```

Every exact `preflight -> generate -> run -> analyze -> grade` process and the
independent postprocessor must be launched with `python -I`. The CLI refuses a
non-isolated interpreter, a non-empty `PYTHONPATH`, or an untrusted startup-hook
closure; isolating only the Cobaya child is not sufficient.

Every new receipt binds
`docs/DESI_W0WA_A_READINESS_AMENDMENT_002.md`. It carries forward the
known-target disclosure and stricter paper-fidelity `bulk ESS >= 1000` overlay
from amendment 001, and freezes the security-only environment revision 2.
Revision 2 remains
`WITHHELD_PENDING_FRESH_PREFLIGHT_AND_SCIENCE_REGRESSION` until a later
immutable amendment records the required validation. Historical revision-1
receipts keep their original amendment-001 binding and must not be rewritten.

### 1. Preflight

The committed `w0wa_exact_reference_cases.json` contains source-pinned upstream
reference points. It contains only registered sampled points and, per exact
likelihood, `expected_chi2`, `absolute_tolerance`, and `source`; it never
contains a trusted `observed` value. Preflight instantiates each exact
likelihood independently and computes every component chi-square live. The
separate committed `w0wa_exact_data_manifest.json` prevents an altered first
installation from becoming trusted merely because it was the first one hashed.

```bash
"$EXACT_VENV/bin/python" -I scripts/cobaya/canonical_full_likelihood_evidence.py preflight \
  --packages-path "$EXACT_PACKAGES" \
  --wheels-path "$EXACT_WHEELHOUSE" \
  --output "$EXACT_PRIMARY_PREFLIGHT"
```

Missing PR3/ACT/PR4-CamSpec data, a mismatched version/hash or any
reference-value failure produces `WITHHELD`. Live reference evaluation includes
the upstream Cobaya v3.6.2 NPIPE CamSpec regression point; inventorying its data
alone is insufficient. A CamSpec/native fallback is not permitted.
The NPIPE commitment contains the 15 official release files exactly. Cobaya's
writable `*_covinv_*.npy` acceleration cache is not an official data product;
the reference evaluator disables that cache and recomputes the inverse from the
frozen covariance in memory, so a stale or poisoned cache cannot be consumed.

### 2. Generate

```bash
"$EXACT_VENV/bin/python" -I scripts/cobaya/canonical_full_likelihood_evidence.py generate \
  --preflight-report "$EXACT_PRIMARY_PREFLIGHT" \
  --packages-path "$EXACT_PACKAGES" \
  --free-output "$EXACT_PRIMARY/free-map-r2.yaml" \
  --fixed-output "$EXACT_PRIMARY/fixed-map-r2.yaml" \
  --adequacy-output-dir "$EXACT_PRIMARY/adequacy-r2" \
  --output "$EXACT_PRIMARY_GENERATION"
```

This step is receipt-gated and derives paired audit configurations from the
single exact source. The parameter-interval reproduction does not consume or
interpret model-preference statistics from them.

### 3. Run

First execute a short smoke run with a new prefix and mark it permanently
non-citable:

```bash
"$EXACT_VENV/bin/python" -I \
  scripts/cobaya/canonical_full_likelihood_evidence.py run \
  --kind chain --evidence-class non_citable_smoke \
  --run-id w0wa-exact-smoke-YYYYMMDD \
  --config "$EXACT_PRIMARY/adequacy-r2/non_citable_smoke.yaml" \
  --prefix cobaya_runs/w0wa_exact_smoke_r2 \
  --packages-path "$EXACT_PACKAGES" \
  --preflight-report "$EXACT_PRIMARY_PREFLIGHT" \
  --generation-report "$EXACT_PRIMARY_GENERATION" \
  --mpi 4
```

Smoke numbers must never be quoted. After smoke validation, launch the formal
four-process run with a fresh prefix. Unlike a non-citable smoke, a successful
formal or model-adequacy completion requires the durable
`EVIDENCE_SIGNING_KEY`/`EVIDENCE_SIGNING_KEY_ID` described below:

```bash
"$EXACT_VENV/bin/python" -I \
  scripts/cobaya/canonical_full_likelihood_evidence.py run \
  --kind chain --evidence-class formal_candidate \
  --run-id w0wa-exact-formal-YYYYMMDD \
  --config scripts/cobaya/w0wa_desi_cmb_pantheonplus_exact.yaml \
  --prefix cobaya_runs/w0wa_exact_formal_r2 \
  --packages-path "$EXACT_PACKAGES" \
  --preflight-report "$EXACT_PRIMARY_PREFLIGHT" \
  --generation-report "$EXACT_PRIMARY_GENERATION" \
  --mpi 4
```

The registered seed vector creates four distinct deterministic MPI streams. The
trusted launcher executes resolved, byte-checked Python and `mpirun` paths and
issues a persistent-key HMAC completion receipt only after the real subprocess
returns. That receipt binds a precommitted nonce, the run ID, profile, upstream
receipts, exact data, termination state, runner log and every output hash;
calling the public completion writer over fabricated files cannot produce a
formal success. A completed run is still not citable.

The exact contract accepts only signing-key ID
`w0wa-strict-20260713-local-v1` and its frozen SHA-256 fingerprint; the secret
key is never recorded in the repository or evidence package. This is explicitly
a trusted-host control: same-UID processes, root, and the machine operator are
inside the trust boundary. The HMAC detects bound-artifact drift or tampering
only while the local host and signing key remain uncompromised. It does not
provide hostile-host isolation, resist a local operator with key access, or
constitute external scientific review. Resolved-path execution and byte checks
are drift controls, not a cryptographic defense against those trusted local
principals on macOS.

### 4. Analyze

Supply every physical claim-support artifact explicitly:

```bash
"$EXACT_VENV/bin/python" -I scripts/cobaya/canonical_full_likelihood_evidence.py analyze \
  --chain-prefix cobaya_runs/w0wa_exact_formal_r2 \
  --packages-path "$EXACT_PACKAGES" \
  --preflight-report "$EXACT_PRIMARY_PREFLIGHT" \
  --generation-report "$EXACT_PRIMARY_GENERATION" \
  --support-path "$EXACT_PRIMARY/protocol-r2.json" \
  --support-path "$EXACT_PRIMARY/diagnostic-report-r2.json" \
  --output "$EXACT_PRIMARY_ANALYSIS"
```

The analyzer removes 30% of physical rows per chain, matching GetDist's
`ignore_rows` convention. Integer Cobaya weights are expanded and recent draws
are aligned to the shortest chain only for ArviZ diagnostics. All post-burn
weighted rows remain in the GetDist reporting sample. Canonical and independent
postprocessors both require the aligned segment to retain at least 90% of every
expanded post-burn chain; a prematurely stopped or severely imbalanced chain is
withheld before intervals are emitted. The amended gate requires
rank-normalized `R-hat < 1.01` and bulk `ESS >= 1000` for every sampled
nuisance/cosmological parameter and each reported derived parameter, while MCSE
is checked separately. For the four reported parameters, MCSE must be below
`0.05` of the preregistered paper standard deviation; for unreported sampled
cosmological and nuisance parameters it must be below `0.05` of that same
closed run's posterior standard deviation. Each parameter records which
denominator was used. Symmetric parameters use posterior
mean-plus-or-minus-standard-deviation; `wa` uses the mean and GetDist marginal
68% limits. Smoke or proxy attestations, duplicate chains and
missing/hash-drifted support files are rejected.

The isolated rerun must have its own venv and its own preflight/generation
receipts. Reusing the primary receipt would correctly fail because execution
environment fingerprints include venv paths. The native trust fingerprint,
however, compares binary/build identity without absolute paths:

```bash
set -eu

export EXACT_ISOLATED_VENV="$EXACT_R2_ROOT/isolated-venv-r2"
export EXACT_ISOLATED="$EXACT_R2_ROOT/isolated-r2"

for path in "$EXACT_ISOLATED_VENV" "$EXACT_ISOLATED"; do
  if [ -e "$path" ] || [ -L "$path" ]; then
    echo "refusing to reuse revision-2 isolated destination: $path" >&2
    exit 1
  fi
done

/opt/homebrew/bin/python3.14 -m venv \
  "$EXACT_ISOLATED_VENV"
mkdir -p "$EXACT_ISOLATED"
"$EXACT_ISOLATED_VENV/bin/pip" install --no-index \
  --find-links "$EXACT_WHEELHOUSE" \
  -r scripts/cobaya/w0wa_exact_requirements.txt
"$EXACT_ISOLATED_VENV/bin/pip" check

"$EXACT_ISOLATED_VENV/bin/python" -I \
  scripts/cobaya/canonical_full_likelihood_evidence.py preflight \
  --packages-path "$EXACT_PACKAGES" \
  --wheels-path "$EXACT_WHEELHOUSE" \
  --output "$EXACT_ISOLATED/preflight-r2.json"
"$EXACT_ISOLATED_VENV/bin/python" -I \
  scripts/cobaya/canonical_full_likelihood_evidence.py generate \
  --preflight-report "$EXACT_ISOLATED/preflight-r2.json" \
  --packages-path "$EXACT_PACKAGES" \
  --free-output "$EXACT_ISOLATED/free-map-r2.yaml" \
  --fixed-output "$EXACT_ISOLATED/fixed-map-r2.yaml" \
  --adequacy-output-dir "$EXACT_ISOLATED/adequacy-r2" \
  --output "$EXACT_ISOLATED/generation-r2.json"
"$EXACT_ISOLATED_VENV/bin/python" -I \
  scripts/cobaya/canonical_full_likelihood_evidence.py run \
  --kind chain --evidence-class model_adequacy \
  --run-id w0wa-exact-isolated-YYYYMMDD \
  --config "$EXACT_ISOLATED/adequacy-r2/independent_reproduction.yaml" \
  --prefix cobaya_runs/w0wa_exact_isolated_r2 \
  --packages-path "$EXACT_PACKAGES" \
  --preflight-report "$EXACT_ISOLATED/preflight-r2.json" \
  --generation-report "$EXACT_ISOLATED/generation-r2.json" \
  --mpi 4
```

Then use the separate postprocessor implementation (it does not import the
canonical analyzer):

```bash
"$EXACT_ISOLATED_VENV/bin/python" -I \
  scripts/cobaya/independent_w0wa_postprocess.py \
  --chain-prefix cobaya_runs/w0wa_exact_isolated_r2 \
  --updated-config cobaya_runs/w0wa_exact_isolated_r2.updated.yaml \
  --run-id w0wa-exact-isolated-YYYYMMDD \
  --primary-execution-fingerprint sha256:<primary-execution-fingerprint> \
  --environment-fingerprint sha256:<isolated-environment-fingerprint> \
  --environment-preflight "$EXACT_ISOLATED/preflight-r2.json" \
  --output "$EXACT_ISOLATED/independent-postprocess-r2.json"
```

The independent postprocessor does not trust the environment fingerprint as a
caller assertion. It revalidates the isolated preflight self-hash, exact lock,
actual `python -I` flags, startup hooks, interpreter, native binaries, thread
settings and all 52 installed distribution file hashes before binding that
receipt into its own report. The runtime fingerprint from the run attestation
and the larger preflight wheel-closure fingerprint are separate fields and may
not be substituted for one another. Bound postprocessing fixes burn-in at 0.30,
and each chain is read once so parsing and SHA-256 use the same immutable byte
snapshot.

Its report hash is required by the `independent_reproduction` adequacy evidence.

### 5. Grade

Grade is the only stage that reads the answer-key file and verifies its
canonical compact-JSON SHA-256 commitment:

`preflight`, `generate`, `run`, and `analyze` produce the primary preflight,
generation, chain, and analysis artifacts shown above. They do **not** produce
the hidden-answer or combined model-adequacy manifests. Before grading, the
operator must place those separately constructed, hash-bound revision-2 inputs
at `$EXACT_HIDDEN_ANSWER` and `$EXACT_PRIMARY_ADEQUACY`; grade validates them
and fails closed. Do not substitute either revision-1 default artifact.

```bash
"$EXACT_VENV/bin/python" -I scripts/cobaya/canonical_full_likelihood_evidence.py grade \
  --manifest "$EXACT_PRIMARY_ANALYSIS" \
  --hidden-answer "$EXACT_HIDDEN_ANSWER" \
  --adequacy-manifest "$EXACT_PRIMARY_ADEQUACY" \
  --target-hash sha256:<pre-registered-commitment> \
  --output "$EXACT_PRIMARY_GRADE"
```

It enforces all numerical, directional and six model-adequacy thresholds. Fake
`passed` booleans are insufficient: every adequacy record needs a real
hash-matched artifact and its preregistered metrics. Only when every gate passes
does grade call the production `ResearchAlphaManifest` constructor and attach a
verifiable HMAC `research_alpha` manifest bound to the exact run, four chain
IDs/seeds/hashes, inputs, intervals, diagnostics, adequacy artifacts and claim
paths.

The three injection-recovery checks define joint 95% coverage using the
recovered (`w0`, `wa`) covariance, Mahalanobis distance, and the chi-square
two-degree-of-freedom 95% threshold. This is an elliptical coverage diagnostic,
not a claim that the region is a general non-Gaussian HPD contour.

Exact manifest signing has no process-local fallback. Before an eligible exact
signing step, operators must inject through the local secret store the durable
`EVIDENCE_SIGNING_KEY` whose SHA-256 fingerprint matches the frozen contract,
with the exact ID `w0wa-strict-20260713-local-v1`. A merely long key or other
non-ephemeral ID is rejected. Receipts bind only key availability, ID and
SHA-256 fingerprint; the secret is never serialized. Development/CI profiles
may use the process-local ephemeral signer, but remain non-publication fixtures.

The committed predictive-check and injection-recovery JSON files freeze inputs,
seeds, statistics and thresholds only. They are intentionally marked
`producer_status=WITHHELD`: no validated source-seeded simulator or independently
reviewed adequacy analyzer is registered yet. Until those separate executable
hashes are implemented, reviewed and added to the shared contract, exact PPC
and injection receipts cannot be signed and grade must remain `WITHHELD`.
Hand-authored “passed” JSON is never an alternative.

Because analyst blinding was not achieved, this known-target run needs a frozen
protocol authority to clear that deviation. This protocol deliberately freezes
both the protocol-adjudication and external-review authority registries as
empty. Environment-selected authority IDs or public keys are only locators and
cannot grant either capability. Therefore this run remains `WITHHELD`, even if
its numerical gates pass; no local HMAC or ad hoc Ed25519 key can waive that
result. Registering a future authority requires an immutable protocol amendment
and a fresh formal run.

The exact-profile software ceiling is
`A_READY_PENDING_EXTERNAL_REVIEW`; it can never emit strict `A`, and
`strict_A_count` stays zero. Any failure emits no A-ready manifest. This
workflow never outputs Wilks p-values, Gaussian-equivalent significance,
Bayes-factor preference or a dynamic-dark-energy discovery claim.
