"""Line-luminosity/FWHM relation workflow helpers: candidate ranking,
fit routing from prompts, and synthetic-substitution suppression.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

from typing import Any


def _line_fit_method_from_prompt(text: str) -> str:
    """Choose the requested LFR fit method from user wording."""
    lowered = str(text or "").lower()
    if any(
        token in lowered
        for token in (
            "bayesian",
            "贝叶斯",
            "errors-in-both",
            "both axes",
            "两轴",
            "xyerr",
            "measurement error",
            "测量误差",
        )
    ):
        return "bayesian_xyerr"
    return "auto"


def _line_fit_cosmology_from_prompt(text: str) -> str | None:
    """Map explicit paper-cosmology wording to a fit_line_lfr cosmology spec."""
    compact = (
        str(text or "")
        .lower()
        .replace(" ", "")
        .replace("ω", "omega")
        .replace("Ω", "omega")
        .replace("ₘ", "m")
    )
    if "riess" in compact and "suzuki" in compact:
        return "FlatLambdaCDM_H73p8_Om0p295"
    if (
        ("h0=73.8" in compact or "h0:73.8" in compact or "h73p8" in compact)
        and ("om0=0.295" in compact or "omegam=0.295" in compact or "om0p295" in compact)
    ):
        return "FlatLambdaCDM_H73p8_Om0p295"
    if "riess22" in compact or "riess+22" in compact:
        return "riess22_shoes"
    return None


def _line_fit_subsample_splits_from_prompt(text: str) -> list[dict[str, float | str]] | None:
    compact = str(text or "").lower().replace(" ", "")
    wants_z1_split = any(
        token in compact
        for token in (
            "z<1",
            "z＞1",
            "z>1",
            "redshiftdependence",
            "redshift依赖",
            "红移依赖",
        )
    )
    if not wants_z1_split:
        return None
    return [
        {"name": "z<1", "z_min": 0.0, "z_max": 1.0},
        {"name": "z>=1", "z_min": 1.0},
    ]


def _line_fit_context(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(token in lowered for token in (
        "[cii]", "c ii", "cii", "l[cii]", "lcii", "line luminosity",
        "fwhm", "line width", "linewidth", "alma", "alpine", "rebels",
        "relation", "correlation", "regression", "fit", "fitting",
    ))


def _is_line_relation_workflow(text: str) -> bool:
    lowered = str(text or "").lower()
    has_line_quantity = any(token in lowered for token in (
        "fwhm", "line width", "linewidth", "线宽",
    ))
    has_luminosity_context = any(token in lowered for token in (
        "lfr", "[cii]", "c ii", "cii", "l[cii]", "lcii", "谱线", "光度",
    ))
    has_fit_request = any(token in lowered for token in (
        "slope", "intercept", "scatter", "regression", "relation",
        "correlation", "fit", "fitting", "拟合", "回归", "标度关系",
        "斜率", "截距", "散布",
    ))
    return has_line_quantity and has_luminosity_context and has_fit_request


def _extract_arxiv_id_from_paper(paper: dict[str, Any]) -> str:
    import re

    bibcode = str(paper.get("bibcode") or "").strip()
    match = re.search(r"arxiv[:\s/]+(\d{4}\.\d{4,5}(?:v\d+)?)", bibcode, re.I)
    if match:
        return match.group(1)

    for value in paper.values():
        if not isinstance(value, str):
            continue
        match = re.search(r"(?:arxiv[:\s/]+)?(\d{4}\.\d{4,5}(?:v\d+)?)", value, re.I)
        if match:
            return match.group(1)
    return ""


def _rank_literature_candidate_for_line_lfr(paper: dict[str, Any]) -> tuple[int, str]:
    """Score search_literature papers for table extraction in line-LFR workflows.

    This is deliberately domain-general: prefer papers likely to contain
    machine-readable line measurements, and heavily penalize obvious topical
    drift.  It does not encode any target paper's final answer.
    """
    title = str(paper.get("title") or "")
    abstract = str(paper.get("abstract") or "")
    bibcode = str(paper.get("bibcode") or "")
    source = str(paper.get("source") or "")
    haystack = " ".join([title, abstract, bibcode, source]).lower()
    score = 0

    arxiv_id = _extract_arxiv_id_from_paper(paper)
    if arxiv_id:
        score += 2

    positive_groups: tuple[tuple[tuple[str, ...], int], ...] = (
        (("[cii]", "c ii", "cii]", "158", "158um", "158 μm", "158 micron"), 6),
        (("fwhm", "line width", "linewidth", "velocity width"), 5),
        (("line luminosity", "luminosity", "l[c", "l'"), 4),
        (("data processing", "catalogs", "catalogues", "statistical source properties"), 10),
        (("catalog", "catalogue", "table", "data release"), 4),
        (("survey strategy", "sample properties", "observations and sample"), 5),
        (("survey", "source properties", "measurements", "detected galaxies"), 3),
        (("alpine", "rebels", "alma large program"), 3),
        (("relation", "correlation", "scaling", "line-flux", "line flux"), 2),
        (("redshift", "high-redshift", "high redshift"), 1),
    )
    for tokens, weight in positive_groups:
        if any(token in haystack for token in tokens):
            score += weight

    negative_groups: tuple[tuple[tuple[str, ...], int], ...] = (
        (("withdrawn", "administratively withdrawn"), 25),
        (("wildfire", "power line", "shutoff", "electric grid"), 25),
        (("nuclear mass", "hartree-bogoliubov", "drhbc"), 22),
        (("access point", "wi-fi", "wireless network"), 18),
        (("semiring", "krotov", "perverse sheaves", "proof of"), 16),
        (("weak lensing cluster mass", "dark matter 2013"), 10),
        (("luminosity function", "sfr relation", "undetected", "serendipitous"), 5),
        (("size of", "halo", "halos", "outflows", "nature of"), 4),
    )
    for tokens, penalty in negative_groups:
        if any(token in haystack for token in tokens):
            score -= penalty

    # Require at least one explicit line/far-infrared hook for extraction;
    # otherwise abstract search can drift into cosmology or generic high-z
    # papers that cannot support L[CII]-FWHM measurements.
    if not any(token in haystack for token in ("cii", "[cii]", "c ii", "158", "alma", "alpine", "rebels")):
        score -= 8

    return score, arxiv_id


def _ranked_literature_arxiv_candidates(tool_results: list[dict]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in tool_results or []:
        if entry.get("tool") != "search_literature":
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for paper in result.get("results") or []:
            if not isinstance(paper, dict):
                continue
            score, arxiv_id = _rank_literature_candidate_for_line_lfr(paper)
            if not arxiv_id or score < 6 or arxiv_id in seen:
                continue
            seen.add(arxiv_id)
            ranked.append({
                "arxiv_id": arxiv_id,
                "score": score,
                "title": str(paper.get("title") or "").strip(),
            })
    ranked.sort(key=lambda item: int(item.get("score") or 0), reverse=True)
    return ranked[:6]


def _verified_line_relation_seed_candidates(latest_user_text: str) -> list[dict[str, Any]]:
    """Return verified table-extraction seed papers when external search is unavailable.

    This does not provide measurement values or fit results.  It only gives
    the agent a vetted arXiv ID that must still pass
    `extract_literature_tables` in the current turn.  The motivation is
    operational: arXiv/ADS searches are rate-limited and can return empty even
    though the platform has a verified [CII] source-table seed.
    """
    text = str(latest_user_text or "").lower()
    if not _line_fit_context(text):
        return []
    if not any(token in text for token in ("[cii]", "cii", "c ii", "158")):
        return []
    try:
        from app.api.admin_literature import DEFAULT_CII_ARXIV_IDS
    except Exception:
        DEFAULT_CII_ARXIV_IDS = ("2002.00962",)
    return [
        {
            "arxiv_id": arxiv_id,
            "score": 100,
            "title": "Verified [CII] line-measurement seed; must be extracted before use",
            "seed_source": "verified_cii_admin_literature",
        }
        for arxiv_id in DEFAULT_CII_ARXIV_IDS
    ]


def _literature_arxiv_candidates(tool_results: list[dict]) -> list[str]:
    return [
        str(item["arxiv_id"])
        for item in _ranked_literature_arxiv_candidates(tool_results)
        if item.get("arxiv_id")
    ]


def _table_extraction_arxiv_ids(tool_results: list[dict]) -> set[str]:
    attempted: set[str] = set()
    for entry in tool_results or []:
        if entry.get("tool") != "extract_literature_tables":
            continue
        tool_input = entry.get("input")
        if not isinstance(tool_input, dict):
            continue
        candidate = _extract_arxiv_id_from_paper(tool_input)
        if not candidate and isinstance(tool_input.get("paper"), dict):
            candidate = _extract_arxiv_id_from_paper(tool_input["paper"])
        if candidate:
            attempted.add(candidate)
    return attempted


def _run_python_reads_real_cache(code: str) -> bool:
    return any(token in str(code or "") for token in (
        "get_cached_results(", "get_search_results(", "get_adql_results(",
        "get_adql_result_sets(", "load_fits(",
    ))


def _should_suppress_line_measurement_synthetic_python(
    tool_call: dict,
    *,
    fit_ready_cache_keys: list[str],
    latest_user_text: str,
    user_requested_synthetic_demo: bool,
) -> bool:
    if tool_call.get("name") != "run_python" or not fit_ready_cache_keys:
        return False
    if user_requested_synthetic_demo:
        return False
    tool_input = tool_call.get("input") if isinstance(tool_call.get("input"), dict) else {}
    declared = str(tool_input.get("data_source") or "").strip()
    if declared and declared != "none_not_analyzing_real_data":
        return False
    code = str(tool_input.get("code") or "")
    if _run_python_reads_real_cache(code):
        return False
    context = "\n".join([
        latest_user_text,
        str(tool_input.get("description") or ""),
        code,
    ])
    return _line_fit_context(context)


def _suppressed_line_measurement_python_result(cache_keys: list[str]) -> dict:
    cache_key = cache_keys[-1] if cache_keys else "latest_literature_tables"
    return {
        "success": True,
        "__tool_status__": "EMPTY",
        "analysis_status": "empty",
        "data_origin": "unavailable",
        "row_count": 0,
        "__internal_suppressed__": True,
        "suppressed_reason": "fit_ready_literature_measurements_available",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"run_python was suppressed because fit-ready literature measurement "
            f"rows are already cached as {cache_key}. You MUST call "
            f"fit_line_lfr(cache_key='{cache_key}') or read cached rows with "
            "data_source='cached:<key>'. Do not create synthetic or hardcoded "
            "literature samples for this fitting task."
        ),
        "__suggested_next_step__": f"Call fit_line_lfr with cache_key={cache_key}.",
        "cache_key": cache_key,
    }


def _suppressed_line_relation_search_result() -> dict:
    return {
        "success": True,
        "__tool_status__": "EMPTY",
        "analysis_status": "empty",
        "data_origin": "unavailable",
        "row_count": 0,
        "results": [],
        "__internal_suppressed__": True,
        "__do_not_claim__": True,
        "__message_to_model__": (
            "Additional search_literature calls were suppressed because this "
            "line-luminosity/FWHM workflow already has enough abstract-level "
            "paper search results for candidate selection. Abstracts cannot "
            "support L[CII]/FWHM measurement or fit claims. Next, use "
            "extract_literature_tables on the best arXiv candidates, or "
            "honestly report that no fit-ready measurement table was found."
        ),
        "suppressed_reason": "line_relation_search_budget_exceeded",
    }


def _suppressed_line_relation_extract_result(max_attempts: int) -> dict:
    return {
        "success": True,
        "__tool_status__": "EMPTY",
        "analysis_status": "empty",
        "data_origin": "unavailable",
        "row_count": 0,
        "line_measurement_count": 0,
        "fit_ready": False,
        "__internal_suppressed__": True,
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Additional extract_literature_tables calls were suppressed after "
            f"{max_attempts} table-extraction attempt(s) without fit-ready "
            "line_measurements. Do not keep trying broad candidates or create "
            "synthetic rows. Summarize the limitation and ask for a specific "
            "paper/table source if needed."
        ),
        "suppressed_reason": "line_relation_extract_budget_exceeded",
    }
