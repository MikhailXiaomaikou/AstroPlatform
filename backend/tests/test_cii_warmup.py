"""PART AG C2 — startup hook fire-and-forget pre-warm of [CII] caches."""

from __future__ import annotations

import asyncio
from unittest.mock import patch


def test_warmup_cii_caches_fans_out_to_default_list() -> None:
    """The startup pre-warm hits every default arxiv id from
    DEFAULT_CII_ARXIV_IDS exactly once.

    Mocks the underlying fetch so the test runs offline + fast.
    """
    from app.api.admin_literature import DEFAULT_CII_ARXIV_IDS
    from app.main import _warmup_cii_caches

    fetched: list[str] = []

    async def fake_fetch(arxiv_id: str) -> dict:
        fetched.append(arxiv_id)
        return {
            "arxiv_id": arxiv_id,
            "bibcode": f"arXiv:{arxiv_id}",
            "line_measurements": [{"row": 1}, {"row": 2}],
            "title": "stub",
        }

    async def fake_sleep(_):
        return None

    with patch("app.services.ai_tools._cached_extract_arxiv_tables_payload", new=fake_fetch), \
         patch("app.main.asyncio.sleep", new=fake_sleep):
        asyncio.run(_warmup_cii_caches())

    assert sorted(fetched) == sorted(DEFAULT_CII_ARXIV_IDS), (
        f"warmup must hit every default paper exactly once; got {fetched}"
    )


def test_warmup_cii_caches_swallows_individual_failures() -> None:
    """A single paper failing must NOT abort the whole pre-warm: the
    surviving papers should still get cached."""
    from app.main import _warmup_cii_caches

    fetched_ok: list[str] = []

    async def fake_fetch(arxiv_id: str) -> dict:
        if arxiv_id == "1308.4708":  # Bothwell+2013 transient error
            raise ConnectionError("ar5iv flaky")
        fetched_ok.append(arxiv_id)
        return {
            "arxiv_id": arxiv_id,
            "bibcode": f"arXiv:{arxiv_id}",
            "line_measurements": [],
        }

    async def fake_sleep(_):
        return None

    with patch("app.services.ai_tools._cached_extract_arxiv_tables_payload", new=fake_fetch), \
         patch("app.main.asyncio.sleep", new=fake_sleep):
        asyncio.run(_warmup_cii_caches())  # must not raise

    # 6 default papers total, 1 fails → 5 succeed
    assert len(fetched_ok) == 5
    assert "1308.4708" not in fetched_ok


def test_warmup_cii_caches_top_level_failure_is_swallowed() -> None:
    """If imports inside the helper raise, the wrapper still must NOT
    propagate — server startup can't be aborted by literature pre-warm."""
    from app.main import _warmup_cii_caches

    async def fake_sleep(_):
        return None

    # Sabotage with an exception inside the inner block.
    async def fake_fetch_raises(arxiv_id: str) -> dict:
        raise RuntimeError("simulated systemic failure")

    with patch("app.main.asyncio.sleep", new=fake_sleep), \
         patch("app.services.ai_tools._cached_extract_arxiv_tables_payload", new=fake_fetch_raises):
        asyncio.run(_warmup_cii_caches())  # must not raise
