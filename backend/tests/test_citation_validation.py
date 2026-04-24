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

    monkeypatch.delenv("PROVENANCE_VALIDATOR_HARDBLOCK", raising=False)
    assert citation_violations_should_block(violations) is False

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
