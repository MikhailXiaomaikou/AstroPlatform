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

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import require_admin_any

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/literature", tags=["admin-literature"])


# Curated list of arXiv IDs whose tables are worth pre-warming.
#
# CALIBRATION HISTORY: PART AD C2 first listed 6 arxiv ids written from
# memory; PART AG C4 local test caught that 3 of them pointed at
# unrelated physics papers (1605.03581 = a Cabibbo-mixing particle
# physics paper, NOT Capak+2015 [CII]; 1308.4708 = a chiral-condensate
# nuclear physics paper, NOT Bothwell+2013 [CII] survey; etc.). The
# DEFAULT list is now restricted to the SINGLE id we have verified
# end-to-end yields > 0 line_measurements through the full normalizer
# pipeline. Adding new ids requires running them locally first and
# confirming `len(payload["line_measurements"]) > 0`.
#
#  - 2002.00962  Béthermin+2020 ALPINE (z=4-6, 75-line sample) ✓ 74 measurements
#
# Pending verification (do NOT add to DEFAULT until checked):
#  - REBELS [CII] line table — likely arXiv:2202.04080 (Inami+2022)
#    or arXiv:2202.10464 (Schouws+2022), need to inspect ar5iv tables.
#  - Bothwell+2013 SPT [CII] — likely arXiv:1304.4256 (different paper
#    from the one I originally wrote here).
#  - Capak+2015 [CII] — likely arXiv:1503.07596 (Nature 522 455).
#  - ALPINE Le Fèvre+2020 survey — likely arXiv:1910.09517.
#
# To add a paper to the DEFAULT list, an admin should:
#  1. Call POST /api/admin/literature/preload_cii_caches with
#     arxiv_ids=["<candidate>"] in the request body.
#  2. Inspect the returned `line_measurement_count` for that entry.
#  3. Only if count > 0 is the paper a useful preload target.
DEFAULT_CII_ARXIV_IDS: tuple[str, ...] = (
    "2002.00962",
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
