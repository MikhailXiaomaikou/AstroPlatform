"""Golden rigor tests (Phase 2 / R6) — cheap, deterministic scenarios
that wire the zero-fabrication gate + provenance envelope end-to-end
against canned tool_result fixtures.

These are intentionally NOT real-archive integration tests (those live
under ``-m integration`` / ``-m slow`` and hit the network).  They prove
the *rigor pipeline* — validator + regen loop + envelope + sanity — does
the right thing on controlled inputs.  Any regression in the rigor gate
shows up immediately here, in every CI run.

Scenarios:
1. Pleiades CMD — AI reply cites age 125 Myr and DM 5.67 (matching
   fixture)  → validator accepts, no regeneration.
2. M31 redshift drift — AI reply cites z = 0.999 (fabricated) →
   validator flags, regeneration is triggered.
3. Crab pulsar spin-down — reply cites values matching fixture →
   validator accepts.
"""

from __future__ import annotations

from app.services.claim_validator import validate_claims
from app.services.result_provenance import normalize_tool_result


# ------------------------------------------------------------------
# 1. Pleiades
# ------------------------------------------------------------------

def test_golden_pleiades_reply_is_fully_cited():
    tool_results = [
        {
            "tool": "fit_isochrone",
            "input": {"age_range": [7.0, 9.0]},
            "result": {
                "best_age_myr": 125.0,
                "best_dm": 5.67,
                "best_av": 0.12,
                "n_members": 842,
            },
        },
    ]
    reply = (
        "Based on the Gaia DR3 members, the Pleiades has age 125 Myr with "
        "DM = 5.67 and A_V = 0.12.  The fit used 842 candidate members."
    )
    r = validate_claims(reply, tool_results)
    assert r.ok, f"Pleiades reply should pass citation check; uncited={r.uncited}"


# ------------------------------------------------------------------
# 2. M31 redshift fabrication
# ------------------------------------------------------------------

def test_golden_m31_fabricated_redshift_is_blocked():
    tool_results = [
        {
            "tool": "get_object_info",
            "input": {"name": "M31"},
            "result": {
                "name": "M31",
                "ra": 10.68,
                "dec": 41.27,
                "rvz_redshift": -0.001,
            },
        },
    ]
    reply = "M31 is at RA = 10.68, Dec = 41.27 and has z = 0.999."
    r = validate_claims(reply, tool_results)
    assert not r.ok
    assert any(c.label in {"redshift_z", "redshift_word"} for c in r.uncited), (
        f"Expected the fabricated z to be flagged; got {[c.label for c in r.uncited]}"
    )


# ------------------------------------------------------------------
# 3. Crab pulsar
# ------------------------------------------------------------------

def test_golden_crab_cited_values_pass():
    tool_results = [{
        "tool": "pulsar_derived_quantities",
        "input": {"name": "J0534+2200"},
        "result": {
            "period_days": 0.033,  # crab P~33ms
            "spindown_luminosity_erg_s": 4.5e38,
        },
    }]
    reply = "The Crab has period 0.033 days and a spin-down luminosity around 4.5e38."
    r = validate_claims(reply, tool_results)
    assert r.ok, f"Crab reply should cite: uncited={r.uncited}"


# ------------------------------------------------------------------
# End-to-end provenance: envelope + sanity warnings
# ------------------------------------------------------------------

def test_golden_provenance_envelope_has_run_id_and_version():
    """Every tool result must carry a reproducibility envelope after normalize."""
    out = normalize_tool_result("search_objects", {"results": []}, tool_input={"q": "M31"})
    env = out["reproducibility"]
    assert "run_id" in env and len(env["run_id"]) >= 32
    assert "tool_version" in env
    assert "query_hash" in env
    assert "timestamp_utc" in env


def test_golden_sanity_warning_is_surfaced_on_negative_parallax():
    """Negative parallax must raise a sanity warning that the UI can render."""
    out = normalize_tool_result(
        "search_objects",
        {"results": [{"parallax": -1.2, "ra": 10.0, "dec": 20.0}]},
        tool_input={"q": "test"},
    )
    warnings = out.get("warnings") or []
    assert any("parallax" in str(w).lower() for w in warnings), (
        f"Expected a parallax sanity warning, got {warnings}"
    )
