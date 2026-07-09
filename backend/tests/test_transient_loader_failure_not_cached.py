"""P3b loader cache-poisoning regression (2026-07-07).

Six lru_cache'd vendored-data loaders (BAO, FSBAO — shared by the DR12
consensus wrapper —, grid BAO, CC diagonal, CC Moresco-2020 full-cov,
eBOSS RSD) used to cache a returned 'unverified' failure record, so ONE
transient read failure blocked the dataset until process restart. They now
use the union3 raise-inside-cache pattern (sn.py::_load_union3_raw): the
cached inner loader raises ValueError on any failure (lru_cache never
caches exceptions), and the public record builder catches it and rebuilds
the refusal fresh per call.

Each case: fail (data dir transiently unavailable) -> loud 'unverified'
record; restore dir -> the very next call heals with NO cache_clear in
between. On the pre-fix code the heal assertions fail (the poisoned record
was cached) — that is the fail-before evidence for this backlog item.

Ordering note: this file must sort AFTER the provenance-binding identity
tests (test_cc_/test_cosmology_/test_rsd_provenance_binding.py), which
assert `is` identity between import-time module globals and the cached
loader records — clearing a loader cache here creates fresh record objects
and would break those assertions if they ran later. Same pre-existing
collection-order convention as test_registry_executable_pins.py and
test_union3_full_vector.py (which also cache_clear shared loaders).
"""
from __future__ import annotations

import numpy as np
import pytest

from app.services import cosmology_likelihoods as cl

# The real vendored dir, captured before any patching.
_REAL_DIR = cl._VENDORED_COSMO_DATA_DIR
# Simulates a transient failure (e.g. unmounted volume): every vendored file
# is unreadable for as long as this dir is patched in.
_BAD_DIR = _REAL_DIR / "nonexistent-transient-failure"

# (public loader name, loader owning the cache to clear, dataset key,
# fidelity a healthy re-load must restore). sdss_6df_bao exercises the
# mixed-probe branch (its verification rides on the sha256-pinned MGS
# chi2(alpha) table, also under the vendored dir); the dr12-consensus case
# exercises the uncached delegating wrapper, whose cache lives on the shared
# fsbao loader.
CASES = [
    pytest.param("load_verified_bao_data", "load_verified_bao_data", "desi_dr2_bao", "full", id="bao-desi_dr2"),
    pytest.param("load_verified_bao_data", "load_verified_bao_data", "sdss_6df_bao", "literature_typed", id="bao-sdss_6df-mgs-table"),
    pytest.param("load_verified_fsbao_data", "load_verified_fsbao_data", "eboss_dr16_lrg_fsbao", "full", id="fsbao-eboss_lrg"),
    pytest.param("load_verified_dr12_consensus_data", "load_verified_fsbao_data", "sdss_dr12_consensus_bao", "full", id="dr12-consensus"),
    pytest.param("load_verified_grid_bao_data", "load_verified_grid_bao_data", "eboss_dr16_elg_bao", "full", id="grid-eboss_elg"),
    pytest.param("load_verified_cc_data", "load_verified_cc_data", "cosmic_chronometers", "diagonal", id="cc-ga2018"),
    pytest.param("load_verified_cc_full_cov_data", "load_verified_cc_full_cov_data", "cosmic_chronometers_moresco20", "full", id="cc-moresco20"),
    pytest.param("load_verified_rsd_data", "load_verified_rsd_data", "eboss_dr16_rsd", "diagonal", id="rsd-eboss_dr16"),
]

# Cached helpers downstream of the loaders — cleared alongside them so no
# derived state (splines built from a stale record) leaks between tests.
_CACHED_HELPERS = (
    "load_verified_mgs_prob_table",
    "_mgs_chi2_spline",
    "_elg_logprob_spline",
    "_lya_loglike_spline",
)


def _clear(loader) -> None:
    loader.cache_clear()
    for name in _CACHED_HELPERS:
        getattr(cl, name).cache_clear()


@pytest.mark.parametrize("loader_name,cache_owner_name,dataset_key,healthy_fidelity", CASES)
def test_one_transient_read_failure_is_not_cached(
    monkeypatch, loader_name, cache_owner_name, dataset_key, healthy_fidelity
):
    loader = getattr(cl, loader_name)
    cache_owner = getattr(cl, cache_owner_name)
    try:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", _BAD_DIR)
        _clear(cache_owner)
        # While failing: a loud, explicit refusal record — and the public
        # loader must NOT raise (audit/import paths need a record).
        first = loader(dataset_key)
        assert first["cov_fidelity"] == "unverified"
        assert first["hash_verified"] is False
        # A repeat call under a PERSISTENT failure stays loud — the fix must
        # not turn refusal into a silent retry that fabricates numbers.
        again = loader(dataset_key)
        assert again["cov_fidelity"] == "unverified"
        assert again["hash_verified"] is False
        # The transient failure ends: the files are back.
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", _REAL_DIR)
        # Deliberately NO cache_clear — pre-fix the poisoned record was
        # cached here, and these are the assertions that failed (RED).
        healed = loader(dataset_key)
        assert healed["cov_fidelity"] == healthy_fidelity
        assert healed["hash_verified"] is True
    finally:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", _REAL_DIR)
        _clear(cache_owner)


def test_bao_chi2_path_refuses_then_recovers(monkeypatch):
    """The real consumer path: while the record is unverified the chi2 REFUSES
    loudly (fail-safe, no wrong numbers); once the transient failure ends the
    SAME process computes a finite chi2 again — no restart, no cache_clear."""
    samples = np.array([[67.4, 0.31, 147.0]])
    order = ["H0", "omegam", "rd"]
    loader = cl.load_verified_bao_data
    try:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", _BAD_DIR)
        _clear(loader)
        with pytest.raises(ValueError, match="unverified"):
            cl._bao_chi2_samples(samples, order, key="desi_dr2_bao")
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", _REAL_DIR)
        chi2 = cl._bao_chi2_samples(samples, order, key="desi_dr2_bao")
        assert np.isfinite(chi2).all()
    finally:
        monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", _REAL_DIR)
        _clear(loader)


# ── chi2 must consume the FRESH verified record, not import snapshots ────────
# Adversarial-review follow-up (2026-07-07): the self-heal fix healed the
# per-call verification record, but _bao_chi2_samples still fitted the
# module-import _BAO_DATA snapshot — gate fresh, data stale. These pin the
# fix: poisoning the import snapshot must not change chi2, because the chi2
# paths read the same record object the gate just verified. All three FAIL
# on the pre-fix consumers (crash on None / fit garbage).

def test_bao_chi2_ignores_stale_import_snapshot(monkeypatch):
    from app.services.cosmology_likelihoods import bao as bao_mod

    samples = np.array([[67.4, 0.31, 147.0]])
    order = ["H0", "omegam", "rd"]
    baseline = bao_mod._bao_chi2_samples(samples, order, key="desi_dr2_bao")
    monkeypatch.setitem(bao_mod._BAO_DATA, "desi_dr2_bao", (None, None))
    poisoned = bao_mod._bao_chi2_samples(samples, order, key="desi_dr2_bao")
    np.testing.assert_allclose(poisoned, baseline)


def test_cc_chi2_ignores_stale_import_snapshot(monkeypatch):
    from app.services.cosmology_likelihoods import cc as cc_mod

    samples = np.array([[67.4, 0.31]])
    order = ["H0", "omegam"]
    baseline = cc_mod._cosmic_chronometer_chi2_samples(samples, order)
    monkeypatch.setattr(
        cc_mod, "COSMIC_CHRONOMETER_HZ", ((0.5, 1.0e6, 1.0),)
    )
    poisoned = cc_mod._cosmic_chronometer_chi2_samples(samples, order)
    np.testing.assert_allclose(poisoned, baseline)


def test_rsd_chi2_ignores_stale_import_snapshot(monkeypatch):
    from app.services.cosmology_likelihoods import rsd as rsd_mod

    samples = np.array([[0.31, 0.81]])
    order = ["omegam", "sigma8"]
    baseline = rsd_mod._eboss_fsigma8_chi2_samples(samples, order)
    monkeypatch.setattr(
        rsd_mod, "EBOSS_DR16_FSIGMA8", ((0.7, 1.0e6, 1.0),)
    )
    poisoned = rsd_mod._eboss_fsigma8_chi2_samples(samples, order)
    np.testing.assert_allclose(poisoned, baseline)
