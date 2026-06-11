"""Candidate-paper pool builder for paper-to-tool mining.

This module creates the input corpus for the 20-paper mining loop.  It is not a
scientific-result generator: it only discovers and normalizes paper candidates,
optionally fetching arXiv metadata/PDF text previews so the mining loop can
extract ToolSpecs from method/table/equation evidence.
"""

from __future__ import annotations

import html
import asyncio
import importlib.util
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Any

import httpx


DEFAULT_MAX_PAPERS = 60
DEFAULT_QUERIES: dict[str, list[str]] = {
    "observational_cosmology": [
        'cat:astro-ph.CO AND ("baryon acoustic oscillations" OR BAO OR DESI OR eBOSS OR BOSS)',
        'cat:astro-ph.CO AND ("type ia supernova" OR Pantheon+ OR DES-SN OR Union3)',
        'cat:astro-ph.CO AND ("weak lensing" OR "cosmic shear" OR KiDS OR DES OR HSC OR ACT)',
        'cat:astro-ph.CO AND (Planck OR "CMB lensing" OR "distance priors" OR "Hubble constant")',
    ],
    "high_redshift_galaxies": [
        'cat:astro-ph.GA AND ("[CII]" OR "C II" OR ALPINE OR REBELS OR ALMA)',
        'cat:astro-ph.GA AND ("line luminosity" OR FWHM OR "scaling relation")',
    ],
}

OBS_COSMO_KEYWORDS = (
    "bao",
    "baryon acoustic",
    "desi",
    "boss",
    "eboss",
    "6df",
    "supernova",
    "pantheon",
    "union",
    "des-sn",
    "cmb",
    "planck",
    "act",
    "spt",
    "weak lensing",
    "cosmic shear",
    "kids",
    "hsc",
    "hubble constant",
    "dark energy",
    "cosmological parameters",
    "likelihood",
    "covariance",
)

METHOD_HINT_KEYWORDS = (
    "likelihood",
    "covariance",
    "mcmc",
    "cobaya",
    "cosmosis",
    "nested",
    "sampler",
    "chain",
    "posterior",
    "data vector",
    "robustness",
    "tension",
    "aic",
    "bic",
    "table",
)


async def build_paper_mining_candidate_pool(
    *,
    domain_tag: str = "observational_cosmology",
    queries: list[str] | None = None,
    seed_papers: list[dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    exclude_paper_ids: list[str] | None = None,
    max_papers: int = DEFAULT_MAX_PAPERS,
    allow_live_search: bool = False,
    fetch_text_preview: bool = False,
    text_preview_limit: int = 20,
    max_pages_per_query: int = 2,
    sort_by: str = "submittedDate",
    sort_order: str = "descending",
) -> dict[str, Any]:
    """Build a deduplicated paper candidate pool for mining.

    ``allow_live_search`` gates network access to arXiv.  Tests and local
    deterministic runs can pass ``seed_papers`` only.  ``fetch_text_preview`` is
    best-effort and bounded; failures leave candidates usable as metadata but
    low-confidence for actual ToolSpec mining.
    """
    domain = str(domain_tag or "observational_cosmology")
    limit = max(1, min(int(max_papers or DEFAULT_MAX_PAPERS), 200))
    excluded_ids = _normalize_excluded_ids(state=state, exclude_paper_ids=exclude_paper_ids)
    candidates: list[dict[str, Any]] = []

    for paper in seed_papers or []:
        if isinstance(paper, dict):
            normalized = _normalize_candidate(paper, source="seed")
            if normalized:
                candidates.append(normalized)

    attempted_queries: list[str] = []
    live_errors: list[str] = []
    if allow_live_search:
        arxiv_sort_by = _normalize_sort_by(sort_by)
        arxiv_sort_order = _normalize_sort_order(sort_order)
        for query in (queries or DEFAULT_QUERIES.get(domain, DEFAULT_QUERIES["observational_cosmology"])):
            if len(candidates) >= limit:
                break
            attempted_queries.append(query)
            try:
                page_size = min(25, limit)
                for page in range(max(1, min(int(max_pages_per_query or 2), 8))):
                    live = await _search_arxiv(
                        query,
                        max_results=page_size,
                        start=page * page_size,
                        sort_by=arxiv_sort_by,
                        sort_order=arxiv_sort_order,
                    )
                    candidates.extend(live)
                    unread = [c for c in _dedupe_candidates(candidates) if _paper_id(c) not in excluded_ids]
                    if len(unread) >= limit:
                        break
            except Exception as exc:
                live_errors.append(f"{query}: {exc.__class__.__name__}: {exc}")

    deduped = [candidate for candidate in _dedupe_candidates(candidates) if _paper_id(candidate) not in excluded_ids]
    scored = sorted(
        (_score_candidate(candidate, domain) for candidate in deduped),
        key=lambda item: (-float(item.get("relevance_score", 0.0)), str(item.get("year") or ""), str(item.get("title") or "")),
    )[:limit]

    if fetch_text_preview:
        scored = await _hydrate_text_previews(
            scored,
            limit=max(0, min(int(text_preview_limit or 20), limit)),
        )
        scored = [_score_candidate(candidate, domain) for candidate in scored]

    return {
        "success": True,
        "__tool_status__": "COMPLETED" if scored else "EMPTY",
        "analysis_status": "PAPER_MINING_CANDIDATE_POOL_READY" if scored else "PAPER_MINING_CANDIDATE_POOL_EMPTY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "domain_tag": domain,
        "candidate_count": len(scored),
        "candidate_papers": scored,
        "excluded_count": len(excluded_ids),
        "attempted_queries": attempted_queries,
        "live_search_enabled": bool(allow_live_search),
        "text_preview_requested": bool(fetch_text_preview),
        "text_preview_limit": int(text_preview_limit or 0) if fetch_text_preview else 0,
        "max_pages_per_query": int(max_pages_per_query or 0) if allow_live_search else 0,
        "sort_by": _normalize_sort_by(sort_by),
        "sort_order": _normalize_sort_order(sort_order),
        "warnings": [
            "Candidate pools are inputs to ToolSpec mining, not scientific evidence.",
            "Abstract-only candidates must be read or supplied with source_sections before high-confidence ToolSpecs are emitted.",
            *live_errors[:5],
        ],
        "__message_to_model__": (
            "Use these candidates as input to run_paper_tool_mining_loop. Do not cite "
            "candidate abstracts as measurement, posterior, or method evidence."
        ),
    }


def _normalize_candidate(raw: dict[str, Any], *, source: str) -> dict[str, Any] | None:
    arxiv_id = _clean_arxiv_id(raw.get("arxiv_id") or raw.get("id") or raw.get("paper_id") or raw.get("url"))
    nested_metadata = raw.get("paper_metadata") if isinstance(raw.get("paper_metadata"), dict) else {}
    title = _clean_text(raw.get("title") or nested_metadata.get("title"))
    abstract = _clean_text(raw.get("abstract") or raw.get("summary") or "")
    if not arxiv_id and not title:
        return None
    authors = raw.get("authors")
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(",") if a.strip()]
    if not isinstance(authors, list):
        authors = []
    year = raw.get("year") or _year_from_text(str(raw.get("published") or raw.get("updated") or ""))
    url = raw.get("url") or raw.get("paper_url") or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "")
    sections = raw.get("sections") or raw.get("source_sections")
    return {
        "paper_id": arxiv_id or title,
        "arxiv_id": arxiv_id,
        "title": title or arxiv_id,
        "authors": [str(a) for a in authors[:12]],
        "year": int(year) if str(year).isdigit() else None,
        "abstract": abstract,
        "url": str(url or ""),
        "source": source,
        "sections": sections if isinstance(sections, dict) else None,
    }


async def _search_arxiv(
    query: str,
    *,
    max_results: int,
    start: int = 0,
    sort_by: str = "submittedDate",
    sort_order: str = "descending",
) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(query)
    order = _normalize_sort_order(sort_order)
    sort = _normalize_sort_by(sort_by)
    url = (
        "https://export.arxiv.org/api/query?"
        f"search_query={encoded}&start={max(0, int(start))}&max_results={max_results}"
        f"&sortBy={sort}&sortOrder={order}"
    )
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
    return [_entry_to_candidate(entry) for entry in _arxiv_entries(response.text)]


def _arxiv_entries(xml_text: str) -> Iterable[ET.Element]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    return root.findall("atom:entry", ns)


def _entry_to_candidate(entry: ET.Element) -> dict[str, Any]:
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    raw_id = entry.findtext("atom:id", default="", namespaces=ns)
    title = _clean_text(entry.findtext("atom:title", default="", namespaces=ns))
    abstract = _clean_text(entry.findtext("atom:summary", default="", namespaces=ns))
    authors = [
        _clean_text(author.findtext("atom:name", default="", namespaces=ns))
        for author in entry.findall("atom:author", ns)
    ]
    published = entry.findtext("atom:published", default="", namespaces=ns)
    doi = entry.findtext("arxiv:doi", default="", namespaces=ns)
    arxiv_id = _clean_arxiv_id(raw_id)
    return {
        "paper_id": arxiv_id or raw_id,
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": [a for a in authors if a],
        "year": _year_from_text(published),
        "abstract": abstract,
        "doi": doi,
        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else raw_id,
        "source": "arxiv",
    }


async def _hydrate_text_previews(candidates: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(4)
    timeout = httpx.Timeout(10.0, connect=5.0, read=8.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def hydrate_one(index: int, candidate: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            if index >= limit:
                return index, candidate
            arxiv_id = str(candidate.get("arxiv_id") or "")
            if not arxiv_id or candidate.get("sections"):
                return index, candidate
            next_candidate = dict(candidate)
            async with semaphore:
                try:
                    text = await _fetch_text_preview(client, arxiv_id)
                    sections = _sections_from_text_preview(text)
                    if sections:
                        next_candidate["sections"] = sections
                        next_candidate["text_preview_chars"] = len(text)
                except Exception as exc:
                    next_candidate.setdefault("warnings", []).append(f"text preview failed: {exc.__class__.__name__}")
            return index, next_candidate

        pairs = await asyncio.gather(*(hydrate_one(i, c) for i, c in enumerate(candidates)))
    return [candidate for _, candidate in sorted(pairs, key=lambda pair: pair[0])]


async def _fetch_text_preview(client: httpx.AsyncClient, arxiv_id: str) -> str:
    """Fetch a bounded paper text preview without adding heavy dependencies."""
    try:
        html_response = await client.get(f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}")
        if html_response.status_code == 200 and "article" in html_response.text[:20_000].lower():
            text = _extract_html_text_preview(html_response.text)
            if text:
                return text[:30_000]
    except Exception:
        pass
    try:
        if importlib.util.find_spec("pdfminer") is None:
            return ""
        pdf = await client.get(f"https://arxiv.org/pdf/{arxiv_id}")
        if pdf.status_code == 200 and len(pdf.content) <= 8_000_000:
            return _extract_pdf_text_preview(pdf.content)
    except Exception:
        return ""
    return ""


def _extract_html_text_preview(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style|nav|header|footer).*?</\1>", " ", html_text)
    text = re.sub(r"(?is)<math.*?</math>", " formula ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(text)


def _extract_pdf_text_preview(content: bytes) -> str:
    try:
        import io

        from pdfminer.high_level import extract_text as extract_pdf_text

        return extract_pdf_text(io.BytesIO(content))[:30_000]
    except Exception:
        return ""


def _sections_from_text_preview(text: str) -> dict[str, str]:
    clean = _clean_text(text)
    if not clean:
        return {}
    sections: dict[str, str] = {}
    lower = clean.lower()
    for label, keywords in {
        "Methods": ("method", "likelihood", "analysis", "data vector", "covariance"),
        "Data": ("data", "sample", "observations", "catalog", "survey"),
        "Robustness": ("robust", "systematic", "null test", "jackknife"),
    }.items():
        starts = [lower.find(keyword) for keyword in keywords if lower.find(keyword) >= 0]
        if not starts:
            continue
        start = max(0, min(starts) - 800)
        sections[label] = clean[start : start + 5000]
    return sections or {"Text preview": clean[:6000]}


def _score_candidate(candidate: dict[str, Any], domain: str) -> dict[str, Any]:
    text = f"{candidate.get('title', '')} {candidate.get('abstract', '')}".lower()
    domain_terms = OBS_COSMO_KEYWORDS if domain == "observational_cosmology" else OBS_COSMO_KEYWORDS
    relevance_hits = [term for term in domain_terms if term in text]
    method_hits = [term for term in METHOD_HINT_KEYWORDS if term in text]
    score = min(1.0, 0.12 * len(set(relevance_hits)) + 0.05 * len(set(method_hits)))
    if candidate.get("sections"):
        score = min(1.0, score + 0.2)
    return {
        **candidate,
        "relevance_score": round(score, 3),
        "relevance_terms": sorted(set(relevance_hits))[:12],
        "method_hint_terms": sorted(set(method_hits))[:12],
        "mining_readiness": "source_sections_ready" if candidate.get("sections") else "metadata_or_abstract_only",
    }


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        key = str(candidate.get("arxiv_id") or candidate.get("doi") or candidate.get("title") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _normalize_excluded_ids(
    *,
    state: dict[str, Any] | None,
    exclude_paper_ids: list[str] | None,
) -> set[str]:
    values: list[Any] = []
    if isinstance(state, dict):
        raw_state_ids = state.get("read_paper_ids")
        if isinstance(raw_state_ids, list):
            values.extend(raw_state_ids)
    if isinstance(exclude_paper_ids, list):
        values.extend(exclude_paper_ids)
    return {_clean_arxiv_id(value) or str(value).strip() for value in values if str(value).strip()}


def _paper_id(candidate: dict[str, Any]) -> str:
    return (
        _clean_arxiv_id(candidate.get("arxiv_id") or candidate.get("paper_id") or candidate.get("url"))
        or str(candidate.get("doi") or candidate.get("bibcode") or candidate.get("title") or "").strip()
    )


def _clean_arxiv_id(raw: Any) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"^arxiv:\s*", "", text, flags=re.I)
    match = re.search(r"([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", text)
    return match.group(1) if match else ""


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_sort_by(value: Any) -> str:
    text = str(value or "submittedDate").strip()
    return text if text in {"relevance", "lastUpdatedDate", "submittedDate"} else "submittedDate"


def _normalize_sort_order(value: Any) -> str:
    text = str(value or "descending").strip()
    return text if text in {"ascending", "descending"} else "descending"


def _year_from_text(value: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return int(match.group(0)) if match else None
