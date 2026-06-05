"""T1-U7: make the provenance binding self-policing.

Every probe the platform fits IN-PROCESS must read a sha256-verified vendored
file (so a future probe cannot silently run on unpinned data — the "fake
receipt" class).  The single honest exception is a probe with no released data
file (a hand-typed literature compilation), which must instead certify
'literature_typed' and is explicitly allowlisted — encoding the exception as
policy rather than letting it pass silently.
"""
from __future__ import annotations

from app.services import cosmology_likelihoods as cl


def test_every_executable_probe_reads_a_verified_pinned_file():
    issues = cl.audit_executable_pins()
    assert issues == [], "executable probes without a verified sha256 pin:\n" + "\n".join(issues)


def test_every_non_allowlisted_executable_probe_certifies_a_file_backed_fidelity():
    # The audit relies on the loader's own hash_verified/cov_fidelity (no parallel
    # role map): each non-allowlisted executable probe must verify to a file-backed
    # grade.
    for k in cl._executable_probe_keys() - cl._NO_RELEASED_FILE_OK:
        if k in cl._BAO_DATA:
            v = cl.load_verified_bao_data(k)
        elif k in cl.EBOSS_DR16_FSBAO_EXECUTABLE_KEYS:
            v = cl.load_verified_fsbao_data(k)
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


def test_sdss_6df_is_the_allowlisted_no_file_probe():
    # The honest exception is explicit and narrow.
    assert cl._NO_RELEASED_FILE_OK == frozenset({"sdss_6df_bao"})
    assert cl.load_verified_bao_data("sdss_6df_bao")["cov_fidelity"] == "literature_typed"


def test_audit_flags_a_probe_whose_pin_regressed(monkeypatch):
    # Simulate a regression: pantheon loses verification -> the audit must flag it.
    monkeypatch.setattr(
        cl, "load_verified_pantheon_plus_data",
        lambda key="pantheon_plus": {"cov_fidelity": "unverified", "hash_verified": False, "sha256": None},
    )
    issues = cl.audit_executable_pins()
    assert any("pantheon_plus" in i for i in issues)
