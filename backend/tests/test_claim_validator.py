"""Tests for the zero-fabrication gate (Phase 1 / R2).

Locks the contract that any numeric astronomical claim in an AI reply is
verified against the turn's tool_results, and uncited claims are flagged
so the agent loop can regenerate or block the reply.
"""

from __future__ import annotations

import pytest

from app.services.claim_validator import (
    _strip_thousands_separators,
    blocked_reply_text,
    build_regeneration_prompt,
    build_zero_data_qualitative_regeneration_prompt,
    extract_claims,
    validate_claims,
)


# -------------------- extract_claims --------------------


def test_extract_redshift_claim():
    claims = extract_claims("The galaxy has z = 0.032 and some nice features.")
    assert any(c.label == "redshift_z" and c.value == pytest.approx(0.032) for c in claims)


def test_extract_multiple_patterns():
    txt = "We find T_eff = 5800 K, log g = 4.2, and age 4.5 Gyr."
    claims = extract_claims(txt)
    labels = {c.label for c in claims}
    assert {"teff_k", "log_g", "age_gyr"} <= labels


def test_extract_scientific_notation():
    claims = extract_claims("parallax of 1.2e-3 mas")
    assert any(c.value == pytest.approx(1.2e-3) for c in claims)


def test_extract_skips_text_without_numbers():
    assert extract_claims("This galaxy is beautiful.") == []


def test_extract_exoplanet_radius_ratio_claims():
    claims = extract_claims("综合估计 Rp/Rs 约为 0.157。")
    assert any(c.label == "radius_ratio" and c.value == pytest.approx(0.157) for c in claims)


def test_extract_cosmology_parameter_claims():
    claims = extract_claims("We find H0 = 70 km/s/Mpc, Om0 = 0.31, w0 = -1.1, wa = 0.2.")
    labels = {c.label for c in claims}
    assert {"cosmology_h0", "cosmology_om0", "cosmology_w0", "cosmology_wa"} <= labels


def test_markdown_cosmology_table_values_are_numeric_claims():
    """A column separator/unit cell must not hide cosmology constraints."""
    reply = """| parameter | result |
|---|---|
| H0 (km/s/Mpc) | 71.4 +/- 0.3 |
| Omega_m | 0.31 |
| n_s | 0.970 |
| tau | 0.055 |
| ombh2 | 0.0224 |
"""
    claims = extract_claims(reply)
    assert {claim.label for claim in claims} >= {
        "cosmology_h0",
        "cosmology_om0",
        "cosmology_ns",
        "cosmology_tau",
        "cosmology_ombh2",
    }
    assert {claim.value for claim in claims} >= {71.4, 0.31, 0.970, 0.055, 0.0224}


def test_markdown_cosmology_table_fails_closed_without_tool_evidence():
    reply = """| parameter | result |
|---|---|
| H₀ (km/s/Mpc) | 71.4 |
| Ωₘ | 0.31 |
| Ω_b h² | 0.0224 |
"""
    result = validate_claims(reply, [])
    assert result.ok is False
    assert result.universe_size == 0
    assert {claim.label for claim in result.uncited} >= {
        "cosmology_h0",
        "cosmology_om0",
        "cosmology_ombh2",
    }


def test_markdown_cosmology_table_without_outer_pipes_is_caught():
    reply = """parameter | result
---|---
H0 (km/s/Mpc) | 71.4
Omega_m | 0.31
"""
    result = validate_claims(reply, [])
    assert result.ok is False
    assert {claim.label for claim in result.uncited} >= {
        "cosmology_h0",
        "cosmology_om0",
    }


def test_latex_parameter_labels_in_markdown_table_are_caught():
    reply = r"""| parameter | result |
|---|---|
| $H_{0}$ | 71.4 |
| $\Omega_m$ | 0.31 |
| $n_{s}$ | 0.970 |
| $\tau$ | 0.055 |
| $\Omega_b h^2$ | 0.0224 |
"""
    result = validate_claims(reply, [])
    assert result.ok is False
    assert {claim.label for claim in result.uncited} >= {
        "cosmology_h0",
        "cosmology_om0",
        "cosmology_ns",
        "cosmology_tau",
        "cosmology_ombh2",
    }


def test_markdown_cosmology_table_passes_with_matching_parameter_evidence():
    reply = """| parameter | result |
|---|---|
| H0 (km/s/Mpc) | 67.36 |
| n_s | 0.9649 |
| tau | 0.0544 |
| ombh2 | 0.02237 |
"""
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "parameters": {
                "H0": {"median": 67.36},
                "ns": {"median": 0.9649},
                "tau": {"median": 0.0544},
                "ombh2": {"median": 0.02237},
            },
        },
    }]
    assert validate_claims(reply, tool_results).ok is True


def test_b15_unicode_minus_claim_is_extracted():
    """B15: a value written with the Unicode minus U+2212 ('w0 = −0.84') must
    still be extracted as a claim; otherwise it bypasses the whole gate."""
    claims = extract_claims("The chain prefers w0 = −0.84 for this dataset.")
    assert any(
        c.label == "cosmology_w0" and c.value == pytest.approx(-0.84) for c in claims
    )


def test_b15_unicode_minus_fabrication_is_flagged():
    """B15: a fabricated negative value typed with U+2212 must NOT pass the
    gate when the tool returned a different number."""
    tool_results = [{"tool": "run_cosmology_likelihood_chain", "result": {"w0": -0.55}}]
    r = validate_claims("The chain prefers w0 = −0.84.", tool_results)
    assert r.ok is False
    assert any(c.value == pytest.approx(-0.84) for c in r.uncited)


def test_b5_sign_flipped_claim_is_flagged():
    """B5: a sign-flipped value (claim +0.84 vs tool -0.84) must be flagged —
    for w0/wa the sign is the physical conclusion, so abs()-matching is wrong."""
    tool_results = [{"tool": "run_cosmology_likelihood_chain", "result": {"w0": -0.84}}]
    r = validate_claims("The fit gives w0 = 0.84 for this dataset.", tool_results)
    assert r.ok is False


def test_b5_correct_sign_still_matches():
    """B5 guard against over-tightening: the correctly-signed value still
    validates against the tool's value (no false positive)."""
    tool_results = [{"tool": "run_cosmology_likelihood_chain", "result": {"w0": -0.84}}]
    r = validate_claims("The fit gives w0 = -0.84 for this dataset.", tool_results)
    assert r.ok is True


# -------------------- validate_claims --------------------


def test_validate_ok_when_value_matches_tool_result():
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"parallax": 7.50}},
    ]
    r = validate_claims("The parallax is 7.50 mas.", tool_results)
    assert r.ok
    assert r.uncited == []


def test_validate_flags_fabricated_number():
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"parallax": 7.50}},
    ]
    r = validate_claims("The parallax is 9.00 mas.", tool_results)
    assert not r.ok
    assert len(r.uncited) == 1
    assert r.uncited[0].label == "parallax_mas"


def test_cosmology_claims_require_publication_ready_mcmc_result():
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "__tool_status__": "PARTIAL",
                "__do_not_claim__": True,
                "publication_ready": False,
                "parameters": {
                    "H0": {"median": 70.0},
                    "w0": {"median": -1.1},
                },
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 70 km/s/Mpc and w0 = -1.1.", tool_results)
    assert not r.ok
    assert {claim.label for claim in r.uncited} >= {"cosmology_h0", "cosmology_w0"}


def test_cosmology_claims_pass_with_publication_ready_mcmc_result():
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "publication_ready": True,
                "parameters": {
                    "H0": {"median": 70.0},
                    "w0": {"median": -1.1},
                },
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 70 km/s/Mpc and w0 = -1.1.", tool_results)
    assert r.ok


def test_cosmology_claims_pass_with_publication_ready_compressed_likelihood_result():
    tool_results = [
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "success": True,
                "publication_ready": True,
                "claim_scope": "compressed_likelihood_preliminary",
                "parameters": {
                    "H0": {"median": 68.1},
                    "S8": {"median": 0.831},
                },
            },
        }
    ]
    r = validate_claims(
        "The compressed-likelihood preliminary result gives H0 = 68.1 km/s/Mpc and S8 = 0.831.",
        tool_results,
    )
    assert r.ok


def test_cosmology_claims_reject_config_only_likelihood_result():
    tool_results = [
        {
            "tool": "build_cosmology_likelihood",
            "result": {
                "success": True,
                "publication_ready": False,
                "priors": {"H0": [50.0, 90.0]},
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 68.1 km/s/Mpc and S8 = 0.831.", tool_results)
    assert not r.ok
    assert {claim.label for claim in r.uncited} >= {"cosmology_h0", "cosmology_s8"}


def test_exploratory_mcmc_numbers_flow_into_numeric_universe():
    """EXPLORATORY chain_tier (2026-05-20): numbers from the posterior should
    flow into the numeric universe so chat-level discussion passes the +/-1%
    check, even though publication_ready=False keeps the result out of the
    bibcode / claimable-success pool."""
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "__tool_status__": "EXPLORATORY",
                "analysis_status": "EXPLORATORY",
                "chain_tier": "exploratory",
                "publication_ready": False,
                "__exploratory_warning__": "Chain min ESS=200 below publication threshold 400.",
                "parameters": {
                    "H0": {"median": 70.0, "ess_bulk": 200.0, "rhat": 1.07},
                    "w0": {"median": -1.1, "ess_bulk": 200.0, "rhat": 1.07},
                },
            },
        }
    ]
    r = validate_claims(
        "An exploratory chain (ESS=200) suggests H0 around 70 km/s/Mpc and w0 around -1.1.",
        tool_results,
    )
    assert r.ok


def test_exploratory_mcmc_not_counted_as_claimable_success():
    """EXPLORATORY MCMC result publication_ready=False -> excluded from the
    claimable-success pool. It cannot be cited as a published constraint or
    add bibcodes to the citation pool."""
    from app.services.claim_validator import _payload_is_claimable_success

    result = {
        "success": True,
        "__tool_status__": "EXPLORATORY",
        "chain_tier": "exploratory",
        "publication_ready": False,
        "parameters": {"H0": {"median": 70.0}},
    }
    assert not _payload_is_claimable_success("fit_cosmology_mcmc", result)


def test_diagnostic_nested_sampler_cannot_support_claims_or_success():
    from app.services.claim_validator import (
        _is_tainted_synthetic_payload,
        _payload_is_claimable_success,
    )

    result = {
        "success": True,
        "__tool_status__": "PARTIAL",
        "analysis_status": "NESTED_SAMPLER_DIAGNOSTIC",
        "publication_ready": False,
        "__do_not_claim__": True,
        "evidence": {"logz": -3.14159, "logzerr": 0.1},
    }

    assert _is_tainted_synthetic_payload(result) is True
    assert not _payload_is_claimable_success("run_nested_sampler", result)


def test_diagnostic_research_matrix_cannot_support_claims_or_success():
    from app.services.claim_validator import (
        _is_tainted_synthetic_payload,
        _payload_is_claimable_success,
    )

    result = {
        "success": True,
        "analysis_status": "RESEARCH_MATRIX_DIAGNOSTIC",
        "publication_ready": False,
        "__do_not_claim__": True,
        "matrix": [
            {
                "publication_ready": False,
                "result": {"parameters": {"omegam": {"median": 0.29424}}},
            }
        ],
    }

    assert _is_tainted_synthetic_payload(result) is True
    assert not _payload_is_claimable_success("run_research_matrix", result)


def test_registry_proposal_means_cannot_launder_as_executed_constraints():
    from app.services.claim_validator import validate_claims
    from app.services.cosmology_likelihoods import (
        list_cosmology_datasets,
        run_likelihood_chain,
    )

    chain = run_likelihood_chain(
        model="lcdm",
        dataset_keys=["planck2018_compressed", "shoes_h0_riess22"],
        n_samples=1000,
        random_seed=42,
    )
    assert chain["parameters"]["H0"]["median"] != 67.36
    assert "sigma8" not in chain["parameters"]

    for reply in (
        "Our constraint is H0 = 67.36 km/s/Mpc.",
        "Our constraint is sigma8 = 0.8111.",
        "Our constraint is S8 = 0.832.",
    ):
        validation = validate_claims(
            reply,
            [{"tool": "run_cosmology_likelihood_chain", "result": chain}],
            require_typed_scientific_match=True,
        )
        assert validation.ok is False, reply

    registry = list_cosmology_datasets(
        dataset_keys=["planck2018_compressed"],
    )
    for reply in (
        "Our constraint is H0 = 67.36 km/s/Mpc.",
        "Our constraint is sigma8 = 0.8111.",
        "Our constraint is S8 = 0.832.",
    ):
        validation = validate_claims(
            reply,
            [{"tool": "list_cosmology_datasets", "result": registry}],
            require_typed_scientific_match=True,
        )
        assert validation.ok is False, reply


def test_cosmology_central_claim_cannot_match_interval_or_error_statistic():
    from app.services.claim_validator import validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "parameters": {
                "H0": {
                    "median": 68.8,
                    "mean": 68.81,
                    "best_fit": {"value": 68.82, "std": 67.36},
                    "std": 0.52,
                    "hdi_low_94": 67.36,
                    "hdi_high_94": 69.79,
                    "hdi_94": [67.36, 69.79],
                }
            }
        },
    }]

    # ``H0 =`` is a central-estimate statement. An exact HDI edge must not
    # certify it, in ordinary chat mode or strict manuscript mode.
    for strict in (False, True):
        rejected = validate_claims(
            "Our constraint is H0 = 67.36 km/s/Mpc.",
            tool_results,
            require_typed_scientific_match=strict,
        )
        assert rejected.ok is False

        accepted = validate_claims(
            "Our constraint is H0 = 68.8 km/s/Mpc.",
            tool_results,
            require_typed_scientific_match=strict,
        )
        assert accepted.ok is True

        nested_best_fit = validate_claims(
            "Our constraint is H0 = 68.82 km/s/Mpc.",
            tool_results,
            require_typed_scientific_match=strict,
        )
        assert nested_best_fit.ok is True


def test_cosmology_interval_claim_matches_only_the_typed_edge():
    from app.services.claim_validator import validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "parameters": {
                "H0": {
                    "median": 68.8,
                    "hdi_94": [67.36, 69.79],
                }
            }
        },
    }]

    assert validate_claims(
        "At the lower HDI edge, H0 = 67.36 km/s/Mpc.", tool_results
    ).ok is True
    assert validate_claims(
        "At the upper HDI edge, H0 = 69.79 km/s/Mpc.", tool_results
    ).ok is True
    assert validate_claims(
        "At the lower HDI edge, H0 = 69.79 km/s/Mpc.", tool_results
    ).ok is False
    assert validate_claims(
        "Our central constraint is H0 = 67.36 km/s/Mpc.", tool_results
    ).ok is False


def test_cosmology_value_with_error_keeps_parameter_and_statistic_types():
    from app.services.claim_validator import extract_claims, validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "parameters": {
                "H0": {
                    "median": 68.8,
                    "std": 0.52,
                    "hdi_high_94": 69.79,
                }
            },
            # A coincidentally equal number elsewhere must not certify H0's
            # uncertainty through the flat universe.
            "unrelated_diagnostic": 0.31,
        },
    }]

    claims = extract_claims("H0 = 68.8 ± 0.52 km/s/Mpc.")
    assert any(claim.label == "cosmology_h0" for claim in claims)
    assert any(
        claim.label == "cosmology_h0_uncertainty" for claim in claims
    )
    assert validate_claims(
        "H0 = 68.8 ± 0.52 km/s/Mpc.", tool_results
    ).ok is True
    assert validate_claims(
        "H0 = 69.79 ± 0.52 km/s/Mpc.", tool_results
    ).ok is False
    assert validate_claims(
        "H0 = 68.8 ± 0.31 km/s/Mpc.", tool_results
    ).ok is False


def test_dimensionless_cosmology_uncertainty_is_typed_and_validated():
    from app.services.claim_validator import extract_claims, validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"S8": {"median": 0.81, "std": 0.02}}},
    }]

    claims = extract_claims("S8 = 0.81 ± 0.02.")
    assert any(claim.label == "cosmology_s8" for claim in claims)
    assert any(
        claim.label == "cosmology_s8_uncertainty" for claim in claims
    )
    assert validate_claims("S8 = 0.81 ± 0.02.", tool_results).ok is True
    assert validate_claims("S8 = 0.81 ± 9.99.", tool_results).ok is False


def test_unicode_cosmology_units_do_not_hide_uncertainty():
    from app.services.claim_validator import extract_claims, validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"H0": {"median": 68.8, "std": 0.52}}},
    }]

    claims = extract_claims("H0 = 68.8 ± 0.52 km s⁻¹ Mpc⁻¹.")
    assert any(claim.label == "cosmology_h0" for claim in claims)
    assert any(
        claim.label == "cosmology_h0_uncertainty" for claim in claims
    )
    assert validate_claims(
        "H0 = 68.8 ± 0.52 km s⁻¹ Mpc⁻¹.", tool_results
    ).ok is True
    assert validate_claims(
        "H0 = 68.8 ± 9.99 km s⁻¹ Mpc⁻¹.", tool_results
    ).ok is False

    latex = r"$H_0 = 68.8 \pm 0.52\ \mathrm{km\,s^{-1}\,Mpc^{-1}}$."
    latex_claims = extract_claims(latex)
    assert any(
        claim.label == "cosmology_h0_uncertainty" for claim in latex_claims
    )
    assert validate_claims(latex, tool_results).ok is True
    assert validate_claims(
        latex.replace(r"\pm 0.52", r"\pm 9.99"), tool_results
    ).ok is False


@pytest.mark.parametrize(
    "spacing",
    [r"\,", r"\;", r"\:", r"\!", r"\ ", "~", r"\quad"],
)
def test_tex_spacing_cannot_hide_cosmology_uncertainty(spacing):
    from app.services.claim_validator import extract_claims, validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"H0": {"median": 68.8, "std": 0.52}}},
    }]
    reply = (
        rf"$H_0 = 68.8{spacing}\pm{spacing}9.99{spacing}"
        r"\mathrm{km\,s^{-1}\,Mpc^{-1}}$."
    )

    claims = extract_claims(reply)
    assert any(
        claim.label == "cosmology_h0_uncertainty" for claim in claims
    )
    assert validate_claims(reply, tool_results).ok is False


def test_tex_uncertainty_from_previous_text_remains_untrusted():
    from app.services.claim_validator import extract_claims, validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"H0": {"median": 68.8, "std": 0.52}}},
    }]
    reply = (
        r"The previous result was $H_0 = 68.8\,\pm\,0.52\,"
        r"\mathrm{km\,s^{-1}\,Mpc^{-1}}$."
    )

    claims = extract_claims(reply)
    assert any(
        claim.label.startswith("untrusted_context_value_with_error.")
        for claim in claims
    )
    assert validate_claims(reply, tool_results).ok is False


def test_sigma_interval_marker_is_not_detection_significance():
    from app.services.claim_validator import extract_claims, validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"H0": {"median": 68.8, "std": 0.52}}},
    }]
    replies = (
        "At 1σ, H0 = 68.8 ± 0.52 km/s/Mpc.",
        "H0 = 68.8 ± 0.52 km/s/Mpc (1σ).",
    )
    for reply in replies:
        claims = extract_claims(reply)
        assert not any(
            claim.label == "significance_sigma" for claim in claims
        )
        assert validate_claims(reply, tool_results).ok is True

    detection = "The detection significance is 1σ."
    assert any(
        claim.label == "significance_sigma"
        for claim in extract_claims(detection)
    )


def test_sigma_semantics_are_bound_to_nearest_discourse_clause():
    from app.services.claim_validator import extract_claims, validate_claims

    h0_result = {
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"H0": {"median": 68.8, "std": 0.52}}},
    }
    mixed_anomaly = (
        "H0 = 68.8 ± 0.52 km/s/Mpc, and the anomaly is 5σ."
    )
    assert any(
        claim.label == "significance_sigma" and claim.value == 5
        for claim in extract_claims(mixed_anomaly)
    )
    assert validate_claims(mixed_anomaly, [h0_result]).ok is False

    discrepancy = "The posterior interval is broad, but the discrepancy is 5σ."
    assert any(
        claim.label == "significance_sigma" and claim.value == 5
        for claim in extract_claims(discrepancy)
    )
    assert validate_claims(discrepancy, []).ok is False

    split_semantics = (
        "The detection significance is 5σ, while "
        "H0 = 68.8 ± 0.52 km/s/Mpc (1σ)."
    )
    sigma_claims = [
        claim.value
        for claim in extract_claims(split_semantics)
        if claim.label == "significance_sigma"
    ]
    assert sigma_claims == [5.0]
    assert validate_claims(
        split_semantics,
        [
            h0_result,
            {"tool": "compare_models", "result": {"equivalent_sigma": 5.0}},
        ],
        require_typed_scientific_match=True,
    ).ok is True


def test_sigma_interval_exemption_never_swallows_comparative_significance():
    # Regression (2026-07-23 review of 9a4f112): the interval-marker exemption
    # keyed off nearby "posterior"/"constraint" wording, so fabricated
    # comparative significances ("posteriors conflict at 4.6σ") extracted no
    # claim at all and bypassed the numeric gate entirely. The exemption is
    # now fail-closed: only 1σ/2σ/3σ coverage labels qualify, and the
    # detection cue list covers comparative wording.
    from app.services.claim_validator import extract_claims, validate_claims

    fabricated = (
        "The two posteriors conflict at 4.6 sigma given the S8 constraint.",
        "The data favor w0waCDM at 4.2 sigma over the LCDM posterior.",
        "KiDS and Planck clash at 3.0 sigma in the S8 constraint.",
        "The posteriors disagree at 2 sigma in the joint constraint.",
        "The posteriors are at odds at 2.5 sigma given the bound.",
    )
    for reply in fabricated:
        assert any(
            claim.label == "significance_sigma"
            for claim in extract_claims(reply)
        ), reply
        assert validate_claims(reply, []).ok is False, reply

    # Specificity: conventional coverage labels stay exempt.
    for reply in (
        "The 2σ upper bound on the neutrino mass sum.",
        "Omega_m = 0.315 +/- 0.007 (1 sigma) from the posterior constraint.",
    ):
        assert not any(
            claim.label == "significance_sigma"
            for claim in extract_claims(reply)
        ), reply


def test_bare_modal_elsewhere_in_sentence_does_not_wash_strong_conclusion():
    # Regression (2026-07-23 review): the non-assertive hedge list matched
    # bare modals anywhere in the sentence, so appending "... and this result
    # should appear in the abstract" washed an assertive conclusion out of
    # the gate. Modals hedge only when they modify a conclusion-like verb.
    from app.services.claim_validator import _strong_conclusion_from_sentence

    washing = (
        "Our joint fit shows dark energy evolves, and this result should "
        "appear in the abstract.",
        "The data reject LCDM; we should publish this immediately.",
    )
    for sentence in washing:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence

    hedged = (
        "Dark energy may evolve according to this fit.",
        "The tension might be resolved by new calibration.",
        "w0waCDM could be preferred once full likelihoods are used.",
        "Future data should help resolve the tension.",
    )
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_one_sigma_value_with_error_keeps_central_value_semantics():
    from app.services.claim_validator import validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {"parameters": {"H0": {"median": 68.8, "std": 0.52}}},
    }]

    assert validate_claims(
        "The one-sigma constraint is H0 = 68.8 ± 0.52 km/s/Mpc.",
        tool_results,
    ).ok is True


def test_cosmology_interval_cues_are_clause_and_parameter_bound():
    from app.services.claim_validator import validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "parameters": {
                "H0": {"median": 68.8, "hdi_94": [67.36, 69.79]},
                "omegam": {"median": 0.31, "hdi_94": [0.29, 0.33]},
            }
        },
    }]

    assert validate_claims(
        "We discuss the upper edge of the omegam posterior, while our "
        "central result is H0 = 69.79 km/s/Mpc.",
        tool_results,
    ).ok is False
    assert validate_claims(
        "The upper edge of the omegam posterior and H0 = 69.79 km/s/Mpc.",
        tool_results,
    ).ok is False
    assert validate_claims(
        "At the lower HDI edge (tail mass 0.025), H0 = 67.36 km/s/Mpc.",
        tool_results,
    ).ok is True


def test_nested_tainted_parameter_statistics_never_enter_typed_buckets():
    from app.services.claim_validator import validate_claims

    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "parameters": {
                "H0": {
                    "median": 68.8,
                    "best_fit": {
                        "__do_not_claim__": True,
                        "value": 71.25,
                    },
                    "hdi_94": {
                        "statistical_role": "proposal_only",
                        "lower": 66.0,
                        "upper": 70.0,
                    },
                }
            }
        },
    }]

    assert validate_claims("H0 = 68.8 km/s/Mpc.", tool_results).ok is True
    assert validate_claims("H0 = 71.25 km/s/Mpc.", tool_results).ok is False
    assert validate_claims(
        "At the lower HDI edge, H0 = 66.0 km/s/Mpc.", tool_results
    ).ok is False


def test_context_statistical_roles_are_skipped_independent_of_envelope_key():
    from app.services.claim_validator import validate_claims

    context_only = [{
        "tool": "future_registry_tool",
        "result": {
            # Deliberately avoid the known ``compressed_likelihood`` key: the
            # role/scope must be authoritative even if a future serializer
            # moves the same record elsewhere.
            "opaque_registry_copy": {
                "statistical_role": "proposal_only",
                "parameters": ["H0", "sigma8", "S8"],
                "mean": [67.36, 0.8111, 0.832],
            },
            "opaque_tension_copy": {
                "statistical_scope": "literature_context",
                "parameter": "H0",
                "value": 67.36,
            },
        },
    }]
    for reply in (
        "Our constraint is H0 = 67.36 km/s/Mpc.",
        "Our constraint is sigma8 = 0.8111.",
        "Our constraint is S8 = 0.832.",
    ):
        validation = validate_claims(reply, context_only)
        assert validation.ok is False, reply

    executable_prior = [{
        "tool": "future_registry_tool",
        "result": {
            "executed_record": {
                "statistical_role": "external_prior",
                "parameters": ["H0"],
                "mean": [73.04],
            }
        },
    }]
    assert validate_claims(
        "The external prior is H0 = 73.04 km/s/Mpc.",
        executable_prior,
    ).ok is True


@pytest.mark.parametrize(
    "reply",
    [
        "The extended fit improves chi-squared by -4.58.",
        "The delta chi-squared is -4.58.",
        "Delta chi2 = -4.58.",
        "The comparison gives Δχ² = -4.58.",
    ],
)
def test_model_comparison_chi_squared_prose_is_extracted(reply):
    from app.services.claim_validator import extract_claims

    claims = extract_claims(reply)
    assert any(
        claim.label == "chi_squared" and claim.value == pytest.approx(-4.58)
        for claim in claims
    )


def test_exploratory_result_not_treated_as_empty_turn():
    """EXPLORATORY status must not flip is_empty_turn to True. The posterior
    is meaningful enough to discuss in chat; only PARTIAL+__do_not_claim__
    and explicit EMPTY/FAILED/UNAVAILABLE qualify as empty."""
    from app.services.claim_validator import is_empty_turn

    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "__tool_status__": "EXPLORATORY",
                "analysis_status": "EXPLORATORY",
                "chain_tier": "exploratory",
                "publication_ready": False,
                "parameters": {"H0": {"median": 70.0}},
            },
        }
    ]
    assert is_empty_turn(tool_results) is False


def test_cosmology_prior_bounds_do_not_support_posterior_claims():
    tool_results = [
        {
            "tool": "fit_cosmology_mcmc",
            "result": {
                "success": True,
                "publication_ready": True,
                "parameters": {
                    "H0": {"median": 70.0},
                    "Om0": {"median": 0.31},
                },
                "priors": {
                    "H0": [50.0, 90.0],
                    "Om0": [0.05, 0.6],
                },
                "chain_diagnostics": {
                    "thresholds": {"ess_min": 400.0, "rhat_max": 1.05},
                },
            },
        }
    ]
    r = validate_claims("The posterior gives H0 = 50 km/s/Mpc and Om0 = 0.05.", tool_results)
    assert not r.ok
    assert {claim.label for claim in r.uncited} >= {"cosmology_h0", "cosmology_om0"}


def test_validate_flags_uncited_radius_ratio():
    r = validate_claims("The planet-to-star radius ratio is 0.157.", [])
    assert not r.ok
    assert any(c.label == "radius_ratio" and c.value == pytest.approx(0.157) for c in r.uncited)


def test_extract_chinese_period_and_percent_claims():
    claims = extract_claims("周期值为 5.366154 天，距离估算约260 pc，误差 5.2%。")
    assert any(c.label == "period_days_zh" and c.value == pytest.approx(5.366154) for c in claims)
    assert any(c.value == pytest.approx(260.0) for c in claims)
    assert any(c.label == "percent_claim" and c.value == pytest.approx(5.2) for c in claims)


def test_validate_tolerance_accepts_rounded_value():
    """7.504 formatted as 7.50 must count as a match under the 1 % tolerance."""
    tool_results = [{"result": {"parallax": 7.504}}]
    r = validate_claims("The parallax is 7.50 mas.", tool_results)
    assert r.ok


def test_validate_universe_walks_nested_payload():
    tool_results = {
        "tool": "get_object_dossier",
        "result": {
            "photometry": {"g_mag": 12.34, "bp_rp": 0.88},
            "motion": [{"pmra": -2.1, "pmdec": 4.9}],
        },
    }
    r = validate_claims("pmra is -2.1 mas and g = 12.34 mag.", tool_results)
    assert r.ok


def test_validate_text_inside_string_values_counts():
    """Numbers embedded in stringified CSV rows still satisfy citation."""
    tool_results = [{"result": {"csv_preview": "name,z\nM31,-0.001\n"}}]
    r = validate_claims("z = -0.001", tool_results)
    assert r.ok


def test_validate_returns_multiple_uncited():
    tool_results = [{"result": {"parallax": 5.0}}]
    r = validate_claims(
        "The log g = 4.4 and [Fe/H] = -0.2 and age 5 Gyr.",
        tool_results,
    )
    assert not r.ok
    assert len(r.uncited) >= 2


def test_schema_numbers_inside_markdown_code_are_not_claims():
    text = (
        "Available tool schema:\n"
        "```json\n"
        '{"limit": 24.0, "radius": 4.0, "max_rows": 70.0}\n'
        "```\n"
        "Use `SELECT TOP 1000 objID FROM PhotoObjAll` for examples."
    )
    claims = extract_claims(text)
    values = {c.value for c in claims}
    assert 24.0 not in values
    assert 4.0 not in values
    assert 70.0 not in values
    assert 1000.0 not in values


def test_spelled_out_summary_numbers_are_claims():
    """R20: 'three point zero' 这种英文拼写数字不能绕过 gate."""
    claims = extract_claims("The synthetic run said the mean was three point zero.")
    assert any(c.label == "spelled_number" and c.value == pytest.approx(3.0) for c in claims)


def test_spelled_out_uncited_number_is_blocked():
    r = validate_claims(
        "The mean was three point zero.",
        [{"result": {"__tool_status__": "SYNTHETIC", "__do_not_claim__": True, "stdout": "mean=3.0"}}],
    )
    assert not r.ok
    assert any(c.value == pytest.approx(3.0) for c in r.uncited)


# -------------------- Prompts & blocked text --------------------


def test_regeneration_prompt_lists_uncited_values():
    r = validate_claims("z = 3.14", [{"result": {"unrelated": 0.02}}])
    assert not r.ok
    prompt = build_regeneration_prompt(r)
    assert "3.14" in prompt
    assert "not determined by my tools" in prompt.lower()


def test_regeneration_prompt_forbids_repeating_untrusted_context_numbers():
    r = validate_claims(
        "The pasted transcript reports 71.43 +/- 0.31.",
        [{"result": {"median": 71.43, "std": 0.31}}],
    )
    assert not r.ok
    prompt = build_regeneration_prompt(r)
    assert "even while disclaiming it" in prompt
    assert "the unverified pasted value" in prompt


@pytest.mark.parametrize(
    "reply",
    [
        "The earlier value was 71.43 ± 0.31.",
        "The previous estimate was 71.43 +/- 0.31.",
        "The pasted result was 71.43+-0.31.",
        "The quoted result was 71.43 ± 0.31.",
        "H0 = 71.43 +/- 0.31 came from that transcript.",
        "The user-supplied result was 71.43+-0.31.",
    ],
)
def test_untrusted_context_bare_uncertainty_pair_extracts_both_values(reply):
    claims = extract_claims(reply)
    contextual = [
        claim for claim in claims
        if claim.label.startswith("untrusted_context_value_with_error.")
    ]
    assert [(claim.label, claim.value) for claim in contextual] == [
        ("untrusted_context_value_with_error.g1", pytest.approx(71.43)),
        ("untrusted_context_value_with_error.g2", pytest.approx(0.31)),
    ]


def test_untrusted_context_uncertainty_pair_never_matches_global_pool():
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "parameters": {"H0": {"median": 71.43, "std": 0.31}},
            "diagnostics": [71.43, 0.31, 1, 2, 3, 4, 5, 6, 7, 8],
        },
    }]
    result = validate_claims(
        "The pasted transcript says H0 = 71.43 +/- 0.31.",
        tool_results,
    )
    assert not result.ok
    assert {(claim.label, claim.value) for claim in result.uncited} == {
        ("untrusted_context_value_with_error.g1", 71.43),
        ("untrusted_context_value_with_error.g2", 0.31),
    }


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "The pasted transcript is untrusted. ",
    ],
)
def test_current_tool_h0_uncertainty_with_evidence_still_passes(prefix):
    result = validate_claims(
        prefix + "The current tool result is H0 = 67.67 ± 0.53 with evidence.",
        [{
            "tool": "run_cosmology_likelihood_chain",
            "result": {"parameters": {"H0": {"median": 67.67, "std": 0.53}}},
        }],
    )
    assert result.ok, result.uncited
    assert not any(
        claim.label.startswith("untrusted_context_value_with_error.")
        for claim in result.claims
    )


def test_blocked_reply_text_is_user_friendly():
    r = validate_claims("z = 9.99", [{"result": {}}])
    assert not r.ok
    text = blocked_reply_text(r)
    assert "withheld" in text.lower()
    assert "cited data lookup" in text.lower()
    assert "redshift_z" not in text


def test_blocked_reply_text_does_not_leak_internal_group_labels():
    r = validate_claims("H0 = 67.0 km/s/Mpc and H0 = 73.0 km/s/Mpc.", [])
    assert not r.ok
    text = blocked_reply_text(r)
    assert "value_bare_unit" not in text
    assert "g1" not in text
    assert "67.0" in text
    assert "73.0" in text


def test_verified_scalar_standardized_difference_is_not_typed_significance():
    result = validate_claims(
        "The absolute standardized difference is 4.5 sigma.",
        [
            {
                "tool": "verify_scalar_derivation",
                "input": {
                    "quantities": [
                        {"value": 67.6, "standard_uncertainty": 1.2},
                        {"value": 73.0, "standard_uncertainty": 0.0},
                    ]
                },
                "result": {
                    "success": True,
                    "calculation_status": "verified_deterministic",
                    "claim_scopes": {
                        "derived_numeric": True,
                        "source_measurement": True,
                    },
                    "result": {
                        "value": -5.4,
                        "standard_uncertainty": 1.2,
                        "standardized_difference_abs": 4.5,
                    },
                    "source_status": "verified_exact",
                },
            }
        ],
        require_typed_scientific_match=True,
    )

    assert not result.ok
    assert [claim.label for claim in result.uncited] == ["significance_sigma"]


def test_verified_scalar_standardized_difference_remains_claimable_as_number():
    result = validate_claims(
        "The absolute standardized difference is 4.5.",
        [
            {
                "tool": "verify_scalar_derivation",
                "input": {
                    "quantities": [
                        {"value": 67.6, "standard_uncertainty": 1.2},
                        {"value": 73.0, "standard_uncertainty": 0.0},
                    ]
                },
                "result": {
                    "success": True,
                    "calculation_status": "verified_deterministic",
                    "claim_scopes": {
                        "derived_numeric": True,
                        "source_measurement": True,
                    },
                    "result": {
                        "value": -5.4,
                        "standard_uncertainty": 1.2,
                        "standardized_difference_abs": 4.5,
                    },
                    "source_status": "verified_exact",
                },
            }
        ],
        require_typed_scientific_match=True,
    )

    assert result.ok, result.uncited


def test_verified_scalar_propagated_error_survives_structural_anti_echo():
    result = validate_claims(
        "The controlled difference is -5.4 ± 1.2 km/s/Mpc.",
        [
            {
                "tool": "verify_scalar_derivation",
                "input": {
                    "quantities": [
                        {"value": 67.6, "standard_uncertainty": 1.2},
                        {"value": 73.0, "standard_uncertainty": 0.0},
                    ]
                },
                "result": {
                    "success": True,
                    "calculation_status": "verified_deterministic",
                    "claim_scopes": {
                        "derived_numeric": True,
                        "source_measurement": True,
                    },
                    "result": {
                        "value": -5.4,
                        "standard_uncertainty": 1.2,
                        "standardized_difference_abs": 4.5,
                    },
                },
            }
        ],
    )

    assert result.ok, result.uncited


def test_linearized_scalar_ratio_remains_claimable_with_explicit_status():
    result = validate_claims(
        "The controlled first-order ratio is 0.5 +/- 0.1.",
        [
            {
                "tool": "verify_scalar_derivation",
                "input": {
                    "quantities": [
                        {"value": 10.0, "standard_uncertainty": 1.0},
                        {"value": 20.0, "standard_uncertainty": 2.0},
                    ]
                },
                "result": {
                    "success": True,
                    "calculation_status": "linearized_approximation",
                    "claim_scopes": {
                        "derived_numeric": True,
                        "source_measurement": True,
                    },
                    "result": {
                        "value": 0.5,
                        "standard_uncertainty": 0.1,
                        "uncertainty_method": "first_order_delta",
                    },
                },
            }
        ],
    )

    assert result.ok, result.uncited


def test_untrusted_tool_cannot_mint_typed_scalar_significance():
    result = validate_claims(
        "The absolute standardized difference is 4.5 sigma.",
        [
            {
                "tool": "run_python",
                "result": {
                    "success": True,
                    "calculation_status": "verified",
                    "claim_scopes": {"derived_numeric": True},
                    "result": {"standardized_difference_abs": 4.5},
                },
            }
        ],
        require_typed_scientific_match=True,
    )

    assert not result.ok
    assert [claim.label for claim in result.uncited] == ["significance_sigma"]


def test_zero_data_qualitative_rewrite_prompt_allows_method_answer_without_numbers():
    r = validate_claims("A DESI+SN comparison may show a 2 sigma deviation at z = 0.4.", [])
    assert not r.ok
    prompt = build_zero_data_qualitative_regeneration_prompt(r)
    assert "qualitative-only answer" in prompt
    assert "Remove every numeric value" in prompt
    assert "method or expected scientific behaviour" in prompt
    assert "Do not call tools" in prompt


# -------------------- F1.1: Pleiades fabrication regression --------------------
#
# The Pleiades reviewer saw the AI invent these numbers despite every tool
# call that turn returning 0 rows or an error:
#   "Member Star Count: 776 stars"
#   "Mean Parallax: 7.353 ± 0.001 mas (weighted mean)"
#   "Distance: 136.0 ± 0.0 pc"
# Every one of these must now be extracted AND flagged as uncited when the
# tool-results universe is empty.

PLEIADES_REPLY = (
    "Member Star Count: 776 stars\n"
    "Mean Parallax: 7.353 ± 0.001 mas (weighted mean)\n"
    "Distance: 136.0 ± 0.0 pc\n"
    "Literature comparison: Excellent agreement."
)


def test_pleiades_regex_extracts_labelled_colon_form():
    claims = extract_claims(PLEIADES_REPLY)
    values = {c.value for c in claims}
    assert 776.0 in values, f"776 (member count) should be extracted; got {values}"
    assert 7.353 in values, f"7.353 (mean parallax) should be extracted; got {values}"
    assert 0.001 in values, f"0.001 (parallax err) should be extracted; got {values}"
    assert 136.0 in values, f"136.0 (distance) should be extracted; got {values}"


def test_pleiades_count_with_noun_captures_776():
    claims = extract_claims("We found 776 member stars in the cluster.")
    assert any(c.value == 776.0 and c.label == "count_with_noun" for c in claims)


def test_pleiades_uncertainty_pair_extracts_both_value_and_err():
    claims = extract_claims("π = 7.353 ± 0.001 mas")
    values = {c.value for c in claims}
    assert 7.353 in values
    assert 0.001 in values


def test_pleiades_empty_tool_results_flags_all_claims():
    """The exact bug: all tool calls this turn failed/returned 0 rows, AI
    still wrote the full Pleiades paragraph.  Every fabricated number
    must now be flagged."""
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"row_count": 0, "rows": []}},
        {"tool": "run_python", "input": {}, "result": {"success": False, "error": "crashed"}},
    ]
    r = validate_claims(PLEIADES_REPLY, tool_results)
    assert not r.ok
    uncited_values = {c.value for c in r.uncited}
    assert 776.0 in uncited_values
    assert 7.353 in uncited_values
    assert 136.0 in uncited_values


def test_strict_mode_rejects_accidental_index_match():
    """F1.3: with a thin tool universe (only row_count=0 and a few
    indices), a value of 776 should NOT match 775 even though 775 is
    within 1% — under strict mode tolerance is 0.1%."""
    tool_results = [{"result": {"row_count": 775}}]  # thin universe
    r = validate_claims("We found 776 stars.", tool_results)
    # Under normal 1% tolerance, 776 would match 775 (diff = 0.13%).
    # Under strict mode (<10 universe entries → 0.1% tol), it must fail.
    assert not r.ok, "strict mode should reject 776 vs 775 (0.13% diff > 0.1%)"
    assert r.strict_mode is True


def test_strict_mode_off_with_rich_universe():
    """When tool_results have >=10 distinct values, normal 1% tolerance
    applies (not strict)."""
    tool_results = [{"result": {f"col_{i}": float(i) + 0.5 for i in range(20)}}]
    r = validate_claims("z = 0.5", tool_results)
    assert r.strict_mode is False


# -------------------- F1.4: zero-data hard block --------------------


def test_is_empty_turn_with_0_rows_adql():
    from app.services.claim_validator import is_empty_turn

    assert is_empty_turn([
        {"tool": "run_adql", "result": {"row_count": 0, "rows": []}},
    ])


def test_is_empty_turn_with_python_failure():
    from app.services.claim_validator import is_empty_turn

    assert is_empty_turn([
        {"tool": "run_python", "result": {"success": False, "error": "crashed"}},
    ])


def test_is_empty_turn_false_when_real_data():
    from app.services.claim_validator import is_empty_turn

    assert not is_empty_turn([
        {"tool": "run_adql", "result": {"row_count": 5, "rows": [[1, 2, 3]]}},
    ])


def test_zero_data_but_quantitative_catches_pleiades():
    """F1.4: when the turn is entirely empty-or-failed AND the reply has
    quantitative claims, return them so the caller can hard-block
    without waiting for the regeneration loop."""
    from app.services.claim_validator import zero_data_but_quantitative

    empty_turn = [
        {"tool": "run_adql", "result": {"row_count": 0, "rows": []}},
        {"tool": "run_python", "result": {"success": False, "error": "crash"}},
    ]
    claims = zero_data_but_quantitative(PLEIADES_REPLY, empty_turn)
    assert len(claims) > 0
    values = {c.value for c in claims}
    assert 776.0 in values and 7.353 in values


def test_zero_data_but_quantitative_skips_when_data_exists():
    from app.services.claim_validator import zero_data_but_quantitative

    real_turn = [{"tool": "run_adql", "result": {"row_count": 10}}]
    claims = zero_data_but_quantitative(PLEIADES_REPLY, real_turn)
    assert claims == []


def test_partial_run_python_stdout_is_not_zero_data_turn():
    from app.services.claim_validator import is_empty_turn, validate_claims, zero_data_but_quantitative

    tool_results = [{
        "tool": "run_python",
        "result": {
            "__tool_status__": "PARTIAL",
            "__partial_output__": True,
            "success": False,
            "stdout": "Maximum 3D velocity: 322.3 km/s\nstars above 300 km/s: 1\n",
            "error": "KeyError: 9",
            "data_origin": "real_archive",
            "analysis_status": "partial",
        },
    }]
    reply = "The partial run found a maximum velocity of 322.3 km/s before the top-10 loop failed."

    assert is_empty_turn(tool_results) is False
    assert zero_data_but_quantitative(reply, tool_results) == []
    assert validate_claims(reply, tool_results).ok


# -------------------- F1.5: universe snapshot in block message --------------------


def test_block_message_includes_universe_snapshot():
    tool_results = [{"result": {"foo": 1.5, "bar": 2.7}}]
    r = validate_claims("z = 99.9", tool_results)
    assert not r.ok
    text = blocked_reply_text(r)
    # universe size should be reported
    assert "2 distinct numeric values" in text or "distinct numeric values" in text


def test_block_message_reports_empty_universe():
    r = validate_claims("z = 1.23", [])
    text = blocked_reply_text(r)
    assert "empty" in text.lower() or "0 distinct" in text.lower()


# -------------------- L1 (audit 2026-04-20): spectral / X-ray / radio units --------------------


def test_wavelength_angstrom_caught():
    """L1: 'Hα emission at 6563 Å' was previously not extracted at all (value_with_error requires
    a ± symbol, label_colon only covers distance/redshift etc., and wavelength unit Å was not
    in the allowlist). After the audit, wavelength claims enter value_bare_unit."""
    tool_results = [{"result": {"foo": 1.0}}]
    r = validate_claims("The Hα emission line is at 6563 Å", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 6563.0 in values, f"6563 Å 没被抽到: {r.uncited}"


def test_xray_luminosity_erg_per_s_caught():
    """L1: X-ray luminosity 'L_X = 1.5e44 erg/s' must be extracted."""
    tool_results = [{"result": {"bar": 2.0}}]
    r = validate_claims("AGN L_X = 1.5e44 erg/s reported", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 1.5e44 in values, f"1.5e44 erg/s 没被抽到: {r.uncited}"


def test_radio_flux_mjy_caught():
    """L1: radio flux '3.2 mJy' must be extracted."""
    tool_results = [{"result": {"qux": 0.5}}]
    r = validate_claims("FIRST flux 3.2 mJy", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 3.2 in values


def test_xray_energy_kev_caught():
    """L1: energy 'E = 5.5 keV' must be extracted."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Peak at 5.5 keV above continuum", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 5.5 in values


def test_frequency_ghz_caught():
    """L1: radio frequency '1.4 GHz' must be extracted."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Observation at 1.4 GHz", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 1.4 in values


def test_gpc_distance_caught():
    """L1: cosmological distance '3.2 Gpc' — distance_pc/kpc/mpc were covered before but Gpc was missing."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("The quasar is at distance 3.2 Gpc", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 3.2 in values


def test_wavelength_with_error_caught():
    """L1: wavelength with error '6563.1 ± 0.5 Å' — both numbers must be extracted."""
    tool_results = [{"result": {"z": 0.1}}]
    r = validate_claims("Line center: 6563.1 ± 0.5 Å", tool_results)
    assert not r.ok
    values = [c.value for c in r.uncited]
    assert 6563.1 in values
    assert 0.5 in values


def test_existing_parallax_still_dedupped_not_duplicated():
    """L1: the newly added value_bare_unit should not cause 'parallax is 9.00 mas' to produce
    two claims for the same value. Span-overlap deduplication ensures only 1 claim."""
    tool_results = [{"result": {"parallax": 7.5}}]
    r = validate_claims("The Pleiades parallax is 9.00 mas", tool_results)
    assert not r.ok
    # the same value (9.0) should not be counted multiple times
    vals_at_9 = [c for c in r.uncited if abs(c.value - 9.0) < 1e-6]
    assert len(vals_at_9) == 1, f"9.00 mas 被重复抽取: {r.uncited}"


# -------------------- L2 (audit 2026-04-20): numeric pool metadata filtering --------------------


def test_row_count_laundering_blocked_pleiades_776():
    """L2: reproduces the laundering from the first Pleiades review.
    Tool returned row_count=776, AI claimed "776 member stars".
    The original validator ingested row_count as an ordinary number, letting 776 pass.
    After the audit, the row_count field is skipped entirely → 776 not in pool → claim blocked."""
    tool_results = [{
        "tool": "run_adql",
        "result": {
            # no actual 776 data rows, only the row_count metadata field
            "row_count": 776,
            "showing": 100,
            "columns": ["source_id", "ra", "dec", "parallax"],
            "data": {
                "parallax": [7.3, 7.5, 7.4, 7.6, 7.35],  # real data, no 776
            },
        },
    }]
    r = validate_claims("Found 776 member stars in the Pleiades.", tool_results)
    assert not r.ok, "776 不该过 — row_count 是元数据不能 launder 成观测"
    # 776 应该在 uncited 里
    assert any(abs(c.value - 776) < 1e-6 for c in r.uncited), \
        f"776 没被拦下, 说明元数据过滤失效: {r.uncited}"


def test_timestamp_not_laundered_as_data():
    """L2: tool returns a UNIX epoch large integer like timestamp=1745136000;
    the AI must not be able to cite it as if it were real observational data."""
    tool_results = [{"result": {
        "timestamp_utc": 1745136000,
        "elapsed_seconds": 12.5,
        "data": {"parallax": [7.5]},
    }}]
    # AI fabricates a distance of 1745136000 pc — should be blocked
    r = validate_claims("The distance is 1745136000 pc", tool_results)
    assert not r.ok, "timestamp 作为元数据不应 launder"


def test_real_data_still_matches_after_metadata_filter():
    """L2: confirms the filter does not discard all numbers. Real data fields (parallax,
    ra, distance, period, etc.) enter the pool as normal."""
    tool_results = [{"result": {
        "row_count": 1,
        "timestamp": 1745136000,
        "data": {"parallax": 7.353, "period": 5.366},
    }}]
    # citing real data 7.353 and 5.366 → should pass (both in pool)
    r = validate_claims("Parallax 7.353 mas and period 5.366 days.", tool_results)
    assert r.ok, (
        f"真实数据被误拦: uncited={[c.raw for c in r.uncited]}, "
        f"universe_size={r.universe_size}"
    )


def test_nested_data_rows_still_get_harvested():
    """L2: numbers inside data rows must not be incorrectly excluded by keys like 'row_count'.
    Numbers in value positions within nested dict/list are treated as ordinary values."""
    tool_results = [{"result": {
        "row_count": 5,
        "rows": [
            {"pf": 5.366154, "pf_err": 0.000109},
            {"pf": 5.366200, "pf_err": 0.000200},
        ],
    }}]
    # citing 5.366154 should match → pass
    r = validate_claims("Gaia period 5.366154 days", tool_results)
    assert r.ok, f"嵌套数据行里的数字被误拦: {r.uncited}"


# -------------------- L24 (audit 2026-04-20): scientific notation tolerance --------------------


def test_scientific_notation_does_not_cross_orders_of_magnitude():
    """L24: the ±1% relative tolerance correctly rejects cross-order-of-magnitude false matches
    (1.23e-24 vs 1.25e-23 differ by 10x). This test locks: anyone who changes _matches_any
    in the future must not treat an order-of-magnitude mismatch as "close"."""
    tool_results = [{"result": {"mass_kg": 1.23e-24}}]
    # claim is the correct order of magnitude → should pass
    r1 = validate_claims("mass = 1.23e-24 kg", tool_results)
    assert r1.ok, "相同数量级内 1.23e-24 应匹配"

    # claim is off by one order of magnitude → should not pass
    r2 = validate_claims("mass = 1.23e-23 kg", tool_results)
    assert not r2.ok, (
        "1.23e-23 跟 tool 的 1.23e-24 差 10 倍, 不该匹配 (相对容差"
        "的物理意义)"
    )


# ---- W1 (PART W): literature prior age/mass/distance hard block ----


def test_literature_prior_age_without_fit_isochrone_is_violation():
    """W1: run_adql returns many Gaia rows, reply says "age ~100 Myr" but this turn
    did not run fit_isochrone / search_literature / get_object_dossier — treated as a literature prior."""
    from app.services.claim_validator import literature_prior_violations
    rows = [{"phot_g_mean_mag": 10.0 + i * 0.01} for i in range(100)]
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"data": {"rows": rows}}},
    ]
    vios = literature_prior_violations(
        "The cluster age is ~100 Myr (young open cluster).", tool_results
    )
    assert len(vios) >= 1
    assert any(c.label == "age_myr" for c in vios)


def test_literature_prior_age_passes_when_fit_isochrone_ran():
    """W1: after running fit_isochrone the AI may cite the age value."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "fit_isochrone", "input": {}, "result": {"best_log_age": 8.0}},
    ]
    assert literature_prior_violations("The best-fit age is 100 Myr.", tool_results) == []


def test_reply_contains_cjk_detects_chinese_prose():
    """X (PART X scheme D): Chinese prose in reply triggers the CJK guard hardblock."""
    from app.services.claim_validator import reply_contains_cjk
    assert reply_contains_cjk("符合昴星团约 100 Myr 的年龄")
    assert reply_contains_cjk("根据 Gaia DR3 ...")
    assert reply_contains_cjk("年龄: ~100 Myr (年轻疏散星团)")


def test_reply_contains_cjk_english_passes():
    """X: pure English reply does not trigger the CJK guard."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk("The Pleiades age is approximately 100 Myr.")
    assert not reply_contains_cjk("")
    assert not reply_contains_cjk("GCVS catalog returns Period = 5.366208 days.")


def test_reply_contains_cjk_scientific_unicode_passes():
    """X: Greek letters / Å / ° / ± / >= / ~ and similar scientific Unicode within DejaVu
    font support are not CJK and are passed through by the guard."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk(r"$\alpha$ Cen A, $T_{\rm eff}$ = 5800 K")
    assert not reply_contains_cjk("6563 Å H-alpha, ±0.3 mag, ≈5780 K, RA 180°")
    assert not reply_contains_cjk("naïve façade")


def test_reply_contains_cjk_threshold_tolerates_single_char():
    """X: threshold 2 — a single CJK character (e.g. a proper-noun citation) does not trigger,
    avoiding false positives. But prose lead words of >= 2 CJK characters will always match."""
    from app.services.claim_validator import reply_contains_cjk
    assert not reply_contains_cjk("A 一 B")   # 1 CJK, below threshold
    assert reply_contains_cjk("根据")          # 2 CJK at threshold
    assert reply_contains_cjk("符合约")        # 3 CJK
    assert reply_contains_cjk("一" * 10)       # well above


def test_literature_prior_distance_passes_with_run_adql():
    """W1: distance supported by run_adql (Gaia parallax) is sufficient to pass."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "run_adql", "input": {}, "result": {"data": {"parallax": [7.35]}}},
    ]
    assert literature_prior_violations("The distance is ~136 pc.", tool_results) == []


def test_literature_prior_mass_with_only_search_objects_is_violation():
    """W1: search_objects is not a measurement/citation tool for mass; a mass claim is a violation."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [
        {"tool": "search_objects", "input": {}, "result": {"results": []}},
    ]
    vios = literature_prior_violations("Typical mass is 2 M_sun.", tool_results)
    assert len(vios) >= 1


def test_literature_prior_no_claim_labels_no_violations():
    """W1: reply contains no age/mass/distance, so naturally no W1 violation (label filtering only)."""
    from app.services.claim_validator import literature_prior_violations
    tool_results = [{"tool": "run_adql", "input": {}, "result": {"data": {"x": [1]}}}]
    assert literature_prior_violations("The period is 5.366 days.", tool_results) == []


# ── Stage 6 P0: blocked_reply_with_narrative — preserve AI narrative ─────


def test_blocked_reply_with_narrative_redacts_uncited_in_place():
    """Stage 6 P0: when AI cites uncited numbers, we should redact them in
    the original reply but keep the surrounding narrative (methodology,
    caveats, qualitative explanations)."""
    from app.services.claim_validator import (
        blocked_reply_with_narrative,
        validate_claims,
    )
    tool_results = [{
        "tool": "fit_line_lfr",
        "result": {"posterior": {"H0_med": 67.36, "slope_med": 0.792}},
    }]
    reply = (
        "Based on the Bayesian linear regression, we find the slope is 0.792 "
        "and the inferred H0 is 73.04 km/s/Mpc which differs from the "
        "Planck baseline. The method used was emcee MCMC with 1500 steps."
    )
    validation = validate_claims(reply, tool_results)
    assert not validation.ok
    assert len(validation.uncited) >= 1
    out = blocked_reply_with_narrative(validation, reply)
    assert "Reply withheld" in out
    assert "---" in out
    # AI's narrative is preserved (methodology + qualitative wording)
    assert "Bayesian linear regression" in out
    assert "emcee MCMC" in out
    assert "differs from the" in out
    # The uncited number 73.04 should be redacted in the narrative section
    assert "[unverified: 73.04]" in out


def test_blocked_reply_with_narrative_falls_back_when_reply_empty():
    """Empty original reply falls back to banner-only (legacy behavior)."""
    from app.services.claim_validator import (
        blocked_reply_text,
        blocked_reply_with_narrative,
        validate_claims,
    )
    tool_results = [{"tool": "run_python", "result": {"value": 1.0}}]
    validation = validate_claims("The mass is 5.5 M_sun.", tool_results)
    out_with_empty_reply = blocked_reply_with_narrative(validation, "")
    out_banner_only = blocked_reply_text(validation)
    assert out_with_empty_reply == out_banner_only


def test_blocked_reply_with_narrative_handles_overlapping_spans():
    """Redaction must handle overlapping/duplicate uncited spans without
    corrupting the reply (dedupe by earliest span)."""
    from app.services.claim_validator import (
        Claim,
        _redact_uncited_phrases,
    )
    reply = "Slope value 0.792 detected here."
    uncited = [
        Claim(label="a", raw="0.792", value=0.792, start=12, end=17),
        Claim(label="b", raw="0.792", value=0.792, start=12, end=17),
    ]
    out = _redact_uncited_phrases(reply, uncited)
    assert out == "Slope value [unverified: 0.792] detected here."


def test_attach_draft_to_banner_with_literature_narrative_banner():
    """Stage 6 P0a follow-up: literature_narrative banner + AI draft assembly;
    the full draft is preserved (banner already lists line numbers for locating, no redaction)."""
    from app.services.claim_validator import (
        CitationViolation,
        attach_draft_to_banner,
        blocked_unsupported_narrative_reply_text,
    )
    violations = [
        CitationViolation(
            kind="literature_fallback",
            match_text="literature values typical for this object",
            line_number=13,
        ),
    ]
    banner = blocked_unsupported_narrative_reply_text(violations)
    draft = (
        "Based on the search results and the Bayesian fit, the slope is "
        "consistent with literature values typical for this object. "
        "The methodology used was emcee MCMC with 1500 steps."
    )
    out = attach_draft_to_banner(banner, draft)
    assert "Reply withheld" in out
    assert "(line 13)" in out
    assert "---" in out
    assert "AI's draft response" in out
    assert "Bayesian fit" in out
    assert "emcee MCMC" in out
    assert "literature values typical for this object" in out


def test_attach_draft_to_banner_with_citation_banner():
    """citation banner + AI draft assembly, the full draft is preserved."""
    from app.services.claim_validator import (
        CitationViolation,
        attach_draft_to_banner,
        blocked_citation_reply_text,
    )
    violations = [
        CitationViolation(
            kind="invalid_bibcode",
            match_text="2024XXX..001A",
            line_number=7,
        ),
    ]
    banner = blocked_citation_reply_text(violations)
    draft = "The fit shows H0 = 67.36 km/s/Mpc per (2024XXX..001A)."
    out = attach_draft_to_banner(banner, draft)
    assert "Reply withheld" in out
    assert "(line 7)" in out
    assert "---" in out
    assert "AI's draft response" in out
    assert "H0 = 67.36 km/s/Mpc" in out


def test_attach_draft_to_banner_falls_back_when_reply_empty():
    """Empty / blank draft falls back to banner-only."""
    from app.services.claim_validator import attach_draft_to_banner

    banner = "⚠ Reply withheld: some reason."
    assert attach_draft_to_banner(banner, "") == banner
    assert attach_draft_to_banner(banner, "   \n\n") == banner


def test_unclassified_literature_violations_blocks_uncited_search_paper():
    """Stage 6 P0c-C (2026-05-19): citing a paper returned by search_literature without
    calling classify_literature_relevance → violation."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [
                    {"bibcode": "2024A&A...678A.123S", "title": "DESI BAO"},
                    {"bibcode": "2024arXiv2401.01001A", "title": "Other"},
                ],
            },
        },
        # no classify_literature_relevance
    ]
    reply = "Based on 2024A&A...678A.123S, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "unclassified_literature"
    assert violations[0].match_text == "2024A&A...678A.123S"


def test_unclassified_literature_violations_passes_after_classify():
    """classify_literature_relevance called and labeled Direct, then cited → no violation."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [{"bibcode": "2024A&A...678A.123S", "title": "DESI BAO"}],
            },
        },
        {
            "tool": "classify_literature_relevance",
            "result": {
                "classifications": [
                    {
                        "bibcode": "2024A&A...678A.123S",
                        "relevance": "Direct",
                        "reason": "DESI DR1 BAO directly answers H0 question",
                    },
                ],
            },
        },
    ]
    reply = "Based on 2024A&A...678A.123S, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert violations == []


def test_unclassified_literature_violations_blocks_cited_off_topic_paper():
    """classify labeled Off-topic, but reply still cites it → violation (kind=cited_off_topic_paper)."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [{"bibcode": "2024A&A...678A.123S", "title": "DESI BAO"}],
            },
        },
        {
            "tool": "classify_literature_relevance",
            "result": {
                "classifications": [
                    {
                        "bibcode": "2024A&A...678A.123S",
                        "relevance": "Off-topic",
                        "reason": "BAO not relevant to this question",
                    },
                ],
            },
        },
    ]
    reply = "Based on 2024A&A...678A.123S, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "cited_off_topic_paper"
    assert violations[0].match_text == "2024A&A...678A.123S"


def test_unclassified_literature_violations_blocks_uncited_arxiv_fallback_paper():
    """2026-07-03: the arXiv fallback of search_literature identifies papers
    as "arXiv:<id>" in the bibcode field (api/citations.py) instead of an ADS
    bibcode. Citing such a paper by arXiv id must hit the same Stage 6
    classification barrier as the bibcode form — previously it bypassed the
    gate entirely (BIBCODE_RE never matches the arXiv form)."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [
                    {
                        "bibcode": "arXiv:2404.03002",
                        "title": "DESI DR1 BAO cosmology",
                        "arxiv_url": "http://arxiv.org/abs/2404.03002",
                    },
                ],
            },
        },
        # no classify_literature_relevance
    ]
    reply = "Based on arXiv:2404.03002, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "unclassified_literature"
    assert violations[0].match_text == "arXiv:2404.03002"


def test_unclassified_literature_violations_passes_arxiv_fallback_after_classify():
    """Classified Direct under the same "arXiv:<id>" identifier -> citable."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [{"bibcode": "arXiv:2404.03002", "title": "DESI DR1 BAO"}],
            },
        },
        {
            "tool": "classify_literature_relevance",
            "result": {
                "classifications": [
                    {
                        "bibcode": "arXiv:2404.03002",
                        "relevance": "Direct",
                        "reason": "DESI DR1 BAO directly answers the question",
                    },
                ],
            },
        },
    ]
    reply = "Based on arXiv:2404.03002, the BAO measurement gives H0 = 67."
    assert unclassified_literature_violations(reply, tool_results) == []


def test_unclassified_literature_violations_blocks_off_topic_arxiv_fallback():
    """Off-topic classification must also bind to the arXiv-id citation form."""
    from app.services.claim_validator import unclassified_literature_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "results": [{"bibcode": "arXiv:2404.03002", "title": "DESI DR1 BAO"}],
            },
        },
        {
            "tool": "classify_literature_relevance",
            "result": {
                "classifications": [
                    {
                        "bibcode": "arXiv:2404.03002",
                        "relevance": "Off-topic",
                        "reason": "not relevant to this question",
                    },
                ],
            },
        },
    ]
    reply = "Based on arXiv:2404.03002, the BAO measurement gives H0 = 67."
    violations = unclassified_literature_violations(reply, tool_results)
    assert len(violations) == 1
    assert violations[0].kind == "cited_off_topic_paper"
    assert violations[0].match_text == "arXiv:2404.03002"


def test_blocked_unclassified_literature_reply_text_groups_unclassified_and_off_topic():
    """banner text contains 2 groups: unclassified list + Off-topic list."""
    from app.services.claim_validator import (
        CitationViolation,
        blocked_unclassified_literature_reply_text,
    )

    violations = [
        CitationViolation(kind="unclassified_literature", match_text="2024A", line_number=3),
        CitationViolation(kind="cited_off_topic_paper", match_text="2023B", line_number=5),
    ]
    text = blocked_unclassified_literature_reply_text(violations)
    assert "Reply withheld" in text
    assert "classify_literature_relevance" in text
    assert "not classified" in text
    assert "Off-topic" in text
    assert "2024A" in text
    assert "2023B" in text
    assert "(line 3)" in text
    assert "(line 5)" in text


# -------------------- PART AD: thousands separator (B3) --------------------


def test_extract_thousands_separator_in_value():
    # 5,800 must be read as 5800, not split into 5 and 800.
    claims = extract_claims("The star has T_eff = 5,800 K.")
    assert any(c.label == "teff_k" and c.value == pytest.approx(5800) for c in claims)


def test_thousands_normalization_leaves_list_separators_untouched():
    # comma+space (coordinate / list separators) must NOT be merged...
    assert _strip_thousands_separators("ra, dec = 12, 345") == "ra, dec = 12, 345"
    # ...but a genuine thousands grouping is normalized, including 1,234,567.
    assert _strip_thousands_separators("count 1,234,567 rows") == "count 1234567 rows"


# -------------------- PART AD: partial-failure fabrication (B2) --------------------


def test_partial_failure_fabricated_number_still_blocked():
    # search_literature failed (0 hits) but run_adql succeeded. A number the
    # failed tool would have provided is not in the universe → still blocked,
    # without a blanket "any failure blocks the turn" rule that would also
    # reject the legitimate run_adql-backed parts.
    tool_results = [
        {"tool": "search_literature",
         "result": {"success": False, "__tool_status__": "EMPTY", "row_count": 0}},
        {"tool": "run_adql", "result": {"parallax": 7.50}},
    ]
    r = validate_claims("Based on the literature, the redshift is z = 2.45.", tool_results)
    assert not r.ok


# -------------------- P0-b: power-of-ten scientific notation --------------------


def test_normalize_sci_notation_basic_forms():
    from app.services.claim_validator import _normalize_sci_notation
    assert _normalize_sci_notation("3.5 × 10^8") == "3.5e8"
    assert _normalize_sci_notation("3.5 x 10^8") == "3.5e8"
    assert _normalize_sci_notation("3.5·10**8") == "3.5e8"
    assert _normalize_sci_notation("1.2 × 10^-3") == "1.2e-3"
    # superscript exponent forms
    assert _normalize_sci_notation("3.5 × 10⁸") == "3.5e8"
    assert _normalize_sci_notation("1.2 × 10⁻³") == "1.2e-3"
    # bare power of ten → implicit mantissa 1
    assert _normalize_sci_notation("about 10^8 M_sun") == "about 1e8 M_sun"


def test_normalize_sci_notation_leaves_prose_untouched():
    from app.services.claim_validator import _normalize_sci_notation
    # "x" not preceded by a number is not a product sign
    assert _normalize_sci_notation("box 10 stars") == "box 10 stars"
    # "2 x 10 stars" has no ^/** exponent marker after 10 → not sci-notation
    assert _normalize_sci_notation("2 x 10 stars") == "2 x 10 stars"
    # plain e-notation is already understood by _NUM, left as-is
    assert _normalize_sci_notation("1.2e-3 mas") == "1.2e-3 mas"


def test_extract_power_of_ten_mass_keeps_full_value():
    # Regression for the "mass_solar parsed 3.5 × 10^8 as 8.0" bug.
    claims = extract_claims("The galaxy mass is 3.5 × 10^8 M_sun.")
    vals = [c.value for c in claims if c.label == "mass_solar"]
    assert any(v == pytest.approx(3.5e8) for v in vals), vals
    assert 8.0 not in vals  # the old broken extraction must be gone


def test_extract_power_of_ten_xray_luminosity():
    claims = extract_claims("AGN L_X = 1.5 × 10^44 erg/s reported.")
    assert any(c.value == pytest.approx(1.5e44) for c in claims)


def test_power_of_ten_fabrication_is_flagged():
    # universe has 4.2e8; reply claims 3.5 × 10^8 (wrong) → must be flagged
    # now that the full value (not the bare exponent 8) is extracted.
    tool_results = [{"result": {"mass": 4.2e8}}]
    r = validate_claims("The mass is 3.5 × 10^8 M_sun.", tool_results)
    assert not r.ok


def test_power_of_ten_correct_value_passes():
    tool_results = [{"result": {"mass": 3.5e8}}]
    r = validate_claims("The mass is 3.5 × 10^8 M_sun.", tool_results)
    assert r.ok


# -------------------- P0-a: free-text fields don't pollute the universe ------


def test_freetext_banner_numbers_excluded_from_universe():
    from app.services.claim_validator import _iter_numeric_values
    payload = {
        "__message_to_model__": "Try a narrower TOP 5000 query within 365 days.",
        "__suggested_next_step__": "Re-run with radius 0.5 deg.",
        "result": {"parallax": 7.353},
    }
    universe = set(_iter_numeric_values(payload))
    assert 7.353 in universe        # real data value still harvested
    assert 5000 not in universe     # banner prose numbers excluded
    assert 365 not in universe
    assert 0.5 not in universe


def test_fabrication_cannot_launder_via_banner_text():
    # A failed tool's banner mentions "5000"; the reply must not be able to
    # cite 5000 as a result just because it appears in injected prose.
    tool_results = [
        {"tool": "run_adql",
         "result": {"__tool_status__": "EMPTY", "row_count": 0,
                    "__message_to_model__": "0 rows; try TOP 5000."}},
    ]
    r = validate_claims("We found 5000 member stars.", tool_results)
    assert not r.ok


def test_data_rows_still_populate_universe():
    # Regression guard: P0-a must NOT strip real numeric data values that
    # happen to sit in nested rows.
    from app.services.claim_validator import _iter_numeric_values
    payload = {"result": {"rows": [{"mag": 12.3}, {"mag": 13.1}]}}
    universe = set(_iter_numeric_values(payload))
    assert 12.3 in universe and 13.1 in universe


# ---------------------------------------------------------------------------
# 2026-09-02 (review H9): a bare hypothesis / forecast noun must not wash a
# strong cosmology conclusion through the conclusion gate.
# ---------------------------------------------------------------------------


def test_bare_hypothesis_or_forecast_noun_does_not_wash_strong_conclusion() -> None:
    from app.services.claim_validator import (
        _strong_conclusion_from_sentence,
        scientific_conclusion_scope_violations,
    )

    washing = [
        "Our hypothesis is confirmed: the Hubble tension is resolved by a local void.",
        "Consistent with our hypothesis, the data reject LCDM and dark energy evolves.",
        "The forecast is now confirmed: the Hubble tension is resolved by a local void.",
        "We hypothesise that the Hubble tension is resolved by a local void.",
        "我们的假设得到证实：哈勃张力已被局部空洞解决。",
        "预测已被证实：哈勃张力已被局部空洞解决。",
    ]
    for sentence in washing:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence
        assert scientific_conclusion_scope_violations(sentence, []), sentence

    hedged = [
        "Hypothesis: a local void resolves the Hubble tension.",
        "**Hypothesis:** a local void resolves the Hubble tension.",
        "- Hypothesis: a local void resolves the Hubble tension.",
        "A local void resolving the Hubble tension is a hypothesis worth testing.",
        "The model forecast that the Hubble tension is resolved once calibration improves.",
        "假设：局部空洞解决哈勃张力。",
        "预测新的标定将解决哈勃张力。",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_hypothesis_narrowing_is_a_strict_subset_of_the_old_exemption() -> None:
    """The narrowed hedge must never exempt a form the bare-noun alternation
    did not already exempt. "forecasts that" (plural) and 预计 were outside
    \bforecast\b / 预测 at origin/main, so they stay non-exempt here; only the
    label and predicate forms that contained the bare noun survive."""
    from app.services.claim_validator import _strong_conclusion_from_sentence

    for sentence in (
        "The model forecasts that the Hubble tension is resolved once calibration improves.",
        "预计新的标定将解决哈勃张力。",
        # The predicate form admits only the two nouns main exempted as bare
        # words, in the singular.  "conjecture", "prediction" and 猜想 were
        # never exempt on main and an earlier revision of this branch quietly
        # admitted them (audit 2026-09-03); plurals were never exempt either.
        "Our conjecture is that a local void resolves the Hubble tension.",
        "Our prediction is that a local void resolves the Hubble tension.",
        "我们的猜想是局部空洞解决哈勃张力。",
        "Our hypotheses are that a local void resolves the Hubble tension.",
        "Our forecasts are that the Hubble tension is resolved after recalibration.",
    ):
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence
    # And the forms that DO survive are exactly the bare-noun ones main had.
    for sentence in (
        "Our hypothesis is that a local void resolves the Hubble tension.",
        "Our forecast is that the Hubble tension is resolved after recalibration.",
        "我们的假设是局部空洞解决哈勃张力。",
        "我们的预测是局部空洞解决哈勃张力。",
    ):
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_hypothesis_label_regex_is_linear_on_a_pathological_prefix() -> None:
    """CodeQL py/polynomial-redos: the first draft wrote the bold marker as an
    optional group between two ``\\s*`` runs, which backtracks polynomially on
    "hypothesis" followed by a long whitespace run.  The reply text is
    attacker-influenced, so the shape matters, not just the result."""
    import time

    from app.services.claim_validator import _HYPOTHESIS_LABEL_RE

    pathological = "hypothesis" + " " * 60_000 + "!"
    started = time.perf_counter()
    assert _HYPOTHESIS_LABEL_RE.search(pathological) is None
    assert time.perf_counter() - started < 0.5


def test_hedge_phrase_does_not_launder_a_confirmed_conclusion() -> None:
    """A hedge word plus a confirmation in the same sentence is an assertion,
    not a hypothesis: "The forecast that X is resolved is now confirmed"
    must not skip the attestation requirement (review 2026-09-03)."""
    from app.services.claim_validator import _strong_conclusion_from_sentence

    for sentence in (
        "The forecast that the Hubble tension is resolved is now confirmed.",
        "This is a hypothesis worth testing, and the Hubble tension is resolved "
        "by a local void as now confirmed.",
        "Hypothesis: confirmed - the Hubble tension is resolved by a local void.",
        "假设：已被证实，哈勃张力被局部空洞解决。",
    ):
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence

    # Unrelated pre-existing gap, recorded so it is not mistaken for this
    # change: the strong-conclusion patterns require "Hubble tension ...
    # resolved" in that order, so "a local void resolving the Hubble tension"
    # is not detected as a conclusion at all, with or without a confirmation.
    assert (
        _strong_conclusion_from_sentence(
            "A local void resolving the Hubble tension is now confirmed."
        )
        is None
    )

    # A hedge without a confirmation is still a hedge.
    for sentence in (
        "Hypothesis: a local void resolves the Hubble tension.",
        "The model forecast that the Hubble tension is resolved once calibration improves.",
    ):
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_hypothesis_label_accepts_nested_markdown_markers() -> None:
    """``- **Hypothesis:** ...`` is the shape a model actually writes; missing
    it turned an explicitly labelled hypothesis into a blocked conclusion."""
    from app.services.claim_validator import _strong_conclusion_from_sentence

    for sentence in (
        "- **Hypothesis:** a local void resolves the Hubble tension.",
        "* **Hypothesis:** a local void resolves the Hubble tension.",
        "> **Hypothesis:** a local void resolves the Hubble tension.",
        "1. **Hypothesis:** a local void resolves the Hubble tension.",
        "- **Hypothesis**: a local void resolves the Hubble tension.",
        "**Hypothesis:** a local void resolves the Hubble tension.",
        "- Hypothesis: a local void resolves the Hubble tension.",
    ):
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_a_confirmation_of_an_unrelated_premise_keeps_the_hedge() -> None:
    """The confirmation guard must qualify the conclusion, not any clause:
    "Although the calibration is confirmed, there is no evidence the Hubble
    tension is resolved" is a denial, and blocking it is a false kill
    (review 2026-09-03)."""
    from app.services.claim_validator import _strong_conclusion_from_sentence

    for sentence in (
        "Although the calibration is confirmed, there is no evidence the Hubble tension is resolved.",
        "The pipeline is confirmed, but we cannot conclude that the Hubble tension is resolved.",
        "The instrument model is confirmed; the data do not show that dark energy evolves.",
    ):
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    # The laundering shapes stay flagged.
    for sentence in (
        "The forecast that the Hubble tension is resolved is now confirmed.",
        "Our hypothesis is confirmed: the Hubble tension is resolved by a local void.",
    ):
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence


def test_chinese_hypothesis_label_survives_markdown_markers() -> None:
    """The English label matcher learned list and bold markers; the Chinese
    one is written in the same forms and must not be blocked."""
    from app.services.claim_validator import _strong_conclusion_from_sentence

    for sentence in (
        "- **假设：** 局部空洞解决哈勃张力。",
        "* 假设：局部空洞解决哈勃张力。",
        "1. **假设：** 局部空洞解决哈勃张力。",
        "> 假设：局部空洞解决哈勃张力。",
        "假设：局部空洞解决哈勃张力。",
    ):
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    assert _strong_conclusion_from_sentence(
        "我们的假设得到证实：哈勃张力已被局部空洞解决。"
    ) is not None


def test_a_denial_of_another_topic_does_not_wash_a_confirmed_conclusion() -> None:
    """An explicit denial only restores the exemption it actually governs.

    The denial was matched against the whole sentence, so a denial about an
    unrelated subject cancelled the confirmation rule and let a confirmed
    conclusion through untouched (Codex review 2026-09-03).  The denial is now
    read in the clause that holds the conclusion; the confirmation and the
    hedge stay sentence-scoped, so this can only remove an exemption.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    washed = [
        "Although there is no evidence for spatial curvature, our forecast "
        "is confirmed: the Hubble tension is resolved by a local void.",
        "There is no evidence for a neutrino mass, and the forecast is now "
        "confirmed: the Hubble tension is resolved by a local void.",
    ]
    for sentence in washed:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence

    # A denial that DOES govern the conclusion still exempts it, and so does
    # every other shape the exemption was built for.
    preserved = [
        "Although the calibration is confirmed, there is no evidence the "
        "Hubble tension is resolved.",
        "There is no evidence that the Hubble tension is resolved by a local void.",
        "Hypothesis: a local void resolves the Hubble tension.",
        "A local void may resolve the Hubble tension.",
        "没有证据表明哈勃张力已解决。",
    ]
    for sentence in preserved:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_narrowing_did_not_take_ordinary_hedges_with_it() -> None:
    """Two exemptions that main had and the H9 narrowing dropped.

    ``Our hypothesis is that X`` is ordinary hedged prose; only the bare noun
    beside a confirmation was meant to stop washing.  And ``the data do not
    resolve the Hubble tension`` is a denial the hedge vocabulary already
    recognised, but the denial vocabulary did not, so scoping the denial to
    the conclusion's clause turned it into a strong conclusion whenever
    anything else in the sentence was confirmed (Codex review 2026-09-03).

    Both are restored here WITHOUT reopening the washing hole: the
    confirmation rule still cancels a hedge, so every washed sentence below
    stays a violation.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    exempt = [
        "Our hypothesis is that a local void resolves the Hubble tension.",
        "Although the calibration is confirmed, the data do not resolve the "
        "Hubble tension.",
        "The data do not resolve the Hubble tension.",
        "This is a hypothesis worth testing: a local void resolves the Hubble tension.",
        "Although the calibration is confirmed, there is no evidence the "
        "Hubble tension is resolved.",
    ]
    for sentence in exempt:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    violations = [
        # The verb form is deliberately not exempt (2026-09-02 user decision).
        "We hypothesise that a local void resolves the Hubble tension.",
        "Our hypothesis is confirmed: the Hubble tension is resolved by a local void.",
        "The forecast is now confirmed: the Hubble tension is resolved by a local void.",
        "Although there is no evidence for spatial curvature, our forecast is "
        "confirmed: the Hubble tension is resolved by a local void.",
        "The model forecasts that a local void resolves the Hubble tension.",
        "The Hubble tension is resolved by a local void.",
    ]
    for sentence in violations:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence


def test_the_hedge_decision_is_made_in_the_conclusion_clause() -> None:
    """A sentence can carry a denied conclusion AND an asserted one.

    Three findings, one root cause (Codex review 2026-09-03): every term of
    the hedge decision -- the hedge, the confirmation that cancels it, the
    denial that restores it -- describes a specific proposition, and reading
    any of them across the whole sentence attached it to the wrong one.  The
    detector also stopped at the first conclusion it matched, so a denied
    curvature claim masked an asserted dark-energy one in the same sentence.

    A confirmation in an EARLIER clause still cancels the hedge when what it
    confirms is the hypothesis itself; confirming some other premise does not.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    asserted = [
        # A denied curvature claim no longer masks the asserted dark-energy one.
        "Although there is no evidence for spatial curvature, our forecast is "
        "confirmed: dark energy evolves with time.",
        # The Hubble regex spans the comma, so the clause is taken from the
        # conclusion's end, not its whole span.
        "There is no evidence the Hubble tension is caused by calibration, but "
        "our forecast is confirmed: a local void resolves the Hubble tension.",
        "Although there is no evidence for spatial curvature, our forecast is "
        "confirmed: the Hubble tension is resolved by a local void.",
        # A confirmation of the hypothesis itself still cancels the hedge,
        # in either language.
        "Our hypothesis is confirmed: the Hubble tension is resolved by a local void.",
        "The forecast is now confirmed: the Hubble tension is resolved by a local void.",
        "假设：已被证实，哈勃张力被局部空洞解决。",
        "The forecast that the Hubble tension is resolved is now confirmed.",
        "Hypothesis: confirmed - the Hubble tension is resolved by a local void.",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence

    hedged = [
        # Confirming a PREMISE leaves the hedge standing.
        "The calibration is confirmed, while our hypothesis is that a local "
        "void resolves the Hubble tension.",
        "The calibration is confirmed, and the Hubble tension may be resolved.",
        # A hedge that introduces the conclusion through a colon labels the
        # whole sentence, as long as it carries no confirmation of its own.
        "This is a hypothesis worth testing: a local void resolves the Hubble tension.",
        "Although the calibration is confirmed, there is no evidence the "
        "Hubble tension is resolved.",
        "There is no evidence that dark energy evolves with time.",
        "Dark energy may evolve with time.",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_a_parenthetical_and_an_unpunctuated_coordination_are_read_correctly() -> None:
    """Two more clause-boundary errors, both false kills (Codex review 2026-09-03).

    A comma pair with no coordinating word is a parenthetical, not a clause
    boundary: "The Hubble tension may, after recalibration, be resolved" was
    reduced to " be resolved by a local void" and lost its own hedge.  The
    hedge pattern also could not span the parenthetical -- a gap present on
    main too -- so the modal now tolerates one interposed comma phrase.

    And two propositions can be joined with no punctuation at all: "The
    calibration is confirmed and our hypothesis is that X" stayed one clause,
    so the confirmation of the premise cancelled the hedge on the hypothesis.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    hedged = [
        "The Hubble tension may, after recalibration, be resolved by a local void.",
        "The Hubble tension may be resolved by a local void.",
        "The calibration is confirmed and our hypothesis is that a local void "
        "resolves the Hubble tension.",
        "The calibration is confirmed, and the Hubble tension may be resolved.",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    asserted = [
        # The same parenthetical around an ASSERTION is still a conclusion.
        "The Hubble tension is, after recalibration, resolved by a local void.",
        "This is a hypothesis worth testing, and the Hubble tension is resolved "
        "by a local void as now confirmed.",
        "The Hubble tension is resolved by a local void.",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence


def test_the_hedge_pattern_stays_linear_with_a_parenthetical() -> None:
    """The interposed group is comma-anchored, so nothing splits ambiguously."""
    import time

    from app.services.claim_validator import _NONASSERTIVE_COSMOLOGY_CONTEXT_RE

    timings = []
    for size in (4000, 16000, 64000):
        probe = "may" + " " * size + "," + "x" * size + "," + " " * size + "z"
        started = time.perf_counter()
        _NONASSERTIVE_COSMOLOGY_CONTEXT_RE.search(probe)
        timings.append(time.perf_counter() - started)
    assert timings[-1] < 1.0


def test_predicate_hedges_and_coordinated_predicates_survive() -> None:
    """Three more false kills from the narrowing and the clause split.

    ``Our forecast is that X`` is the same predicate shape as ``our
    hypothesis is that X``; the Chinese ``我们的假设是 X`` had no equivalent
    at all after the bare 假设 alternative was narrowed; and treating every
    ``and`` as a clause boundary detached a coordinated predicate from its
    own modal -- ``may weaken and ultimately be resolved`` lost the ``may``
    (Codex review 2026-09-03).

    ``and``/``yet`` now split only when a new SUBJECT follows, which is what
    separates "and our hypothesis is that X" from "and ultimately be
    resolved".
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    hedged = [
        "Our forecast is that the Hubble tension is resolved after recalibration.",
        "我们的假设是局部空洞解决哈勃张力。",
        "The Hubble tension may weaken and ultimately be resolved by a local void.",
        "The calibration is confirmed and our hypothesis is that a local void "
        "resolves the Hubble tension.",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    asserted = [
        "The forecast is now confirmed: the Hubble tension is resolved by a local void.",
        "假设：已被证实，哈勃张力被局部空洞解决。",
        "哈勃张力被本地空洞解决。",
        "The Hubble tension is resolved by a local void.",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence


def test_an_adverb_in_the_auxiliary_and_a_shared_modal() -> None:
    """Two more shapes, one in each direction (Codex review 2026-09-03).

    ``has now been confirmed`` is as ordinary as ``has been confirmed``, and
    the auxiliary pattern did not admit the adverb, so the confirmation was
    missed and a laundered conclusion stayed exempt.

    And one modal can scope two coordinated clauses: "The Hubble tension may
    weaken and the remaining discrepancy be resolved by a local void" leaves
    the second clause with a bare infinitive and no modal of its own.  Only a
    bare infinitive triggers the lookback, so a second clause with its own
    finite verb still stands alone.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    asserted = [
        "The forecast that the Hubble tension is resolved has now been confirmed.",
        "Our hypothesis has now been confirmed: the Hubble tension is resolved "
        "by a local void.",
        # A finite verb after "and" is its own clause, hedge or not.
        "The data may be noisy and the Hubble tension is resolved by a local void.",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence

    hedged = [
        "The Hubble tension may weaken and the remaining discrepancy be "
        "resolved by a local void.",
        "The Hubble tension may weaken and ultimately be resolved by a local void.",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_a_coordinator_a_subject_bound_anchor_and_a_confirmed_denial() -> None:
    """Four more false kills, each from reading the wrong clause (Codex review
    2026-09-03).

    PRRT_kwDORoeoE86etYLJ: ", and after repeated checks," starts with a
    coordinating word, so it opens a new proposition rather than an aside;
    reading it as a parenthetical reverted the clause to the sentence start,
    where an unrelated confirmation cancelled "may be resolved".

    PRRT_kwDORoeoE86etYLM: the dark-energy anchor was the LAST evolution word
    in the sentence, which belonged to "galaxy formation evolves" in the
    unrelated second clause, so the conclusion's own clause lost its "may".
    The anchor now follows its subject, which also closes the mirror image:
    a hedge in the OTHER clause no longer washes an asserted evolution.

    PRRT_kwDORoeoE86eypXC: a confirmed forecast in an earlier clause cancelled
    "our hypothesis is that" across a "while".  A prefix confirmation cancels
    only when it introduces the conclusion clause: the prefix ends with a
    colon or dash, or no coordinating word stands between the two.

    PRRT_kwDORoeoE86eypXG asked for "confirmed / shown / found not to
    resolve" to be read as a negative result.  That exemption was withdrawn
    (2026-09-03, round eleven): every relaxation found after it -- "confirmed
    not to resolve the S8 tension yet resolves the Hubble tension" and thirty
    more -- was downstream of it, so the phrase is now read exactly as
    origin/main reads it: no hedge.  The two sentences it used to exempt are
    asserted below: "shown not to resolve" exactly as on main, and "Our
    hypothesis is confirmed not to resolve" through the narrowed bare-noun
    hedge (origin/main still exempts that one via the bare noun).  A
    user-signed relaxation may reinstate them.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    hedged = [
        "The calibration is confirmed, and after repeated checks, the Hubble "
        "tension may be resolved by a local void.",
        "Dark energy may evolve with time, while galaxy formation evolves nonlinearly.",
        "Our dark-energy forecast is confirmed, while our hypothesis is that a "
        "local void may resolve the Hubble tension.",
        # Confirming a premise still leaves the predicate hedge standing.
        "The calibration is confirmed, while our hypothesis is that a local "
        "void resolves the Hubble tension.",
        # The anchor follows its subject in either clause order.
        "Galaxy formation evolves nonlinearly, while dark energy may evolve with time.",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    asserted = [
        # 1: the same coordinator around an assertion; a real aside (no
        # coordinating word) still reverts the clause to the confirmation.
        "The calibration is confirmed, and after repeated checks, the Hubble "
        "tension is resolved by a local void.",
        "Our hypothesis is confirmed, after repeated checks, the Hubble tension "
        "is resolved by a local void.",
        "The Hubble tension is, after repeated checks, resolved by a local void.",
        # 2: a hedge on the unrelated clause does not reach the dark-energy one.
        "Dark energy evolves with time, while galaxy formation may evolve nonlinearly.",
        "Galaxy formation may evolve nonlinearly, while dark energy evolves with time.",
        "Galaxy formation evolves nonlinearly, and dark energy evolves with time.",
        # 3: a confirmation that INTRODUCES the clause still cancels its hedge.
        "Our hypothesis is confirmed: the Hubble tension is resolved by a local void.",
        "The forecast is now confirmed: the Hubble tension is resolved by a local void.",
        "假设：已被证实，哈勃张力被局部空洞解决。",
        "Our hypothesis has now been confirmed: the Hubble tension is resolved "
        "by a local void.",
        "Our hypothesis is confirmed: a local void may resolve the Hubble tension.",
        "Our hypothesis is confirmed, a local void may resolve the Hubble tension.",
        "Our dark-energy forecast is confirmed, while the Hubble tension is "
        "resolved by a local void.",
        # 4: "confirmed / shown / found not to" is not a hedge (main parity;
        # the round-seven exemption was withdrawn), and a confirmation OF the
        # resolution is still a conclusion.
        "Our hypothesis is confirmed not to resolve the Hubble tension.",
        "The void model is shown not to resolve the Hubble tension.",
        "Our hypothesis is confirmed to resolve the Hubble tension.",
        "Our hypothesis is confirmed: a local void is shown to resolve the Hubble tension.",
        "The void model is confirmed not to be ruled out and the Hubble tension "
        "is resolved by a local void.",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence


def test_a_confirmed_denial_is_read_as_main_reads_it() -> None:
    """"confirmed / shown / found not to <verb>" is not a hedge.

    Round seven read it as a negative result (PRRT_kwDORoeoE86eypXG).  Every
    relaxation found since -- "The void model is confirmed not to resolve
    the S8 tension yet resolves the Hubble tension", "... because it
    resolves ...", "A void model confirmed not to resolve the S8 tension
    still resolves ...", "Dark energy is confirmed not to evolve at low
    redshift yet evolves at high redshift", an emphasised verb, a verbless
    conclusion after the denial -- came from that one exemption, so it is
    withdrawn and the phrase is read exactly as origin/main (3a7e6e4) reads
    it.  The four negative-result sentences the PR body had disclosed as
    exempt are caught again, as on main; a user-signed relaxation may
    reinstate them later.  The denial forms main already had are untouched.

    Known and disclosed, not pinned: "The void model is confirmed not to
    resolve the S8 tension yet may resolve the Hubble tension" is caught
    (main exempts it), because the confirmation in the clause cancels the
    modal and only the withdrawn alternative used to restore it.  Reading
    "confirmed not to" as a denial again is the exemption this test
    withdraws, so that sentence waits for a user-signed decision.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    asserted = [
        # The four disclosed negative-result sentences, caught as on main.
        "The void model is shown not to resolve the Hubble tension.",
        "The void model is found not to alleviate the Hubble tension.",
        "The void model is confirmed not to resolve the Hubble tension.",
        "The data are confirmed not to favour spatial curvature.",
        # The relaxations the exemption let through.
        "The void model is confirmed not to resolve the S8 tension yet resolves "
        "the Hubble tension.",
        "The void model is confirmed not to resolve the S8 tension because it "
        "resolves the Hubble tension instead.",
        "A void model confirmed not to resolve the S8 tension still resolves the "
        "Hubble tension.",
        "Dark energy is confirmed not to evolve at low redshift yet evolves at "
        "high redshift.",
        "The void model is confirmed not to resolve the S8 tension yet "
        "**resolves** the Hubble tension.",
        "The void model is shown not to resolve the Hubble tension, which is "
        "instead resolved by a local void.",
        "The Hubble tension is resolved by a model confirmed not to resolve the "
        "S8 tension.",
        "The void model is confirmed not to resolve the S8 tension nor the "
        "Hubble tension.",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence

    # The denial forms main already had still hedge.
    hedged = [
        "The data do not resolve the Hubble tension.",
        "The void model failed to resolve the Hubble tension.",
        "There is no evidence that the Hubble tension is resolved by a local void.",
        "The void model does not show that the Hubble tension is resolved.",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence


def test_a_bare_infinitive_conjunct_inherits_the_modal_before_it() -> None:
    """A conjunct with no finite verb takes its modal from the conjunct before.

    "The Hubble tension may weaken, and the remaining discrepancy be resolved
    by a local void" is one hedged prediction: "be resolved" is a bare
    infinitive and "may" scopes over both conjuncts.  Splitting the sentence
    at the comma left the second conjunct with no modal of its own, and the
    lookback saw only " and", so an honest hedge was refused while
    origin/main exempts it (Codex review 2026-09-03, round seven).

    A conjunct whose verb is a bare infinitive -- be / get / become plus its
    complement, or a bare stem such as "persist" -- inherits the modal of
    the nearest earlier conjunct that has one, walking back across
    consecutive bare-infinitive conjuncts.  A conjunct with a finite verb
    inherits nothing, so "..., and a local void resolves it" stays caught:
    main exempts it only because it reads whole sentences, and keeping it
    caught is a tightening.  Only a modal is inherited, and a modal that a
    confirmation cancelled is not.  A Chinese conjunct that 而 / 并 / 且
    continues inherits 可能 the same way; a bare fullwidth comma does not.
    """
    from app.services.claim_validator import _strong_conclusion_from_sentence

    hedged = [
        "The Hubble tension may weaken, and the remaining discrepancy be "
        "resolved by a local void.",
        "The Hubble tension might weaken, or the remaining discrepancy be "
        "resolved by a local void.",
        "The Hubble tension may weaken, but the remaining discrepancy be "
        "resolved by a local void.",
        "The Hubble tension may weaken, and then be resolved by a local void.",
        "The Hubble tension might weaken, and the remaining discrepancy get "
        "resolved by a local void.",
        "The Hubble tension could weaken, the S8 tension persist, and the "
        "remaining discrepancy be resolved by a local void.",
        "The Hubble tension might weaken, and, in fact, the remaining "
        "discrepancy be resolved by a local void.",
        "The Hubble tension may weaken and the remaining discrepancy be "
        "resolved by a local void.",
        "哈勃张力可能减弱，而剩余差异则被局部空洞解决。",
    ]
    for sentence in hedged:
        assert _strong_conclusion_from_sentence(sentence) is None, sentence

    asserted = [
        # A finite verb in the conjunct: nothing is inherited.
        "The Hubble tension may weaken, and a local void resolves it.",
        "The Hubble tension may weaken, and the remaining discrepancy is "
        "resolved by a local void.",
        "The Hubble tension may weaken, and the remaining discrepancy has been "
        "resolved by a local void.",
        "The Hubble tension may weaken, and the remaining discrepancy is to be "
        "resolved by a local void.",
        "The Hubble tension could weaken, the S8 tension persists, and the "
        "remaining discrepancy is resolved by a local void.",
        # The walk back stops at the first conjunct with a finite verb; one
        # with no modal, or with a cancelled one, gives nothing.
        "The calibration is confirmed, the S8 tension persist, and the Hubble "
        "tension be resolved by a local void.",
        "The calibration is confirmed, and we require that the Hubble tension "
        "be resolved by a local void.",
        "Our hypothesis is confirmed: the Hubble tension may weaken, and the "
        "remaining discrepancy be resolved by a local void.",
        # A Chinese comma with no connective is a clause of its own.
        "哈勃张力可能减弱，剩余的差异被局部空洞解决。",
    ]
    for sentence in asserted:
        assert _strong_conclusion_from_sentence(sentence) is not None, sentence
