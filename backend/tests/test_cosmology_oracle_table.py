"""T1-U9: the published-anchor oracle table is well-formed and drift-guarded."""
from __future__ import annotations

import re

from app.services.cosmology_likelihoods import get_cosmology_dataset
from app.services.cosmology_oracle import PUBLISHED_ANCHORS, get_anchor

_ARXIV = re.compile(r"^\d{4}\.\d{4,5}$")


def test_oracle_table_nonempty_and_well_formed():
    assert PUBLISHED_ANCHORS, "oracle table is empty"
    for a in PUBLISHED_ANCHORS:
        assert a.goal_key and a.parameter and a.model
        assert a.tol > 0, f"{a.goal_key} has non-positive tolerance"
        assert a.datasets, f"{a.goal_key} has no datasets"
        assert _ARXIV.match(a.source_arxiv), f"{a.goal_key} source_arxiv {a.source_arxiv!r} not an arXiv id"
        assert a.source_label


def test_goal_keys_are_unique():
    keys = [a.goal_key for a in PUBLISHED_ANCHORS]
    assert len(keys) == len(set(keys))


def test_oracle_values_match_live_registry_constants():
    """Drift guard: SN/CMB anchor values must equal the live registry compressed
    means, so a registry typo breaks this test instead of silently shifting the
    'right answer' the harness checks against."""
    def mean(key, param):
        spec = get_cosmology_dataset(key).compressed_likelihood
        return spec.mean[list(spec.parameters).index(param)]

    assert get_anchor("pantheon_plus_omegam").value == mean("pantheon_plus", "omegam")
    assert get_anchor("pantheon_plus_h0").value == mean("pantheon_plus", "H0")
    assert get_anchor("des_sn5yr_omegam").value == mean("des_sn5yr", "omegam")
    assert get_anchor("union3_omegam").value == mean("union3", "omegam")
    assert get_anchor("planck2018_h0").value == mean("planck2018_compressed", "H0")
    assert get_anchor("planck2018_omegam").value == mean("planck2018_compressed", "omegam")


def test_get_anchor_returns_none_for_off_anchor_goal():
    assert get_anchor("w0wa_dark_energy_eos") is None


# ── T1-U13: anchors are classified independent (genuine reproduction) vs
# consistency (a compressed summary recovers its own input). ──

def test_every_anchor_is_classified_independent_or_consistency():
    for a in PUBLISHED_ANCHORS:
        assert a.independence in ("independent", "consistency"), a.goal_key


def test_compressed_anchors_are_consistency_desi_is_independent():
    by_key = {a.goal_key: a.independence for a in PUBLISHED_ANCHORS}
    assert by_key["desi_dr1_bao_omegam"] == "independent"
    for k in (
        "pantheon_plus_omegam", "pantheon_plus_h0", "des_sn5yr_omegam",
        "union3_omegam", "planck2018_h0", "planck2018_omegam",
    ):
        assert by_key[k] == "consistency", k


def test_desi_cmb_joint_is_an_independent_anchor():
    # T1-U14: DESI BAO + Planck CMB combination genuinely recovers Ωm between the
    # two inputs (a real joint fit, not echoing one input).
    a = get_anchor("desi_cmb_omegam")
    assert a is not None
    assert a.independence == "independent"
    assert a.datasets == ("desi_dr1_bao", "planck2018_compressed")
    assert a.value == 0.307  # DESI 2024 VI BAO+CMB flat-ΛCDM
