"""ACT DR6 CMB lensing — real bandpower likelihood integration (2026-07-07).

The act_dr6_lensing registry entry used to be ONLY a hand-typed compressed
Gaussian of the ACT+Planck joint summary. This integration wires the real
thing up to the honest boundary:

- pure-Python `act_dr6_lenslike` package pip-installed (requirements.txt),
  adapter `external:act_dr6_lenslike.ACTDR6LensLike` FILLED;
- the act_baseline lens_only data subset (bandpowers + binning matrix +
  CMB-marginalized covariance + the unconditionally-loaded covmat_act.txt)
  plus the package's fiducial test spectra, extracted from the official NASA
  LAMBDA tarball by scripts/fetch_act_dr6_lenslike.py, vendored under
  data/cobaya_packages/data/ACT_dr6_likelihood/v1.2 and sha256-pinned in the
  registry entry;
- verification gate `load_verified_act_dr6_lenslike_data` (cmb.py);
- anchor: the package's OWN reference value — act_baseline lens_only
  chi2 = 14.06 at the bundled fiducial spectra (act_dr6_lenslike
  tests/test_act.py) — reproduced against OUR vendored files.

NOT yet done (deliberate, tracked in plan/cosmology-completion-backlog.md):
execution_mode stays "compressed_gaussian" until the cobaya_runner
runtime-hash gate (_CMB_PINNED_DATA) and YAML wiring (variant/lens_only)
exist — that file is owned by another workstream. A test below pins this
boundary so flipping it without the gate fails loudly.
"""
from __future__ import annotations

import shutil

import numpy as np
import pytest

import app.services.cosmology_likelihoods as cl
from app.services.cobaya_adapter_registry import is_translation_pending, resolve
from app.services.cosmology_likelihoods import cmb as cmb_mod
from app.services.cosmology_likelihoods.cmb import (
    ACT_DR6_LENSLIKE_DATA_DIR,
    ACT_DR6_LENSLIKE_FILES,
    load_verified_act_dr6_lenslike_data,
)

_KEY = "act_dr6_lensing"

_DATA_PRESENT = all(
    (ACT_DR6_LENSLIKE_DATA_DIR / name).is_file()
    for name in ACT_DR6_LENSLIKE_FILES.values()
)
needs_data = pytest.mark.skipif(
    not _DATA_PRESENT,
    reason="vendored ACT DR6 lensing data missing — run scripts/fetch_act_dr6_lenslike.py",
)


# ── Adapter + package ────────────────────────────────────────────────────────

def test_adapter_resolver_is_filled_not_pending():
    assert resolve("external:act_dr6_lenslike.ACTDR6LensLike") == "act_dr6_lenslike.ACTDR6LensLike"
    assert is_translation_pending("external:act_dr6_lenslike.ACTDR6LensLike") is False


def test_package_importable_with_cobaya_class():
    act = pytest.importorskip("act_dr6_lenslike")
    assert hasattr(act, "ACTDR6LensLike")
    assert hasattr(act, "load_data") and hasattr(act, "generic_lnlike")


# ── Registry pins ────────────────────────────────────────────────────────────

def test_registry_pins_cover_the_loaded_file_set():
    entry = cl.get_cosmology_dataset(_KEY)
    pins = {p.role: p.sha256 for p in entry.data_products if p.sha256}
    for role in ACT_DR6_LENSLIKE_FILES:
        assert pins.get(role), f"missing sha256 pin for role {role}"
    # The source tarball itself is pinned (not vendored — 345 MB).
    assert pins.get("source_tarball"), "missing sha256 pin for the LAMBDA tarball"
    # Fiducial test spectra pinned too (the chi2 anchor's theory input).
    assert pins.get("fiducial_lensed_cls")
    assert pins.get("fiducial_lenspotential_cls")


# ── Verification gate ────────────────────────────────────────────────────────

@needs_data
def test_vendored_data_matches_registry_pins():
    verified = load_verified_act_dr6_lenslike_data()
    assert verified["issues"] == []
    assert verified["hash_verified"] is True
    assert verified["cov_fidelity"] == "full"
    assert set(verified["files_sha256"]) == set(ACT_DR6_LENSLIKE_FILES.values())


@needs_data
def test_verification_gate_flags_tampered_file(tmp_path, monkeypatch):
    tampered = tmp_path / "v1.2"
    tampered.mkdir()
    for name in ACT_DR6_LENSLIKE_FILES.values():
        dst = tampered / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ACT_DR6_LENSLIKE_DATA_DIR / name, dst)
    # Flip one byte in the covariance — the gate must go unverified, never
    # silently substitute or accept.
    cov_path = tampered / ACT_DR6_LENSLIKE_FILES["covariance_cmbmarg"]
    cov_path.write_bytes(cov_path.read_bytes() + b"\n# tampered\n")
    monkeypatch.setattr(cmb_mod, "ACT_DR6_LENSLIKE_DATA_DIR", tampered)
    verified = load_verified_act_dr6_lenslike_data()
    assert verified["hash_verified"] is False
    assert verified["cov_fidelity"] == "unverified"
    assert any("covariance_cmbmarg" in issue for issue in verified["issues"])


def test_verification_gate_reports_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(cmb_mod, "ACT_DR6_LENSLIKE_DATA_DIR", tmp_path / "empty")
    verified = load_verified_act_dr6_lenslike_data()
    assert verified["hash_verified"] is False
    assert verified["cov_fidelity"] == "unverified"
    assert len(verified["issues"]) == len(ACT_DR6_LENSLIKE_FILES)


# ── Anchor: package's own reference chi2 on OUR vendored bytes ───────────────

@needs_data
def test_act_baseline_lens_only_chi2_reproduces_package_anchor():
    """act_dr6_lenslike's own test suite pins act_baseline lens_only
    chi2 = 14.06 (assertAlmostEqual places=1) at the bundled fiducial
    cosmo2017 spectra. Reproduce it against the sha256-verified vendored
    subset — proving the vendored bytes ARE the released likelihood inputs,
    not lookalikes."""
    act = pytest.importorskip("act_dr6_lenslike")
    verified = load_verified_act_dr6_lenslike_data()
    assert verified["hash_verified"], verified["issues"]
    ddir = str(ACT_DR6_LENSLIKE_DATA_DIR)

    ell, cl_tt, cl_ee, cl_bb, cl_te = np.loadtxt(
        f"{ddir}/like_corrs/cosmo2017_10K_acc3_lensedCls.dat", unpack=True
    )
    ellp = np.loadtxt(
        f"{ddir}/like_corrs/cosmo2017_10K_acc3_lenspotentialCls.dat",
        unpack=True, usecols=[0],
    )
    cl_pp = np.loadtxt(
        f"{ddir}/like_corrs/cosmo2017_10K_acc3_lenspotentialCls.dat",
        unpack=True, usecols=[5],
    )
    prefac = 2 * np.pi / ell / (ell + 1.0)
    cl_kk = cl_pp / 4 * 2 * np.pi
    data = act.load_data(
        "act_baseline", ddir=ddir, lens_only=True, like_corrections=False
    )
    chi2 = -2 * act.generic_lnlike(
        data, ellp, cl_kk, ell,
        cl_tt * prefac, cl_ee * prefac, cl_te * prefac, cl_bb * prefac,
        trim_lmax=2998,
    )
    assert abs(chi2 - 14.06) < 0.05, chi2


@needs_data
def test_lens_only_covariance_is_positive_definite():
    act = pytest.importorskip("act_dr6_lenslike")
    data = act.load_data(
        "act_baseline", ddir=str(ACT_DR6_LENSLIKE_DATA_DIR),
        lens_only=True, like_corrections=False,
    )
    np.linalg.cholesky(data["cov"])
    assert np.all(np.isfinite(data["data_binned_clkk"]))


# ── Honest boundary: compressed execution unchanged until the runner gate ────

def test_entry_still_executes_compressed_until_runner_gate_exists():
    """Flipping execution_mode to external_cobaya without the cobaya_runner
    runtime-hash-gate entry would let an unpinned run through — this pin
    fails loudly if someone flips it before wiring the gate (see module
    docstring)."""
    entry = cl.get_cosmology_dataset(_KEY)
    assert entry.execution_mode == "compressed_gaussian"
    assert entry.cobaya_likelihood == "external:act_dr6_lenslike.ACTDR6LensLike"
    from app.services import cobaya_runner
    if entry.key in getattr(cobaya_runner, "_CMB_PINNED_DATA", {}):
        pytest.fail(
            "cobaya_runner._CMB_PINNED_DATA now covers act_dr6_lensing — "
            "revisit this boundary pin: execution_mode may be upgraded to "
            "external_cobaya once the YAML wiring (variant/lens_only, "
            "packages_path) ships."
        )


def test_gate_covers_every_registry_pinned_vendored_file():
    """Adversarial-review follow-up (2026-07-07): the gate's 'EVERY pinned
    file' contract must be mechanically true — the role->filename dict the
    gate iterates has to match exactly the registry data_products that are
    vendored (local_path) AND pinned (sha256). A new pinned product added to
    the registry without a gate entry fails here instead of silently
    shrinking the verification surface."""
    entry = cl.get_cosmology_dataset(_KEY)
    vendored_pinned_roles = {
        p.role for p in entry.data_products if p.local_path and p.sha256
    }
    assert vendored_pinned_roles == set(ACT_DR6_LENSLIKE_FILES)


@pytest.mark.skipif(not _DATA_PRESENT, reason="vendored ACT DR6 files absent")
def test_gate_hashes_the_fiducial_spectra_too():
    """The fiducial spectra are chi2 anchor inputs; the gate must include
    them in files_sha256 (pre-fix it silently checked only 4 of 6 files)."""
    record = load_verified_act_dr6_lenslike_data()
    assert record["hash_verified"] is True, record["issues"]
    hashed = set(record["files_sha256"])
    assert "like_corrs/cosmo2017_10K_acc3_lensedCls.dat" in hashed
    assert "like_corrs/cosmo2017_10K_acc3_lenspotentialCls.dat" in hashed
