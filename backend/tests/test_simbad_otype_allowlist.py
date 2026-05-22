"""W2 (PART W): SIMBAD otype allow-list — rvz_redshift for non-extragalactic objects
is no longer displayed as a cosmological z.

Fixes B4 Pleiades regression: otype_txt="Open (galactic) Cluster" caused the legacy
substring-matching `_is_galactic_stellar_type` to miss the classification, causing z=2.01e-05
(actually radial_velocity/c internal encoding) to be shown as a cosmological redshift.

The new `_otype_is_extragalactic` uses an allow-list — only explicit otype codes
({G, QSO, AGN, Sy1, Sy2, Bla, BCG, GrG, LSB, ...}) retain z; everything else is stripped by default.
This is safer against future SIMBAD otype additions.
"""
from __future__ import annotations

from app.connectors.simbad import _otype_is_extragalactic


# ---- allow-list basic behaviour ----


def test_otype_galactic_stellar_stripped():
    """Stellar otype values must not be allowed cosmological z."""
    assert not _otype_is_extragalactic("*")
    assert not _otype_is_extragalactic("**")  # binary star
    assert not _otype_is_extragalactic("V*")  # variable star
    assert not _otype_is_extragalactic("Cepheid")
    assert not _otype_is_extragalactic("WD*")


def test_otype_cluster_stripped():
    """Open clusters and globular clusters must not be allowed cosmological z (B4 core bug)."""
    assert not _otype_is_extragalactic("OpC")  # Pleiades
    assert not _otype_is_extragalactic("GlC")
    assert not _otype_is_extragalactic("Cl*")


def test_otype_mw_objects_stripped():
    """All Milky Way objects have z stripped (SNR / HII / Neb / PN / Psr)."""
    assert not _otype_is_extragalactic("SNR")
    assert not _otype_is_extragalactic("HII")
    assert not _otype_is_extragalactic("Neb")
    assert not _otype_is_extragalactic("PN")
    assert not _otype_is_extragalactic("Psr")
    assert not _otype_is_extragalactic("MolCld")


def test_otype_none_and_empty_stripped():
    """Empty / None / unknown otype defaults to galactic (safer)."""
    assert not _otype_is_extragalactic(None)
    assert not _otype_is_extragalactic("")
    assert not _otype_is_extragalactic("   ")
    assert not _otype_is_extragalactic("UnknownType99")


# ---- extragalactic types that allow-list should pass ----


def test_otype_galaxy_allowed():
    """Generic galaxy otype values must be allowed z."""
    assert _otype_is_extragalactic("G")
    assert _otype_is_extragalactic("GiC")
    assert _otype_is_extragalactic("GiG")
    assert _otype_is_extragalactic("GrG")
    assert _otype_is_extragalactic("ClG")


def test_otype_special_galaxy_allowed():
    """Special galaxy types (LSB / BCG / EmG / SBG / LINER) must be allowed z."""
    assert _otype_is_extragalactic("LSB")
    assert _otype_is_extragalactic("BCG")
    assert _otype_is_extragalactic("EmG")
    assert _otype_is_extragalactic("SBG")
    assert _otype_is_extragalactic("H2G")


def test_otype_agn_allowed():
    """AGN types (Sy1/2 / QSO / Bla) must be allowed z."""
    assert _otype_is_extragalactic("AGN")
    assert _otype_is_extragalactic("Sy1")
    assert _otype_is_extragalactic("Sy2")
    assert _otype_is_extragalactic("QSO")
    assert _otype_is_extragalactic("Bla")
    assert _otype_is_extragalactic("BLL")
    assert _otype_is_extragalactic("BLLac")
    assert _otype_is_extragalactic("rG")


def test_otype_lensing_allowed():
    """Gravitational lenses / GW events must be allowed z."""
    assert _otype_is_extragalactic("Lev")
    assert _otype_is_extragalactic("LeG")
    assert _otype_is_extragalactic("LeQ")
    assert _otype_is_extragalactic("grv")
    assert _otype_is_extragalactic("GWE")


def test_otype_case_sensitivity():
    """Whitespace is stripped but case is strict (SIMBAD otype codes are case-sensitive)."""
    assert _otype_is_extragalactic("QSO")
    assert not _otype_is_extragalactic("qso")  # lowercase is not a valid SIMBAD code
    assert _otype_is_extragalactic("  G  ")  # whitespace stripped
