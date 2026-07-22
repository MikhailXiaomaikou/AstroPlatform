# DESI `w0wa` exact-environment security amendment 003

Effective: 2026-07-22, before any run using the dependency closure frozen by
this amendment.

This immutable amendment rolls the offline DESI 2024 VI `w0wa` exact-profile
environment forward to the first patched `setuptools` release for
CVE-2026-59890 / GHSA-h35f-9h28-mq5c. It does not amend the paper target,
data, likelihoods, cosmological model, sampled parameters, priors,
convergence thresholds, numerical acceptance tolerances, allowed claims, or
forbidden claims frozen by the original protocol and amendments 001–002.

Amendment 002 remains unchanged in Git history. New exact executions bind
this file and the new hashes below. Historical artifacts remain bound to the
amendment and exact-environment identity that created them; they are never
rewritten or promoted into the new closure.

## Revision identity and current status

```text
revision_id=w0wa-exact-environment-r2-security-20260722
status=WITHHELD_PENDING_FRESH_PREFLIGHT_AND_SCIENCE_REGRESSION
scientific_contract_changed=false
supersedes_dependency_lock_sha256=sha256:6d40a07a26b021b3cab6de36dbec3df446115718998a25b68abf08bde0a7f833
```

The revision remains in the isolated revision-2 lineage. Canonical defaults
and the registered operator recipe use the `r2-a003` filesystem identity for
environments, wheelhouses, likelihood packages, receipts, generated
configurations, chains, logs, analyses, adequacy records, independent reports,
and grades. The initial Amendment-002 `*-r2` and `w0wa_exact_*_r2` paths are
historical and read-only.

An explicit custom path need not contain the literal `r2-a003`. It is eligible
only when its resolved path is not a revision-1/Amendment-002 alias, every
output file and run-prefix namespace is new, every reused output directory is
empty and not a symlink, and the custom interpreter passes the full exact
83.0.0/hash-bound preflight. Existing receipts or artifacts are never
overwritten.

This revision is not eligible for `A_READY_PENDING_EXTERNAL_REVIEW`, `A`, or
any publication-ready claim. Installing the package or passing ordinary CI is
not scientific validation. The deterministic finalizer must remain
`WITHHELD` until a later immutable amendment records every required scientific
validation and review as passed.

This gate also applies to the legacy full-likelihood evidence-manifest helper.
It may report that numerical checks passed, but while this environment status
is pending it must emit `publication_ready=false`, `claim_eligible=false`, no
scientific conclusion attestations, and the machine-readable pending reason.

## Security-only dependency change

Exactly one distribution changes:

| Distribution | Amendment 002 closure | Amendment 003 closure | Role |
|---|---:|---:|---|
| `setuptools` | 82.0.1 | 83.0.0 | Python packaging/bootstrap; not a likelihood or numerical kernel |

All other 51 exact pins are byte-for-byte unchanged. In particular, the
numerical stack remains fixed at Cobaya 3.6.2, CAMB 1.6.6, NumPy 2.4.3,
SciPy 1.17.1, GetDist 1.7.7, `clipy-like` 0.15, and
`act_dr6_lenslike` 1.2.1.

The official non-yanked PyPI wheel record is:

```text
filename=setuptools-83.0.0-py3-none-any.whl
size_bytes=1008090
sha256=29b23c360f22f414dc7336bb39178cc7bcbf6021ed2733cde173f09dba19abb3
source=https://pypi.org/pypi/setuptools/83.0.0/json
```

The downloaded wheel bytes were independently checked against that published
size and SHA-256 before this amendment was frozen.

## Frozen current commitments

The environment remains Python 3.14.5 on Apple ARM64 and the manifest still
contains exactly 52 compatible, non-yanked wheel records.

```text
dependency_lock_sha256=sha256:cd1f2fef709506ca19a7eda578f392e72fe5d81fa8a3ea83729df3935b84f8a3
wheel_manifest_sha256=sha256:37c9926fae0ebb49e833f6ecfd51001a11a96470a5631dfae8d58fb09d3bcb36
likelihood_code_manifest_sha256=sha256:2ac837fce8fc5d7114f49af41c3541dc7e1085289141453b15c9feb81a584b31
canonical_producer_sha256=sha256:94cc8ba7ee022572783761c815d44520d7182a95cad030b1217e4c98f0f2d063
independent_postprocessor_sha256=sha256:8b2e519faa67bcdb6234f629f78cdb88a940f8152b294ac9c437776b8f3cd52d
trusted_source_base_commit=ebb2f8d8eef202dbe8a8a85b0cb753829f3899a2
wheel_count=52
python_version=3.14.5
platform=macOS-arm64
```

The revision-1 lock remains identified by:

```text
sha256:f4cfe85aa7a7f08084eb1c8092784143e3fe711e44ed8379cd689278a119793d
```

The initial amendment-002 lock remains identified by:

```text
sha256:6d40a07a26b021b3cab6de36dbec3df446115718998a25b68abf08bde0a7f833
```

Both are historical identities. Neither may satisfy a new preflight against
this amendment.

The source-ancestry root is the clean pre-Amendment-003 `main` commit
`ebb2f8d8eef202dbe8a8a85b0cb753829f3899a2`. A clean branch or detached HEAD
must descend from that commit and bind every required source byte. The older
`f9efb4ac6f7850d4c7739ac038d08beb37ea785e` commitment was not an ancestor of
the pre-Amendment-003 `main` used here and is deliberately not selected as this
revision's trust root.

## Mandatory validation before activation

All of the following must be recorded against the exact hashes above:

1. Verify all 52 wheel archive hashes, install them in a clean Python 3.14.5
   environment with `--no-index`, and pass `pip check`.
2. Run a fresh exact preflight against the pinned data, likelihood source
   trees, native runtime, three-thread policy, and this amendment.
3. Recompute every fixed reference-likelihood point within its frozen
   tolerance.
4. Run the focused exact-pipeline, independent-postprocessor, Research Alpha
   manifest, and cosmology benchmark suites.
5. Run a fresh scientific smoke or formal reproduction and compare its
   numerical products with the frozen paper targets and prior baseline under
   the unchanged acceptance rules.
6. Complete the independent postprocessing path and record human review of
   the environment roll-forward.

Steps 1–4 are necessary engineering checks. They do not authorize a scientific
claim. Until steps 5–6 also pass and a later immutable amendment activates the
revision, every result remains `WITHHELD`.

## Binding and preservation rule

Every new preflight, generation, run, analysis, grade, independent postprocess,
and Research Alpha artifact must bind the exact SHA-256 of this file plus the
new dependency, wheel, and likelihood-code manifest hashes. Missing or changed
bytes fail closed.

The canonical CLI and independent postprocessor must reject revision-1 and
Amendment-002 paths, their symlink aliases, old Python environments, and old
runner binaries before they analyze data or create an output. They must also
reject any pre-existing output/receipt file and every occupied chain or map
prefix, including derived smoke configuration, reservation, runner-log, and
attestation names. This guard is an execution-identity requirement, not merely
an operator convention.

Amendments 001 and 002 stay in the source-state inventory. Their known-target
disclosure, paper-fidelity `bulk ESS >= 1000` requirement, revision-1 evidence
preservation rule, and revision-2 path isolation continue to apply. No old
receipt, chain, analysis, grade, manifest, or Evidence Pack may be edited,
re-hashed, re-signed, or relabeled as belonging to this closure.
