"""Cross-match two lists of coordinates."""

import math

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/crossmatch", tags=["crossmatch"])


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
    # Clamp for numerical safety
    cos_d = max(-1.0, min(1.0, cos_d))
    sep_rad = math.acos(cos_d)
    return sep_rad * (180.0 / math.pi) * 3600.0


@router.post("", response_model=list[MatchResult])
async def crossmatch(req: CrossMatchRequest):
    """Cross-match two lists of coordinates within a given radius."""
    matches: list[MatchResult] = []
    for a in req.list_a:
        for b in req.list_b:
            sep = angular_separation_arcsec(a.ra, a.dec, b.ra, b.dec)
            if sep <= req.radius_arcsec:
                matches.append(MatchResult(
                    a_name=a.name,
                    b_name=b.name,
                    a_ra=a.ra,
                    a_dec=a.dec,
                    b_ra=b.ra,
                    b_dec=b.dec,
                    separation_arcsec=round(sep, 4),
                ))
    return matches
