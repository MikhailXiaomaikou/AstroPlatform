"""T1-U9: a single, sourced table of published cosmology anchors.

The platform's "right answers" — published parameter constraints it can reproduce
in-process — were scattered across benchmark bands and module constants.  This
centralizes them as one frozen, citable table so the reproduce-anchor harness
(T1-U10) and the oracle-coverage measurement (T1-U11) share a single basis.

Each anchor records the published central value, an absolute tolerance, the
datasets + model that reproduce it, and the arXiv identifier of the source.  We
store arXiv ids (which match the registry DatasetCitation entries) rather than
hand-typed bibcodes to avoid introducing an unverifiable citation string.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OracleAnchor:
    goal_key: str            # stable id, e.g. "desi_dr1_bao_omegam"
    parameter: str           # the posterior parameter the harness reads
    value: float             # published central value
    tol: float               # absolute tolerance for "reproduced"
    datasets: tuple[str, ...]
    model: str
    source_arxiv: str        # arXiv id of the source paper
    source_label: str


# Anchors seeded ONLY from constraints the platform already reproduces in-process
# (each has a backing benchmark or provenance test).  SN/CMB values are kept in
# sync with the live registry compressed means by test_oracle_values_match_live_
# registry_constants, so a registry typo breaks the oracle test instead of
# silently shifting the "right answer".
PUBLISHED_ANCHORS: tuple[OracleAnchor, ...] = (
    OracleAnchor(
        "desi_dr1_bao_omegam", "omegam", 0.295, 0.02, ("desi_dr1_bao",), "lcdm",
        "2404.03002", "DESI DR1 BAO flat-ΛCDM Ωm (Adame et al. 2024)",
    ),
    OracleAnchor(
        "pantheon_plus_omegam", "omegam", 0.334, 0.05, ("pantheon_plus",), "lcdm",
        "2202.04077", "Pantheon+SH0ES SN Ωm (Brout et al. 2022)",
    ),
    OracleAnchor(
        "pantheon_plus_h0", "H0", 73.04, 3.0, ("pantheon_plus",), "lcdm",
        "2112.04510", "SH0ES SN-calibrated H0 (Riess et al. 2022)",
    ),
    OracleAnchor(
        "des_sn5yr_omegam", "omegam", 0.352, 0.03, ("des_sn5yr",), "lcdm",
        "2401.02929", "DES-SN5YR SN-only Ωm (Abbott et al. 2024)",
    ),
    OracleAnchor(
        "union3_omegam", "omegam", 0.356, 0.05, ("union3",), "lcdm",
        "2311.12098", "Union3 SN-only Ωm (Rubin et al. 2023)",
    ),
    OracleAnchor(
        "planck2018_h0", "H0", 67.36, 1.0, ("planck2018_compressed",), "lcdm",
        "1807.06209", "Planck 2018 CMB-only H0 (Planck Collaboration 2020)",
    ),
    OracleAnchor(
        "planck2018_omegam", "omegam", 0.3153, 0.02, ("planck2018_compressed",), "lcdm",
        "1807.06209", "Planck 2018 CMB-only Ωm (Planck Collaboration 2020)",
    ),
)


def get_anchor(goal_key: str) -> OracleAnchor | None:
    """Return the anchor for a goal key, or None if it is off-anchor."""
    return next((a for a in PUBLISHED_ANCHORS if a.goal_key == goal_key), None)
