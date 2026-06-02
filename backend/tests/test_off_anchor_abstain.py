"""T1-U12: off-anchor goals route to human review and never emit a conclusion.

v1 must produce NO off-anchor autonomous conclusions.  A goal not backed by a
reproduced published anchor routes to human review and carries a structured-
abstention envelope (the existing __tool_status__/__do_not_claim__ banner
vocabulary, so it renders via the existing HonestAbstentionCard — no new UI),
never a numeric result and never publication_ready.
"""
from __future__ import annotations

from app.services.cosmology_oracle import off_anchor_abstention, route_goal


def test_genuine_goal_routes_to_answer():
    # Only genuinely-reproduced goals (independent parameter recovery or
    # fit-quality) are autonomously answerable.
    assert route_goal("desi_dr1_bao_omegam")["route"] == "answer"       # independent
    assert route_goal("cc_fit_quality")["route"] == "answer"            # fit_quality


def test_consistency_goal_routes_to_human_review():
    # A compressed self-consistency anchor (the summary echoes its own input) is
    # NOT a genuine reproduction, so it must NOT be autonomously answered.
    r = route_goal("pantheon_plus_omegam")
    assert r["route"] == "human_review"
    assert r["reason"] == "consistency_only_not_independently_reproduced"


def test_off_anchor_goal_routes_to_human_review():
    r = route_goal("omega_k_curvature")
    assert r["route"] == "human_review"
    assert r["reason"] == "off_anchor_not_in_oracle_coverage"
    assert r["suggested_next_step"]


def test_off_anchor_abstention_envelope_blocks_claims():
    a = off_anchor_abstention("w0_dark_energy_eos")
    assert a["off_anchor_abstained"] is True
    assert a["publication_ready"] is False
    assert a["__do_not_claim__"] is True
    assert a["__tool_status__"] == "UNAVAILABLE"
    assert a["__message_to_model__"] and a["__suggested_next_step__"]
    # the envelope carries NO numeric conclusion to quote — a robust check (no
    # float values anywhere) rather than a brittle 'median' substring that would
    # pass if a numeric field were named anything else.
    assert not any(isinstance(v, float) for v in a.values())


def test_abstention_passes_through_genuine_goal():
    out = off_anchor_abstention("desi_dr1_bao_omegam")  # genuine independent
    assert out["route"] == "answer"
    assert not out.get("off_anchor_abstained")


def test_consistency_goal_is_abstained_not_answered():
    out = off_anchor_abstention("pantheon_plus_omegam")  # consistency self-echo
    assert out["route"] == "human_review"
    assert out["off_anchor_abstained"] is True
    assert out["publication_ready"] is False


def test_extended_models_converge_but_stay_off_anchor_review():
    """The chat tool runs extended models with allow_emcee_fallback=True, so they
    CONVERGE. The safety guard: a converged off-anchor extended model (novel w0/wa)
    is at most 'exploratory' — never publication_ready — and carries
    off_anchor_review_required, so claim_validator (posterior claims only when
    publication_ready=True) cannot let its w0/wa be quoted as a published
    conclusion. lcdm goals are anchored, so they are unaffected and can publish."""
    from app.services.cosmology_likelihoods import run_likelihood_chain

    r = run_likelihood_chain(
        model="w0wa_cdm",
        dataset_keys=["desi_dr1_bao", "pantheon_plus", "planck2018_compressed"],
        n_samples=2000, random_seed=42, allow_emcee_fallback=True,
    )
    assert r["publication_ready"] is False
    assert r["chain_tier"] != "publication"
    assert r.get("off_anchor_review_required") is True

    # lcdm (anchored Ωm/H0) is NOT off-anchor — unaffected, may still publish.
    r2 = run_likelihood_chain(model="lcdm", dataset_keys=["desi_dr1_bao"], n_samples=2000, random_seed=42)
    assert not r2.get("off_anchor_review_required")
