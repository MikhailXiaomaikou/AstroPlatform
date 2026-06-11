"""Literature intelligence engine for structured ADS queries and comparison.

Provides async functions to query NASA ADS, build structured paper context,
and prepare comparison prompts for the AI assistant. Results are cached
in-memory with a 1-hour TTL.
"""

import logging
import os
import time
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)

ADS_API_BASE = "https://api.adsabs.harvard.edu/v1/search/query"
ADS_FIELDS = "bibcode,title,abstract,author,year,citation_count,pub,doi"

# ── In-memory cache with TTL ──

_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # 1 hour in seconds
_CACHE_MAX_SIZE = 200


def _cache_key(topic: str, object_name: str | None, max_papers: int) -> str:
    return f"{topic}|{object_name or ''}|{max_papers}"


def _cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: dict) -> None:
    # Evict oldest entries if cache is full
    if len(_cache) >= _CACHE_MAX_SIZE:
        oldest_key = min(_cache, key=lambda k: _cache[k][0])
        del _cache[oldest_key]
    _cache[key] = (time.monotonic(), value)


async def resolve_bibcode_exists(
    *,
    bibcode: str | None = None,
    arxiv: str | None = None,
    doi: str | None = None,
) -> bool:
    """Confirm a citation identifier resolves to >=1 real ADS record.

    Gates the manual_attestation citeable upgrade for inline cosmology rows:
    a self-declared bibcode is only honoured if ADS actually has it, so a
    fabricated reference cannot launder inline rows into a citeable fit.

    Fails closed: missing ADS_API_KEY, network error, timeout, or zero hits
    all return False, keeping the rows audit-only (the safe direction).
    """
    bibcode = (bibcode or "").strip()
    arxiv = (arxiv or "").strip()
    doi = (doi or "").strip()
    if bibcode:
        query = f'bibcode:"{bibcode}"'
    elif doi:
        query = f'doi:"{doi}"'
    elif arxiv:
        query = f'arxiv:"{arxiv}"'
    else:
        return False

    ads_api_key = os.getenv("ADS_API_KEY", "")
    if not ads_api_key:
        logger.info("resolve_bibcode_exists: ADS_API_KEY unset; %r treated as unverified", query)
        return False

    headers = {"Authorization": f"Bearer {ads_api_key}"}
    params = {"q": query, "fl": "bibcode", "rows": 1}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(ADS_API_BASE, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("resolve_bibcode_exists: ADS lookup failed for %r: %s", query, exc)
        return False
    return int(data.get("response", {}).get("numFound", 0)) >= 1


def _format_first_author(authors: list[str]) -> str:
    """Format author list as 'First Author et al.' or single author."""
    if not authors:
        return "Unknown"
    if len(authors) == 1:
        return authors[0]
    return f"{authors[0]} et al."


def _extract_arxiv_id(bibcode: str) -> str | None:
    """Extract arXiv ID from bibcode if it looks like an arXiv entry.

    ADS bibcodes for arXiv papers typically look like '2024arXiv240112345X'
    or the bibcode field might directly be 'arXiv:2401.12345'.
    """
    if not bibcode:
        return None
    if bibcode.startswith("arXiv:"):
        return bibcode[6:]
    # ADS bibcodes: YYYYarXivMMDD.NNNNNx format
    if "arXiv" in bibcode:
        # e.g. '2024arXiv240112345G' -> '2401.12345'
        try:
            idx = bibcode.index("arXiv") + 5
            digits = bibcode[idx:].rstrip(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ."
            )
            if len(digits) >= 9:
                return f"{digits[:4]}.{digits[4:]}"
        except (ValueError, IndexError):
            pass
    return None


def _build_ads_query(topic: str, object_name: str | None = None) -> str:
    """Build an ADS search query combining topic and optional object name."""
    parts = []
    if object_name:
        parts.append(f'object:"{object_name}"')
    if topic:
        parts.append(topic)
    return " ".join(parts)


def _current_year() -> int:
    return datetime.now().year


async def get_literature_context(
    topic: str,
    object_name: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    max_papers: int = 10,
) -> dict:
    """Query ADS for papers and return structured literature context.

    Parameters
    ----------
    topic : str
        Search keywords (e.g. "stellar mass function", "AGN variability").
    object_name : str, optional
        Specific astronomical object to include in query.
    ra, dec : float, optional
        Coordinates (reserved for future positional search; not used yet).
    max_papers : int
        Maximum papers to return (default 10).

    Returns
    -------
    dict
        Structured result with papers, total count, query string, and summary.
    """
    # Check cache first
    ck = _cache_key(topic, object_name, max_papers)
    cached = _cache_get(ck)
    if cached is not None:
        logger.debug("Literature cache hit for %r", ck)
        return cached

    ads_api_key = os.getenv("ADS_API_KEY", "")
    if not ads_api_key:
        result = {
            "topic": topic,
            "papers": [],
            "total_found": 0,
            "query_used": "",
            "summary": (
                "ADS_API_KEY is not configured. Set the ADS_API_KEY environment "
                "variable to enable literature search via NASA ADS."
            ),
        }
        return result

    query = _build_ads_query(topic, object_name)
    current_year = _current_year()
    recency_cutoff = current_year - 3

    # To give 2x weight to recent papers, we use ADS function queries.
    # We boost papers from the last 3 years using ADS classic scoring combined
    # with citation_count sort. ADS supports function queries like:
    #   sort=citation_count desc
    # We request more papers than needed, then re-rank locally to apply the
    # recency boost, since ADS doesn't natively support a weighted composite sort.
    fetch_rows = min(max_papers * 3, 50)

    headers = {"Authorization": f"Bearer {ads_api_key}"}
    params = {
        "q": query,
        # PART AD C3: scope ADS to the astronomy database. Without this
        # filter, search terms like "wildfire" / "shutoff" / "neutron"
        # routinely matched power-engineering, nuclear-physics, or
        # planetary-science papers that have no astrophysical content
        # (R2.9 / M4 audit reproducer: "wildfire ignition" power-line
        # paper appearing in a [CII] line-relation literature query).
        # ADS supports `database:astronomy` as a filter query; this is
        # the canonical scope for astrophysics-only searches.
        "fq": "database:astronomy",
        "fl": ADS_FIELDS,
        "rows": fetch_rows,
        "sort": "citation_count desc",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(ADS_API_BASE, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        logger.warning("ADS query timed out for topic=%r", topic)
        return {
            "topic": topic,
            "papers": [],
            "total_found": 0,
            "query_used": query,
            "summary": "ADS query timed out. Try again or use a more specific search.",
        }
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "ADS returned HTTP %d for topic=%r", exc.response.status_code, topic
        )
        return {
            "topic": topic,
            "papers": [],
            "total_found": 0,
            "query_used": query,
            "summary": f"ADS returned an error (HTTP {exc.response.status_code}). Check your API key and query.",
        }
    except Exception as exc:
        logger.warning("ADS request failed: %s", exc)
        return {
            "topic": topic,
            "papers": [],
            "total_found": 0,
            "query_used": query,
            "summary": f"ADS request failed: {exc}",
        }

    response_block = data.get("response", {})
    total_found = response_block.get("numFound", 0)
    docs = response_block.get("docs", [])

    # Parse documents into structured papers
    parsed = []
    for doc in docs:
        year_val = doc.get("year")
        try:
            year_int = int(year_val) if year_val else 0
        except (ValueError, TypeError):
            year_int = 0

        citation_count = doc.get("citation_count") or 0
        bibcode = doc.get("bibcode", "")
        arxiv_id = _extract_arxiv_id(bibcode)
        authors_raw = doc.get("author", [])

        parsed.append(
            {
                "bibcode": bibcode,
                "title": (doc.get("title") or [""])[0],
                "authors": _format_first_author(authors_raw),
                "year": year_int,
                "citation_count": citation_count,
                "abstract": doc.get("abstract") or "",
                "arxiv_id": arxiv_id,
                "doi": (doc.get("doi") or [None])[0],
                "_score": citation_count * (2.0 if year_int >= recency_cutoff else 1.0),
            }
        )

    # Re-rank: 2x weight for papers from the last 3 years
    parsed.sort(key=lambda p: p["_score"], reverse=True)

    # Take top max_papers and remove internal scoring field
    papers = []
    for p in parsed[:max_papers]:
        paper = {k: v for k, v in p.items() if k != "_score"}
        papers.append(paper)

    # Build summary
    if papers:
        top = papers[0]
        summary = (
            f"Found {total_found} papers about {topic}. "
            f"Most cited: {top['title']} ({top['year']}, {top['citation_count']} citations)."
        )
    else:
        summary = f"No papers found for query: {query}"

    result = {
        "topic": topic,
        "papers": papers,
        "total_found": total_found,
        "query_used": query,
        "summary": summary,
    }

    _cache_set(ck, result)
    return result


def search_literature(topic: str, max_papers: int = 10) -> list[dict]:
    """Synchronous wrapper to search ADS for papers on a topic.

    Returns a list of paper dicts with bibcode, title, authors, year, abstract.
    """
    ads_api_key = os.getenv("ADS_API_KEY", "")
    if not ads_api_key:
        return []

    query = _build_ads_query(topic)
    headers = {"Authorization": f"Bearer {ads_api_key}"}
    params = {
        "q": query,
        # PART AD C3: scope to ADS astronomy database — same rationale
        # as the async path above. Without this, search_literature
        # tool calls have leaked power-engineering / nuclear-physics
        # papers into astrophysics queries (R2.9 / M4 reproducer).
        "fq": "database:astronomy",
        "fl": ADS_FIELDS,
        "rows": min(max_papers * 3, 50),
        "sort": "citation_count desc",
    }

    try:
        resp = httpx.get(ADS_API_BASE, params=params, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    docs = data.get("response", {}).get("docs", [])
    papers = []
    for doc in docs[:max_papers]:
        papers.append({
            "bibcode": doc.get("bibcode", ""),
            "title": (doc.get("title") or [""])[0],
            "authors": doc.get("author", []),
            "year": doc.get("year", ""),
            "abstract": doc.get("abstract", ""),
            "citation_count": doc.get("citation_count", 0),
            "doi": (doc.get("doi") or [None])[0],
        })
    return papers


async def compare_with_literature(user_results: dict, literature: dict) -> dict:
    """Prepare a structured comparison between user results and literature.

    This is a simple data-assembly helper -- no LLM call. It extracts key
    information and builds a prompt string that an AI model can interpret.

    Parameters
    ----------
    user_results : dict
        User's analysis results. Can contain arbitrary keys; this function
        will attempt to summarise them.
    literature : dict
        Output from ``get_literature_context``.

    Returns
    -------
    dict
        Structure with user findings, relevant papers, and a comparison prompt.
    """
    # Summarise user findings -- pull out numeric values and short strings
    user_summary_parts = []
    for key, val in user_results.items():
        if isinstance(val, (int, float)):
            user_summary_parts.append(f"{key}={val}")
        elif isinstance(val, str) and len(val) < 200:
            user_summary_parts.append(f"{key}: {val}")
        elif isinstance(val, list):
            user_summary_parts.append(f"{key}: [{len(val)} items]")

    user_summary = (
        "; ".join(user_summary_parts) if user_summary_parts else str(user_results)[:500]
    )

    # Take top 5 literature papers for context
    lit_papers = (literature.get("papers") or [])[:5]

    # Build concise paper summaries for the prompt
    paper_summaries = []
    for p in lit_papers:
        paper_summaries.append(
            f"- {p.get('title', 'Untitled')} ({p.get('authors', 'Unknown')}, "
            f"{p.get('year', '?')}, {p.get('citation_count', 0)} cit.)"
        )

    papers_text = (
        "\n".join(paper_summaries) if paper_summaries else "No relevant papers found."
    )

    comparison_prompt = (
        f"The user found: {user_summary}. "
        f"The most relevant papers are:\n{papers_text}\n"
        f"How do these compare?"
    )

    return {
        "user_findings": user_results,
        "literature_context": lit_papers,
        "comparison_prompt": comparison_prompt,
    }


def build_citation_network(bibcodes: list[str], depth: int = 1) -> dict:
    """Build a citation network from a list of seed papers.

    Returns nodes (papers) and edges (citations) for visualization.
    """

    ads_key = os.environ.get("ADS_API_KEY", "")
    if not ads_key:
        return {"error": "ADS_API_KEY not configured", "nodes": [], "edges": []}

    nodes = {}
    edges = []
    to_process = list(bibcodes)
    processed = set()

    for level in range(depth + 1):
        next_batch = []
        for bib in to_process:
            if bib in processed:
                continue
            processed.add(bib)

            try:
                resp = httpx.get(
                    "https://api.adsabs.harvard.edu/v1/search/query",
                    params={"q": f"bibcode:{bib}", "fl": "bibcode,title,author,year,citation_count,reference,citation"},
                    headers={"Authorization": f"Bearer {ads_key}"},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    docs = resp.json().get("response", {}).get("docs", [])
                    if docs:
                        doc = docs[0]
                        nodes[bib] = {
                            "bibcode": bib,
                            "title": doc.get("title", [""])[0],
                            "authors": doc.get("author", [])[:3],
                            "year": doc.get("year", ""),
                            "citations": doc.get("citation_count", 0),
                            "level": level,
                        }

                        # Add citation edges (papers this one cites)
                        for ref in (doc.get("reference", []) or [])[:20]:
                            edges.append({"from": bib, "to": ref, "type": "cites"})
                            if level < depth and ref not in processed:
                                next_batch.append(ref)

                        # Add cited-by edges
                        for citer in (doc.get("citation", []) or [])[:10]:
                            edges.append({"from": citer, "to": bib, "type": "cites"})
                            if level < depth and citer not in processed:
                                next_batch.append(citer)
            except Exception:
                continue

        to_process = next_batch[:50]  # Limit breadth

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "n_papers": len(nodes),
        "n_edges": len(edges),
        "depth": depth,
    }


def synthesize_bibliography(topic: str, findings: str = "", max_papers: int = 10) -> dict:
    """AI-powered bibliography synthesis: find relevant papers and generate
    a contextualized literature review paragraph."""

    # First search for papers
    papers = search_literature(topic, max_papers=max_papers)

    if not papers:
        return {"text": "No relevant papers found.", "papers": []}

    # Build context for AI synthesis
    paper_summaries = []
    for i, p in enumerate(papers[:max_papers]):
        authors = p.get("authors", ["Unknown"])
        first_author = authors[0].split(",")[0] if authors else "Unknown"
        year = p.get("year", "")
        title = p.get("title", "")
        abstract = p.get("abstract", "")[:200]
        paper_summaries.append(
            f"[{i+1}] {first_author} et al. ({year}): {title}. {abstract}..."
        )

    context = "\n".join(paper_summaries)

    synthesis = {
        "topic": topic,
        "papers": papers[:max_papers],
        "paper_count": len(papers),
        "context_for_ai": (
            f"The following {len(papers)} papers are relevant to the topic '{topic}':\n\n"
            f"{context}\n\n"
            f"User's findings: {findings}\n\n"
            f"Please synthesize a brief literature review paragraph citing these papers "
            f"and contextualizing the user's findings within the existing literature."
        ),
        "suggested_citations": [
            {"bibcode": p.get("bibcode", ""), "citation": f"{(p.get('authors') or ['Unknown'])[0].split(',')[0]} et al. ({p.get('year', '')})"}
            for p in papers[:max_papers] if p.get("bibcode")
        ],
    }

    return synthesis
