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


def test_cosmology_registry_label_supports_author_year_citation():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "provenance": {
                    "cosmology_likelihood": {
                        "citations": [
                            {
                                "label": "Madhavacheril et al. ACT DR6 lensing",
                                "year": 2024,
                                "arxiv": "2304.05203",
                            }
                        ]
                    }
                }
            },
        }
    ]

    assert provenance_citation_violations(
        "The ACT DR6 lensing input follows Madhavacheril et al. (2024).",
        tool_results,
    ) == []


def test_cosmology_registry_bibcode_supports_planck_citation():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [
        {
            "tool": "run_cosmology_likelihood_chain",
            "result": {
                "provenance": {
                    "cosmology_likelihood": {
                        "citations": [
                            {
                                "label": "Planck Collaboration VI 2020",
                                "year": 2020,
                                "doi": "10.1051/0004-6361/201833910",
                                "bibcode": "2020A&A...641A...6P",
                            }
                        ]
                    }
                }
            },
        }
    ]

    assert provenance_citation_violations(
        "The Planck prior is 2020A&A...641A...6P.",
        tool_results,
    ) == []


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


def test_cosmology_registry_arxiv_and_label_citations_are_supported():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "list_cosmology_datasets",
        "result": {
            "success": True,
            "datasets": [{
                "display_name": "DESI DR1 BAO",
                "citations": [{
                    "label": "DESI Collaboration 2024 DR1 BAO cosmology",
                    "year": 2024,
                    "arxiv": "2404.03002",
                }],
            }, {
                "display_name": "Pantheon+",
                "citations": [{
                    "label": "Scolnic et al. Pantheon+ sample",
                    "year": 2022,
                    "arxiv": "2112.03863",
                }],
            }],
        },
    }]

    reply = (
        "DESI DR1 BAO is cited as DESI Collaboration (2024; arXiv:2404.03002). "
        "Pantheon+ is cited as Scolnic et al. (2022; arXiv:2112.03863)."
    )

    assert provenance_citation_violations(reply, tool_results) == []


def test_cosmology_registry_collaboration_label_supports_generic_author_year():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "list_cosmology_datasets",
        "result": {
            "success": True,
            "datasets": [{
                "display_name": "SDSS + 6dF BAO compilation",
                "citations": [{
                    "label": "eBOSS Collaboration DR16 cosmology",
                    "year": 2021,
                    "arxiv": "2007.08991",
                }],
            }],
        },
    }]

    reply = "The BAO compilation includes eBOSS Collaboration (2021; arXiv:2007.08991)."

    assert provenance_citation_violations(reply, tool_results) == []


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


def test_publication_ready_cosmology_chain_supports_literature_source_phrase():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    reply = (
        "These compressed priors are drawn from the literature, but the quoted "
        "H0 and S8 values below come from the publication-ready compressed chain."
    )
    tool_results = [{
        "tool": "run_cosmology_likelihood_chain",
        "result": {
            "success": True,
            "publication_ready": True,
            "model": "lcdm",
            "posterior": {
                "H0": {"median": 67.53},
                "S8": {"median": 0.832},
            },
        },
    }]

    assert unsupported_literature_narrative_violations(reply, tool_results) == []


def test_desi_lrg_bin_tension_requires_bin_level_evidence():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    reply = "The DESI LRG bin at z_eff≈0.51 shows tension with ΛCDM."
    tool_results = [{
        "tool": "list_cosmology_datasets",
        "result": {
            "success": True,
            "datasets": [{"key": "desi_dr1_bao", "version": "DR1"}],
        },
    }]

    violations = unsupported_literature_narrative_violations(reply, tool_results)

    assert violations
    assert violations[0].kind == "unsupported_literature_narrative"


def test_desi_lrg_bin_assessment_supports_bin_tension_language():
    from app.services.claim_validator import unsupported_literature_narrative_violations

    reply = "The DESI LRG bin at z_eff≈0.51 shows tension with ΛCDM."
    tool_results = [{
        "tool": "assess_bao_bin_anomaly",
        "result": {
            "success": True,
            "bin_level_assessment": [{
                "bin": "LRG z_eff=0.51",
                "pull_sigma": 2.1,
                "interpretation": "mild tension",
            }],
        },
    }]

    assert unsupported_literature_narrative_violations(reply, tool_results) == []


def test_registry_first_author_label_supports_author_year_citation():
    from app.services.claim_validator import provenance_citation_violations

    tool_results = [{
        "tool": "list_cosmology_datasets",
        "result": {
            "success": True,
            "datasets": [{
                "key": "planck2018_compressed",
                "citations": [{
                    "label": "Chen, Huang & Wang distance priors",
                    "year": 2019,
                    "arxiv": "1808.05724",
                    "doi": "10.1088/1475-7516/2019/02/028",
                }],
            }],
        },
    }]

    reply = "Planck compressed priors follow Chen, Huang & Wang (2019)."

    assert provenance_citation_violations(reply, tool_results) == []


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


# ── PART AI #1: lock-down regression for diagnostics bundles 04-24 ──────


def test_bundle_5841_warmup_reply_must_be_blocked():
    """PART AI #1 lock-down: 04-24 warmup chat (.local/diagnostics/5841...)
    生产时 reply 没被拦下来; 现在的 claim_validator 必须能完整抓住 3 类
    fabrication 同时触发. 防止以后规则被弱化导致类似 reply 再通过."""
    from app.services.claim_validator import (
        provenance_citation_violations,
        unsupported_literature_narrative_violations,
        validate_claims,
    )

    reply = (
        "[CII] luminosities typically range from ~10^7 to 10^9 L_sun, "
        "and FWHM generally spans 100-600 km/s. Carniani et al. (2020) "
        "measured these trends."
    )
    # 当时只调了 search_literature 一个工具且返回空 results
    tool_results = [{
        "tool": "search_literature",
        "result": {"success": True, "results": []},
    }]

    # 1. validate_claims 必须 fail (uncited 数字)
    result = validate_claims(reply, tool_results)
    assert result.ok is False
    assert len(result.uncited) >= 1

    # 2. provenance citation 必须含 suspicious_author_year (Carniani 没经
    #    extract 直接被引)
    cite_violations = provenance_citation_violations(reply, tool_results)
    assert any(v.kind == "suspicious_author_year" for v in cite_violations)
    assert any("Carniani" in v.match_text for v in cite_violations)

    # 3. unsupported_literature_narrative 必须含整段文学陈述
    narrative_violations = unsupported_literature_narrative_violations(
        reply, tool_results,
    )
    assert len(narrative_violations) >= 1


def test_bundle_6202_partial_fit_with_uncited_pearson_must_be_flagged():
    """PART AI #1 lock-down: 04-24 LFR run (.local/diagnostics/6202... 与
    84ad...) 生产 reply 报数字, fit_line_lfr=PARTIAL, 但 reply 没说
    exploratory. methodology validator 必须触发
    line_relation_exploratory_label_missing; Pearson r=0.45 不在 universe
    必须触发 validate_claims uncited."""
    from app.services.claim_validator import (
        methodology_consistency_violations,
        validate_claims,
    )

    reply = (
        "For the 74 ALPINE [CII] rows, the Bayesian fit gives slope beta = "
        "0.75, intercept alpha = 6.88, intrinsic scatter = 0.31 dex, and "
        "Pearson r ≈ 0.45."
    )
    tool_results = [{
        "tool": "fit_line_lfr",
        "result": {
            "success": True,
            "fit_method": "bayesian_xyerr_linmix",
            "alpha": 6.88,
            "beta": 0.75,
            "intrinsic_scatter_dex": 0.31,
            "n_used": 74,
            "publication_ready": False,
            "__tool_status__": "PARTIAL",
            "__do_not_claim__": True,
        },
    }]

    # validate_claims: pearson r=0.45 不在 universe
    result = validate_claims(reply, tool_results)
    assert result.ok is False, "Pearson r=0.45 不在 universe, 必须 fail"

    # methodology: PARTIAL fit 报数字但 reply 无 exploratory label →
    # line_relation_exploratory_label_missing
    methodology_v = methodology_consistency_violations(reply, tool_results)
    assert any(
        v.kind == "line_relation_exploratory_label_missing"
        for v in methodology_v
    ), (
        f"PARTIAL fit + 数字 + 无 exploratory label 必须 flag, "
        f"got: {[v.kind for v in methodology_v]}"
    )


def test_bundle_e8d9_fit_lfr_bypass_must_be_blocked():
    """PART AI #1 lock-down: 04-24 LFR run (.local/diagnostics/e8d9...)
    只调 1 个 run_python 没调 fit_line_lfr, reply 报 LFR 数字 — Step 3
    fit_line_lfr_bypass detector 必须抓. 这条 e2e 在 step 3 也覆盖,
    放在这里让 4 个 bundle 完整 lock-down."""
    from app.services.claim_validator import methodology_consistency_violations

    reply = (
        "Using the cached ALPINE measurement table, the Bayesian fit on the "
        "L'[CII]-FWHM relation gives slope beta = 0.766, intercept alpha = "
        "9.823, and intrinsic scatter = 0.315 dex."
    )
    tool_results = [{
        "tool": "run_python",
        "result": {"success": True, "stdout": "ok", "figures": []},
    }]

    violations = methodology_consistency_violations(reply, tool_results)
    assert any(v.kind == "fit_line_lfr_bypass" for v in violations)
