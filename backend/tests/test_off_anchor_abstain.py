"""T1-U12: off-anchor goals route to human review and never emit a conclusion.

v1 must produce NO off-anchor autonomous conclusions.  A goal not backed by a
reproduced published anchor routes to human review and carries a structured-
abstention envelope (the existing __tool_status__/__do_not_claim__ banner
vocabulary, so it renders via the existing HonestAbstentionCard — no new UI),
never a numeric result and never publication_ready.
"""
from __future__ import annotations

import json

from app.services.cosmology_oracle import off_anchor_abstention, route_goal


def test_covered_goal_routes_to_answer():
    assert route_goal("desi_dr1_bao_omegam")["route"] == "answer"


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
    # the envelope carries NO numeric conclusion to quote
    assert "median" not in json.dumps(a)


def test_off_anchor_abstention_passes_through_covered_goal():
    out = off_anchor_abstention("pantheon_plus_omegam")
    assert out["route"] == "answer"
    assert not out.get("off_anchor_abstained")
