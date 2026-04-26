from __future__ import annotations


B7_REPLY = (
    "GCVS gives delta Cep a period of 5.366341 days. "
    "Fernie et al. (1995): 5.366341 / Berdnikov (2008): 5.366249 / "
    "Groenewegen (2013): 5.366207."
)


def _gcvs_tool_results() -> list[dict]:
    return [
        {
            "tool": "run_adql",
            "result": {
                "service": "vizier",
                "columns": ["period"],
                "data": {"period": [5.366341]},
                "row_count": 1,
                "provenance": {
                    "datasets": [
                        {
                            "service_key": "vizier",
                            "article": "2017ARep...61...80S",
                            "archive_version": "B/gcvs",
                        }
                    ],
                    "field_bibcodes": None,
                },
            },
        }
    ]


def test_b7_warning_mode_logs_and_counts_suspicious_author_year(caplog, monkeypatch):
    from app.observability.metrics import get_registry
    from app.services.claim_validator import citation_violations_should_block, provenance_citation_violations

    # PART Y Batch 1: hardblock 现在默认开启, 显式 PROVENANCE_VALIDATOR_HARDBLOCK=false 才进 warn-only
    monkeypatch.setenv("PROVENANCE_VALIDATOR_HARDBLOCK", "false")
    get_registry().reset()

    with caplog.at_level("WARNING", logger="app.services.claim_validator"):
        violations = provenance_citation_violations(B7_REPLY, _gcvs_tool_results())

    assert len(violations) == 3
    assert all(v.kind == "suspicious_author_year" for v in violations)
    assert citation_violations_should_block(violations) is False
    assert sum("Citation provenance violation" in record.message for record in caplog.records) == 3

    snap = get_registry().snapshot()
    labels = snap["counters"]["fabrication_blocked_total"]
    suspicious_total = sum(
        value
        for label_tuple, value in labels.items()
        if ("reason", "suspicious_author_year") in label_tuple
    )
    assert suspicious_total == 3.0


def test_b7_hardblock_mode_blocks_reply(monkeypatch):
    from app.services.claim_validator import (
        blocked_citation_reply_text,
        citation_violations_should_block,
        provenance_citation_violations,
    )

    monkeypatch.setenv("PROVENANCE_VALIDATOR_HARDBLOCK", "true")

    violations = provenance_citation_violations(B7_REPLY, _gcvs_tool_results())
    blocked = blocked_citation_reply_text(violations)

    assert citation_violations_should_block(violations) is True
    assert "Reply withheld" in blocked
    assert "Fernie et al. (1995)" in blocked
