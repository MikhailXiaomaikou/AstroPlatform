"""compute_model_comparison cross-representation validity (2026-06-11).

planck2018_compressed is a model-DEPENDENT compressed representation: extended
flat-DE chains swap its diagonal ΛCDM posterior summary for the Chen-Huang-Wang
(R, l_A, ombh2) distance prior, which adds an ombh2 sampled axis (the batch-2
fix that stopped Planck silently contributing zero chi2 to wCDM). An lcdm-vs-
wcdm pair on that selection therefore compares chi2 against two DIFFERENT
likelihoods — the deltas are not a model comparison. Locks:

1. Mismatched-representation pair → comparison_valid=False, preferred=
   "undetermined", warning naming the offending axes (fail-closed, no
   confidently-wrong AIC verdict).
2. Same-likelihood pair (DESI BAO only — model-invariant) → comparison_valid=
   True, exactly 1 extra param (w), ΛCDM not disfavored at w≈-1.
"""
from __future__ import annotations

from app.services.cosmology_likelihoods import (
    compute_model_comparison,
    run_likelihood_chain,
)


def test_cross_representation_pair_is_flagged_invalid():
    ds = ["desi_dr1_bao", "planck2018_compressed"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    wcdm = run_likelihood_chain(model="wcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    # Precondition for the test to be meaningful: the wcdm chain really does
    # sample the extra ombh2 axis (distance-prior representation).
    assert "ombh2" in (wcdm.get("parameters") or {})
    assert "ombh2" not in (lcdm.get("parameters") or {})

    cmp = compute_model_comparison(lcdm, wcdm)
    assert cmp["comparison_valid"] is False
    assert cmp["preferred"] == "undetermined"
    assert "ombh2" in cmp["comparison_warning"]
    # The factual deltas stay reported (they are real numbers from real fits);
    # only the verdict is withheld.
    assert cmp["delta_chi2"] is not None


def test_same_likelihood_pair_is_valid_with_one_extra_param():
    ds = ["desi_dr1_bao"]
    lcdm = run_likelihood_chain(model="lcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    wcdm = run_likelihood_chain(model="wcdm", dataset_keys=ds, n_samples=400, random_seed=42)
    cmp = compute_model_comparison(lcdm, wcdm)
    assert cmp["comparison_valid"] is True
    assert "comparison_warning" not in cmp
    assert cmp["n_extra_params"] == 1
    assert cmp["preferred"] in {"lcdm", "inconclusive"}
