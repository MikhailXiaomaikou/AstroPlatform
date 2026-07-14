"""Compressed-path extended-model hard gate (2026-06-12 user decision: 直接拦截).

The phase-1 in-process runner is flat-geometry, massless-neutrino only — it
NEVER samples omegak or mnu. Running 'lcdm_mnu' / 'ok_*' there would only
relabel a ΛCDM-shaped chain (the silent-mislabeling fabrication class). The
gate itself dates from 2026-05-02 (commit 39dbc22) but had ZERO test coverage;
the 2026-06-12 completeness survey flagged the behavior as a suspected live
bug precisely because nothing pinned it. These tests pin:

1. Every curvature/neutrino-mass model × every in-process dataset mix →
   refused outright (blocked, NO_COMPRESSED_LIKELIHOOD, __do_not_claim__,
   zero sampled parameters).
2. The refusal points to the path where those extensions ARE genuinely
   sampled: the Planck 2018 likelihood datasets on the external Cobaya path
   of this same run_cosmology_likelihood_chain tool, gated behind
   EXTERNAL_COBAYA_ENABLED (mnu/omegak entered the CMB parameter order in
   commits c4d8bc3/08ed57c). NOT run_cobaya_cosmology — that is a
   phase-1-disabled placeholder; naming it was a review BLOCKER.
3. Both refusal branches carry the redirect: the sampling-path one (some
   selected entry is in-process executable) and the pure-compressed one.
4. Controls: flat-DE extensions (wcdm/w0wa_cdm) and plain lcdm still run.
"""
from __future__ import annotations

import pytest

from app.services.cosmology_likelihoods import run_likelihood_chain

GATED_MODELS = ["ok_lcdm", "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu", "w0wa_cdm_mnu"]


@pytest.mark.parametrize("model", GATED_MODELS)
def test_extended_model_on_compressed_path_is_refused(model):
    r = run_likelihood_chain(
        model=model,
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=800,
        random_seed=42,
    )
    assert r["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert r["chain_tier"] == "blocked"
    assert r["__do_not_claim__"] is True
    assert r["publication_ready"] is False
    assert not r.get("parameters")  # never a relabeled ΛCDM posterior
    assert r["datasets_used"] == []


def test_refusal_points_to_the_cmb_path():
    r = run_likelihood_chain(
        model="lcdm_mnu",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=800,
        random_seed=42,
    )
    msg = r["__message_to_model__"]
    # The redirect must name the real path (the Planck 2018 likelihood
    # datasets on the external Cobaya path of this same tool) AND its env
    # gate — naming a tool that cannot run them was a review BLOCKER.
    assert "planck_2018_highl_TTTEEE_lite" in msg
    assert "EXTERNAL_COBAYA_ENABLED" in msg
    assert "geometric degeneracy" in msg
    # The do-not-claim instruction must survive the message rewrite.
    assert "Do not quote posterior constraints" in msg


@pytest.mark.parametrize("dataset_keys", [["union3"], ["cosmic_chronometers"]])
def test_gate_covers_non_bao_dataset_mixes(dataset_keys):
    r = run_likelihood_chain(
        model="lcdm_mnu", dataset_keys=dataset_keys, n_samples=800, random_seed=42,
    )
    assert r["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert r["chain_tier"] == "blocked"
    assert not r.get("parameters")


def test_pure_compressed_refusal_branch_also_points_to_the_cmb_path():
    """The pure-compressed branch (no in-process-executable entry selected) is
    a SEPARATE refusal site — the 2026-06-12 re-review caught it still
    carrying the old vague wording while only the sampling-path branch got
    the honest redirect."""
    r = run_likelihood_chain(
        model="lcdm_mnu",
        dataset_keys=["planck2018_compressed"],
        n_samples=800,
        random_seed=42,
    )
    assert r["analysis_status"] == "NO_COMPRESSED_LIKELIHOOD"
    assert r["chain_tier"] == "blocked"
    msg = r["__message_to_model__"]
    assert "planck_2018_highl_TTTEEE_lite" in msg
    assert "EXTERNAL_COBAYA_ENABLED" in msg


def test_control_flat_de_extensions_still_run():
    r = run_likelihood_chain(
        model="wcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=400,
        random_seed=42,
        # The importance proposal is intentionally fail-closed when its ESS
        # collapses for the extra w dimension.  This control is about whether
        # wCDM remains executable at all, so opt into the real emcee recovery
        # path instead of expecting redacted low-ESS importance output.
        allow_emcee_fallback=True,
    )
    assert r["analysis_status"] != "NO_COMPRESSED_LIKELIHOOD"
    assert r["sampler"] == "compressed_emcee"
    assert "w" in (r.get("parameters") or {})


def test_control_lcdm_still_returns_preliminary_posterior():
    r = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["desi_dr1_bao", "planck2018_compressed"],
        n_samples=2000,
        random_seed=42,
    )
    assert r["chain_tier"] == "exploratory"
    assert r["publication_ready"] is False
    assert r["preliminary_ready"] is True
    assert "H0" in r["parameters"]
