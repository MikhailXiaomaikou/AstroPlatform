"""Planck 2018 high-l plik_lite TT/TE/EE — the first PRIMARY (non-compressed) CMB
likelihood in the registry, run via cobaya's clik-free *native* plik_lite over a
CAMB theory spectrum (gated behind EXTERNAL_COBAYA_ENABLED).

These tests pin the vendored data (sha256), the step-3 adapter resolver, the CMB
parameter set, the off-gate publication block, and — when cobaya+camb are
importable — reproduce the Planck 2018 base-LCDM best-fit chi2 (~584.5 / dof~0.96)
straight off the vendored, checksum-pinned data.
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

import app.services.cosmology_likelihoods as cl
from app.services.cobaya_adapter_registry import is_translation_pending, resolve

_KEY = "planck_2018_highl_TTTEEE_lite"
_ADAPTER = "external:planck_2018_highl_plik.TTTEEE_lite_native"
_PACKAGES = pathlib.Path(cl.__file__).resolve().parents[2] / "data" / "cobaya_packages"
_VENDORED = _PACKAGES / "data" / "planck_2018_pliklite_native"


def _pinned_sha(role: str) -> str:
    entry = cl.get_cosmology_dataset(_KEY)
    return next(p.sha256 for p in entry.data_products if p.role == role)


def test_pliklite_vendored_data_matches_pinned_sha256():
    for role, fname in (
        ("measurement_vector", "cl_cmb_plik_v22.dat"),
        ("covariance", "c_matrix_plik_v22.dat"),
    ):
        f = _VENDORED / fname
        assert f.is_file(), f
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        assert digest == _pinned_sha(role), (role, fname, digest)


def test_pliklite_resolver_is_filled_not_pending():
    # The step-3 resolver maps the adapter to the real cobaya likelihood id.
    assert resolve(_ADAPTER) == "planck_2018_highl_plik.TTTEEE_lite_native"
    assert is_translation_pending(_ADAPTER) is False


def test_pliklite_cobaya_parameter_order_is_the_cmb_set():
    entries = [cl.get_cosmology_dataset(_KEY)]
    order = cl._cobaya_parameter_order("lcdm", entries)
    assert order == ["ombh2", "omch2", "H0", "ns", "As", "tau", "A_planck"]
    # every CMB param must have runner prior bounds, else the YAML can't be built
    priors = cl._sanitize_runner_priors(order, None)
    assert set(priors) == set(order)


def test_pliklite_blocks_when_external_cobaya_disabled(monkeypatch):
    # Gate OFF (default): the CMB entry carries no compressed_likelihood, so it
    # cannot run in-process and must NOT come back publication-ready.
    monkeypatch.delenv("EXTERNAL_COBAYA_ENABLED", raising=False)
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=[_KEY], n_samples=100, random_seed=42
    )
    assert r.get("publication_ready") is not True


def test_pliklite_reproduces_planck_2018_bestfit_chi2():
    # The real proof: the vendored, sha256-pinned plik_lite data + cobaya's native
    # likelihood + a CAMB spectrum reproduce the Planck 2018 base-LCDM best-fit
    # chi2 (613 bandpowers, chi2/dof ~ 0.96). Bypasses the MCMC runner — this is a
    # single likelihood evaluation, robust and fast (~0.3 s).
    pytest.importorskip("cobaya")
    pytest.importorskip("camb")
    from cobaya.model import get_model

    info = {
        "likelihood": {"planck_2018_highl_plik.TTTEEE_lite_native": None},
        "theory": {"camb": {"extra_args": {"lens_potential_accuracy": 1}}},
        "params": {
            "ombh2": 0.02237, "omch2": 0.1200, "H0": 67.36,
            "tau": 0.0544, "ns": 0.9649, "As": 2.1e-9, "A_planck": 1.0,
        },
        "packages_path": str(_PACKAGES),
    }
    model = get_model(info)
    chi2 = -2.0 * float(sum(model.loglikes({})[0]))
    assert 560.0 < chi2 < 610.0, chi2


def test_bbn_only_stays_publication_blocked():
    # Regression: the CMB sampled params (ombh2/ns/As/tau/A_planck) must live in
    # CMB_PARAMETER_PRIORS, NOT the shared RUNNER_PARAMETER_PRIORS — otherwise
    # _compressed_parameter_order picks up ombh2 and the BBN-only compressed
    # selection (spec == ('ombh2',)) silently flips from publication-BLOCKED to
    # publication-ready (a faithful prior echo, not a real fit).
    assert "ombh2" not in cl.RUNNER_PARAMETER_PRIORS
    assert "ombh2" in cl.CMB_PARAMETER_PRIORS
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=["bbn_ombh2_schoeneberg24"], n_samples=200, random_seed=42
    )
    assert r.get("publication_ready") is not True


def test_pliklite_runtime_sha256_mismatch_blocks(monkeypatch, tmp_path):
    # The CMB run must REFUSE to fit (and never stamp the official pins) when the
    # on-disk data at the resolved COBAYA_PACKAGES_PATH does not match the registry
    # pins — a redirect or post-deploy edit. Verified at the gate (no subprocess).
    from app.services import cobaya_runner

    monkeypatch.setenv("COBAYA_PACKAGES_PATH", str(tmp_path))  # empty dir -> files missing
    entries = [cl.get_cosmology_dataset(_KEY)]
    ver = cobaya_runner._verify_pinned_cmb_data(entries)
    assert ver is not None and ver["hash_verified"] is False and ver["mismatches"]

    order = cl._cobaya_parameter_order("lcdm", entries)
    priors = cl._sanitize_runner_priors(order, None)
    out = cobaya_runner.dispatch_external_cobaya(
        model_key="lcdm", entries=entries, prior_bounds=priors,
        parameter_order=order, seed=42, sample_count=1,
    )
    assert out.get("publication_ready") is not True
    assert out.get("error_class") == "cobaya_data_sha256_mismatch"
