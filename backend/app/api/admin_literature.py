"""PART AD C2 — admin endpoint to pre-warm the literature-table cache.

Audit M4 caught a regression where every chat round only had ALPINE
[CII] data because no other survey paper had been extracted into
`connector_cache` yet. This endpoint lets a platform admin pre-warm
the cache for a curated list of [CII] survey / sample papers so the
next chat round that asks for "fit a [CII] L-FWHM relation" already
has multi-survey rows to draw from.

Each entry is a real published [CII] line-measurement table referenced
by its arXiv ID. The default set covers ~5 high-z (z>4) and ~1 z~0
landmark surveys so a fit_line_lfr at any z range has at least one
non-ALPINE source.

The endpoint runs each fetch through `_cached_extract_arxiv_tables_payload`
which already has the 24h connector_cache + retry circuit-breaker, so
calling it twice is idempotent and the second call is a fast no-op.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import require_admin_any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/literature", tags=["admin-literature"])


# Curated list of arXiv IDs whose tables are worth pre-warming.
#
# Picked to give fit_line_lfr a multi-survey [CII] sample at z>4 plus
# a z~0 anchor:
#  - 2002.00962  Béthermin+2020 ALPINE (z=4-6, 75-line sample)
#  - 2009.10727  Le Fèvre+2020   ALPINE survey paper (z=4-6)
#  - 2106.13719  Bouwens+2022   REBELS overview (z>=6.5)
#  - 1605.03581  Capak+2015     z~5-6 [CII] HZ1-HZ10 (early ALMA)
#  - 1308.4708   Bothwell+2013  z~3 ULIRG [CII] sample
#  - 2105.10474  Aravena+2024   ASPECS [CII] (z~6)
DEFAULT_CII_ARXIV_IDS: tuple[str, ...] = (
    "2002.00962",
    "2009.10727",
    "2106.13719",
    "1605.03581",
    "1308.4708",
    "2105.10474",
)


class PreloadRequest(BaseModel):
    arxiv_ids: list[str] | None = Field(
        default=None,
        description=(
            "Optional override for the curated [CII] paper list. "
            "When omitted, the platform's default 6-paper set is used."
        ),
    )
    line_id: str = Field(
        default="[CII]",
        description=(
            "Line filter label echoed back so the admin caller can audit "
            "which line family this preload targets."
        ),
    )


class PreloadEntry(BaseModel):
    arxiv_id: str
    success: bool
    line_measurement_count: int = 0
    error: str | None = None
    bibcode: str | None = None


class PreloadResponse(BaseModel):
    line_id: str
    requested_count: int
    succeeded_count: int
    total_line_measurements: int
    entries: list[PreloadEntry]


@router.post("/preload_cii_caches", response_model=PreloadResponse)
async def preload_cii_caches(
    req: PreloadRequest = PreloadRequest(),
    _admin: None = Depends(require_admin_any),
) -> PreloadResponse:
    """Fan-out the cached arxiv-table extractor over the curated paper list.

    Each fetch is funneled through `_cached_extract_arxiv_tables_payload`
    which has 24h connector_cache + httpx circuit-breaker, so this
    endpoint is idempotent and cheap to call again.
    """
    from app.services.ai_tools import _cached_extract_arxiv_tables_payload

    ids = list(req.arxiv_ids) if req.arxiv_ids else list(DEFAULT_CII_ARXIV_IDS)

    async def _one(arxiv_id: str) -> PreloadEntry:
        try:
            payload = await _cached_extract_arxiv_tables_payload(arxiv_id)
            line_count = len(payload.get("line_measurements") or [])
            return PreloadEntry(
                arxiv_id=arxiv_id,
                success=True,
                line_measurement_count=line_count,
                bibcode=str(payload.get("bibcode") or "") or None,
            )
        except Exception as exc:
            logger.warning(
                "preload_cii_caches: arxiv:%s failed: %s",
                arxiv_id, exc,
            )
            return PreloadEntry(
                arxiv_id=arxiv_id,
                success=False,
                line_measurement_count=0,
                error=f"{type(exc).__name__}: {exc}",
            )

    entries = await asyncio.gather(*[_one(aid) for aid in ids])
    succeeded = [e for e in entries if e.success]
    return PreloadResponse(
        line_id=req.line_id,
        requested_count=len(ids),
        succeeded_count=len(succeeded),
        total_line_measurements=sum(e.line_measurement_count for e in succeeded),
        entries=list(entries),
    )
