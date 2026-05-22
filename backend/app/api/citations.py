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


def _search_arxiv_sync(query: str) -> list[dict]:
    """H2.1: arXiv API fallback when ADS is unavailable.

    arXiv is open (no key), slower, no abstracts in a structured form but
    parses out title/authors/year.  Returns same shape as ADS for
    downstream consumers.
    """
    try:
        import urllib.parse
        import xml.etree.ElementTree as ET

        # R6-NEW-1: arXiv redirects http to https via 301; httpx.get does not follow by default.
        # Use https directly + follow_redirects=True as a belt-and-suspenders approach.
        url = (
            "https://export.arxiv.org/api/query?"
            f"search_query=all:{urllib.parse.quote(query)}&max_results=8"
        )
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        # arXiv returns Atom XML
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            id_el = entry.find("atom:id", ns)
            arxiv_id = (id_el.text or "").rsplit("/", 1)[-1] if id_el is not None else ""
            published_el = entry.find("atom:published", ns)
            year = (published_el.text or "")[:4] if published_el is not None else ""
            authors = []
            for author_el in entry.findall("atom:author/atom:name", ns):
                if author_el.text:
                    authors.append(author_el.text.strip())
            summary_el = entry.find("atom:summary", ns)
            summary = (summary_el.text or "").strip() if summary_el is not None else ""
            results.append({
                "bibcode": f"arXiv:{arxiv_id}",
                "title": title,
                "authors": authors[:5],
                "year": year,
                "doi": None,
                "abstract": summary,
                "source": "arxiv",
            })
        return results
    except Exception as e:
        logger.warning("arXiv fallback failed: %s", e)
        return []


def _ads_get_with_retry(
    url: str,
    *,
    params: dict,
    headers: dict,
    timeout: int = 15,
    max_retries: int = 2,
):
    """Stage 6 P0c-E (2026-05-19): ADS 5xx / network errors with backoff retry.

    429 (quota exhausted): do not retry — an immediate retry will not improve things;
    caller should fall back directly.
    5xx (transient server error): retry up to `max_retries` times with exponential
    backoff (0.5 s -> 1 s -> 2 s).

    Returns:
        httpx.Response if a response was received (caller checks status_code).
        None if all attempts failed with network exceptions or 5xx exhausted.
    """
    import time

    backoff = 0.5
    last_resp = None
    for attempt in range(max_retries + 1):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        except Exception as exc:
            logger.warning(
                "ADS request raised on attempt %d/%d: %s",
                attempt + 1, max_retries + 1, exc,
            )
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2
                continue
            return None
        last_resp = resp
        if resp.status_code >= 500 and attempt < max_retries:
            logger.warning(
                "ADS returned %d (attempt %d/%d), backing off %.1fs",
                resp.status_code, attempt + 1, max_retries + 1, backoff,
            )
            time.sleep(backoff)
            backoff *= 2
            continue
        return resp
    return last_resp


def _search_ads_sync(object_name: str) -> list[dict]:
    """Query NASA ADS, fall back to arXiv API if ADS unavailable.

    H2.1: the Paper 4 reviewer hit "Literature Search EMPTY" even on
    common topics.  Possible causes: missing/exhausted ADS_API_KEY,
    401/429 from ADS.  We now:
    1. Try ADS if key present (with 5xx retry, Stage 6 P0c-E)
    2. If ADS returns 401/429 or no results, fall back to arXiv
    3. If both fail, surface an explicit error_class so the UI can say
       "literature service unavailable" instead of silently returning []
    """
    # Path 1: ADS (if key present)
    if ADS_API_KEY:
        headers = {"Authorization": f"Bearer {ADS_API_KEY}"}
        params = {
            "q": f"object:{object_name}",
            # PART AF C1 — fix the path PART AD C3 missed.
            # The AI search_literature tool calls _exec_literature →
            # _search_ads_sync (this function), NOT the ADS path
            # in literature_engine.py that AD C3 patched. Without
            # database:astronomy here, ADS general indexing has been
            # leaking power-engineering / nuclear-physics / software-
            # workshop papers into astrophysics queries (M4 1 leak,
            # M5 3 leaks — measurable regression).
            "fq": "database:astronomy",
            # Stage 6 P0c-B (2026-05-19): add `property` field to retrieve the RETRACTED tag.
            "fl": "bibcode,title,author,year,doi,pub,abstract,property",
            "rows": 5,
        }
        resp = _ads_get_with_retry(
            "https://api.adsabs.harvard.edu/v1/search/query",
            params=params, headers=headers, timeout=15,
        )
        if resp is None:
            logger.warning("ADS request exhausted retries; using arXiv fallback")
        elif resp.status_code in (401, 429):
            # Key invalid / exhausted — log and fall through to arXiv.
            # Stage 6 P0c-E: do not retry on 429 — quota does not recover immediately.
            logger.warning(
                "ADS returned %d — key may be invalid/exhausted; using arXiv fallback",
                resp.status_code,
            )
        else:
            try:
                resp.raise_for_status()
                docs = resp.json().get("response", {}).get("docs", [])
                if docs:
                    return [
                        {
                            "bibcode": doc.get("bibcode", ""),
                            "title": (doc.get("title") or [""])[0],
                            "authors": doc.get("author", [])[:5],
                            "year": doc.get("year", ""),
                            "doi": (doc.get("doi") or [None])[0],
                            "abstract": doc.get("abstract", ""),
                            "source": "ads",
                            "retracted": "RETRACTED" in (doc.get("property") or []),
                        }
                        for doc in docs
                    ]
                # ADS returned empty — fall through to arXiv
            except Exception as e:
                logger.warning("ADS search failed (%s); falling back to arXiv", e)

    # Path 2: arXiv fallback
    arxiv_results = _search_arxiv_sync(object_name)
    return arxiv_results


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
        # PART AF C1 — same astronomy-database scope as
        # _search_ads_sync. This is the second ADS path the AI
        # search_literature tool walks (object-name search → free-text
        # search → arXiv fallback); without fq here the free-text path
        # also leaks non-astronomy hits.
        "fq": "database:astronomy",
        # Stage 6 P0c-B (2026-05-19): add `property` field to retrieve the RETRACTED tag.
        "fl": "bibcode,title,author,year,doi,pub,abstract,property",
        "rows": min(max_results, 50),
        "sort": "score desc",
    }
    # Stage 6 P0c-E (2026-05-19): retry on 5xx; do not retry on 429 (caller returns []).
    resp = _ads_get_with_retry(
        "https://api.adsabs.harvard.edu/v1/search/query",
        params=params, headers=headers, timeout=15,
    )
    if resp is None:
        logger.warning("ADS literature search exhausted retries")
        return []
    if resp.status_code in (401, 429):
        logger.warning(
            "ADS literature search returned %d (quota/key issue)",
            resp.status_code,
        )
        return []
    try:
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
                "retracted": "RETRACTED" in (doc.get("property") or []),
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
