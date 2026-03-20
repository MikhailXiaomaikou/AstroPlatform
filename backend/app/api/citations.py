"""NASA ADS citation lookup endpoints."""

import asyncio
import os
import logging
from functools import partial

import requests
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/citations", tags=["citations"])
logger = logging.getLogger(__name__)

ADS_API_KEY = os.getenv("ADS_API_KEY", "")
ADS_BASE = "https://api.adsabs.harvard.edu/v1"


def _search_ads_sync(object_name: str) -> list[dict]:
    headers: dict[str, str] = {}
    if ADS_API_KEY:
        headers["Authorization"] = f"Bearer {ADS_API_KEY}"

    url = f"{ADS_BASE}/search/query"
    params = {
        "q": f"object:{object_name}",
        "fl": "bibcode,title,author,year,doi,pub",
        "rows": 5,
    }

    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    docs = data.get("response", {}).get("docs", [])
    results = []
    for doc in docs:
        results.append({
            "bibcode": doc.get("bibcode", ""),
            "title": (doc.get("title") or [""])[0],
            "authors": doc.get("author", []),
            "year": doc.get("year", ""),
            "doi": (doc.get("doi") or [None])[0],
        })
    return results


def _get_bibtex_sync(bibcode: str) -> str:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if ADS_API_KEY:
        headers["Authorization"] = f"Bearer {ADS_API_KEY}"

    url = f"{ADS_BASE}/export/bibtex"
    payload = {"bibcode": [bibcode]}

    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("export", "")


@router.get("/ads")
async def search_ads(object_name: str = Query(..., description="Object name to search")):
    """Query NASA ADS for references about an astronomical object."""
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, partial(_search_ads_sync, object_name))
    return results


@router.get("/bibtex")
async def get_bibtex(bibcode: str = Query(..., description="ADS bibcode")):
    """Get BibTeX entry for a given bibcode."""
    loop = asyncio.get_running_loop()
    bibtex = await loop.run_in_executor(None, partial(_get_bibtex_sync, bibcode))
    return {"bibtex": bibtex}
