"""Dossier API — cross-match dossier generation endpoint."""

from fastapi import APIRouter, Query

from app.services.dossier_generator import generate_dossier

router = APIRouter(prefix="/api/dossier", tags=["dossier"])


@router.get("")
async def get_dossier(
    ra: float = Query(..., description="Right ascension in degrees"),
    dec: float = Query(..., description="Declination in degrees"),
    name: str | None = Query(None, description="Optional object name"),
):
    """Generate a cross-match dossier for the given sky coordinates.

    Queries SIMBAD, Gaia DR3, SDSS, 2MASS, AllWISE, NED, and TNS
    concurrently, then returns a structured dossier with photometry,
    astrometry, redshift, host galaxy info, and prior classifications.
    """
    dossier = await generate_dossier(ra=ra, dec=dec, name=name)
    return dossier
