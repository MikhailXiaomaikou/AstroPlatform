---
name: add-dataset
description: Checklist for integrating a new cosmology dataset (or upgrading a compressed entry to a full likelihood) into the backend/app/services/cosmology_likelihoods/ package. Use whenever the task is "add/integrate/wire dataset X", "upgrade X to full covariance", or "make X executable in-process". Every step below exists because skipping it produced a real bug — do not skim.
---

# Add a cosmology dataset

File layout (2026-07-03 split): `cosmology_likelihoods` is a package, not a
single file. `registry.py` holds `_REGISTRY`; each probe family keeps its
loaders + data dicts + chi² together (`bao.py`, `sn.py`, `cc.py`, `rsd.py`,
`cmb.py`); `_is_executable_*` gates + cov-fidelity + `audit_executable_pins`
live in `verification.py`; runners in `runners.py`; samplers in `sampling.py`.
`__init__.py` re-exports every pre-split name, so
`from app.services.cosmology_likelihoods import X` keeps working — and it fans
package-level monkeypatches out to the submodules (one-namespace semantics),
so tests keep patching `cl.<name>` as before.

The platform's bet is provenance + zero fabrication, not fitting power. A dataset
integration is correct when every number it can ever emit is traceable to a
checksummed public file — and when it can NEVER be silently dropped while still
being reported in `datasets_used`.

## Templates — copy the closest one, don't invent

| Shape | Template entry | Pattern |
|---|---|---|
| Full mean+cov vector (BAO-like) | `desi_dr2_bao` | fetch script + sha256 pins + `_BAO_DATA` (cosmology_likelihoods/bao.py) |
| Scalar Gaussian prior | `cchp_h0_freedman24` | one registry entry, no data file (literature_typed) |
| Big SN vector behind env flag | `des_sn5yr` / `pantheon_plus` | vendored npz + opt-in `*_FULL_CHI2_ENABLED` |
| External cobaya native likelihood | plik_lite (`planck_2018_highl_plik.TTTEEE_lite_native`) | vendored cobaya packages + adapter registry + runtime sha256 gate |

For `external:*` candidates, check `python -m cobaya install <like>` data size
FIRST: plik_lite was 1.47 MB (vendorable); full Planck clik is ~GB (not).

## Checklist

1. **Source**: real public data product (official repo / Zenodo / CobayaSampler
   bao_data), real citation (arXiv + bibcode). No hand-typed substitutes for
   released files.
2. **Fetch script** `backend/scripts/fetch_<name>.py`: download VERBATIM bytes,
   abort unless sha256 matches the pins, vendor the file under `data/`
   (committed). Copy `backend/scripts/fetch_des_sn5yr.py` or
   `fetch_desi_dr2_bao.py` as the template (the older Pantheon+ script lives
   at repo-root `scripts/fetch_pantheon_plus.py`, not backend/scripts/).
3. **Registry entry** in `_REGISTRY`
   (backend/app/services/cosmology_likelihoods/registry.py): data_products with pinned sha256,
   citations, honest fidelity grade. Set reciprocal `do_not_combine_with` for
   any overlapping sample (DR1↔DR2, CCHP↔TRGB-2019, DES↔Pantheon+/Union3).
4. **Adapter registry**: any `external:*` likelihood must be registered in
   `cobaya_adapter_registry.ADAPTER_TO_COBAYA` (real import path, or `None`
   placeholder — `test_cobaya_adapter_registry` enforces presence).
5. **Threading**: check the `_is_executable_*` gate first
   (cosmology_likelihoods/verification.py) — it is often just
   `key in _DATA`, and if the in-process predictor already handles the
   observables, wiring = vendor files + add to the data dict in the probe's
   family module (bao.py / sn.py / cc.py / rsd.py) — a ~2-file job,
   not an external-package integration.
6. **Silent-drop audit** (both lessons were publication-grade bugs):
   - Every analytic **fast-path opt-out guard** must exclude the new probe
     category. Commit `20f6274`: the BAO-only fast path silently dropped the
     1829-SN DES-SN5YR likelihood while reporting `publication_ready=True`
     and listing it in `datasets_used`.
   - Every **per-probe dispatch loop** needs a loud `else: raise` so an
     unregistered-but-gathered key fails instead of contributing χ²=0
     (commit `a503b92` mirrored this to both SN loops).
   - Unverified/corrupt data must **raise/block**, never fall back to a
     substitute vector or return 0.
7. **Publication gate**: `hash_verified` + `cov_fidelity` must feed
   `publication_ready`; an unverified file blocks the publication tier.
8. **Verify** (all of these, in order):
   - **FULL backend suite** — `./venv/bin/python3 -m pytest tests/` from
     `backend/` (the venv python — system python lacks the deps and dies in
     collection). Iterate with targeted runs **adding `--no-cov`**
     (pytest.ini carries a `--cov-fail-under` floor, so any small selection exits
     1 with a coverage FAIL even when every test passes), but gate on one
     full run before commit: cross-cutting tests like
     `test_cobaya_adapter_registry` and the manifest-consistency suite fail
     fine when run directly — the trap is that a targeted selection won't
     think to include them.
   - `backend/scripts/audit_registry.py` and
     `backend/scripts/audit_citation_pool.py` both clean.
   - Smoke fit recovering the published values (e.g. the dataset paper's
     Ωm/H0 within quoted errors, χ²/dof ≈ 1) — run `/cosmology-smoke` for the
     standing anchors.
9. **Adversarial review before commit**: multi-agent review of the diff with
   at least a data-fidelity lens and a silent-drop lens (project practice:
   4-opus adversarial workflow; it has caught a real blocker or major in
   nearly every dataset integration so far).

## Don'ts

- Don't mark a compressed/placeholder Gaussian as anything but its honest
  fidelity grade ("have data but no full likelihood" is the real gap class).
- Don't let registry/doc counts drift: stats and audits are the source of
  truth, not hardcoded numbers in docs.
- Don't combine overlapping datasets silently — `do_not_combine_with` plus the
  `_combination_warnings` guard exist for that.
