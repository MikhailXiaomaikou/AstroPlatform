from __future__ import annotations


def _tool_with_dataset(article: str) -> list[dict]:
    return [
        {
            "tool": "run_adql",
            "result": {
                "provenance": {
                    "datasets": [{"article": article}],
                    "field_bibcodes": None,
                }
            },
        }
    ]


def test_bibcode_in_tool_result_has_no_violations():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = _tool_with_dataset("2023A&A...674A...1G")

    assert provenance_citation_violations("Gaia DR3 is cited as 2023A&A...674A...1G.", tool_results) == []


def test_bibcode_not_in_tool_result_is_violation():
    from app.services.claim_validator import provenance_citation_violations

    violations = provenance_citation_violations(
        "The value follows 1995IBVS.4148....1F.",
        _tool_with_dataset("2023A&A...674A...1G"),
    )

    assert len(violations) == 1
    assert violations[0].kind == "invalid_bibcode"
    assert violations[0].match_text == "1995IBVS.4148....1F"


def test_author_year_with_supporting_bibcode_has_no_violations():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = _tool_with_dataset("2000A&AS..143....9W")

    assert provenance_citation_violations("SIMBAD follows Wenger et al. (2000).", tool_results) == []


def test_author_year_without_year_match_is_violation():
    from app.services.claim_validator import provenance_citation_violations

    violations = provenance_citation_violations(
        "The period was reported by Fernie et al. (1995).",
        _tool_with_dataset("2017A&A...605A.100G"),
    )

    assert len(violations) == 1
    assert violations[0].kind == "suspicious_author_year"


def test_empty_tool_results_and_empty_reply_has_no_violations():
    from app.services.claim_validator import provenance_citation_violations

    assert provenance_citation_violations("", []) == []


def test_field_level_bibcode_pool_supports_reply():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [
        {
            "tool": "run_adql",
            "result": {
                "provenance": {
                    "datasets": [],
                    "field_bibcodes": {
                        "columns": {"plx_bibcode": ["2020yCat.1350....0G"]},
                        "mapping": {"plx_bibcode": "plx_value"},
                    },
                }
            },
        }
    ]

    assert provenance_citation_violations("Parallax uses 2020yCat.1350....0G.", tool_results) == []


def test_hardblock_flag_tracks_environment(monkeypatch):
    from app.services.claim_validator import citation_violations_should_block, provenance_citation_violations

    violations = provenance_citation_violations(
        "Fernie et al. (1995) reported this.",
        _tool_with_dataset("2023A&A...674A...1G"),
    )

    # PART Y Batch 1: 默认 (env 不设) → hardblock 开启
    monkeypatch.delenv("PROVENANCE_VALIDATOR_HARDBLOCK", raising=False)
    assert citation_violations_should_block(violations) is True

    # 显式禁用 → warn-only
    monkeypatch.setenv("PROVENANCE_VALIDATOR_HARDBLOCK", "false")
    assert citation_violations_should_block(violations) is False

    # 显式启用 → hardblock (保持向后兼容)
    monkeypatch.setenv("PROVENANCE_VALIDATOR_HARDBLOCK", "true")
    assert citation_violations_should_block(violations) is True


def test_alma_metadata_does_not_support_cii_luminosity_fwhm_claims():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    tool_results = [
        {
            "tool": "search_objects",
            "result": {
                "success": True,
                "results": [
                    {
                        "source": "alma",
                        "extra": {
                            "measurement_scope": "observation_metadata_only",
                            "line_measurements_available": False,
                            "line_measurement_note": (
                                "ALMA archive rows describe observations. Derived line luminosity "
                                "or FWHM values require a cited line-measurement table."
                            ),
                        },
                    }
                ],
                "provenance": {
                    "datasets": [
                        {
                            "service_key": "alma",
                            "service_name": "ALMA Science Archive",
                            "archive_version": "ALMA Science Archive current",
                        }
                    ]
                },
            },
        }
    ]

    violations = unsupported_literature_narrative_violations(
        "The log L[CII]-FWHM relation is visible in the ALMA metadata sample.",
        tool_results,
    )

    assert violations
    assert violations[0].kind == "unsupported_literature_narrative"


def test_search_literature_alone_does_not_support_cii_relation_claims():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    tool_results = [
        {
            "tool": "search_literature",
            "result": {
                "success": True,
                "result_granularity": "paper_abstract",
                "supports_measurement_claims": False,
                "results": [
                    {
                        "title": "ALPINE survey",
                        "authors": ["Example, A."],
                        "year": "2022",
                        "bibcode": "arXiv:2211.04968",
                    }
                ],
            },
        }
    ]

    violations = unsupported_literature_narrative_violations(
        "The log L[CII]-FWHM relation is visible in the literature sample.",
        tool_results,
    )

    assert violations


def test_literature_table_measurements_support_cii_relation_and_arxiv_author_year():
    from app.services.claim_validator import (
        provenance_citation_violations,
        unsupported_literature_narrative_violations,
        validate_claims,
    )

    tool_results = [
        {
            "tool": "extract_literature_tables",
            "result": {
                "success": True,
                "line_measurements": [
                    {
                        "source_name": "MACS1149-JD1",
                        "log_luminosity": 8.15,
                        "fwhm_km_s": 245.0,
                        "bibcode": "arXiv:2211.04968",
                        "arxiv_id": "2211.04968",
                        "citation": {
                            "bibcode": "arXiv:2211.04968",
                            "arxiv_id": "2211.04968",
                            "authors": ["Example, A."],
                            "year": "2022",
                            "table_label": "Table 2",
                        },
                    }
                ],
            },
        }
    ]
    reply = (
        "Table 2 of Example et al. (2022; arXiv:2211.04968) gives "
        "log L[CII] = 8.15 and FWHM = 245 km/s."
    )

    assert unsupported_literature_narrative_violations(reply, tool_results) == []
    assert provenance_citation_violations(reply, tool_results) == []
    assert validate_claims(reply, tool_results).ok


def test_arxiv_version_suffix_is_equivalent_for_table_citations():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "extract_literature_tables",
        "result": {
            "success": True,
            "arxiv_id": "2002.00962v4",
            "line_measurements": [{
                "source_name": "ALPINE",
                "citation": {"arxiv_id": "2002.00962v4"},
            }],
        },
    }]

    assert provenance_citation_violations("Béthermin et al. (2020; arXiv:2002.00962).", tool_results) == []


def test_markdown_backtick_does_not_break_bibcode_match():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "compare_luminosity_distances",
        "result": {
            "success": True,
            "current_cosmology": {"bibcode": "2020A&A...641A...6P"},
        },
    }]

    assert provenance_citation_violations("Baseline was Planck18 `2020A&A...641A...6P`.", tool_results) == []


def test_planck_collaboration_vi_year_is_not_author_year_noise():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "compare_luminosity_distances",
        "result": {
            "success": True,
            "current_cosmology": {"bibcode": "2020A&A...641A...6P"},
        },
    }]

    assert provenance_citation_violations("Planck Collaboration VI 2020 was the baseline.", tool_results) == []


def test_author_year_on_line_with_valid_arxiv_is_supported():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "read_arxiv_paper",
        "result": {
            "success": True,
            "arxiv_id": "2211.04968",
            "authors": ["Wu, Y.-H.", "Gao, H.", "Wang, J.-F."],
            "year": "2022",
        },
    }]

    reply = "Wu, Gao & Wang (2022; arXiv:2211.04968) compiled the target sample."

    assert provenance_citation_violations(reply, tool_results) == []


def test_planck_vi_parenthetical_with_valid_bibcode_is_supported():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "compare_luminosity_distances",
        "result": {
            "success": True,
            "current_cosmology": {"bibcode": "2020A&A...641A...6P"},
        },
    }]

    reply = "Planck Collaboration VI (2020; bibcode `2020A&A...641A...6P`) was the baseline."

    assert provenance_citation_violations(reply, tool_results) == []


def test_collaboration_lead_token_supports_author_year_shorthand():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "read_arxiv_paper",
        "result": {
            "success": True,
            "arxiv_id": "1807.06209",
            "authors": ["Planck Collaboration"],
            "year": "2018",
        },
    }]

    reply = "The Planck 2018 baseline values are abstract-level claims from this paper."

    assert provenance_citation_violations(reply, tool_results) == []


def test_catalog_identifier_ngc_number_is_not_author_year():
    from app.services.claim_validator import provenance_citation_violations

    reply = "The HST TRGB + NGC 4258/Cepheid combination was mentioned as a limitation."

    assert provenance_citation_violations(reply, []) == []


def test_failed_attempted_arxiv_id_allowed_in_limitation_line():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "extract_literature_tables",
        "input": {"arxiv_id": "1204.3674"},
        "result": {
            "success": False,
            "__tool_status__": "FAILED",
            "error_class": "rate_limit_exceeded",
        },
    }]

    reply = "extract_literature_tables failed for arXiv:1204.3674, so no authoritative table values were obtained."

    assert provenance_citation_violations(reply, tool_results) == []


def test_negative_author_year_context_does_not_trigger_citation_violation():
    from app.services.claim_validator import provenance_citation_violations

    reply = 'No validated author-year prose citation such as "Wong et al. (2019)" was obtained.'

    assert provenance_citation_violations(reply, []) == []


def test_author_year_phrase_present_in_claimable_payload_is_supported():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "read_arxiv_paper",
        "result": {
            "success": True,
            "arxiv_id": "1807.06209",
            "abstract": "The abstract reports an approximately 2 sigma tension with Planck 2018.",
        },
    }]

    reply = "The abstract also states an approximately 2 sigma tension with Planck 2018 (arXiv:1807.06209)."

    assert provenance_citation_violations(reply, tool_results) == []


def test_author_year_phrase_allows_hyphenated_payload_variant():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "read_arxiv_paper",
        "result": {
            "success": True,
            "arxiv_id": "1807.06209",
            "abstract": "Comparing our result with Planck-2018 observations gives a tension.",
        },
    }]

    reply = "The abstract states an approximately 2 sigma tension with Planck 2018 (arXiv:1807.06209)."

    assert provenance_citation_violations(reply, tool_results) == []


def test_paper_level_numeric_claim_requires_same_sentence_citation():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "read_arxiv_paper",
        "result": {
            "success": True,
            "arxiv_id": "2404.03002",
            "authors": ["DESI Collaboration"],
            "year": "2024",
            "abstract": "DESI BAO gives Omega_m = 0.295 +/- 0.015.",
        },
    }]

    violations = provenance_citation_violations(
        "DESI BAO gives Ωm = 0.295 ± 0.015.",
        tool_results,
    )

    assert violations
    assert violations[0].kind == "paper_numeric_missing_citation"
    assert "Ωm" in violations[0].match_text


def test_paper_level_numeric_claim_with_same_sentence_arxiv_is_supported():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "read_arxiv_paper",
        "result": {
            "success": True,
            "arxiv_id": "2404.03002",
            "authors": ["DESI Collaboration"],
            "year": "2024",
            "abstract": "DESI BAO gives Omega_m = 0.295 +/- 0.015.",
        },
    }]

    reply = "DESI BAO gives Ωm = 0.295 ± 0.015 (DESI Collaboration 2024; arXiv:2404.03002)."

    assert provenance_citation_violations(reply, tool_results) == []


def test_paper_level_numeric_claim_needs_citation_in_same_sentence():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "search_literature",
        "result": {
            "success": True,
            "results": [{
                "title": "DESI 2024",
                "authors": ["DESI Collaboration"],
                "year": "2024",
                "arxiv_id": "2404.03002",
            }],
        },
    }]

    reply = "DESI Collaboration (2024) reports the BAO constraints. Ωm = 0.295 ± 0.015."
    violations = provenance_citation_violations(reply, tool_results)

    assert violations
    assert violations[0].kind == "paper_numeric_missing_citation"


def test_non_paper_tool_numeric_claim_does_not_need_paper_sentence_citation():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "compare_luminosity_distances",
        "result": {
            "success": True,
            "current_cosmology": {
                "H0": 73.8,
                "Om0": 0.295,
                "bibcode": "2011ApJ...730..119R",
            },
        },
    }]

    assert provenance_citation_violations("H0 = 73.8 km/s/Mpc.", tool_results) == []


def test_supporting_bibcode_fields_enter_valid_pool():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "compare_luminosity_distances",
        "result": {
            "success": True,
            "current_cosmology": {
                "bibcode": "2020A&A...641A...6P",
                "tcmb_bibcode": "2009ApJ...707..916F",
            },
        },
    }]

    reply = "Tcmb follows Fixsen 2009 (`2009ApJ...707..916F`)."
    assert provenance_citation_violations(reply, tool_results) == []


def test_unseen_doi_is_citation_violation():
    from app.services.claim_validator import provenance_citation_violations

    violations = provenance_citation_violations(
        "The table is available at doi:10.9999/example.fake.",
        _tool_with_dataset("2023A&A...674A...1G"),
    )

    assert violations
    assert violations[0].kind == "invalid_doi"


def test_generic_cii_ranges_require_measurement_rows():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    reply = (
        "[CII] luminosities typically range from ~10^7 to 10^9 L_sun, "
        "and FWHM generally spans 100-600 km/s."
    )
    tool_results = [{"tool": "search_literature", "result": {"success": True, "results": []}}]

    violations = unsupported_literature_narrative_violations(reply, tool_results)

    assert violations
    assert all(v.kind == "unsupported_literature_narrative" for v in violations)


def test_publication_ready_line_fit_supports_generic_cii_ranges():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    reply = (
        "[CII] luminosities typically range from ~10^7 to 10^9 L_sun, "
        "and FWHM generally spans 100-600 km/s."
    )
    tool_results = [{
        "tool": "fit_line_lfr",
        "result": {
            "success": True,
            "publication_ready": True,
            "n_used": 12,
            "alpha": 8.0,
            "beta": 0.5,
        },
    }]

    assert unsupported_literature_narrative_violations(reply, tool_results) == []


def test_unseen_author_year_still_flags_after_literature_search():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "search_literature",
        "result": {
            "success": True,
            "results": [{
                "title": "ALPINE survey",
                "authors": ["Le Fèvre, O."],
                "year": "2019",
                "bibcode": "2019A&A...625A..51L",
            }],
        },
    }]

    violations = provenance_citation_violations("Carniani et al. (2020) measured [CII].", tool_results)

    assert violations
    assert violations[0].kind == "suspicious_author_year"
