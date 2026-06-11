"""Planck 2018 CMB lensing (CMBlikes native) — completes the clik-free Planck
2018 stack: TT/TE/EE (plik_lite) + low-l TT/EE + lensing.

Pins the vendored data (5 flat files by sha256, the two 9-file window sets by
DIRECTORY AGGREGATE digests — windows are chi2-load-bearing, the plik_lite
bweight lesson), the adapter resolver, the parameter rules (lensing consumes
the shared A_planck via planck_calib), the directory-digest mechanism itself,
and — when cobaya+camb are importable — the anchor: -2lnL = 8.82 over 9 bins
at the Planck 2018 base-LCDM best fit (chi2/dof ~ 0.98, matching the
published Planck 2018 VIII goodness of fit).
"""
from __future__ import annotations

import hashlib
import pathlib

import pytest

import app.services.cosmology_likelihoods as cl
from app.services import cobaya_runner
from app.services.cobaya_adapter_registry import is_translation_pending, resolve

_KEY = "planck_2018_lensing"
_PACKAGES = pathlib.Path(cl.__file__).resolve().parents[2] / "data" / "cobaya_packages"


def _pinned_sha(role: str) -> str:
    entry = cl.get_cosmology_dataset(_KEY)
    return next(p.sha256 for p in entry.data_products if p.role == role)


def test_lensing_vendored_data_matches_pins():
    for relpath, role in cobaya_runner._CMB_PINNED_DATA[_KEY].items():
        target = _PACKAGES / relpath
        if relpath.endswith("/"):
            assert target.is_dir(), relpath
            digest = cobaya_runner._directory_aggregate_sha256(target)
        else:
            assert target.is_file(), relpath
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        assert digest == _pinned_sha(role), (role, relpath, digest)


def test_lensing_resolver_is_filled_not_pending():
    assert resolve("external:planck_2018_lensing.native") == "planck_2018_lensing.native"
    assert is_translation_pending("external:planck_2018_lensing.native") is False


def test_lensing_samples_cmb_set_with_a_planck():
    # The lensing likelihood's params include the planck_calib defaults, so a
    # lensing-only run samples A_planck explicitly (otherwise cobaya would
    # pull it from the likelihood defaults outside our parameter_order and
    # the chain summary would silently miss a sampled axis).
    entries = [cl.get_cosmology_dataset(_KEY)]
    order = cl._cobaya_parameter_order("lcdm", entries)
    assert order == ["ombh2", "omch2", "H0", "ns", "As", "tau", "A_planck"]


def test_lensing_not_combinable_with_pr4():
    assert "planck_pr4_lensing" in cl.get_cosmology_dataset(_KEY).do_not_combine_with
    assert "planck_2018_lensing" in cl.get_cosmology_dataset("planck_pr4_lensing").do_not_combine_with


def test_directory_aggregate_digest_detects_tamper(tmp_path):
    d = tmp_path / "windows"
    d.mkdir()
    (d / "window1.dat").write_text("a")
    (d / "window2.dat").write_text("b")
    base = cobaya_runner._directory_aggregate_sha256(d)
    # Edit one file -> digest changes.
    (d / "window2.dat").write_text("c")
    assert cobaya_runner._directory_aggregate_sha256(d) != base
    # Restore content but RENAME -> digest still changes (names are hashed).
    (d / "window2.dat").write_text("b")
    assert cobaya_runner._directory_aggregate_sha256(d) == base
    (d / "window2.dat").rename(d / "window3.dat")
    assert cobaya_runner._directory_aggregate_sha256(d) != base


def test_lensing_runtime_hash_verifies_full_stack(monkeypatch):
    monkeypatch.delenv("COBAYA_PACKAGES_PATH", raising=False)
    entries = [
        cl.get_cosmology_dataset("planck_2018_highl_TTTEEE_lite"),
        cl.get_cosmology_dataset("planck_2018_lowl_TT"),
        cl.get_cosmology_dataset("planck_2018_lowl_EE"),
        cl.get_cosmology_dataset(_KEY),
    ]
    ver = cobaya_runner._verify_pinned_cmb_data(entries)
    assert ver is not None
    assert ver["hash_verified"] is True, ver["mismatches"]
    # 6 plik_lite + 5 lowT + 1 lowE + 7 lensing (5 files + 2 dir aggregates)
    assert len(ver["files_sha256"]) == 19


def test_lensing_reproduces_planck_2018_bestfit_chi2():
    pytest.importorskip("cobaya")
    pytest.importorskip("camb")
    from cobaya.model import get_model

    info = {
        "likelihood": {"planck_2018_lensing.native": None},
        "theory": {"camb": {"extra_args": {"lens_potential_accuracy": 1}}},
        "params": {
            "ombh2": 0.02237, "omch2": 0.1200, "H0": 67.36, "ns": 0.9649,
            "As": 2.1e-9, "tau": 0.0544, "A_planck": 1.0,
        },
        "packages_path": str(_PACKAGES),
    }
    model = get_model(info)
    chi2 = -2.0 * float(model.loglikes({})[0][0])
    assert 5.0 < chi2 < 14.0, chi2  # 9 bins, live-measured 8.82
