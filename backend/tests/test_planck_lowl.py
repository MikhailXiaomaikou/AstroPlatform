"""Planck 2018 low-l Commander TT + SimAll EE — completes the clik-free Planck
2018 primary likelihood stack (with planck_2018_highl_TTTEEE_lite), run via
cobaya's native low-l likelihoods over a CAMB theory spectrum (gated behind
EXTERNAL_COBAYA_ENABLED).

These tests pin the vendored data (sha256), the step-3 adapter resolver, the
A_planck-only-with-plik_lite parameter rule, the tau Gaussian-pin drop when the
real low-l EE likelihood is selected, and — when cobaya+camb are importable —
reproduce the Planck 2018 base-LCDM best-fit values straight off the vendored,
checksum-pinned data: Commander TT -2lnL = 23.44 (paper 23.4), SimAll EE
-2lnL = 395.52 (paper 395.7), arXiv:1907.12875.
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

import app.services.cosmology_likelihoods as cl
from app.services import cobaya_runner
from app.services.cobaya_adapter_registry import is_translation_pending, resolve

_TT_KEY = "planck_2018_lowl_TT"
_EE_KEY = "planck_2018_lowl_EE"
_PLIK_KEY = "planck_2018_highl_TTTEEE_lite"
_PACKAGES = pathlib.Path(cl.__file__).resolve().parents[2] / "data" / "cobaya_packages"


def _pinned_sha(key: str, role: str) -> str:
    entry = cl.get_cosmology_dataset(key)
    return next(p.sha256 for p in entry.data_products if p.role == role)


def test_lowl_vendored_data_matches_pinned_sha256():
    # Every file the runner's _CMB_PINNED_DATA names must exist in the vendored
    # dir and hash to the registry pin (role -> sha256). Walking the runner map
    # also catches a role typo between the map and the entry's data_products.
    for key in (_TT_KEY, _EE_KEY):
        for relpath, role in cobaya_runner._CMB_PINNED_DATA[key].items():
            f = _PACKAGES / relpath
            assert f.is_file(), f
            digest = hashlib.sha256(f.read_bytes()).hexdigest()
            assert digest == _pinned_sha(key, role), (key, role, relpath, digest)


def test_lowl_resolvers_are_filled_not_pending():
    for adapter, cobaya_id in (
        ("external:planck_2018_lowl.TT", "planck_2018_lowl.TT"),
        ("external:planck_2018_lowl.EE", "planck_2018_lowl.EE"),
    ):
        assert resolve(adapter) == cobaya_id
        assert is_translation_pending(adapter) is False


def test_lowl_alone_samples_cmb_set_without_a_planck():
    # A lowl-only run deliberately fixes calib=1 instead of sampling A_planck:
    # the native low-l likelihoods CAN consume it, but a 0.25% calibration
    # uncertainty is negligible against l<=29 cosmic variance.
    entries = [cl.get_cosmology_dataset(_TT_KEY), cl.get_cosmology_dataset(_EE_KEY)]
    order = cl._cobaya_parameter_order("lcdm", entries)
    assert order == ["ombh2", "omch2", "H0", "ns", "As", "tau"]
    priors = cl._sanitize_runner_priors(order, None)
    assert set(priors) == set(order)


def test_full_stack_keeps_a_planck_for_pliklite():
    entries = [
        cl.get_cosmology_dataset(_PLIK_KEY),
        cl.get_cosmology_dataset(_TT_KEY),
        cl.get_cosmology_dataset(_EE_KEY),
    ]
    order = cl._cobaya_parameter_order("lcdm", entries)
    assert order == ["ombh2", "omch2", "H0", "ns", "As", "tau", "A_planck"]


def _tau_block(yaml_text: str) -> list[str]:
    lines = yaml_text.splitlines()
    start = lines.index("  tau:")
    block = []
    for line in lines[start + 1:]:
        if not line.startswith("    "):
            break
        block.append(line.strip())
    return block


def _build_yaml(entry_keys: list[str], tmp_path: pathlib.Path) -> str:
    entries = [cl.get_cosmology_dataset(k) for k in entry_keys]
    order = cl._cobaya_parameter_order("lcdm", entries)
    priors = cl._sanitize_runner_priors(order, None)
    return cobaya_runner._build_cobaya_yaml(
        model_key="lcdm", entries=entries, prior_bounds=priors,
        parameter_order=order, sampler="evaluate",
        output_prefix=tmp_path / "run", seed=42,
    )


def test_tau_gaussian_pin_dropped_when_real_lowe_selected(tmp_path):
    # With the real SimAll EE in the run, the tau Gaussian pin would count the
    # same data twice — tau must come out flat (min/max), not dist: norm.
    yaml_text = _build_yaml([_PLIK_KEY, _TT_KEY, _EE_KEY], tmp_path)
    assert "planck_2018_lowl.EE: null" in yaml_text
    tau = _tau_block(yaml_text)
    assert any(line.startswith("min:") for line in tau), tau
    assert "dist: norm" not in tau
    # A_planck keeps its Gaussian calibration prior regardless.
    assert "dist: norm" in yaml_text


def test_tau_gaussian_pin_stays_without_lowe(tmp_path):
    # plik_lite (or lowl TT) without the EE likelihood cannot constrain tau —
    # the lowE Gaussian posterior pin must stay.
    for keys in ([_PLIK_KEY], [_PLIK_KEY, _TT_KEY]):
        tau = _tau_block(_build_yaml(keys, tmp_path))
        assert "dist: norm" in tau, (keys, tau)


def test_lowl_runtime_hash_verifies_against_vendored_dir(monkeypatch):
    # The full three-entry stack must hash-verify clean against the committed
    # vendored dir (also exercises every role mapping in _CMB_PINNED_DATA).
    monkeypatch.delenv("COBAYA_PACKAGES_PATH", raising=False)
    entries = [
        cl.get_cosmology_dataset(_PLIK_KEY),
        cl.get_cosmology_dataset(_TT_KEY),
        cl.get_cosmology_dataset(_EE_KEY),
    ]
    ver = cobaya_runner._verify_pinned_cmb_data(entries)
    assert ver is not None
    assert ver["hash_verified"] is True, ver["mismatches"]
    assert len(ver["files_sha256"]) == 12  # 6 plik_lite + 5 lowT + 1 lowE


def test_lowl_blocks_when_external_cobaya_disabled(monkeypatch):
    monkeypatch.delenv("EXTERNAL_COBAYA_ENABLED", raising=False)
    r = cl.run_likelihood_chain(
        model="lcdm", dataset_keys=[_TT_KEY, _EE_KEY], n_samples=100, random_seed=42
    )
    assert r.get("publication_ready") is not True


def test_w0wa_cmb_stack_builds_and_recovers_lcdm_at_w0_minus_one(tmp_path):
    # Regression for the w0-orphan bug: a w0wa_cdm run over the full CMB stack
    # used to die at cobaya model build ("Could not find anything to use input
    # parameter(s) {'w0'}") because cobaya/CAMB name the CPL pair (w, wa). The
    # generated YAML now samples w0 with drop:true + a dynamic `w` alias. The
    # physics lock: at (w0=-1, wa=0) the PPF model IS Lambda, so each likelihood
    # must land on its LCDM best-fit anchor.
    pytest.importorskip("cobaya")
    pytest.importorskip("camb")
    from cobaya.model import get_model
    from cobaya.yaml import yaml_load

    entries = [
        cl.get_cosmology_dataset(_PLIK_KEY),
        cl.get_cosmology_dataset(_TT_KEY),
        cl.get_cosmology_dataset(_EE_KEY),
    ]
    order = cl._cobaya_parameter_order("w0wa_cdm", entries)
    assert order[-2:] == ["w0", "wa"]
    priors = cl._sanitize_runner_priors(order, None)
    yaml_text = cobaya_runner._build_cobaya_yaml(
        model_key="w0wa_cdm", entries=entries, prior_bounds=priors,
        parameter_order=order, sampler="evaluate",
        output_prefix=tmp_path / "run", seed=42,
    )
    info = yaml_load(yaml_text)
    info.pop("output", None)
    info.pop("sampler", None)
    info["packages_path"] = str(_PACKAGES)
    model = get_model(info)  # the build itself is the regression
    point = {
        "ombh2": 0.02237, "omch2": 0.1200, "H0": 67.36, "ns": 0.9649,
        "As": 2.1e-9, "tau": 0.0544, "A_planck": 1.0, "w0": -1.0, "wa": 0.0,
    }
    by_name = dict(zip(list(model.likelihood), model.loglikes(point)[0]))
    assert 560.0 < -2.0 * by_name["planck_2018_highl_plik.TTTEEE_lite_native"] < 610.0
    assert 21.0 < -2.0 * by_name["planck_2018_lowl.TT"] < 26.0
    assert 390.0 < -2.0 * by_name["planck_2018_lowl.EE"] < 401.0


def test_mnu_models_sample_mnu_on_cmb_path():
    # Regression for the silent-mnu gap (2026-06-12): *_mnu chains over CMB
    # entries ran CAMB's fixed default neutrino mass while the result carried
    # the mnu model name — same class as the w0 orphan, but silent.
    entries = [cl.get_cosmology_dataset(_PLIK_KEY)]
    order = cl._cobaya_parameter_order("lcdm_mnu", entries)
    assert order == ["ombh2", "omch2", "H0", "ns", "As", "tau", "A_planck", "mnu"]
    order_w = cl._cobaya_parameter_order("w0wa_cdm_mnu", entries)
    assert order_w[-3:] == ["w0", "wa", "mnu"]
    priors = cl._sanitize_runner_priors(order, None)
    assert priors["mnu"] == (0.0, 5.0)
    # The compressed in-process path must NOT pick mnu up (its kernels do not
    # respond to neutrino mass) — guarded by keeping mnu out of the shared dict.
    assert "mnu" not in cl.RUNNER_PARAMETER_PRIORS


def test_mnu_cmb_chain_builds_and_likelihood_responds(tmp_path):
    # Physics lock: cobaya accepts the sampled mnu and plik_lite RESPONDS to
    # it — at the Planck best fit, mnu=0.06 eV sits on the chi2~584.5 anchor
    # while mnu=0.5 eV is catastrophically worse (live-measured -2lnL~2044).
    # A regression that silently drops mnu from the theory input would make
    # both points identical.
    pytest.importorskip("cobaya")
    pytest.importorskip("camb")
    from cobaya.model import get_model
    from cobaya.yaml import yaml_load

    entries = [cl.get_cosmology_dataset(_PLIK_KEY)]
    order = cl._cobaya_parameter_order("lcdm_mnu", entries)
    priors = cl._sanitize_runner_priors(order, None)
    yaml_text = cobaya_runner._build_cobaya_yaml(
        model_key="lcdm_mnu", entries=entries, prior_bounds=priors,
        parameter_order=order, sampler="evaluate",
        output_prefix=tmp_path / "run", seed=42,
    )
    info = yaml_load(yaml_text)
    info.pop("output", None)
    info.pop("sampler", None)
    info["packages_path"] = str(_PACKAGES)
    model = get_model(info)
    base_point = {
        "ombh2": 0.02237, "omch2": 0.1200, "H0": 67.36, "ns": 0.9649,
        "As": 2.1e-9, "tau": 0.0544, "A_planck": 1.0,
    }
    chi2_low = -2.0 * float(model.loglikes({**base_point, "mnu": 0.06})[0][0])
    chi2_high = -2.0 * float(model.loglikes({**base_point, "mnu": 0.5})[0][0])
    assert 560.0 < chi2_low < 610.0, chi2_low
    assert chi2_high > chi2_low + 100.0, (chi2_low, chi2_high)


def test_lowl_reproduces_planck_2018_bestfit_loglikes():
    # The real proof: the vendored, sha256-pinned low-l data + cobaya's native
    # likelihoods + a CAMB spectrum reproduce the Planck 2018 base-LCDM best-fit
    # values — Commander TT -2lnL = 23.44 (paper 23.4), SimAll EE -2lnL = 395.52
    # (paper 395.7). Single likelihood evaluation, no MCMC.
    pytest.importorskip("cobaya")
    pytest.importorskip("camb")
    from cobaya.model import get_model

    info = {
        "likelihood": {"planck_2018_lowl.TT": None, "planck_2018_lowl.EE": None},
        "theory": {"camb": {"extra_args": {"lens_potential_accuracy": 1}}},
        "params": {
            "ombh2": 0.02237, "omch2": 0.1200, "H0": 67.36,
            "tau": 0.0544, "ns": 0.9649, "As": 2.1e-9,
        },
        "packages_path": str(_PACKAGES),
    }
    model = get_model(info)
    loglikes = model.loglikes({})[0]
    by_name = dict(zip(list(model.likelihood), loglikes))
    chi2_tt = -2.0 * float(by_name["planck_2018_lowl.TT"])
    chi2_ee = -2.0 * float(by_name["planck_2018_lowl.EE"])
    assert 21.0 < chi2_tt < 26.0, chi2_tt
    assert 390.0 < chi2_ee < 401.0, chi2_ee
