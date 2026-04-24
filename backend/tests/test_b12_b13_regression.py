from __future__ import annotations


def _b12_tool_results() -> list[dict]:
    return [
        {
            "tool": "run_adql",
            "result": {
                "success": True,
                "row_count": 1,
                "data": {"source_id": [2133061988321336576], "radial_velocity": [-12.82]},
            },
        },
        {
            "tool": "run_adql",
            "result": {
                "__tool_status__": "FAILED",
                "success": False,
                "error": "ADQL query failed after 8s of retries, may have exceeded retry budget",
            },
        },
        {"tool": "search_lightcurve", "result": {"found": 0, "results": []}},
        {"tool": "search_objects", "result": {"__tool_status__": "EMPTY", "row_count": 0}},
        {
            "tool": "run_python",
            "result": {
                "__tool_status__": "SYNTHETIC",
                "__do_not_claim__": True,
                "data_origin": "synthetic",
                "stdout": (
                    "Period: 5.366208 days\n"
                    "dP/dt ~ 10^-8 to 10^-7 day/day\n"
                    "monitored for over 200 years since John Goodricke's discovery in 1784\n"
                ),
            },
        },
    ]


def test_b12_unsupported_history_and_period_change_are_hardblock_candidates():
    from app.observability.metrics import get_registry
    from app.services.claim_validator import (
        blocked_unsupported_narrative_reply_text,
        unsupported_literature_narrative_violations,
    )

    get_registry().reset()
    reply = (
        "delta Cephei has been monitored for over 200 years since John Goodricke's "
        "discovery in 1784.  As the prototype Classical Cepheid, its dP/dt is "
        "about 10^-8 to 10^-7 day/day, so the evolutionary change implies "
        "remarkable stability over centuries."
    )

    violations = unsupported_literature_narrative_violations(reply, _b12_tool_results())
    blocked = blocked_unsupported_narrative_reply_text(violations)

    assert violations
    assert all(v.kind == "unsupported_literature_narrative" for v in violations)
    assert "Goodricke" in blocked
    snap = get_registry().snapshot()
    labels = snap["counters"]["fabrication_blocked_total"]
    unsupported_total = sum(
        value
        for label_tuple, value in labels.items()
        if ("reason", "unsupported_literature_narrative") in label_tuple
    )
    assert unsupported_total >= 1.0


def test_b13_typical_literature_age_without_fit_or_literature_is_blocked():
    from app.services.claim_validator import (
        literature_prior_violations,
        unsupported_literature_narrative_violations,
    )

    tool_results = [
        {"tool": "run_adql", "result": {"success": True, "row_count": 732, "data": [{"g": 12.1}]}},
        {"tool": "fit_isochrone", "result": {"success": False, "error": "subprocess crashed"}},
    ]
    reply = "Age estimate: ~125 Myr (typical for Pleiades from literature)."

    assert unsupported_literature_narrative_violations(reply, tool_results)
    assert literature_prior_violations(reply, tool_results)


def test_b13_extinction_literature_values_without_tool_are_blocked():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    tool_results = [
        {"tool": "run_adql", "result": {"success": True, "row_count": 732, "data": [{"g": 12.1}]}},
    ]
    reply = "Extinction: E(B-V) = 0.040 mag, A_V = 0.124 mag (literature values)."

    violations = unsupported_literature_narrative_violations(reply, tool_results)

    assert violations
    assert any("literature values" in v.match_text.lower() for v in violations)


def test_failed_or_empty_data_fetch_classifier_catches_soft_failures():
    from app.api.chat import _is_failed_or_empty_data_fetch

    assert _is_failed_or_empty_data_fetch({
        "success": False,
        "error": "ADQL query failed after 8s of retries, may have exceeded retry budget",
    })
    assert _is_failed_or_empty_data_fetch({"found": 0, "results": []})
    assert _is_failed_or_empty_data_fetch({"success": True, "data": []})
    assert _is_failed_or_empty_data_fetch({"__tool_status__": "UNAVAILABLE"})
    assert not _is_failed_or_empty_data_fetch({"success": True, "row_count": 2, "data": [{"x": 1}]})


def test_run_python_cmd_unphysical_absolute_magnitude_is_unciteable():
    from app.services.ai_tools import _apply_cmd_sanity_guard

    response = _apply_cmd_sanity_guard({
        "success": True,
        "stdout": "M_G range (dereddened): -86.1 to -73.2\n",
    })

    assert response["__tool_status__"] == "PARTIAL"
    assert response["analysis_status"] == "partial"
    assert response["__do_not_claim__"] is True
    assert response["cmd_sanity_error"] == "unphysical_absolute_magnitude"
    assert "distance-modulus" in response["__message_to_model__"]


def test_run_python_keyerror_classification_points_to_column_inspection():
    from app.services.ai_tools import _classify_sandbox_error, _missing_key_from_error

    err = "Traceback...\nKeyError: 'source_id'"

    assert _classify_sandbox_error(err) == "key_error"
    assert _missing_key_from_error(err) == "source_id"
