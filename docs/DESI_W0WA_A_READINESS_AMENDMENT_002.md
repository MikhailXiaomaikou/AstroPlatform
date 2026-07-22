# DESI `w0wa` exact-environment security amendment 002

Effective: 2026-07-21, before any environment-revision-2 preflight, smoke run,
formal chain, or scientific regression.

This is an immutable security roll-forward for the offline DESI 2024 VI
`w0wa` exact-profile reproduction. It does not amend the paper target, data,
likelihoods, cosmological model, sampled parameters, priors, convergence
thresholds, numerical acceptance tolerances, allowed claims, or forbidden
claims frozen by the original protocol and amendment 001.

## Revision identity and current status

```text
revision_id=w0wa-exact-environment-r2-security-20260721
status=WITHHELD_PENDING_FRESH_PREFLIGHT_AND_SCIENCE_REGRESSION
scientific_contract_changed=false
```

Revision 2 is not eligible for `A_READY_PENDING_EXTERNAL_REVIEW`, `A`, or any
publication-ready claim. A successful dependency installation or ordinary CI
run is not scientific validation. The status must remain `WITHHELD` until a
later immutable amendment records all validation requirements below as passed.

## Security-only dependency changes

The exact Python version, platform, and 52-distribution closure are retained.
Only these five distributions change:

| Distribution | Revision 1 | Revision 2 | Role in the exact closure |
|---|---:|---:|---|
| `idna` | 3.11 | 3.15 | URL dependency; no direct scientific import |
| `Pillow` | 12.1.1 | 12.3.0 | Matplotlib image backend; no likelihood implementation |
| `Pygments` | 2.19.2 | 2.20.0 | Formatting dependency; no direct scientific import |
| `pytest` | 8.4.2 | 9.0.3 | Environment test tool; no production likelihood import |
| `requests` | 2.32.5 | 2.33.0 | Download/HTTP dependency; no numerical kernel |

The numerical stack remains fixed, including Cobaya 3.6.2, CAMB 1.6.6,
NumPy 2.4.3, SciPy 1.17.1, GetDist 1.7.7, `clipy-like` 0.15, and
`act_dr6_lenslike` 1.2.1. This makes numerical drift unlikely, but it does not
prove that the scientific result is unchanged.

## Frozen revision-2 commitments

The environment remains Python 3.14.5 on Apple ARM64. The project wheel
manifest resolves exactly 52 non-yanked compatible wheels.

```text
dependency_lock_sha256=sha256:6d40a07a26b021b3cab6de36dbec3df446115718998a25b68abf08bde0a7f833
wheel_manifest_sha256=sha256:e45ea8e098a3470622cd26cd7ed5061262859a09a6f84f93d97eaf49e56541bc
likelihood_code_manifest_sha256=sha256:780f6bbd14d4c79a494f80cd1a6ec0f3fff535e815455e54f1a58afc51a078cf
wheel_count=52
python_version=3.14.5
platform=macOS-arm64
```

The revision-1 dependency lock remains identified by:

```text
sha256:f4cfe85aa7a7f08084eb1c8092784143e3fe711e44ed8379cd689278a119793d
```

It is superseded for new execution, not erased from history.

## Mandatory validation before activation

All of the following must be recorded against the exact revision-2 hashes:

1. Download all 52 registered wheels, verify every archive SHA-256, install
   them into a clean Python 3.14.5 environment with `--no-index`, and pass
   `pip check`.
2. Run a fresh exact preflight against the pinned data, likelihood source
   trees, native runtime, three-thread policy, and revision-2 amendment.
3. Recompute every fixed reference-likelihood point within its frozen
   tolerance.
4. Run the focused exact-pipeline, independent-postprocessor, Research Alpha
   manifest, and cosmology benchmark suites.
5. Run a fresh scientific smoke or formal reproduction and compare its
   numerical products with the frozen paper targets and the revision-1
   baseline using the existing acceptance rules.
6. Complete the independent postprocessing path and record human review of
   the environment roll-forward.

Passing steps 1 through 4 is necessary but does not authorize a scientific
claim. Until steps 5 and 6 are also complete and a later immutable amendment
activates the revision, deterministic finalization must return `WITHHELD`.

## Old-evidence preservation

No revision-1 receipt, chain, analysis, grade, Research Alpha manifest, or
Evidence Pack may be edited, re-hashed, re-signed, or relabeled as revision 2.
Historical evidence remains verifiable with the code and trust commitments
from its originating release or Git commit.

Revision-1 preflight, generation, run, and analysis receipts do not satisfy
revision 2. Their dependency lock, wheel manifest, likelihood-code manifest,
and amendment hashes differ, so current verification must fail closed rather
than silently reusing them.

## Binding rule

Every revision-2 preflight, generation, run, analysis, grade, independent
postprocess, and Research Alpha artifact must bind the exact SHA-256 of this
file. Amendment 001 remains historical and its known-target disclosure and
paper-fidelity `bulk ESS >= 1000` rule remain in force. Missing or changed
revision-2 amendment bytes, or an environment status other than the frozen
pending-validation state, leaves the workflow `WITHHELD`.
