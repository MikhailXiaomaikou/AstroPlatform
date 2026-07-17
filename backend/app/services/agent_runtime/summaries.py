"""Deterministic tool-grounded summaries (research / line-LFR / statistics /
cosmology) used when model prose is empty, blocked, or timed out.

Moved verbatim from app/api/chat.py (2026-07-03 god-file split).
"""

import math
import re
from typing import Any

from app.services.agent_runtime.prompt_routing import (
    COSMOLOGY_DATASET_FAMILY_ALIASES,
    COSMOLOGY_PLUS_RELEASE_FAMILIES,
    _dataset_mention_is_non_execution,
)


def _tool_grounded_timeout_summary(tool_results: list[dict], timeout_s: int) -> str:
    """Build a safe user-facing summary when the workflow budget is exhausted."""
    grounded = (
        _research_tool_grounded_summary(tool_results)
        or _line_lfr_tool_grounded_summary(tool_results)
        or _statistics_tool_grounded_summary(tool_results)
        or _cosmology_tool_grounded_summary(tool_results)
        or ""
    )
    if grounded.strip():
        return (
            "The workflow reached its time budget before the final language "
            "answer could be completed. The tools below did run in this turn, "
            "so I am returning a deterministic tool-grounded partial summary; "
            "no unsupported conclusion is being added.\n\n"
            + grounded
        )
    if tool_results:
        tool_names = ", ".join({
            str(tr.get("tool") or tr.get("name") or "unknown")
            for tr in tool_results
            if isinstance(tr, dict)
        })
        return (
            f"The workflow reached its {timeout_s}s time budget after running "
            f"these tools: {tool_names}. The tool cards are the source of "
            "truth for this partial run; no scientific conclusion is claimed "
            "from the timeout path."
        )
    return ""


def _format_dataset_gap_item(item: Any) -> str:
    """Return a user-facing name for a missing/config-only dataset entry."""
    if isinstance(item, dict):
        key = str(item.get("key") or "").strip()
        display = str(item.get("display_name") or item.get("service_name") or "").strip()
        if display and key:
            return f"{display} ({key})"
        if display:
            return display
        if key:
            return key
        return "unnamed dataset"
    return str(item)


def _finite_number(value: Any) -> float | None:
    """Return a finite diagnostic number without accepting booleans."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _line_measurement_count_from_result(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    explicit = result.get("line_measurement_count")
    if isinstance(explicit, int):
        return max(0, explicit)
    rows = result.get("line_measurements")
    if isinstance(rows, list):
        return len(rows)
    summary = result.get("llm_summary")
    if isinstance(summary, dict) and isinstance(summary.get("line_measurement_count"), int):
        return max(0, int(summary["line_measurement_count"]))
    return 0


def _line_fit_publication_ready_from_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    status = str(result.get("__tool_status__") or "").strip().upper()
    if (
        result.get("publication_ready") is False
        or result.get("__do_not_claim__") is True
        or status in {"PARTIAL", "EMPTY", "FAILED", "UNAVAILABLE", "SYNTHETIC"}
    ):
        return False
    if result.get("publication_ready") is True:
        return True
    n_used = result.get("n_used") or result.get("n_rows") or result.get("n_fit")
    if isinstance(n_used, int) and n_used >= 5 and result.get("success") is True:
        return True
    if (
        isinstance(n_used, int)
        and n_used >= 5
        and result.get("alpha") is not None
        and result.get("beta") is not None
        and (
            result.get("intrinsic_scatter_dex") is not None
            or result.get("sigma_int") is not None
        )
    ):
        return True
    return False


def _line_fit_partial_from_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if _line_fit_publication_ready_from_result(result):
        return False
    status = str(result.get("__tool_status__") or "").strip().upper()
    n_used = result.get("n_used") or result.get("n_rows") or result.get("n_fit")
    has_stats = (
        result.get("alpha") is not None
        and result.get("beta") is not None
        and (
            result.get("intrinsic_scatter_dex") is not None
            or result.get("sigma_int") is not None
        )
    )
    return bool(
        result.get("success") is True
        and isinstance(n_used, int)
        and n_used >= 5
        and has_stats
        and (
            status == "PARTIAL"
            or result.get("__do_not_claim__") is True
            or result.get("publication_ready") is False
        )
    )


def _fmt_tool_number(value: Any, digits: int = 4) -> str:
    if isinstance(value, (int, float)):
        return f"{float(value):.{digits}g}"
    return "not reported"


def _line_lfr_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic user-facing summary when the LLM returns empty prose."""
    fit: dict[str, Any] | None = None
    for entry in reversed(tool_results or []):
        if entry.get("tool") != "fit_line_lfr":
            continue
        result = entry.get("result")
        if _line_fit_publication_ready_from_result(result):
            fit = result
            break
    if not fit:
        return None

    citation_summary = fit.get("citation_summary")
    citations: list[str] = []
    table_labels: list[str] = []
    if isinstance(citation_summary, dict):
        citations = [str(c) for c in citation_summary.get("citations") or [] if c]
        table_labels = [str(t) for t in citation_summary.get("table_labels") or [] if t]

    scatter = fit.get("intrinsic_scatter_dex")
    if scatter is None:
        scatter = fit.get("sigma_int")
    scatter_hdi = fit.get("intrinsic_scatter_dex_hdi")
    scatter_text = _fmt_tool_number(scatter)
    if isinstance(scatter_hdi, list) and len(scatter_hdi) >= 2:
        scatter_text += (
            f" dex (94% HDI {_fmt_tool_number(scatter_hdi[0])}"
            f"-{_fmt_tool_number(scatter_hdi[1])})"
        )
    else:
        scatter_text += " dex"

    subsample_note = ""
    subs = fit.get("subsample_significance_test")
    if isinstance(subs, dict) and isinstance(subs.get("subsamples"), list):
        parts = []
        for item in subs["subsamples"]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "subsample")
            n = item.get("n")
            beta = item.get("beta")
            if beta is None:
                reason = str(item.get("skipped_reason") or "not fitted")
                parts.append(f"{name}: n={n}, {reason}")
            else:
                parts.append(f"{name}: n={n}, beta={_fmt_tool_number(beta)}")
        if parts:
            subsample_note = " Subsample check: " + "; ".join(parts) + "."

    cosmology = str(fit.get("cosmology_used") or "not reported")
    cosmo_manifest = fit.get("cosmology_manifest")
    cosmo_cite = ""
    if isinstance(cosmo_manifest, dict) and cosmo_manifest.get("bibcode"):
        cosmo_cite = f" ({cosmo_manifest['bibcode']})"

    n_used = fit.get("n_used")
    n_available = fit.get("n_available")
    source_text = "cited measurement rows"
    if citations:
        source_text = ", ".join(citations)
        if table_labels:
            source_text += " / " + ", ".join(table_labels)

    return (
        "Tool-grounded summary: the literature-table fit completed with "
        f"publication_ready=true, so the following numbers are copied from "
        f"`fit_line_lfr`, not from memory.\n\n"
        f"- Model: `{fit.get('model') or 'log_luminosity = alpha + beta * log10(FWHM_km_s / 100)'}`.\n"
        f"- Sample: {n_used} of {n_available} rows used from {source_text}.\n"
        f"- Fit: alpha = {_fmt_tool_number(fit.get('alpha'))} +/- "
        f"{_fmt_tool_number(fit.get('alpha_stderr'))}; beta = "
        f"{_fmt_tool_number(fit.get('beta'))} +/- "
        f"{_fmt_tool_number(fit.get('beta_stderr'))}; intrinsic scatter = "
        f"{scatter_text}.\n"
        f"- Method: {fit.get('fit_method') or 'not reported'}; "
        f"Pearson r = {_fmt_tool_number(fit.get('pearson_r'))}, "
        f"p = {_fmt_tool_number(fit.get('pearson_p'))}.\n"
        f"- Cosmology used by the tool: {cosmology}{cosmo_cite}; "
        f"cosmology_recomputed={bool(fit.get('cosmology_recomputed'))}.\n"
        f"- Lensing: demagnified sources = {fit.get('lensed_sources_demagnified')}; "
        f"lensing status unknown for {fit.get('n_lensed_unknown')} rows."
        f"{subsample_note}\n\n"
        "Scope note: this is the fit-ready table subset available this turn. "
        "It is not automatically the full multi-survey sample of the target paper "
        "unless additional extracted tables are merged into the cache."
    )


def _statistics_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic summary for inline-array statistics when prose is empty."""
    stats: dict[str, Any] | None = None
    for entry in reversed(tool_results or []):
        if entry.get("tool") != "astro_statistics_toolbox":
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("success") is True:
            stats = result
            break
    if not stats:
        return None
    analysis_type = str(stats.get("analysis_type") or "")
    if analysis_type != "linear_regression":
        return None

    return (
        "Tool-grounded statistics summary: I used `astro_statistics_toolbox` "
        "on the x/y arrays supplied in this turn.\n\n"
        f"- Method: {stats.get('method') or 'not reported'}; n = {stats.get('n')}.\n"
        f"- Slope: {_fmt_tool_number(stats.get('slope'))} +/- "
        f"{_fmt_tool_number(stats.get('slope_stderr'))}.\n"
        f"- Intercept: {_fmt_tool_number(stats.get('intercept'))} +/- "
        f"{_fmt_tool_number(stats.get('intercept_stderr'))}.\n"
        f"- Residual RMS: {_fmt_tool_number(stats.get('residual_rms'))}.\n"
        f"- publication_ready={bool(stats.get('publication_ready'))}."
    )


def _cosmology_requested_redshift(prompt: str) -> float | None:
    """Largest explicit redshift the user asked a quantity to be reported AT
    (e.g. 'Omega_m at z = 12' → 12.0). Requires an explicit z=/redshift marker
    so we do not match incidental digits."""
    import re

    values: list[float] = []
    for pattern in (
        r"\bz\s*[=≈~]\s*([0-9]+(?:\.[0-9]+)?)",
        r"\bredshift\s*(?:of\s*)?[=≈~]?\s*([0-9]+(?:\.[0-9]+)?)",
    ):
        for match in re.finditer(pattern, prompt or "", re.IGNORECASE):
            try:
                values.append(float(match.group(1)))
            except ValueError:
                continue
    return max(values) if values else None


def _cosmology_max_z_coverage(tool_results: list[dict]) -> float | None:
    """Highest z_coverage upper bound across the datasets surfaced this turn
    (registry list, chain datasets_used, or a loaded data product)."""
    zmax: float | None = None

    def _consider(cov: Any) -> None:
        nonlocal zmax
        if isinstance(cov, (list, tuple)) and len(cov) == 2 and isinstance(cov[1], (int, float)):
            zmax = float(cov[1]) if zmax is None else max(zmax, float(cov[1]))

    for entry in tool_results or []:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for key in ("datasets", "datasets_used"):
            for item in result.get(key) or []:
                if isinstance(item, dict):
                    _consider(item.get("z_coverage"))
        top = result.get("z_coverage_max")
        if isinstance(top, (int, float)):
            zmax = float(top) if zmax is None else max(zmax, float(top))
    return zmax


def _cosmology_outside_coverage_disclosure(
    tool_results: list[dict],
    user_prompt: str,
) -> str | None:
    """Return a deterministic disclosure for an out-of-coverage request.

    ``list_cosmology_datasets`` now returns a structured coverage verdict when
    the agent loop passes the requested redshift.  The fallback calculation
    keeps older stored tool records safe and makes the final reply independent
    of provider wording.
    """

    requested_z = _cosmology_requested_redshift(user_prompt)
    if requested_z is None:
        return None
    structured_outside = False
    for entry in tool_results or []:
        result = entry.get("result")
        if isinstance(result, dict) and result.get("coverage_status") == "outside":
            structured_outside = True
            break
    coverage_zmax = _cosmology_max_z_coverage(tool_results)
    if not structured_outside and not (
        coverage_zmax is not None and requested_z > coverage_zmax + 1e-9
    ):
        return None
    range_text = (
        f" (the registered measurements end at z = {coverage_zmax:g})"
        if coverage_zmax is not None
        else ""
    )
    return (
        "Coverage status: outside. The requested redshift is beyond the "
        f"selected dataset's measured range{range_text}. Any value there is "
        "a model-dependent extrapolation, not a measurement or data constraint "
        "from that dataset."
    )


def _dataset_release_markers(text: str) -> dict[str, set[str]]:
    """Extract release identifiers without interpreting scientific numbers.

    The categories keep paper years separate from survey-scale names (for
    example KiDS-1000), which avoids treating a nearby citation year as a
    requested data release.  This deliberately recognises release *shapes*
    rather than a case-specific list of survey names.
    """
    raw_text = str(text or "")
    normalized = raw_text.lower().replace("_", " ").replace("-", " ")
    normalized = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", normalized)
    markers: dict[str, set[str]] = {"release": set(), "year": set(), "scale": set()}
    for prefix, value in re.findall(
        r"\b(dr|pr|y)\s*([0-9]+[a-z]?)\b", normalized, re.IGNORECASE
    ):
        markers["release"].add(f"{prefix.upper()}{value.upper()}")
    for value in re.findall(r"\bdata\s+release\s*([0-9]+[a-z]?)\b", normalized):
        markers["release"].add(f"DR{value.upper()}")
    for value in re.findall(r"\b([0-9]+)\s*(?:yr|year)\b", normalized):
        markers["release"].add(f"{value}yr")
    if re.search(r"\blegacy\b", normalized):
        markers["release"].add("Legacy")
    if re.search(r"\bplus\b", normalized):
        markers["release"].add("Plus")
    for value in re.findall(r"\b(?:19|20)[0-9]{2}\b", normalized):
        markers["year"].add(value)
    for value in re.findall(r"\b[0-9]{3,4}\b", normalized):
        if not re.fullmatch(r"(?:19|20)[0-9]{2}", value):
            markers["scale"].add(value)
    return markers


def _dataset_family_token(item: dict[str, Any]) -> str:
    """Derive a stable survey-family token from a registry key."""
    key = str(item.get("key") or "").strip().lower()
    head = key.split("_", 1)[0]
    # kids1000 -> kids, planck2018 -> planck, spt3g -> spt.  Release
    # comparison remains generic because the concrete marker is extracted
    # independently from the complete key/display/version text.
    return re.sub(r"[0-9]+[a-z]*$", "", head)


def _markers_near_dataset_family(prompt: str, family: str) -> dict[str, set[str]]:
    """Read only the dataset phrase around ``family``, not the whole prompt."""
    found = {"release": set(), "year": set(), "scale": set()}
    if not family:
        return found
    prompt_text = str(prompt or "")
    separator = re.compile(
        r"[,;/]|\s+\+\s+|\b(?:and|with|versus|vs)\b", re.IGNORECASE
    )
    aliases = COSMOLOGY_DATASET_FAMILY_ALIASES.get(family, (family,))

    def family_matches():
        for alias in aliases:
            alias_pattern = re.escape(alias).replace(r"\ ", r"\s+")
            yield from re.finditer(
                rf"(?<![a-z0-9]){alias_pattern}(?![a-z0-9])",
                prompt_text,
                re.IGNORECASE,
            )

    matches = family_matches()
    for match in matches:
        if _dataset_mention_is_non_execution(
            prompt_text, match.start(), match.end()
        ):
            continue
        before = prompt_text[max(0, match.start() - 32) : match.start()]
        after = prompt_text[match.end() : match.end() + 64]
        after = re.split(
            r"[\(\[]\s*(?:not|without|excluding?|avoid(?:ing)?)\b",
            after,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        if family in COSMOLOGY_PLUS_RELEASE_FAMILIES and after.startswith("+"):
            # Survey names such as Pantheon+ may be immediately followed by a
            # companion label (Pantheon+SH0ES).  This is a release marker, not
            # the infix dataset-union operator in "DESI + Planck".
            found["release"].add("Plus")
        for prefix, value in re.findall(
            r"\bwith\s+(?:the\s+)?(?:registered\s+)?(dr|pr|y)\s*([0-9]+[a-z]?)\b",
            after,
            re.IGNORECASE,
        ):
            # "DESI BAO with DR3" still refers to DESI; the generic phrase
            # separator below protects "DESI with Planck PR4" from assigning
            # Planck's release to DESI.
            found["release"].add(f"{prefix.upper()}{value.upper()}")
        before = separator.split(before)[-1]
        after = separator.split(after)[0]
        markers = _dataset_release_markers(before + family + after)
        markers["release"].discard("Legacy")
        markers["release"].discard("Plus")
        found["release"].update(markers["release"])
        if re.search(r"\blegacy[\s_-]*$", before, re.IGNORECASE) or re.match(
            r"^[\s_-]*legacy\b", after, re.IGNORECASE
        ):
            found["release"].add("Legacy")
        if family in COSMOLOGY_PLUS_RELEASE_FAMILIES and (
            after.startswith("+")
            or re.match(r"^[\s_-]*plus\b", after, re.IGNORECASE)
            or re.search(r"\bplus[\s_-]*$", before, re.IGNORECASE)
        ):
            found["release"].add("Plus")

        # Bare years and 3-4 digit survey-scale identifiers are meaningful only
        # when directly attached to the family name (Planck 2018, KiDS-1000).
        # A wider phrase can contain a paper year, sample count, sky area, or
        # redshift that must never be reinterpreted as a requested release.
        left_identifier = re.search(
            r"\b((?:19|20)[0-9]{2}|[0-9]{3,4})[\s_-]*$", before
        )
        right_identifier = re.match(
            r"^[\s_-]*((?:19|20)[0-9]{2}|[0-9]{3,4})\b", after
        )
        for identifier_match in (left_identifier, right_identifier):
            if identifier_match is None:
                continue
            identifier = identifier_match.group(1)
            category = (
                "year" if re.fullmatch(r"(?:19|20)[0-9]{2}", identifier) else "scale"
            )
            found[category].add(identifier)
    return found


def _cosmology_dataset_substitution_disclosures(
    tool_results: list[dict], user_prompt: str
) -> list[str]:
    """Describe explicit requested-release markers absent from the selection.

    Routing may map a survey family to its default registered product.  That is
    useful for generic requests, but it must not silently relabel an explicitly
    named release.  The comparison is registry-driven and works for DR/Y/PR,
    year-labelled, ``Legacy``/``Plus``, and survey-scale releases.
    """
    registry_selected: dict[str, dict[str, Any]] = {}
    used: dict[str, dict[str, Any]] = {}
    not_run: dict[str, dict[str, Any]] = {}
    for entry in tool_results or []:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        for item in result.get("datasets") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key:
                registry_selected[key] = item
        for item in result.get("datasets_used") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key:
                used[key] = item
        for item in result.get("datasets_not_run") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if key:
                not_run[key] = item

    if used:
        # Once an execution tool reports datasets_used, those records are the
        # only legitimate identity source for numerical results.  Earlier
        # registry candidates remain useful solely to explain what was not run.
        selected = dict(used)
        registry_not_used = {
            key: item for key, item in registry_selected.items() if key not in used
        }
    else:
        selected = {
            key: item
            for key, item in registry_selected.items()
            if key not in not_run
        }
        registry_not_used = {}

    selected_by_family: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, item in selected.items():
        selected_by_family.setdefault(_dataset_family_token(item), []).append((key, item))
    not_run_by_family: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, item in not_run.items():
        not_run_by_family.setdefault(_dataset_family_token(item), []).append((key, item))
    registry_not_used_by_family: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, item in registry_not_used.items():
        registry_not_used_by_family.setdefault(_dataset_family_token(item), []).append(
            (key, item)
        )

    def marker_union(
        items: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, set[str]]:
        combined = {"release": set(), "year": set(), "scale": set()}
        for key, item in items:
            item_markers = _dataset_release_markers(
                " ".join(
                    str(item.get(field) or "")
                    for field in ("key", "display_name", "version")
                )
            )
            for category in combined:
                combined[category].update(item_markers[category])
        return combined

    disclosures: list[str] = []
    for family, family_items in selected_by_family.items():
        requested = _markers_near_dataset_family(user_prompt, family)
        if not any(requested.values()):
            continue
        selected_markers = marker_union(family_items)
        missing_by_category = {
            "release": requested["release"] - selected_markers["release"],
            "year": set(),
            "scale": set(),
        }
        # Compare a year/scale only when the registered product itself uses
        # that kind of identifier; this filters nearby paper years.
        if selected_markers["year"]:
            missing_by_category["year"].update(
                requested["year"] - selected_markers["year"]
            )
        if selected_markers["scale"]:
            missing_by_category["scale"].update(
                requested["scale"] - selected_markers["scale"]
            )
        missing = set().union(*missing_by_category.values())
        if not missing:
            continue
        not_run_markers = marker_union(not_run_by_family.get(family, []))
        registry_not_used_markers = marker_union(
            registry_not_used_by_family.get(family, [])
        )
        requested_release_was_not_run = any(
            missing_by_category[category] & not_run_markers[category]
            for category in missing_by_category
        )
        requested_release_was_registry_only = any(
            missing_by_category[category] & registry_not_used_markers[category]
            for category in missing_by_category
        )
        selected_labels = [
            f"{str(item.get('display_name') or key).strip()} (`{key}`)"
            for key, item in family_items
        ]
        display_text = ", ".join(selected_labels)
        marker_text = ", ".join(sorted(missing, key=str.lower))
        if requested_release_was_not_run:
            availability_text = (
                "the requested release was listed in `datasets_not_run` but was not "
                "executed in this turn"
            )
        elif requested_release_was_registry_only:
            availability_text = (
                "the requested release appeared in the registry selection but was not "
                "executed in this turn"
            )
        else:
            availability_text = (
                "that requested release is not registered under those identifiers "
                "for this turn"
            )
        disclosures.append(
            "Dataset substitution disclosure: the prompt requested or named "
            f"the {family or 'dataset'} release identifier(s) {marker_text}, but "
            f"{availability_text}. The selected registered product(s) instead were "
            f"{display_text}; any configuration or result belongs only to those "
            "selected products, not to the requested release."
        )
    return disclosures


def _cosmology_tool_grounded_summary(
    tool_results: list[dict], user_prompt: str = ""
) -> str | None:
    """Deterministic cosmology summary when the LLM returns empty prose."""
    registry: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    chain: dict[str, Any] | None = None
    ap_test: dict[str, Any] | None = None
    for entry in tool_results or []:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        tool = entry.get("tool")
        if tool == "list_cosmology_datasets":
            registry = result
        elif tool in {"build_cosmology_likelihood", "build_cosmology_robustness_matrix"}:
            config = result
        elif tool in {
            "run_cosmology_likelihood_chain",
            "run_cosmology_robustness_matrix",
            "run_dark_energy_evidence_matrix",
            "run_cmb_rotation_likelihood",
        }:
            chain = result
        elif tool == "assess_bao_bin_anomaly":
            ap_test = result

    if not any((registry, config, chain, ap_test)):
        return None

    dataset_names: list[str] = []
    data_product_notes: list[str] = []
    citation_refs: list[str] = []
    if isinstance(registry, dict):
        for item in registry.get("datasets") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("display_name") or item.get("key") or "").strip()
            if name:
                dataset_names.append(name)
            products = item.get("data_products")
            if isinstance(products, list) and products:
                data_product_notes.append(f"{name}: {len(products)} machine-readable product(s)")
            # Carry the registry's own citations into the summary so it is
            # provenance-complete (these bibcodes come straight from this turn's
            # tool_results, so they are already in the citation pool).
            for cite in item.get("citations") or []:
                if not isinstance(cite, dict):
                    continue
                ref = str(cite.get("bibcode") or cite.get("arxiv") or cite.get("doi") or "").strip()
                if ref and ref not in citation_refs:
                    citation_refs.append(ref)

    lines = ["Tool-grounded cosmology summary:"]
    if dataset_names:
        lines.append(f"- Registry selection: {', '.join(dataset_names)}.")
    for disclosure in _cosmology_dataset_substitution_disclosures(
        tool_results, user_prompt
    ):
        lines.append(f"- {disclosure}")
    if data_product_notes:
        lines.append(f"- Data products: {'; '.join(data_product_notes)}.")
    if citation_refs:
        lines.append(f"- Source citations: {', '.join(citation_refs[:8])}.")

    if isinstance(config, dict):
        model = config.get("model") or config.get("model_label") or "not reported"
        config_hash = str(config.get("config_hash") or "")[:12] or "not reported"
        lines.append(
            f"- Likelihood configuration: model={model}, config_hash={config_hash}. "
            "This is configuration metadata, not a posterior result."
        )

    if isinstance(chain, dict):
        ready = chain.get("publication_ready") is True and chain.get("__do_not_claim__") is not True
        used = [
            str(item.get("display_name") or item.get("key") or "").strip()
            for item in (chain.get("datasets_used") or [])
            if isinstance(item, dict)
        ]
        not_run = [
            str(item.get("display_name") or item.get("key") or "").strip()
            for item in (chain.get("datasets_not_run") or [])
            if isinstance(item, dict)
        ]
        if ready:
            params = chain.get("parameters")
            param_parts: list[str] = []
            if isinstance(params, dict):
                for name in ("H0", "omegam", "sigma8", "S8", "beta_deg"):
                    item = params.get(name)
                    if not isinstance(item, dict):
                        continue
                    median = item.get("median")
                    hdi = item.get("hdi_94")
                    text = f"{name}={_fmt_tool_number(median)}"
                    if isinstance(hdi, list) and len(hdi) >= 2:
                        text += f" (94% HDI {_fmt_tool_number(hdi[0])}-{_fmt_tool_number(hdi[1])})"
                    param_parts.append(text)
            lines.append(
                "- Posterior status: publication-ready for the compressed-likelihood "
                "preliminary runner."
            )
            if used:
                lines.append(f"- Numerically included datasets: {', '.join(used)}.")
            if not_run:
                lines.append(
                    f"- Not included in the numerical posterior: {', '.join(not_run)}; "
                    "these still require external likelihood execution."
                )
            if param_parts:
                lines.append("- Compressed preliminary parameters: " + "; ".join(param_parts) + ".")
            lines.append(
                "Scope note: these numbers are compressed-likelihood preliminary results, "
                "not a full external Cobaya/CosmoSIS likelihood reproduction."
            )
            lines.append(
                "Do not describe these datasets as ready for full likelihood analyses "
                "unless a full external Cobaya/CosmoSIS chain has actually run."
            )
        else:
            reason = "no publication-ready full-likelihood posterior was produced"
            warnings = chain.get("warnings")
            if isinstance(warnings, list) and warnings:
                reason = str(warnings[0])
            lines.append(f"- Posterior status: not publication-ready ({reason}).")
            if not_run:
                lines.append(
                    f"- Dataset(s) requiring external likelihood execution: {', '.join(not_run)}."
                )
            lines.append(
                "Therefore this turn can document data products and build configs, "
                "but it cannot support H0/Omega_m/S8/tension or dark-energy posterior claims."
            )

    # Alcock-Paczynski geometric test (assess_bao_bin_anomaly): surface the
    # tool-grounded Ωm result deterministically so a research-mode AP turn shows
    # the actual finding instead of an empty/blocked card (the model's draft is
    # discarded in research mode, so this summary must carry the result itself).
    if isinstance(ap_test, dict) and isinstance(ap_test.get("omega_m_best"), (int, float)):
        om = float(ap_test["omega_m_best"])
        lo = ap_test.get("omega_m_1sigma_low")
        hi = ap_test.get("omega_m_1sigma_high")
        chi2 = ap_test.get("chi2_min")
        ndof = ap_test.get("n_dof")
        ap_line = f"- Alcock-Paczynski geometric test (DESI DR1 BAO): best-fit Ωm = {_fmt_tool_number(om)}"
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            ap_line += f" (1σ {_fmt_tool_number(lo)}–{_fmt_tool_number(hi)})"
        if isinstance(chi2, (int, float)) and isinstance(ndof, (int, float)) and ndof:
            ap_line += f"; χ² = {_fmt_tool_number(chi2)} ({_fmt_tool_number(chi2 / ndof)} per dof)"
        ap_line += "."
        lines.append(ap_line)
        lines.append(
            "- The AP test constrains Ωm through the DM/DH ratio, which is independent "
            "of H0 and the sound horizon r_d (both cancel in the ratio). Method: "
            "Alcock & Paczynski 1979."
        )

    # Deterministic out-of-coverage guard: if the prompt asks for a quantity AT a
    # redshift beyond every included dataset's z_coverage, append an extrapolation
    # caveat that does not depend on the model's wording. References only the
    # sourced coverage bound (never echoes the requested z as if it were measured),
    # so it cannot itself trip the numeric claim-validator.
    requested_z = _cosmology_requested_redshift(user_prompt)
    coverage_zmax = _cosmology_max_z_coverage(tool_results)
    if requested_z is not None and coverage_zmax is not None and requested_z > coverage_zmax + 1e-9:
        lines.append(
            f"Out-of-coverage extrapolation: the requested redshift lies beyond the included "
            f"data's coverage (z ≤ {coverage_zmax:g}). Any value reported at that redshift is a "
            f"ΛCDM model extrapolation, not a measurement or data constraint from these datasets "
            f"— the (1+z) evolution is model-dependent, not directly observed."
        )

    return "\n".join(lines) if len(lines) > 1 else None


def _enforce_cosmology_dataset_identity(
    reply: str,
    tool_results: list[dict],
    user_prompt: str,
) -> tuple[str, bool]:
    """Replace model prose when an executed release differs from the request.

    Appending a correction is not sufficient: the draft may explicitly claim
    that two differently named releases are equivalent.  In that case the
    deterministic tool-grounded summary becomes the entire public reply, so the
    contradicted narrative cannot survive above or below the correction.
    """
    disclosures = _cosmology_dataset_substitution_disclosures(
        tool_results, user_prompt
    )
    if not disclosures:
        return reply, False
    safe_summary = _cosmology_tool_grounded_summary(tool_results, user_prompt)
    if safe_summary:
        return safe_summary, True
    return (
        "Dataset identity correction (tool-grounded):\n- "
        + "\n- ".join(disclosures),
        True,
    )


def _successful_research_report_export(tool_results: list[dict]) -> bool:
    """Return true only when a report artifact actually completed."""
    for item in tool_results:
        if not isinstance(item, dict) or item.get("tool") != "export_research_report":
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("success") is not True:
            continue
        statuses = {
            str(result.get(key) or "").strip().upper()
            for key in ("__tool_status__", "analysis_status", "status")
        }
        if statuses & {"FAILED", "ERROR", "BLOCKED", "CANCELLED", "TIMEOUT"}:
            continue
        return True
    return False


def _research_tool_grounded_summary(tool_results: list[dict]) -> str | None:
    """Deterministic Research Mode summary that avoids unsupported numbers."""
    plan: dict[str, Any] | None = None
    matrix: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    fact_check: dict[str, Any] | None = None
    for entry in tool_results or []:
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        if entry.get("tool") == "plan_research_program":
            plan = result.get("research_plan") if isinstance(result.get("research_plan"), dict) else None
        elif entry.get("tool") == "run_research_matrix":
            matrix = result
        elif entry.get("tool") == "build_evidence_graph":
            evidence = result.get("evidence_graph") if isinstance(result.get("evidence_graph"), dict) else None
        elif entry.get("tool") == "verify_research_facts":
            fact_check = result.get("fact_check_report") if isinstance(result.get("fact_check_report"), dict) else result
    if not plan and not matrix:
        return None

    ready_cells: list[str] = []
    blocked_cells: list[str] = []
    ready_cell_summaries: list[str] = []
    executed_not_ready_summaries: list[str] = []
    config_only_summaries: list[str] = []
    if isinstance(matrix, dict):
        # Aggregate research matrices are diagnostic containers.  When the
        # parent is tainted, a timeout/partial-summary path must not launder a
        # child cell's numbers around the normal numeric-claim validator.
        matrix_numbers_claimable = matrix.get("__do_not_claim__") is not True
        for cell in matrix.get("matrix") or []:
            if not isinstance(cell, dict):
                continue
            label = str(cell.get("label") or "unnamed cell")
            execution_level = str(cell.get("execution_level") or "").strip()
            if cell.get("publication_ready") is True:
                ready_cells.append(label)
                result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
                params = (
                    result.get("parameters")
                    if matrix_numbers_claimable
                    and isinstance(result, dict)
                    and isinstance(result.get("parameters"), dict)
                    else {}
                )
                diagnostics = (
                    result.get("chain_diagnostics")
                    if matrix_numbers_claimable
                    and isinstance(result, dict)
                    and isinstance(result.get("chain_diagnostics"), dict)
                    else {}
                )
                parts = [label]
                h0 = params.get("H0") if isinstance(params, dict) and isinstance(params.get("H0"), dict) else None
                if isinstance(h0, dict) and h0.get("median") is not None:
                    parts.append(f"H0 median {_fmt_tool_number(h0.get('median'))}")
                omegam = (
                    params.get("omegam")
                    if isinstance(params, dict) and isinstance(params.get("omegam"), dict)
                    else params.get("Omega_m")
                    if isinstance(params, dict) and isinstance(params.get("Omega_m"), dict)
                    else None
                )
                if isinstance(omegam, dict) and omegam.get("median") is not None:
                    # Keep the parameter name in code formatting so Markdown
                    # does not consume the underscore and render "Omegam".
                    parts.append(f"`Omega_m` median {_fmt_tool_number(omegam.get('median'))}")
                s8 = params.get("S8") if isinstance(params, dict) and isinstance(params.get("S8"), dict) else None
                if isinstance(s8, dict) and s8.get("median") is not None:
                    parts.append(f"S8 median {_fmt_tool_number(s8.get('median'))}")
                if isinstance(diagnostics, dict):
                    ess = diagnostics.get("proposal_ess")
                    if ess is None:
                        ess = diagnostics.get("ess_bulk")
                    rhat = diagnostics.get("rhat")
                    if ess is not None:
                        parts.append(f"ESS {_fmt_tool_number(ess)}")
                    if rhat is not None:
                        parts.append(f"Rhat {_fmt_tool_number(rhat)}")
                ready_cell_summaries.append(" · ".join(parts))
            else:
                blocked_cells.append(label)
                result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
                diagnostics = (
                    result.get("chain_diagnostics")
                    if matrix_numbers_claimable
                    and isinstance(result, dict)
                    and isinstance(result.get("chain_diagnostics"), dict)
                    else {}
                )
                warnings = [str(w) for w in cell.get("warnings") or [] if w]
                datasets_not_run = (
                    result.get("datasets_not_run")
                    if isinstance(result, dict) and isinstance(result.get("datasets_not_run"), list)
                    else []
                )
                if execution_level == "executed_not_ready" or diagnostics:
                    detail_parts = [label]
                    ess = diagnostics.get("proposal_ess")
                    if ess is None:
                        ess = diagnostics.get("ess_bulk")
                    rhat = diagnostics.get("rhat")
                    threshold = (
                        diagnostics.get("thresholds", {}).get("ess_min")
                        if isinstance(diagnostics.get("thresholds"), dict)
                        else None
                    )
                    if ess is not None:
                        if threshold is not None:
                            ess_number = _finite_number(ess)
                            threshold_number = _finite_number(threshold)
                            if ess_number is not None and threshold_number is not None:
                                comparison = (
                                    "below" if ess_number < threshold_number else "meets"
                                )
                                detail_parts.append(
                                    f"ESS {_fmt_tool_number(ess)} {comparison} threshold "
                                    f"{_fmt_tool_number(threshold)}"
                                )
                            else:
                                detail_parts.append(
                                    f"ESS {_fmt_tool_number(ess)}; publication threshold "
                                    f"{_fmt_tool_number(threshold)}"
                                )
                        else:
                            detail_parts.append(f"ESS {_fmt_tool_number(ess)}")
                    elif diagnostics.get("ess_source") == "autocorr_failed":
                        # 2026-06-12: the ess_unknown path reports ess=None —
                        # without this branch the cell collapses to a bare
                        # "not claimable" with no reason at all.
                        detail_parts.append(
                            "ESS unavailable (autocorrelation failed; convergence unverified)"
                        )
                    if rhat is not None:
                        detail_parts.append(f"Rhat {_fmt_tool_number(rhat)}")
                    publication_gate = (
                        result.get("publication_gate")
                        if isinstance(result.get("publication_gate"), dict)
                        else {}
                    )
                    gate_reasons = [
                        str(reason)
                        for reason in publication_gate.get("reasons") or []
                        if reason
                    ]
                    publication_blocker = str(
                        diagnostics.get("publication_blocker") or ""
                    ).strip()
                    if gate_reasons:
                        detail_parts.append(
                            "publication gate: "
                            + ", ".join(f"`{reason}`" for reason in gate_reasons[:4])
                            + (
                                ""
                                if len(gate_reasons) <= 4
                                else f", +{len(gate_reasons) - 4} more"
                            )
                        )
                    elif publication_blocker:
                        detail_parts.append(publication_blocker)
                    elif warnings:
                        detail_parts.append(warnings[0])
                    detail_parts.append("not claimable")
                    executed_not_ready_summaries.append(" · ".join(detail_parts))
                elif execution_level == "config_only" or datasets_not_run:
                    reason = ""
                    if datasets_not_run:
                        gap_names = [_format_dataset_gap_item(x) for x in datasets_not_run[:3]]
                        suffix = f"; +{len(datasets_not_run) - 3} more" if len(datasets_not_run) > 3 else ""
                        reason = "missing executable dataset(s): " + ", ".join(gap_names) + suffix
                    elif warnings:
                        reason = warnings[0]
                    else:
                        reason = "configuration only, no posterior run"
                    config_only_summaries.append(f"{label} · {reason}")
                else:
                    reason = warnings[0] if warnings else "not runnable in this turn"
                    config_only_summaries.append(f"{label} · {reason}")

    gaps = [
        str(item)
        for item in (plan or {}).get("blocking_gaps", [])
        if item
    ]
    claimable = []
    if isinstance(evidence, dict):
        claimable = [str(item) for item in evidence.get("claimable_parameters") or []]

    lines = ["Research-mode summary:", ""]
    if gaps:
        lines.extend([
            "Model-level limitation (read first)",
            "- This paper-style question cannot be fully tested at its intended "
            "model level because the required runner(s) are missing: "
            + "; ".join(gaps[:3])
            + ("." if len(gaps) <= 3 else f"; +{len(gaps) - 3} more."),
            (
                "- The executable baseline below is compressed-likelihood preliminary only."
                if ready_cells
                else "- No publication-ready direct likelihood result completed this turn."
            ),
            "",
        ])
    lines.extend([
        "What can be tested now",
        "- The research plan and matrix have been built from registered datasets and controlled runners.",
    ])
    readiness = (plan or {}).get("partial_pass_readiness")
    if isinstance(readiness, dict):
        status = (
            "meets B-level partial-pass readiness"
            if readiness.get("meets_partial_pass") is True
            else "does not yet meet B-level partial-pass readiness"
        )
        coverage = str(readiness.get("coverage_status") or "unknown")
        note = str(readiness.get("important_note") or "").strip()
        lines.append(
            f"- Blind-test target check: {status} "
            f"(coverage: {coverage}; score floor: {readiness.get('score_floor', 'unknown')})."
        )
        if note:
            lines.append(f"- {note}")
    if ready_cells:
        lines.append(
            "- Numerically executed compressed-likelihood preliminary cells: "
            + ", ".join(ready_cells[:6])
            + ("." if len(ready_cells) <= 6 else f", +{len(ready_cells) - 6} more.")
        )
    else:
        lines.append("- No publication-ready direct likelihood result completed this turn.")

    lines.extend(["", "Executed analyses"])
    if plan:
        probes = ", ".join(str(item) for item in plan.get("required_probes", []) if item)
        models = ", ".join(str(item) for item in plan.get("model_families", []) if item)
        if probes:
            lines.append(f"- Required probes identified: {probes}.")
        if models:
            lines.append(f"- Model families planned: {models}.")
    if isinstance(matrix, dict):
        lines.append(
            f"- Research matrix cells evaluated: {matrix.get('ready_cells', 0)} ready out of {matrix.get('matrix_size', 0)}."
        )

    lines.extend(["", "Preliminary findings"])
    if ready_cell_summaries:
        lines.append(
            "- Ready compressed-likelihood preliminary cells: "
            + "; ".join(ready_cell_summaries[:5])
            + ("." if len(ready_cell_summaries) <= 5 else f"; +{len(ready_cell_summaries) - 5} more.")
        )
        lines.append(
            "- These are compressed-likelihood preliminary numbers, not full external Cobaya/CosmoSIS likelihood results."
        )
    elif ready_cells:
        lines.append("- The ready cells can support compressed-likelihood preliminary interpretation.")
    else:
        lines.append("- This turn supports dataset/method availability only, not posterior claims.")

    lines.extend(["", "Robustness"])
    if executed_not_ready_summaries:
        lines.append(
            "- Executed but not claimable (`execution_level=executed_not_ready`) "
            "because publication requirements were not met: "
            + "; ".join(executed_not_ready_summaries[:5])
            + ("." if len(executed_not_ready_summaries) <= 5 else f"; +{len(executed_not_ready_summaries) - 5} more.")
        )
    if config_only_summaries:
        lines.append(
            "- Config-only or not-runnable branches: "
            + "; ".join(config_only_summaries[:5])
            + ("." if len(config_only_summaries) <= 5 else f"; +{len(config_only_summaries) - 5} more.")
        )
    if not executed_not_ready_summaries and not config_only_summaries:
        lines.append("- All planned matrix cells that were generated are runnable in the current compressed layer.")

    # 2026-06-12: the phase-1 gate now runs flat-DE extension cells, so
    # model_comparisons reaches the summary. Render verdicts ONLY from
    # comparison_valid=true entries; invalid ones are counted, not quoted.
    matrix_model_comparisons = (
        matrix.get("model_comparisons")
        if isinstance(matrix, dict) and isinstance(matrix.get("model_comparisons"), list)
        else []
    )
    valid_comparison_lines: list[str] = []
    invalid_comparison_count = 0
    for comparison in matrix_model_comparisons:
        if not isinstance(comparison, dict):
            continue
        comparison_tiers = {
            comparison.get("baseline_chain_tier"),
            comparison.get("extended_chain_tier"),
        }
        # Defense in depth: the producer never emits comparison_valid=true
        # with a blocked/unknown tier, but the renderer re-checks instead of
        # trusting the pair — a forged or stale entry must not render a
        # verdict with a soft caveat.
        renderable_tiers = comparison_tiers <= {None, "publication", "exploratory"}
        if comparison.get("comparison_valid") is True and renderable_tiers:
            datasets_txt = "+".join(str(k) for k in comparison.get("dataset_keys") or [])
            comparison_parts = [
                f"{comparison.get('extended_model')} vs {comparison.get('baseline_model')}"
                + (f" on {datasets_txt}" if datasets_txt else "")
            ]
            if comparison.get("delta_aic") is not None:
                comparison_parts.append(f"dAIC {_fmt_tool_number(comparison.get('delta_aic'))}")
            comparison_parts.append(f"preferred: {comparison.get('preferred')}")
            tier_flags = [
                f"{side} fit {comparison.get(f'{side}_chain_tier')}-tier"
                for side in ("baseline", "extended")
                if comparison.get(f"{side}_chain_tier") not in (None, "publication")
            ]
            if tier_flags:
                comparison_parts.append(
                    ", ".join(tier_flags) + " — compressed preliminary, not a published-anchor result"
                )
            valid_comparison_lines.append(" · ".join(comparison_parts))
        else:
            invalid_comparison_count += 1
    if valid_comparison_lines or invalid_comparison_count:
        lines.extend(["", "Model comparison"])
        if valid_comparison_lines:
            lines.append(
                "- " + "; ".join(valid_comparison_lines[:4])
                + ("." if len(valid_comparison_lines) <= 4 else f"; +{len(valid_comparison_lines) - 4} more.")
            )
        if invalid_comparison_count:
            lines.append(
                f"- {invalid_comparison_count} comparison(s) withheld (comparison_valid=false: "
                "a representation mismatch between the paired fits, or at least one "
                "blocked, unvetted, or convergence-unverified fit) — their delta values "
                "are not model-preference evidence."
            )

    lines.extend(["", "What drives the result"])
    if claimable:
        lines.append(
            "- Claimable parameters in the evidence graph: "
            + ", ".join(claimable[:8])
            + ("." if len(claimable) <= 8 else f", +{len(claimable) - 8} more.")
        )
    else:
        lines.append("- No numeric claim should be made unless it appears in a publication-ready tool card.")

    if isinstance(fact_check, dict):
        lines.extend(["", "Fact verification"])
        _fc_status = str(fact_check.get("status", "unknown"))
        _rewrites = fact_check.get("safe_rewrites")
        _has_rewrites = isinstance(_rewrites, list) and bool(_rewrites)
        _verified = fact_check.get("verified_claim_count", 0)
        _unsupported = fact_check.get("unsupported_claim_count", 0)
        if _fc_status == "blocked" and _has_rewrites:
            lines.append(
                f"- Unsafe draft claims were detected and rewritten out of this "
                f"final summary — the visible answer is safe "
                f"({_verified} verified, {_unsupported} removed/rewritten)."
            )
        elif _fc_status == "blocked":
            lines.append(
                f"- Fact-check blocked the draft and no safe rewrite was available "
                f"({_unsupported} unsupported/contradicted) — treat the tool cards "
                f"as the source of truth."
            )
        else:
            lines.append(
                f"- Fact-check {_fc_status}: {_verified} verified, "
                f"{_unsupported} unsupported/contradicted."
            )

    lines.extend(["", "What is not yet supported"])
    if gaps:
        lines.extend(f"- {gap}" for gap in gaps[:5])
    elif executed_not_ready_summaries or config_only_summaries:
        if executed_not_ready_summaries:
            lines.append("- Some executed cells need stronger diagnostics before their values can be treated as results.")
        if config_only_summaries:
            lines.append("- Some requested cells still need an executable likelihood runner or registered covariance.")
    else:
        lines.append("- Full external Cobaya/CosmoSIS reproduction is still outside the compressed preliminary layer.")
    gap_matrix = (plan or {}).get("capability_gap_matrix")
    if isinstance(gap_matrix, list):
        missing_components = [
            row
            for row in gap_matrix
            if isinstance(row, dict)
            and str(row.get("status")) in {
                "missing",
                "registered_config_only",
                "config_only",
                "literature_context",
                "context_only",
                "partial",
            }
        ]
        if missing_components:
            parts = []
            for row in missing_components[:5]:
                component = str(row.get("component") or "unknown component")
                status = str(row.get("status") or "unknown")
                details = str(row.get("details") or "").strip()
                parts.append(f"{component} ({status}: {details})")
            lines.append("- Capability gap matrix: " + "; ".join(parts) + ".")

    lines.extend(["", "Next experiment"])
    lines.append(
        "- Run the missing external likelihoods or add the corresponding executable runner before making stronger research claims."
    )
    return "\n".join(lines)


def _tool_grounded_summary(
    tool_results: list[dict],
    user_prompt: str = "",
) -> str | None:
    """Return the safest deterministic summary available from same-turn tools."""

    return (
        _research_tool_grounded_summary(tool_results)
        or _line_lfr_tool_grounded_summary(tool_results)
        or _statistics_tool_grounded_summary(tool_results)
        or _cosmology_tool_grounded_summary(tool_results, user_prompt)
    )
