"""Citation lookup — NASA ADS with SIMBAD fallback."""

import asyncio
import os
import logging
from functools import partial

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/citations", tags=["citations"])
logger = logging.getLogger(__name__)

ADS_API_KEY = os.getenv("ADS_API_KEY", "")


def _search_ads_sync(object_name: str) -> list[dict]:
    """Query NASA ADS. Requires ADS_API_KEY."""
    if not ADS_API_KEY:
        return []

    headers = {"Authorization": f"Bearer {ADS_API_KEY}"}
    params = {
        "q": f"object:{object_name}",
        "fl": "bibcode,title,author,year,doi,pub",
        "rows": 5,
    }
    try:
        resp = httpx.get(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params=params, headers=headers, timeout=15,
        )
        resp.raise_for_status()
        docs = resp.json().get("response", {}).get("docs", [])
        return [
            {
                "bibcode": doc.get("bibcode", ""),
                "title": (doc.get("title") or [""])[0],
                "authors": doc.get("author", [])[:5],  # limit authors
                "year": doc.get("year", ""),
                "doi": (doc.get("doi") or [None])[0],
            }
            for doc in docs
        ]
    except Exception as e:
        logger.warning("ADS search failed: %s", e)
        return []


def _search_simbad_refs(object_name: str) -> list[dict]:
    """Fallback: query SIMBAD for basic references."""
    try:
        from astroquery.simbad import Simbad
        result = Simbad.query_tap(f"""
            SELECT TOP 1 oid, otype, main_id, nbref
            FROM basic
            WHERE main_id = '{object_name.replace("'", "''")}'
        """)
        if result is None or len(result) == 0:
            return []
        # Return basic info — SIMBAD doesn't give full bibcodes easily
        row = result[0]
        return [{
            "bibcode": "",
            "title": f"SIMBAD entry for {row['main_id']}",
            "authors": [],
            "year": "",
            "doi": None,
            "note": f"Object has {row['nbref']} references in SIMBAD. Set ADS_API_KEY for full citation search.",
        }]
    except Exception as e:
        logger.warning("SIMBAD ref fallback failed: %s", e)
        return []


def _get_bibtex_sync(bibcode: str) -> str:
    if not ADS_API_KEY:
        # Generate a minimal BibTeX stub
        return f"% ADS_API_KEY not configured. Visit https://ui.adsabs.harvard.edu/abs/{bibcode}\n@misc{{{bibcode}}}"

    headers = {
        "Authorization": f"Bearer {ADS_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(
            "https://api.adsabs.harvard.edu/v1/export/bibtex",
            json={"bibcode": [bibcode]}, headers=headers, timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("export", "")
    except Exception as e:
        logger.warning("ADS bibtex failed: %s", e)
        return f"% BibTeX fetch failed. Visit https://ui.adsabs.harvard.edu/abs/{bibcode}"


@router.get("/ads")
async def search_ads(object_name: str = Query(..., description="Object name to search")):
    """Query NASA ADS for references. Falls back to SIMBAD info if no ADS key."""
    loop = asyncio.get_running_loop()

    # Try ADS first
    results = await loop.run_in_executor(None, partial(_search_ads_sync, object_name))
    if results:
        return results

    # Fallback to SIMBAD
    results = await loop.run_in_executor(None, partial(_search_simbad_refs, object_name))
    if results:
        return results

    return [{"bibcode": "", "title": f"No references found for {object_name}", "authors": [], "year": "", "doi": None,
             "note": "Set ADS_API_KEY environment variable for full citation search via NASA ADS."}]


@router.get("/bibtex")
async def get_bibtex(bibcode: str = Query(..., description="ADS bibcode")):
    """Get BibTeX entry for a given bibcode."""
    if not bibcode.strip():
        raise HTTPException(status_code=400, detail="Bibcode required")
    loop = asyncio.get_running_loop()
    bibtex = await loop.run_in_executor(None, partial(_get_bibtex_sync, bibcode.strip()))
    return {"bibtex": bibtex}
