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
        "fl": "bibcode,title,author,year,doi,pub,abstract",
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
                "authors": doc.get("author", [])[:5],
                "year": doc.get("year", ""),
                "doi": (doc.get("doi") or [None])[0],
                "abstract": doc.get("abstract", ""),
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


def _search_literature_ads(query: str, max_results: int = 20) -> list[dict]:
    """General literature search via NASA ADS (free-text query)."""
    if not ADS_API_KEY:
        return []

    headers = {"Authorization": f"Bearer {ADS_API_KEY}"}
    params = {
        "q": query,
        "fl": "bibcode,title,author,year,doi,pub,abstract",
        "rows": min(max_results, 50),
        "sort": "score desc",
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
                "authors": doc.get("author", [])[:10],
                "year": doc.get("year", ""),
                "doi": (doc.get("doi") or [None])[0],
                "abstract": doc.get("abstract", ""),
                "pub": doc.get("pub", ""),
            }
            for doc in docs
        ]
    except Exception as e:
        logger.warning("ADS literature search failed: %s", e)
        return []


def _search_literature_arxiv(query: str, max_results: int = 20) -> list[dict]:
    """Fallback literature search via arXiv API (no key needed)."""
    import urllib.parse
    search_query = urllib.parse.quote(query)
    url = (
        f"https://export.arxiv.org/api/query?search_query=all:{search_query}"
        f"&start=0&max_results={min(max_results, 50)}&sortBy=relevance&sortOrder=descending"
    )
    try:
        resp = httpx.get(url, timeout=15)
        resp.raise_for_status()
        import re
        entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)
        results = []
        for entry in entries:
            title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            title = re.sub(r"\s+", " ", title_m.group(1).strip()) if title_m else ""

            authors = re.findall(r"<author>\s*<name>(.*?)</name>", entry)

            published_m = re.search(r"<published>(.*?)</published>", entry)
            year = published_m.group(1)[:4] if published_m else ""

            summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            abstract = re.sub(r"\s+", " ", summary_m.group(1).strip()) if summary_m else ""

            # Extract arXiv ID for bibcode-like identifier
            id_m = re.search(r"<id>(.*?)</id>", entry)
            arxiv_url = id_m.group(1).strip() if id_m else ""
            arxiv_id = arxiv_url.split("/abs/")[-1] if "/abs/" in arxiv_url else arxiv_url

            doi_m = re.search(r'<arxiv:doi[^>]*>(.*?)</arxiv:doi>', entry)
            doi = doi_m.group(1).strip() if doi_m else None

            results.append({
                "bibcode": f"arXiv:{arxiv_id}",
                "title": title,
                "authors": authors[:10],
                "year": year,
                "doi": doi,
                "abstract": abstract,
                "pub": "arXiv",
                "arxiv_url": arxiv_url,
            })
        return results
    except Exception as e:
        logger.warning("arXiv literature search failed: %s", e)
        return []


@router.get("/search")
async def search_literature(
    q: str = Query(..., description="Free-text search query"),
    max_results: int = Query(20, ge=1, le=50, description="Max results to return"),
):
    """Search literature via ADS (preferred) with arXiv fallback."""
    loop = asyncio.get_running_loop()

    # Try ADS first
    results = await loop.run_in_executor(
        None, partial(_search_literature_ads, q, max_results)
    )
    source = "ads"

    # Fall back to arXiv if ADS returns nothing (no key or failure)
    if not results:
        results = await loop.run_in_executor(
            None, partial(_search_literature_arxiv, q, max_results)
        )
        source = "arxiv"

    return {"results": results, "source": source, "query": q}


@router.get("/bibtex")
async def get_bibtex(bibcode: str = Query(..., description="ADS bibcode")):
    """Get BibTeX entry for a given bibcode."""
    if not bibcode.strip():
        raise HTTPException(status_code=400, detail="Bibcode required")
    loop = asyncio.get_running_loop()
    bibtex = await loop.run_in_executor(None, partial(_get_bibtex_sync, bibcode.strip()))
    return {"bibtex": bibtex}
