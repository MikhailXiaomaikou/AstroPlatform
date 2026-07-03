"""Literature search / relevance classification / paper-measurement caching.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: search_literature, classify_literature_relevance.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio
import re
from typing import Any

from app.services.ai_tools import _session_cache_key, logger

TOOL_SCHEMAS = [
    {
        "name": "search_literature",
        "description": (
            "Search NASA ADS for academic papers about an astronomical object or topic, "
            "with arXiv fallback when ADS has no key/results. Returns titles, authors, "
            "years, abstracts, and source metadata that you can cite in your response. "
            "This is paper/abstract-level only; it does not provide measurement-table "
            "values such as L[CII] or FWHM."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Object name or search query for ADS"},
            },
            "required": ["query"],
        },
    },
    {
        # Stage 6 P0c-C (2026-05-19): hard-block upgrade — promotes the Stage 5/6.2
        # abstract secondary-filter prompt MUST rule (soft) to a dedicated tool (hard).
        # The backend claim_validator.unclassified_literature_violations check
        # requires every paper returned by search_literature to be classified
        # through this tool before it can be cited in a narrative; otherwise the
        # entire passage is blocked by the banner.
        "name": "classify_literature_relevance",
        "description": (
            "REQUIRED after every search_literature call (hard rule, not advisory). "
            "Read each returned paper's abstract and classify it as Direct, Marginal, "
            "or Off-topic relative to the user's current question. Only Direct + "
            "Marginal papers may be cited downstream; citing an unclassified or "
            "Off-topic paper will trigger a citation hard-block on the reply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "classifications": {
                    "type": "array",
                    "description": "One entry per paper returned by the most recent search_literature.",
                    "items": {
                        "type": "object",
                        "required": ["bibcode", "relevance", "reason"],
                        "properties": {
                            "bibcode": {
                                "type": "string",
                                "description": "Paper bibcode exactly as returned by search_literature (e.g. 2024arXiv2404.03002D).",
                            },
                            "relevance": {
                                "type": "string",
                                "enum": ["Direct", "Marginal", "Off-topic"],
                                "description": (
                                    "Direct: paper directly answers the user's question. "
                                    "Marginal: related but does not directly answer. "
                                    "Off-topic: keyword overlap but topic mismatch."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": "One-sentence reason from the abstract.",
                            },
                        },
                    },
                },
            },
            "required": ["classifications"],
        },
    },
]


def _build_paper_links(r: dict[str, Any]) -> dict[str, str]:
    """Stage 5 (2026-05-19): build clickable URLs for one paper.

    Returns a dict with any subset of {arxiv_url, pdf_url, doi_url, ads_url}.
    ads_url is always present when bibcode exists; arxiv/pdf are present when
    the paper is on arXiv (auto-detected); doi_url is present when DOI exists.
    """
    out: dict[str, str] = {}
    bibcode = str(r.get("bibcode") or "").strip()
    if bibcode:
        out["ads_url"] = f"https://ui.adsabs.harvard.edu/abs/{bibcode}"
    arxiv_url = str(r.get("arxiv_url") or "").strip()
    if not arxiv_url and bibcode.startswith("arXiv:"):
        arxiv_id = bibcode[len("arXiv:"):]
        arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"
    if arxiv_url:
        out["arxiv_url"] = arxiv_url
        out["pdf_url"] = arxiv_url.replace("/abs/", "/pdf/")
    doi = str(r.get("doi") or "").strip()
    if doi:
        out["doi_url"] = f"https://doi.org/{doi}"
    return out


async def _exec_literature(inp: dict) -> dict:
    try:
        from functools import partial
        from app.api.citations import (
            _search_ads_sync,
            _search_literature_ads,
            _search_literature_arxiv,
        )

        query = str(inp.get("query") or "").strip()
        if not query:
            return {
                "results": [],
                "error": "search_literature requires query",
                "error_class": "missing_argument",
                "argument": "query",
            }
        loop = asyncio.get_running_loop()
        raw = await loop.run_in_executor(None, _search_ads_sync, query)
        source = "ads_or_arxiv_object"
        # `_search_ads_sync` favors object:<name> searches. In peer-review
        # workflows the question is often about a topic / method / catalog name,
        # so if the object search returns nothing we fall through to free-text
        # ADS and arXiv to avoid a premature EMPTY result.
        if not raw:
            raw = await loop.run_in_executor(None, partial(_search_literature_ads, query, 8))
            source = "ads_free_text"
        if not raw:
            raw = await loop.run_in_executor(None, partial(_search_literature_arxiv, query, 8))
            source = "arxiv_free_text"
        if not raw:
            return {
                "results": [],
                "source": source,
                "message": (
                    "No papers found via ADS object search, ADS free-text search, "
                    "or arXiv fallback."
                ),
            }
        visible = [
            r for r in raw
            if not _literature_hit_should_be_hidden(r)
        ]
        filtered, filtered_out_count = _filter_literature_hits_for_query(query, visible)
        result_papers = [
            {
                "title": r["title"],
                "authors": r["authors"][:3],
                "year": r["year"],
                "bibcode": r["bibcode"],
                "abstract": (r.get("abstract") or "")[:500],
                "source": r.get("source") or r.get("pub") or source,
                # Stage 6 P0c-B (2026-05-19): pass the ADS RETRACTED flag through to LLM + frontend
                "retracted": bool(r.get("retracted", False)),
                **_build_paper_links(r),
            }
            for r in filtered[:8]
        ]
        retracted_count = sum(1 for p in result_papers if p["retracted"])
        # Stage 6.2 P2 (2026-05-19): enforce abstract second-screening.
        # Stage 5 added a MUST prompt rule but AI skipped it in prod tests.
        # Add __message_to_model__ in the standard anti-fabrication pattern
        # used by the line-relation workflow (chat.py _suppressed_*); AI
        # almost never ignores this banner on the next iteration.
        msg_to_model = (
            "REQUIRED before any further tool call: read each abstract "
            "above and output a Markdown table with columns "
            "`| # | Title (short) | Relevance | One-sentence reason |`. "
            "Relevance MUST be one of: Direct (paper directly answers the "
            "user's question), Marginal (related but does not directly "
            "answer), Off-topic (keyword overlap but topic mismatch). "
            "Only Direct + Marginal papers may be cited / mined downstream; "
            "drop Off-topic ones from your reasoning. If 0 papers are "
            "Direct, propose a refined query instead of citing marginally-"
            "relevant work as if it were direct."
        )
        if retracted_count:
            # Stage 6 P0c-B: strictly prohibit citing retracted papers
            msg_to_model = (
                f"⚠ {retracted_count} of {len(result_papers)} returned paper(s) "
                f"are marked RETRACTED by ADS. You MUST NOT cite or mine data "
                f"from any paper with `retracted=true`; treat it as if it does "
                f"not exist. In the Relevance table above, mark retracted papers "
                f"as Off-topic with reason 'RETRACTED'.\n\n"
                + msg_to_model
            )
        return {
            "source": source,
            "result_granularity": "paper_abstract",
            "supports_measurement_claims": False,
            "filtered_out_count": filtered_out_count,
            "relevance_filter": "off_topic_blacklist",
            "retracted_count": retracted_count,
            "results": result_papers,
            "__message_to_model__": msg_to_model,
        }
    except Exception as e:
        return {"error": str(e)}


async def _exec_classify_literature_relevance(inp: dict, python_session_id: str = "default") -> dict:
    """Stage 6 P0c-C (2026-05-19): hard barrier upgrade.

    Old approach: `__message_to_model__` in search_literature return prompted the
    LLM to output a Direct/Marginal/Off-topic table (soft prompt injection).
    Production testing showed the LLM skipped it (run 1 produced the table,
    run 2 did not).

    New approach: classification is a dedicated tool. The LLM must call it first;
    afterwards claim_validator's `unclassified_literature_violations` check
    verifies that every cited bibcode passed through this tool — any that did not
    result in a hard-blocked reply.

    This function itself only performs lightweight validation + structured return;
    the actual blocking happens in the claim-validation step of the chat.py pipeline.
    """
    classifications = inp.get("classifications") or []
    if not isinstance(classifications, list) or not classifications:
        return {
            "classifications": [],
            "error": "classify_literature_relevance requires a non-empty `classifications` list",
            "error_class": "missing_argument",
            "argument": "classifications",
        }
    valid_relevance = {"Direct", "Marginal", "Off-topic"}
    cleaned: list[dict[str, str]] = []
    for c in classifications:
        if not isinstance(c, dict):
            continue
        bibcode = str(c.get("bibcode") or "").strip()
        relevance = str(c.get("relevance") or "").strip()
        reason = str(c.get("reason") or "").strip()
        if not bibcode or relevance not in valid_relevance:
            continue
        cleaned.append({"bibcode": bibcode, "relevance": relevance, "reason": reason})
    direct = sum(1 for c in cleaned if c["relevance"] == "Direct")
    marginal = sum(1 for c in cleaned if c["relevance"] == "Marginal")
    off_topic = sum(1 for c in cleaned if c["relevance"] == "Off-topic")
    msg = (
        f"Classified {len(cleaned)} paper(s): "
        f"{direct} Direct, {marginal} Marginal, {off_topic} Off-topic. "
        "ONLY Direct + Marginal papers may be cited in your narrative; "
        "downstream provenance check will hard-block any Off-topic or "
        "unclassified bibcode."
    )
    if direct == 0:
        msg += (
            " 0 Direct papers — propose a refined search_literature query "
            "before citing any of the Marginal papers."
        )
    return {
        "classifications": cleaned,
        "summary": {
            "direct": direct,
            "marginal": marginal,
            "off_topic": off_topic,
            "total": len(cleaned),
        },
        "__message_to_model__": msg,
    }


async def _extract_and_cache_paper_measurements(
    arxiv_id: str,
    api_key: str,
    python_session_id: str = "default",
    fields: list[str] | None = None,
) -> dict:
    """Stage 6.3 (2026-05-20 sink): internal helper for fit_line_lfr —
    LLM measurement extraction + ±1% cell verification + cache write.

    The spike module `llm_paper_extractor.extract_with_llm_and_verify` provides
    the core logic (fetch HTML / parse tables / score+filter / LLM call / ±1%
    cell verification). This function is an async wrapper that:
      1. Runs the spike module in run_in_executor (sync httpx + LLM call,
         prevents blocking the event loop)
      2. Converts passed records to the fit_line_lfr-compatible schema and writes
         to the session-scoped `latest_literature_tables:<sid>` cache plus the
         raw `latest_literature_tables` key
      3. failed_mismatch / failed_no_cell records are not cached
         (claim_validator automatically rejects any AI citations of them)

    History: previously a top-level tool `extract_paper_measurements_with_llm`;
    sunk into fit_line_lfr as an internal dependency on 2026-05-20. Users now
    pass arxiv_id directly to fit_line_lfr for a single-step workflow.
    """
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import store_search_results
    if not arxiv_id:
        return {
            "success": False,
            "error": "arxiv_id is required",
            "error_class": "missing_argument",
            "argument": "arxiv_id",
        }
    if not api_key:
        return {
            "success": False,
            "error": (
                "LLM-based paper extraction requires a Claude API key. "
                "Configure your Anthropic key in /account (BYOK)."
            ),
            "error_class": "missing_api_key",
        }
    if not fields:
        fields = ["source_name", "fwhm_km_s", "log_luminosity", "z"]

    try:
        from app.services.llm_paper_extractor import extract_with_llm_and_verify
        loop = asyncio.get_running_loop()
        records = await loop.run_in_executor(
            None,
            lambda: extract_with_llm_and_verify(arxiv_id, fields, api_key),
        )
    except Exception as exc:
        return {
            "success": False,
            "error": f"LLM extraction failed: {exc}",
            "error_class": "llm_extraction_failed",
            "arxiv_id": arxiv_id,
        }

    passed = [r for r in records if r.validation_status == "passed"]
    failed_mismatch = [r for r in records if r.validation_status == "failed_mismatch"]
    failed_no_cell = [r for r in records if r.validation_status == "failed_no_cell"]

    cleaned_arxiv = arxiv_id.replace("arXiv:", "").replace("arxiv:", "").strip()
    bibcode = f"arXiv:{cleaned_arxiv}"
    line_measurements = []
    for r in passed:
        line_measurements.append({
            "source_name": r.source_name,
            "fwhm_km_s": r.fwhm_km_s,
            "log_luminosity": r.log_luminosity,
            "z": r.z,
            "z_line": r.z,
            "bibcode": bibcode,
            "arxiv_id": cleaned_arxiv,
            "source_url": f"https://arxiv.org/abs/{cleaned_arxiv}",
            "extraction_method": "llm_with_cell_reverify",
            "cell_provenance": r.cell_provenance,
            "table_idx": r.table_idx,
            "row_idx": r.row_idx,
            "is_lensed": False,
            "mu_lens": None,
            "fwhm_err_km_s": None,
            "log_luminosity_err": None,
            "source_cosmology": None,
        })

    cache_key = (
        _session_cache_key("latest_literature_tables", python_session_id)
        or "latest_literature_tables"
    )
    cache_payload = {
        "arxiv_id": cleaned_arxiv,
        "bibcode": bibcode,
        "cache_key": cache_key,
        "line_measurements": line_measurements,
        "extraction_method": "llm_with_cell_reverify",
        "tables": [],
    }
    if line_measurements:
        store_search_results(cache_key, cache_payload)
        if cache_key != "latest_literature_tables":
            store_search_results("latest_literature_tables", cache_payload)

    return {
        "success": True,
        "arxiv_id": cleaned_arxiv,
        "bibcode": bibcode,
        "cache_key": cache_key,
        "line_measurements": line_measurements,
        "passed_count": len(passed),
        "failed_mismatch_count": len(failed_mismatch),
        "failed_no_cell_count": len(failed_no_cell),
        "rejected_rows": [
            {
                "source_name": r.source_name,
                "validation_status": r.validation_status,
                "validation_notes": r.validation_notes,
            }
            for r in failed_mismatch + failed_no_cell
        ],
    }


def _literature_hit_should_be_hidden(row: dict[str, Any]) -> bool:
    """Hide search hits that are known-bad rather than merely off-topic."""
    blob = " ".join(
        str(row.get(key) or "")
        for key in ("title", "abstract", "bibcode", "source", "pub")
    ).lower()
    known_bad_phrases = (
        "withdrawn by arxiv administrators",
        "contains fictitious content",
        "submitted under a pseudonym",
    )
    return any(phrase in blob for phrase in known_bad_phrases)


_LITERATURE_STOPWORDS: frozenset[str] = frozenset({
    "about", "after", "again", "against", "also", "analysis", "and", "are",
    "between", "both", "can", "could", "data", "different", "does", "for",
    "from", "give", "given", "have", "into", "model", "models", "more",
    "over", "paper", "papers", "result", "results", "sample", "samples",
    "show", "the", "their", "these", "this", "through", "using", "when",
    "with", "would",
})

_COSMOLOGY_QUERY_MARKERS: tuple[str, ...] = (
    "desi", "bao", "baryon acoustic", "pantheon", "union3", "des-5yr",
    "des 5yr", "sn ia", "sne ia", "supernova", "lcdm", "Λcdm", "dark energy",
    "gaussian process", "om(z)", "w_tot", "h0", "omega", "Ω",
)
_LINE_QUERY_MARKERS: tuple[str, ...] = (
    "[cii]", "cii", "c ii", "158", "fwhm", "line width", "line luminosity",
    "line-flux", "lfr", "alpine", "rebels", "alma",
)
_OFF_TOPIC_HIGH_ENERGY_MARKERS: tuple[str, ...] = (
    "besiii", "lhcb", "ckm angle", "charmonium", "branching fraction",
    "decay asymmetry", "w-annihilation", "semileptonic decay", "j/ψ",
    "j/psi", "electron-positron collider", "b meson", "d_s", "lambda_c",
    "ξ", "xi baryon",
)
_OFF_TOPIC_GENERAL_MARKERS: tuple[str, ...] = (
    "wildfire", "power line", "shutoff", "electric grid", "nuclear mass",
    "hartree-bogoliubov", "drhbc", "access point", "wi-fi",
    "wireless network", "semiring", "perverse sheaves",
)
_COSMOLOGY_RELEVANCE_ANCHORS: tuple[str, ...] = (
    "cosmolog", "hubble", "dark energy", "bao", "baryon acoustic",
    "supernova", "supernovae", "pantheon", "cmb", "planck", "desi",
    "weak lensing", "sigma8", "omega_m", "omegam", "lcdm",
)


def _filter_literature_hits_for_query(
    query: str,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Stage 6 P0c-a v2 (2026-05-19): backend filter changed to blocklist veto.

    Old approach: keyword scoring, keeping only rows with score >= 2. This was
    too aggressive at the edges of cosmology queries — a paper whose abstract
    lacked anchor words but was genuinely relevant would be dropped (prod run 2:
    same prompt, 24 papers one run, 0 papers the next; the 0-paper path had all
    8 ADS results score < 2 and be discarded).

    New approach: no scoring. Only check against an obvious off-topic blocklist.
    Fine-grained relevance scoring is delegated to the Direct/Marginal/Off-topic
    table that Stage 6.2 forces the LLM to output. The backend retains only the
    anti-leak hard-block (R2.9/M4 audit: BESIII / power-engineering papers
    genuinely leaked into cosmology searches in production).

    Three blocklist categories:
      1. Known particle-physics off-topic (BESIII / LHCb / CKM / b meson / ...)
      2. General dirty words (wildfire / power grid / wi-fi / semiring / ...)
      3. "particle physics" text but NO cosmology anchor (PDG
         "The Cosmological Parameters" review is exempt)

    Generic domain queries are not filtered (same as the old approach).
    """
    if not rows:
        return [], 0

    domain = _literature_query_domain(query)
    if domain == "generic":
        return rows, 0

    kept = [row for row in rows if not _literature_hit_is_blacklisted(row, domain)]
    filtered_out = len(rows) - len(kept)
    if filtered_out:
        logger.info(
            "search_literature blacklist removed %d/%d off-topic hits domain=%s query=%r",
            filtered_out,
            len(rows),
            domain,
            query[:120],
        )
    return kept, filtered_out


def _literature_hit_is_blacklisted(row: dict[str, Any], domain: str) -> bool:
    """Return True if any blocklist entry matches (single-veto logic)."""
    blob = _normalize_literature_text(" ".join(
        str(row.get(key) or "")
        for key in ("title", "abstract", "bibcode", "source", "pub")
    ))
    if any(marker in blob for marker in _OFF_TOPIC_HIGH_ENERGY_MARKERS):
        return True
    if any(marker in blob for marker in _OFF_TOPIC_GENERAL_MARKERS):
        return True
    if domain == "cosmology":
        if "particle physics" in blob and not any(
            marker in blob for marker in _COSMOLOGY_RELEVANCE_ANCHORS
        ):
            return True
    return False


def _literature_query_domain(query: str) -> str:
    normalized = _normalize_literature_text(query)
    if any(marker in normalized for marker in _LINE_QUERY_MARKERS):
        return "line"
    if any(_normalize_literature_text(marker) in normalized for marker in _COSMOLOGY_QUERY_MARKERS):
        return "cosmology"
    return "generic"


def _literature_relevance_score(query: str, row: dict[str, Any], domain: str) -> int:
    query_norm = _normalize_literature_text(query)
    blob = _normalize_literature_text(" ".join(
        str(row.get(key) or "")
        for key in ("title", "abstract", "bibcode", "source", "pub")
    ))
    score = 0

    query_terms = _literature_query_terms(query_norm)
    for term in query_terms:
        if term in blob:
            score += 2

    for phrase in _literature_priority_phrases(query_norm):
        if phrase in blob:
            score += 4

    if domain == "cosmology":
        if any(marker in blob for marker in ("desi", "bao", "baryon acoustic", "dark energy", "supernova", "pantheon", "union3", "lcdm", "cosmolog")):
            score += 3
        if any(marker in blob for marker in _OFF_TOPIC_HIGH_ENERGY_MARKERS):
            score -= 14
        # "Review of Particle Physics — Cosmological Parameters" is a
        # legitimate cosmology hit, but generic particle-physics schools or
        # collider proceedings are not.  Penalize the latter only when no
        # cosmology anchor appears anywhere in the hit.
        if "particle physics" in blob and not any(
            marker in blob for marker in _COSMOLOGY_RELEVANCE_ANCHORS
        ):
            score -= 14
    elif domain == "line":
        if any(marker in blob for marker in ("cii", "c ii", "158", "alma", "alpine", "rebels", "fwhm", "line width")):
            score += 3
        if any(marker in blob for marker in ("high redshift", "galax", "survey", "source properties", "catalog")):
            score += 1

    if any(marker in blob for marker in _OFF_TOPIC_GENERAL_MARKERS):
        score -= 14

    return score


def _normalize_literature_text(text: str) -> str:
    normalized = str(text or "").lower()
    normalized = normalized.replace("λ", "lambda").replace("Λ", "lambda")
    normalized = normalized.replace("ω", "omega").replace("Ω", "omega")
    normalized = normalized.replace("₀", "0").replace("ₐ", "a").replace("ₘ", "m")
    normalized = normalized.replace("μ", "mu").replace("‑", "-").replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[^a-z0-9+\-_'\\[\\]()./ ]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _literature_query_terms(query_norm: str) -> set[str]:
    raw_terms = set(re.findall(r"[a-z0-9][a-z0-9+'-]{2,}", query_norm))
    terms = {
        term
        for term in raw_terms
        if term not in _LITERATURE_STOPWORDS and not term.isdigit()
    }
    # Expand common astronomy/cosmology shorthands into the words most often
    # present in ADS abstracts and titles.
    if "bao" in terms:
        terms.update({"baryon", "acoustic", "oscillation"})
    if "desi" in terms:
        terms.update({"spectroscopic", "instrument"})
    if "sn" in terms or "sne" in terms:
        terms.update({"supernova", "supernovae"})
    if "lcdm" in terms or "lambda" in terms:
        terms.update({"cosmolog", "dark", "energy"})
    return terms


def _literature_priority_phrases(query_norm: str) -> set[str]:
    phrases: set[str] = set()
    for phrase in (
        "desi dr1", "dark energy spectroscopic instrument", "baryon acoustic",
        "gaussian process", "pantheon plus", "pantheon+", "des-5yr",
        "des 5yr", "union3", "lrg1", "c ii", "[cii]", "line width",
        "line luminosity", "158 micron", "158 mu m",
    ):
        if phrase in query_norm:
            phrases.add(phrase)
    return phrases


def _arxiv_id_from_table_input(inp: dict[str, Any]) -> str:
    for key in ("arxiv_id", "arxiv_url", "url"):
        value = str(inp.get(key) or "").strip()
        if value:
            return value
    paper = inp.get("paper")
    if isinstance(paper, dict):
        for key in ("arxiv_id", "arxiv_url", "url"):
            value = str(paper.get(key) or "").strip()
            if value:
                return value
        bibcode = str(paper.get("bibcode") or "").strip()
        if bibcode.lower().startswith("arxiv:"):
            return bibcode
    return ""
