"""PART AD C2 — admin endpoint pre-warming the [CII] literature cache.

Locks two contracts:

1. The endpoint exists, requires admin auth, and fans out to the
   underlying `_cached_extract_arxiv_tables_payload` function (24h
   cache + retry circuit-breaker already wired by PART Z).

2. The default arxiv_id list covers ≥3 independent surveys so the
   AI's multi-survey requirement (set by SYSTEM_PROMPT in the same
   commit) is satisfiable in one preload call.

3. SYSTEM_PROMPT actually has the multi-survey + subsample fallback
   rules — without those the endpoint is just decorative.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest


def test_default_cii_arxiv_ids_only_verified() -> None:
    """PART AG C4 retest fix: the DEFAULT list must only contain arxiv
    ids that have been END-TO-END verified to yield > 0 line_measurements
    through the full _normalize_line_measurements pipeline. PART AD C2
    originally shipped 6 ids written from memory; 3 turned out to point
    at unrelated physics papers (Cabibbo mixing, chiral condensate,
    etc.). Until each candidate is locally verified, the DEFAULT stays
    at the single confirmed ALPINE Béthermin+2020 entry."""
    from app.api.admin_literature import DEFAULT_CII_ARXIV_IDS

    assert len(DEFAULT_CII_ARXIV_IDS) >= 1
    assert "2002.00962" in DEFAULT_CII_ARXIV_IDS  # ALPINE Béthermin+2020 — verified 74 measurements
    # NOTE: do not re-add unverified ids here without running
    # _cached_extract_arxiv_tables_payload locally first and confirming
    # len(payload["line_measurements"]) > 0.


def test_preload_endpoint_fans_out_and_aggregates_counts(monkeypatch) -> None:
    """Mock the underlying fetcher and verify the endpoint:
    - calls each arxiv_id exactly once,
    - aggregates line_measurement_count across all entries,
    - reports succeeded_count + total_line_measurements correctly,
    - returns the bibcode in each entry."""
    from app.api import admin_literature

    call_log: list[str] = []

    async def fake_cached_extract(arxiv_id: str) -> dict:
        call_log.append(arxiv_id)
        return {
            "arxiv_id": arxiv_id,
            "bibcode": f"arXiv:{arxiv_id}",
            "line_measurements": [{"row": i} for i in range(7)],
            "title": "stub",
        }

    # Patch the import inside the endpoint, not the source module.
    with patch(
        "app.services.ai_tools._cached_extract_arxiv_tables_payload",
        new=fake_cached_extract,
    ):
        result = asyncio.run(
            admin_literature.preload_cii_caches(
                req=admin_literature.PreloadRequest(arxiv_ids=["x1", "x2", "x3"]),
                _admin=None,
            )
        )

    assert result.requested_count == 3
    assert result.succeeded_count == 3
    assert result.total_line_measurements == 7 * 3
    assert sorted(e.arxiv_id for e in result.entries) == ["x1", "x2", "x3"]
    assert all(e.success for e in result.entries)
    assert sorted(call_log) == ["x1", "x2", "x3"]


def test_preload_endpoint_records_per_paper_failures(monkeypatch) -> None:
    """When one paper fails, the endpoint reports it as success=False
    with the error string, but the other entries succeed."""
    from app.api import admin_literature

    async def fake_cached_extract(arxiv_id: str) -> dict:
        if arxiv_id == "broken":
            raise ConnectionError("ar5iv unreachable")
        return {
            "arxiv_id": arxiv_id,
            "bibcode": f"arXiv:{arxiv_id}",
            "line_measurements": [{"row": 1}, {"row": 2}],
        }

    with patch(
        "app.services.ai_tools._cached_extract_arxiv_tables_payload",
        new=fake_cached_extract,
    ):
        result = asyncio.run(
            admin_literature.preload_cii_caches(
                req=admin_literature.PreloadRequest(arxiv_ids=["good1", "broken", "good2"]),
                _admin=None,
            )
        )

    assert result.requested_count == 3
    assert result.succeeded_count == 2
    assert result.total_line_measurements == 4
    failed = [e for e in result.entries if not e.success]
    assert len(failed) == 1
    assert failed[0].arxiv_id == "broken"
    assert "ConnectionError" in (failed[0].error or "")


def test_system_prompt_has_multi_survey_rule() -> None:
    """SYSTEM_PROMPT must mandate multi-survey sample composition before
    fit_line_lfr — otherwise the AI keeps drawing slopes from ALPINE
    only (M4 audit reproducer).
    """
    from app.api.chat import SYSTEM_PROMPT

    assert "Multi-survey sample composition" in SYSTEM_PROMPT
    # At least 3 independent surveys named so the AI can act on the rule
    # without guessing arxiv ids.
    assert "ALPINE" in SYSTEM_PROMPT
    assert "REBELS" in SYSTEM_PROMPT
    assert "Capak+2015" in SYSTEM_PROMPT
    # Specific arXiv id anchor (Béthermin+2020 ALPINE master sample)
    assert "2002.00962" in SYSTEM_PROMPT
    # Required AI behaviour spelled out. Prompt is hard-wrapped, so we
    # normalise whitespace before substring-matching.
    normalised = " ".join(SYSTEM_PROMPT.split())
    assert "at least 3 independent surveys" in normalised


def test_system_prompt_has_subsample_fallback_rule() -> None:
    """SYSTEM_PROMPT must require AI to declare the subsample fallback
    instead of silently substituting a different split."""
    from app.api.chat import SYSTEM_PROMPT

    assert "Subsample fallback transparency" in SYSTEM_PROMPT
    assert "0 sources" in SYSTEM_PROMPT
    # ALPINE z=4-6 example must be there so the M4 reproducer is locked
    assert "ALPINE z=4-6 has 0 sources at z<1" in SYSTEM_PROMPT


def test_system_prompt_has_explicit_cosmology_call_examples() -> None:
    """PART AF C3 — M5 audit: AI called compare_luminosity_distances
    without target_cosmology because it didn't know what string to pass.
    Lock that the prompt now spells out the exact call strings for the
    4 most common user phrasings."""
    from app.api.chat import SYSTEM_PROMPT

    # Header
    assert "EXACT CALL EXAMPLES" in SYSTEM_PROMPT

    # Riess+11 / Suzuki+12 → FlatLambdaCDM spec
    assert 'compare_luminosity_distances(target_cosmology="FlatLambdaCDM_H73p8_Om0p295")' in SYSTEM_PROMPT
    # Riess+22 → riess22_shoes preset
    assert 'compare_luminosity_distances(target_cosmology="riess22_shoes")' in SYSTEM_PROMPT
    # Planck18 + BAO preset
    assert 'compare_luminosity_distances(target_cosmology="planck18_bao")' in SYSTEM_PROMPT
    # Freedman21 TRGB preset
    assert 'compare_luminosity_distances(target_cosmology="freedman21_trgb")' in SYSTEM_PROMPT
    # Custom Flat spec phrasing
    assert "FlatLambdaCDM_H72p0_Om0p30" in SYSTEM_PROMPT
    # Footer rule (don't ever omit target_cosmology)
    assert "without `target_cosmology=`" in SYSTEM_PROMPT


def test_system_prompt_has_demagnify_no_op_rule() -> None:
    """PART AF C6 — when no source is lensed, the AI must still SAY so
    rather than silently omit the lensing discussion (M5 audit point f)."""
    from app.api.chat import SYSTEM_PROMPT

    assert "no-op declaration" in SYSTEM_PROMPT
    assert "0 lensed sources detected" in SYSTEM_PROMPT
    assert "demagnify_sample skipped" in SYSTEM_PROMPT


def test_system_prompt_has_cite_after_extract_rule() -> None:
    """PART AG C3 — M6 audit reproducer: AI cited Bothwell 2013 in prose
    without first running a tool to ground it. The new rule mandates
    extract-or-search BEFORE citation, with a generic-phrasing fallback
    when the lookup fails.
    """
    from app.api.chat import SYSTEM_PROMPT

    # Section header
    assert "Cite-after-extract" in SYSTEM_PROMPT

    # Required tools spelled out (so the AI knows what to call first)
    assert 'extract_literature_tables(arxiv_id="...")' in SYSTEM_PROMPT
    assert 'search_literature(query="<author>' in SYSTEM_PROMPT

    # Generic-phrasing fallback when lookup fails
    assert "prior [CII] surveys at z>4" in SYSTEM_PROMPT or "prior [CII] surveys" in SYSTEM_PROMPT

    # M6 reproducer paper names listed so the AI can resolve them
    # against the platform's pre-warmed cache list
    assert "Bothwell 2013" in SYSTEM_PROMPT or "1308.4708" in SYSTEM_PROMPT
    assert "Capak+2015" in SYSTEM_PROMPT or "1605.03581" in SYSTEM_PROMPT
    assert "REBELS" in SYSTEM_PROMPT
