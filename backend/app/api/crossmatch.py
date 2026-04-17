"""Cross-match two lists of coordinates.

Uses the CrossMatchEngine service which supports both astropy (default)
and STILTS (optional) backends.
"""

import math
from typing import Literal

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel

from app.services.crossmatch_engine import get_crossmatch_engine

router = APIRouter(prefix="/api/crossmatch", tags=["crossmatch"])


# ── Legacy models (backward compatible) ──


class CoordItem(BaseModel):
    ra: float
    dec: float
    name: str = ""


class CrossMatchRequest(BaseModel):
    list_a: list[CoordItem]
    list_b: list[CoordItem]
    radius_arcsec: float = 3.0


class MatchResult(BaseModel):
    a_name: str
    b_name: str
    a_ra: float
    a_dec: float
    b_ra: float
    b_dec: float
    separation_arcsec: float


# ── New enhanced models ──


class EnhancedCrossMatchRequest(BaseModel):
    list_a: list[CoordItem]
    list_b: list[CoordItem]
    radius_arcsec: float = 3.0
    join: Literal["1and2", "1or2", "all1", "all2", "1not2", "2not1"] = "1and2"
    find: Literal["best", "all"] = "best"
    use_stilts: bool = False


class EnhancedMatchResult(BaseModel):
    """Result row from the enhanced cross-match endpoint."""

    a_ra: float | None = None
    a_dec: float | None = None
    a_name: str | None = None
    b_ra: float | None = None
    b_dec: float | None = None
    b_name: str | None = None
    separation_arcsec: float | None = None


# ── Helper kept for backward compat ──


def angular_separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Compute angular separation in arcseconds using spherical law of cosines."""
    d2r = math.pi / 180.0
    ra1_r = ra1 * d2r
    dec1_r = dec1 * d2r
    ra2_r = ra2 * d2r
    dec2_r = dec2 * d2r

    cos_d = (
        math.sin(dec1_r) * math.sin(dec2_r)
        + math.cos(dec1_r) * math.cos(dec2_r) * math.cos(ra1_r - ra2_r)
    )
    cos_d = max(-1.0, min(1.0, cos_d))
    sep_rad = math.acos(cos_d)
    return sep_rad * (180.0 / math.pi) * 3600.0


# ── Endpoints ──


@router.post("", response_model=list[MatchResult])
async def crossmatch(req: CrossMatchRequest):
    """Cross-match two lists of coordinates within a given radius.

    Backward-compatible endpoint using the CrossMatchEngine.
    """
    engine = get_crossmatch_engine()

    t1 = pd.DataFrame(
        [{"ra": c.ra, "dec": c.dec, "name": c.name} for c in req.list_a]
    )
    t2 = pd.DataFrame(
        [{"ra": c.ra, "dec": c.dec, "name": c.name} for c in req.list_b]
    )

    result = await engine.crossmatch(
        t1, t2, radius_arcsec=req.radius_arcsec, join="1and2", find="all"
    )

    matches: list[MatchResult] = []
    for _, row in result.iterrows():
        matches.append(
            MatchResult(
                a_name=row.get("name", ""),
                b_name=row.get("name_2", ""),
                a_ra=row["ra"],
                a_dec=row["dec"],
                b_ra=row["ra_2"],
                b_dec=row["dec_2"],
                separation_arcsec=round(row["separation_arcsec"], 4),
            )
        )
    return matches


@router.post("/enhanced", response_model=list[EnhancedMatchResult])
async def crossmatch_enhanced(req: EnhancedCrossMatchRequest):
    """Enhanced cross-match with configurable join type and match strategy.

    Supports all STILTS-style join types:
    - 1and2: inner join (only matched rows)
    - all1:  left join (all rows from list_a)
    - all2:  right join (all rows from list_b)
    - 1or2:  full outer join
    - 1not2: anti-join (list_a rows with no match)
    - 2not1: anti-join (list_b rows with no match)
    """
    engine = get_crossmatch_engine()

    t1 = pd.DataFrame(
        [{"ra": c.ra, "dec": c.dec, "name": c.name} for c in req.list_a]
    )
    t2 = pd.DataFrame(
        [{"ra": c.ra, "dec": c.dec, "name": c.name} for c in req.list_b]
    )

    result = await engine.crossmatch(
        t1,
        t2,
        radius_arcsec=req.radius_arcsec,
        join=req.join,
        find=req.find,
        use_stilts=req.use_stilts,
    )

    matches: list[EnhancedMatchResult] = []
    for _, row in result.iterrows():
        sep = row.get("separation_arcsec")
        matches.append(
            EnhancedMatchResult(
                a_ra=_safe(row.get("ra")),
                a_dec=_safe(row.get("dec")),
                a_name=row.get("name") if pd.notna(row.get("name")) else None,
                b_ra=_safe(row.get("ra_2")),
                b_dec=_safe(row.get("dec_2")),
                b_name=(
                    row.get("name_2") if pd.notna(row.get("name_2")) else None
                ),
                separation_arcsec=round(sep, 4) if pd.notna(sep) else None,
            )
        )
    return matches


def _safe(val) -> float | None:
    """Return None for NaN/None, else float."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val)
