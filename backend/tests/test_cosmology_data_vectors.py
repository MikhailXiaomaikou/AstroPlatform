"""Integrity guardrail for the hand-typed cosmology data vectors (Tier 1.2).

The DESI DR1 BAO, cosmic-chronometer H(z), and eBOSS DR16 RSD fσ8 vectors are
hard-coded Python literals in cosmology_likelihoods.py (transcribed from the
cited published tables). The science benchmarks only bound a *reduced χ²* to a
wide band, so a single fat-fingered digit (a future merge conflict, a careless
edit) could silently bias every χ² without going red.

This test pins each published vector element-wise / by checksum + structural
invariants, so any drift in a value, length, or ordering fails CI immediately
and forces a deliberate, source-cited update. It does NOT re-derive the physics
(the benchmarks + the PDF-sourced values do that) — it is purely a freeze on the
transcribed numbers. If you legitimately update a vector, update the pinned hash
here in the same commit, citing the source.
"""
from __future__ import annotations

import hashlib


def _checksum(obj: object) -> str:
    return hashlib.sha256(repr(obj).encode()).hexdigest()[:16]


def test_eboss_dr16_fsigma8_vector_pinned() -> None:
    """eBOSS DR16 RSD-only fσ8 — Alam et al. 2021 (arXiv:2007.08991) Table III,
    RSD-Only column, 6 SDSS bins (MGS / BOSS×2 / eBOSS LRG·ELG·QSO). Lyα at
    z=2.33 reports no fσ8; 6dFGS is not in the SDSS-only consensus."""
    from app.services.cosmology_likelihoods import EBOSS_DR16_FSIGMA8

    assert EBOSS_DR16_FSIGMA8 == (
        (0.15, 0.53, 0.16),
        (0.38, 0.500, 0.047),
        (0.51, 0.455, 0.039),
        (0.70, 0.448, 0.043),
        (0.85, 0.315, 0.095),
        (1.48, 0.462, 0.045),
    )
    assert _checksum(EBOSS_DR16_FSIGMA8) == "6b9d8c5be84ce0d9"


def test_cosmic_chronometer_hz_vector_pinned() -> None:
    """Cosmic-chronometer H(z) — Gómez-Valent & Amendola 2018 (arXiv:1802.01505)
    Table 1, 31 differential-age points, z ∈ [0.07, 1.965]."""
    from app.services.cosmology_likelihoods import COSMIC_CHRONOMETER_HZ

    assert len(COSMIC_CHRONOMETER_HZ) == 31
    zs = [row[0] for row in COSMIC_CHRONOMETER_HZ]
    assert zs == sorted(zs), "CC redshifts must stay monotonically increasing"
    assert zs[0] == 0.07 and zs[-1] == 1.965
    # every H(z) and σ positive and physically sane (40–250 km/s/Mpc over z≤2)
    assert all(40.0 < h < 260.0 and 0.0 < s < 80.0 for _z, h, s in COSMIC_CHRONOMETER_HZ)
    assert _checksum(COSMIC_CHRONOMETER_HZ) == "a2b5ae6c99f0b98b"


def test_desi_dr1_bao_vectors_pinned() -> None:
    """DESI DR1 BAO — 12-element mean vector + 12×12 block covariance
    (arXiv:2404.03002, ALL_GCcomb), transcribed literals."""
    from app.services.cosmology_likelihoods import (
        DESI_DR1_BAO_MEAN_VECTOR,
        DESI_DR1_BAO_COVARIANCE,
    )

    assert len(DESI_DR1_BAO_MEAN_VECTOR) == 12
    assert len(DESI_DR1_BAO_COVARIANCE) == 12
    assert all(len(row) == 12 for row in DESI_DR1_BAO_COVARIANCE)
    assert _checksum(DESI_DR1_BAO_MEAN_VECTOR) == "b5a2f522c95970ee"
    assert _checksum(DESI_DR1_BAO_COVARIANCE) == "631649139045cbea"
