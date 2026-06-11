"""T1-U7: make the provenance binding self-policing.

Every probe the platform fits IN-PROCESS must read a sha256-verified vendored
file (so a future probe cannot silently run on unpinned data — the "fake
receipt" class).  The single honest exception is a MIXED probe: 6dFGS is a
hand-typed literature Gaussian with no released file, while its MGS half reads
the sha256-pinned released chi2(alpha) table (2026-06-12).  It is allowlisted
to certify 'literature_typed' (weakest half sets the grade) but its pinned
half must still verify — tampering the table makes the AUDIT dirty, not just
the runtime loud.
"""
from __future__ import annotations

import pathlib

from app.services import cosmology_likelihoods as cl


def test_every_executable_probe_reads_a_verified_pinned_file():
    issues = cl.audit_executable_pins()
    assert issues == [], "executable probes without a verified sha256 pin:\n" + "\n".join(issues)


def test_every_non_allowlisted_executable_probe_certifies_a_file_backed_fidelity():
    # The audit relies on the loader's own hash_verified/cov_fidelity (no parallel
    # role map): each non-allowlisted executable probe must verify to a file-backed
    # grade.
    for k in cl._executable_probe_keys() - cl._MIXED_LITERATURE_PLUS_PINNED_OK:
        if k in cl._BAO_DATA:
            v = cl.load_verified_bao_data(k)
        elif k in cl.EBOSS_DR16_FSBAO_EXECUTABLE_KEYS:
            v = cl.load_verified_fsbao_data(k)
        elif k == "des_sn5yr":
            v = cl.load_verified_des_sn5yr_data(k)
        elif k == "union3":
            v = cl.load_verified_union3_data(k)
        elif k in cl.COSMIC_CHRONOMETER_FULL_COV_KEYS:
            v = cl.load_verified_cc_full_cov_data(k)
        elif k in cl.COSMIC_CHRONOMETER_EXECUTABLE_KEYS:
            v = cl.load_verified_cc_data(k)
        elif k in cl.EBOSS_DR16_FSIGMA8_EXECUTABLE_KEYS:
            v = cl.load_verified_rsd_data(k)
        else:
            v = cl.load_verified_pantheon_plus_data(k)
        assert v["hash_verified"] is True, k
        assert v["cov_fidelity"] in ("full", "diagonal"), (k, v["cov_fidelity"])


def test_sdss_6df_is_the_allowlisted_mixed_probe():
    # The honest exception is explicit and narrow, and its stamp now carries
    # the MGS table's verification (2026-06-12): literature_typed grade (the
    # hand-typed 6dFGS half) WITH the verified table digest.
    assert cl._MIXED_LITERATURE_PLUS_PINNED_OK == frozenset({"sdss_6df_bao"})
    v = cl.load_verified_bao_data("sdss_6df_bao")
    assert v["cov_fidelity"] == "literature_typed"
    assert v["hash_verified"] is True
    assert v["sha256"] == cl._registry_product_sha256("sdss_6df_bao", "mgs_alpha_chi2_table")


def test_audit_flags_a_tampered_mgs_table(monkeypatch, tmp_path):
    # The 2026-06-12 review blocker: a tampered MGS table used to leave
    # audit_executable_pins clean while the runtime hard-failed.  Now the
    # audit must go dirty too.
    bad_dir = tmp_path / "sdss_6df_bao"
    bad_dir.mkdir()
    (bad_dir / "sdss_MGS_prob.txt").write_text("1.0\n" * 399)
    monkeypatch.setattr(cl, "_VENDORED_COSMO_DATA_DIR", tmp_path)
    cl.load_verified_mgs_prob_table.cache_clear()
    cl._mgs_chi2_spline.cache_clear()
    cl.load_verified_bao_data.cache_clear()
    try:
        issues = cl.audit_executable_pins()
        assert any("sdss_6df_bao" in i for i in issues), issues
    finally:
        # Restore pristine caches for other tests; the real dir verifies again.
        monkeypatch.setattr(
            cl, "_VENDORED_COSMO_DATA_DIR",
            pathlib.Path(cl.__file__).resolve().parents[2] / "data" / "cosmology",
        )
        cl.load_verified_mgs_prob_table.cache_clear()
        cl._mgs_chi2_spline.cache_clear()
        cl.load_verified_bao_data.cache_clear()


def test_audit_flags_a_probe_whose_pin_regressed(monkeypatch):
    # Simulate a regression: pantheon loses verification -> the audit must flag it.
    monkeypatch.setattr(
        cl, "load_verified_pantheon_plus_data",
        lambda key="pantheon_plus": {"cov_fidelity": "unverified", "hash_verified": False, "sha256": None},
    )
    issues = cl.audit_executable_pins()
    assert any("pantheon_plus" in i for i in issues)
