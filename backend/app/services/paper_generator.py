"""Automatic paper draft generation from saved analysis sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import version

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.citations import _get_bibtex_sync
from app.common.regex import BIBCODE_RE as _BIBCODE_RE
from app.models.schemas import ChatSession
from app.services.event_collector import track_event

_ACKNOWLEDGMENTS = {
    "gaia": "This work has made use of data from the European Space Agency (ESA) mission Gaia.",
    "sdss": "Funding for the Sloan Digital Sky Survey (SDSS) has been provided by the Alfred P. Sloan Foundation and participating institutions.",
    "simbad": "This research has made use of the SIMBAD database, operated at CDS, Strasbourg, France.",
    "vizier": "This research has made use of the VizieR catalogue access tool, CDS, Strasbourg, France.",
    "mast": "This work made use of observations from the Mikulski Archive for Space Telescopes (MAST).",
    "jwst": "This work made use of archival observations from the James Webb Space Telescope via MAST.",
    "ned": "This research has made use of the NASA/IPAC Extragalactic Database (NED).",
    "chandra": "This work made use of data obtained from the Chandra Data Archive.",
}


@dataclass
class SessionArtifacts:
    session: ChatSession | None
    user_prompts: list[str]
    assistant_text: list[str]
    search_calls: list[dict]
    adql_calls: list[dict]
    python_calls: list[dict]
    pipeline_calls: list[dict]
    figure_refs: list[dict]
    bibcodes: list[str]
    tool_results: list[dict]
    provenance_datasets: list[dict]
    provenance_runs: list[dict]
    cosmology_runs: list[dict]
    source_urls: list[str]


def _escape_latex(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def _format_table_cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        if value != value:
            return "NaN"
        if abs(value) >= 1_000 or (0 < abs(value) < 1e-3):
            return f"{value:.3e}"
        return f"{value:.5g}"
    return str(value)


def _extract_actions(messages: list[dict]) -> SessionArtifacts:
    user_prompts: list[str] = []
    assistant_text: list[str] = []
    search_calls: list[dict] = []
    adql_calls: list[dict] = []
    python_calls: list[dict] = []
    pipeline_calls: list[dict] = []
    figure_refs: list[dict] = []
    bibcodes: list[str] = []
    tool_results: list[dict] = []
    provenance_datasets: list[dict] = []
    provenance_runs: list[dict] = []
    cosmology_runs: list[dict] = []
    source_urls: list[str] = []

    for message in messages:
        role = message.get("role", "")
        content = str(message.get("content", "") or "")
        if role == "user" and content:
            user_prompts.append(content)
        elif role == "assistant" and content:
            assistant_text.append(content)
            bibcodes.extend(_BIBCODE_RE.findall(content))

        for action in message.get("actions") or []:
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("action", ""))
            if action_name in {"search", "search_objects"}:
                search_calls.append(action)
            elif action_name in {"adql", "run_adql"}:
                adql_calls.append(action)
            elif action_name == "run_python":
                python_calls.append(action)
            elif action_name in {"generate_pipeline", "run_pipeline", "modify_pipeline"}:
                pipeline_calls.append(action)
            elif action_name == "plot":
                figure_refs.append({
                    "figure_number": len(figure_refs) + 1,
                    "caption": f"{action.get('chart_type', 'Plot')} generated during the session.",
                    "session_figure_ref": f"plot_{len(figure_refs) + 1}",
                })

            tool_result = action.get("tool_result")
            for result_dict in _iter_tool_result_dicts(tool_result):
                tool_results.append(result_dict)
                provenance_datasets.extend(_extract_provenance_datasets(result_dict))
                run = _extract_reproducibility_run(result_dict)
                if run:
                    provenance_runs.append({
                        "action": action_name,
                        **run,
                    })
                source_urls.extend(_extract_source_urls(result_dict))
                cosmology = _extract_cosmology_provenance(result_dict)
                if cosmology:
                    cosmology_runs.append(cosmology)
                bibcodes.extend(_extract_bibcodes_from_provenance(result_dict))
            if isinstance(tool_result, dict) and tool_result.get("bibcode"):
                bibcodes.append(str(tool_result["bibcode"]))
            elif isinstance(tool_result, list):
                for item in tool_result:
                    if isinstance(item, dict) and item.get("bibcode"):
                        bibcodes.append(str(item["bibcode"]))

    return SessionArtifacts(
        session=None,  # patched by caller
        user_prompts=user_prompts,
        assistant_text=assistant_text,
        search_calls=search_calls,
        adql_calls=adql_calls,
        python_calls=python_calls,
        pipeline_calls=pipeline_calls,
        figure_refs=figure_refs,
        bibcodes=list(dict.fromkeys(bibcodes)),
        tool_results=tool_results,
        provenance_datasets=_dedupe_datasets(provenance_datasets),
        provenance_runs=_dedupe_runs(provenance_runs),
        cosmology_runs=_dedupe_cosmology_runs(cosmology_runs),
        source_urls=list(dict.fromkeys(source_urls)),
    )


def _iter_tool_result_dicts(tool_result: object):
    if isinstance(tool_result, dict):
        yield tool_result
    elif isinstance(tool_result, list):
        for item in tool_result:
            if isinstance(item, dict):
                yield item


def _extract_provenance_datasets(tool_result: dict) -> list[dict]:
    datasets: list[dict] = []
    provenance = tool_result.get("provenance")
    if isinstance(provenance, dict):
        raw = provenance.get("datasets")
        if isinstance(raw, list):
            datasets.extend(item for item in raw if isinstance(item, dict))
    raw_top = tool_result.get("datasets")
    if isinstance(raw_top, list):
        datasets.extend(item for item in raw_top if isinstance(item, dict))
    return datasets


def _extract_reproducibility_run(tool_result: dict) -> dict | None:
    provenance = tool_result.get("provenance")
    repro = None
    if isinstance(provenance, dict) and isinstance(provenance.get("reproducibility"), dict):
        repro = provenance["reproducibility"]
    elif isinstance(tool_result.get("reproducibility"), dict):
        repro = tool_result["reproducibility"]
    if not isinstance(repro, dict):
        return None
    keys = ("run_id", "query_hash", "archive_version", "tool_version")
    run = {key: str(repro[key]) for key in keys if repro.get(key)}
    return run or None


def _extract_source_urls(tool_result: dict) -> list[str]:
    urls: list[str] = []
    for value in tool_result.get("source_urls") or []:
        if value:
            urls.append(str(value))
    for dataset in _extract_provenance_datasets(tool_result):
        for value in dataset.get("source_urls") or []:
            if value:
                urls.append(str(value))
        for key in ("reference_url", "credits_page_url"):
            if dataset.get(key):
                urls.append(str(dataset[key]))
    return list(dict.fromkeys(urls))


def _extract_bibcodes_from_provenance(tool_result: dict) -> list[str]:
    bibcodes: list[str] = []
    for dataset in _extract_provenance_datasets(tool_result):
        article = dataset.get("article")
        if article:
            bibcodes.extend(_BIBCODE_RE.findall(str(article)))
    provenance = tool_result.get("provenance")
    field_bibcodes = provenance.get("field_bibcodes") if isinstance(provenance, dict) else tool_result.get("field_bibcodes")
    if isinstance(field_bibcodes, dict):
        columns = field_bibcodes.get("columns") or {}
        if isinstance(columns, dict):
            for values in columns.values():
                if isinstance(values, list):
                    for value in values:
                        bibcodes.extend(_BIBCODE_RE.findall(str(value)))
    return bibcodes


def _extract_cosmology_provenance(tool_result: dict) -> dict | None:
    provenance = tool_result.get("provenance")
    cosmology = provenance.get("cosmology") if isinstance(provenance, dict) else None
    if not isinstance(cosmology, dict):
        return None
    keys = (
        "model",
        "sampler",
        "priors",
        "data_hash",
        "random_seed",
        "package_versions",
        "chain_diagnostics",
        "publication_ready",
    )
    result = {key: cosmology.get(key) for key in keys if key in cosmology}
    return result or None


def _dedupe_datasets(datasets: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict] = []
    for dataset in datasets:
        key = (
            str(dataset.get("ivoid") or "").lower(),
            str(dataset.get("service_key") or dataset.get("service_name") or "").lower(),
            str(dataset.get("archive_version") or "").lower(),
            str(dataset.get("article") or "").lower(),
        )
        if not any(key) or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(dataset))
    return deduped


def _dedupe_cosmology_runs(runs: list[dict]) -> list[dict]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for run in runs:
        key = (
            str(run.get("model") or ""),
            str(run.get("sampler") or ""),
            str(run.get("data_hash") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(run)
    return deduped


def _dedupe_runs(runs: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for run in runs:
        key = (str(run.get("run_id") or ""), str(run.get("query_hash") or ""))
        if not any(key) or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(run))
    return deduped


def _summarize_data_sources(artifacts: SessionArtifacts) -> tuple[list[str], str]:
    used_sources: list[str] = []
    data_lines: list[str] = []
    for action in artifacts.search_calls:
        sources = action.get("sources") or []
        if isinstance(sources, list):
            used_sources.extend(str(source).lower() for source in sources)
        data_lines.append(
            f"Catalog search for {action.get('query', 'target')} using {', '.join(map(str, sources or ['simbad']))}."
        )
    for action in artifacts.adql_calls:
        service = str(action.get("service", "gaia")).lower()
        used_sources.append(service)
        query = str(action.get("query", "")).strip().replace("\n", " ")
        data_lines.append(f"ADQL query on {service}: {query[:180]}")
    for dataset in artifacts.provenance_datasets:
        service = str(dataset.get("service_key") or dataset.get("service_name") or "").lower()
        if service:
            used_sources.append(service)
        archive_version = dataset.get("archive_version")
        article = dataset.get("article")
        parts = [
            str(dataset.get("service_name") or dataset.get("service_key") or "dataset"),
            f"archive_version={archive_version}" if archive_version else "",
            f"article={article}" if article else "",
        ]
        data_lines.append("Dataset provenance: " + ", ".join(part for part in parts if part) + ".")

    deduped_sources = list(dict.fromkeys(source for source in used_sources if source))
    data_text = " ".join(data_lines) or "The session primarily consists of interactive analysis and interpretation."
    return deduped_sources, data_text


def _build_acknowledgments(sources: list[str], datasets: list[dict] | None = None) -> str:
    lines: list[str] = []
    for dataset in datasets or []:
        template = dataset.get("acknowledgement_template")
        if template:
            lines.append(str(template))
            continue
        service_key = str(dataset.get("service_key") or "").lower()
        if service_key in _ACKNOWLEDGMENTS:
            lines.append(_ACKNOWLEDGMENTS[service_key])
    lines.extend(_ACKNOWLEDGMENTS[source] for source in sources if source in _ACKNOWLEDGMENTS)
    if not lines:
        lines.append("This work made use of the Standard Astro research platform for interactive data analysis.")
    return " ".join(dict.fromkeys(lines))


def _build_data_provenance_text(artifacts: SessionArtifacts) -> str:
    lines: list[str] = []
    for run in artifacts.provenance_runs:
        fields = [
            f"tool={run.get('action')}" if run.get("action") else "",
            f"run_id={run.get('run_id')}" if run.get("run_id") else "",
            f"query_hash={run.get('query_hash')}" if run.get("query_hash") else "",
            f"archive_version={run.get('archive_version')}" if run.get("archive_version") else "",
            f"tool_version={run.get('tool_version')}" if run.get("tool_version") else "",
        ]
        lines.append("; ".join(field for field in fields if field))
    if artifacts.source_urls:
        lines.append("Source URLs: " + "; ".join(artifacts.source_urls))
    for run in artifacts.cosmology_runs:
        diagnostics = run.get("chain_diagnostics") if isinstance(run.get("chain_diagnostics"), dict) else {}
        status = diagnostics.get("overall_status") or "unknown"
        ready = run.get("publication_ready")
        lines.append(
            "Cosmology MCMC provenance: "
            f"model={run.get('model')}; sampler={run.get('sampler')}; "
            f"data_hash={run.get('data_hash')}; random_seed={run.get('random_seed')}; "
            f"diagnostics={status}; publication_ready={ready}."
        )
    return "\n".join(lines)


def _format_fit_value(value: object, fmt: str = ".4g") -> str:
    """Best-effort numeric format for the fit diagnostics renderer; returns
    "N/A" on None / non-numeric so the section never displays empty cells."""
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return str(value)
    try:
        return format(float(value), fmt)
    except (TypeError, ValueError):
        return str(value)


def build_fit_line_lfr_diagnostics_section(
    artifacts: SessionArtifacts,
) -> str | None:
    """PART AI #5/#6 — render the most recent fit_line_lfr tool_result as
    a paper-ready Markdown section covering:
      - readiness checks (5 gates: rows / citations / units / method /
        Bayesian sampler)
      - relation_claimability (can_claim + blocking_reasons)
      - Bayesian diagnostics (rhat, ESS, HDI 94%, n_chains, package, ref)
      - lensing_summary (5 buckets including papers_default_lensed)
      - cosmology recompute audit
      - sample composition (n_used, surveys merged, line_id)

    Returns None when no fit_line_lfr result is in this session — the
    caller treats None as "skip this section in the draft".
    """
    fit_results = [
        r for r in artifacts.tool_results
        if isinstance(r, dict) and r.get("tool") == "fit_line_lfr"
        and r.get("success") is True
    ]
    if not fit_results:
        return None
    fit = fit_results[-1]  # 用本 session 最新一次成功 fit

    lines: list[str] = []
    lines.append("## Fit Diagnostics: line luminosity-FWHM relation")
    lines.append("")

    # ── Sample composition ───────────────────────────────────────────
    line_id = fit.get("line_id") or "(unspecified)"
    n_used = fit.get("n_used")
    n_surveys = fit.get("n_surveys_merged")
    contributing = fit.get("contributing_cache_keys") or []
    lines.append(f"**Sample**: {n_used} rows of {line_id} used in fit; "
                 f"{n_surveys} surveys merged")
    if contributing:
        lines.append(f"  ; contributing cache keys: {', '.join(map(str, contributing[:5]))}"
                     + ("..." if len(contributing) > 5 else ""))
    lines.append("")

    # ── Fit equation + units ─────────────────────────────────────────
    lines.append("**Model**:")
    lines.append("")
    lines.append("```")
    lines.append(str(fit.get("model") or ""))
    lines.append("```")
    lines.append("")
    lines.append(f"- intercept_unit: `{fit.get('intercept_unit') or 'N/A'}`")
    lines.append(f"- slope_unit: `{fit.get('slope_unit') or 'N/A'}`")
    lines.append(f"- luminosity_kind: `{fit.get('luminosity_kind') or 'L_solar (default)'}`")
    n_unit_converted = fit.get("n_unit_converted")
    if n_unit_converted:
        lines.append(f"- n_rows_unit_converted: {n_unit_converted}")
    lines.append("")

    # ── Best-fit values ──────────────────────────────────────────────
    lines.append("**Best-fit values**:")
    lines.append("")
    fit_method = fit.get("fit_method") or "(unknown)"
    lines.append(f"- fit_method: `{fit_method}`"
                 + (f"  (downgraded from `{fit.get('fit_method_requested')}`: "
                    f"{fit.get('fit_method_downgrade_reason')})"
                    if fit.get("fit_method_downgrade_reason") else ""))
    alpha = _format_fit_value(fit.get("alpha"))
    alpha_err = _format_fit_value(fit.get("alpha_stderr"))
    beta = _format_fit_value(fit.get("beta"))
    beta_err = _format_fit_value(fit.get("beta_stderr"))
    lines.append(f"- alpha (intercept) = {alpha} +/- {alpha_err}")
    lines.append(f"- beta (slope)      = {beta} +/- {beta_err}")
    lines.append(f"- residual_rms_dex  = {_format_fit_value(fit.get('residual_rms_dex'))}")
    intrinsic = fit.get("intrinsic_scatter_dex")
    if intrinsic is not None:
        hdi = fit.get("intrinsic_scatter_dex_hdi") or [None, None]
        lines.append(
            f"- intrinsic_scatter_dex = {_format_fit_value(intrinsic)} "
            f"[94% HDI: {_format_fit_value(hdi[0])}, {_format_fit_value(hdi[1])}]"
        )
    lines.append(f"- pearson_r = {_format_fit_value(fit.get('pearson_r'))}")
    lines.append(f"- spearman_r = {_format_fit_value(fit.get('spearman_r'))}")
    lines.append("")

    # ── Bayesian diagnostics (if Bayesian path ran) ──────────────────
    bayes = fit.get("bayesian_summary")
    if isinstance(bayes, dict):
        lines.append("**Bayesian sampler diagnostics** (Kelly 2007 linmix):")
        lines.append("")
        lines.append(f"- package: `{bayes.get('package') or 'n/a'}`")
        lines.append(f"- reference: {bayes.get('reference') or 'n/a'}")
        lines.append(f"- n_chains: {bayes.get('n_chains')}; "
                     f"n_draws_total: {bayes.get('n_draws_total')}; "
                     f"K = {bayes.get('K')}")
        lines.append(f"- converged: {bayes.get('converged')}; "
                     f"publication_ready: {bayes.get('publication_ready')}")
        params = bayes.get("parameters") or {}
        if isinstance(params, dict):
            for pname in ("alpha", "beta", "sigma_int"):
                p = params.get(pname)
                if not isinstance(p, dict):
                    continue
                lines.append(
                    f"  - {pname}: median = {_format_fit_value(p.get('median'))}, "
                    f"std = {_format_fit_value(p.get('std'))}, "
                    f"94% HDI = [{_format_fit_value(p.get('hdi_low_94'))}, "
                    f"{_format_fit_value(p.get('hdi_high_94'))}], "
                    f"ess = {p.get('ess')}, rhat = {_format_fit_value(p.get('rhat'))}"
                )
        lines.append("")

    # ── Readiness gates ──────────────────────────────────────────────
    readiness = fit.get("publication_readiness") or {}
    checks = readiness.get("checks") if isinstance(readiness, dict) else None
    if isinstance(checks, dict):
        lines.append("**Publication readiness gates**:")
        lines.append("")
        for gate_name, gate in checks.items():
            if not isinstance(gate, dict):
                continue
            mark = "✓" if gate.get("passed") else "✗"
            extra: list[str] = []
            for k, v in gate.items():
                if k == "passed":
                    continue
                extra.append(f"{k}={v}")
            lines.append(f"- {mark} `{gate_name}`: " + ("; ".join(extra) if extra else ""))
        status = readiness.get("status") or "(unknown)"
        lines.append("")
        lines.append(f"**Overall status**: `{status}`")
        lines.append("")

    # ── Relation claimability ────────────────────────────────────────
    claim = fit.get("relation_claimability")
    if isinstance(claim, dict):
        can = claim.get("can_claim_relation")
        scope = claim.get("claim_scope")
        blockers = claim.get("blocking_reasons") or []
        lines.append("**Claim scope**:")
        lines.append("")
        lines.append(f"- can_claim_relation: {can}")
        lines.append(f"- claim_scope: `{scope}`")
        if blockers:
            lines.append(f"- blocking_reasons: {', '.join(map(str, blockers))}")
        lines.append("")

    # ── Lensing summary ──────────────────────────────────────────────
    lensing = fit.get("lensing_summary")
    if isinstance(lensing, dict):
        lines.append("**Lensing coverage**:")
        lines.append("")
        lines.append(f"- n_unlensed_in_fit: {lensing.get('n_unlensed_in_fit')}")
        lines.append(f"- n_lensed_demagnified_in_fit: {lensing.get('n_lensed_demagnified_in_fit')}")
        lines.append(f"- n_lensed_skipped_no_mu: {lensing.get('n_lensed_skipped_no_mu')}")
        lines.append(f"- n_lensed_unknown: {lensing.get('n_lensed_unknown')}")
        papers = lensing.get("papers_default_lensed") or []
        if papers:
            lines.append(
                f"- papers_default_lensed: "
                f"{', '.join(str(p) for p in papers[:5])}"
                + ("..." if len(papers) > 5 else "")
            )
        lines.append("")

    # ── Cosmology recompute audit ────────────────────────────────────
    if fit.get("cosmology_recomputed"):
        # Bug fix (Phase 4 e2e): fit.cosmology_used is a string (the
        # preset name) per fit_line_lfr's result envelope. The full
        # manifest dict (with H0_km_s_Mpc / Om0 / bibcode) lives under
        # fit.cosmology_manifest. Earlier render code assumed dict and
        # crashed with AttributeError when given a string.
        raw_used = fit.get("cosmology_used")
        if isinstance(raw_used, dict):
            used = raw_used
        else:
            used = fit.get("cosmology_manifest") or {}
            if not isinstance(used, dict):
                used = {}
            if isinstance(raw_used, str) and "name" not in used:
                used = {**used, "name": raw_used}
        baseline = fit.get("cosmology_baseline") or {}
        shift = fit.get("dl_shift_summary") or {}
        lines.append("**Cosmology recomputation**:")
        lines.append("")
        lines.append(f"- cosmology_used: {used.get('name') or '(unspecified)'} "
                     f"(H0 = {used.get('H0_km_s_Mpc')}, Om0 = {used.get('Om0')})")
        lines.append(f"- baseline: {baseline.get('name') or '(unspecified)'} "
                     f"(H0 = {baseline.get('H0_km_s_Mpc')}, Om0 = {baseline.get('Om0')})")
        if shift:
            lines.append(
                f"- median_log_l_shift_dex: {shift.get('median_log_l_shift_dex')}, "
                f"max_abs: {shift.get('max_abs_log_l_shift_dex')}, "
                f"n_rows_shifted: {shift.get('n_rows_shifted')}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _build_results_tables(artifacts: SessionArtifacts) -> list[dict]:
    tables: list[dict] = []
    for action in artifacts.search_calls:
        tool_result = action.get("tool_result")
        if isinstance(tool_result, list) and tool_result:
            first_rows = tool_result[:5]
            if isinstance(first_rows[0], dict):
                headers = list(first_rows[0].keys())[:6]
                rows = [headers]
                for row in first_rows:
                    rows.append([row.get(header) for header in headers])
                tables.append({
                    "table_number": len(tables) + 1,
                    "caption": f"Representative catalog results for {action.get('query', 'the target')}.",
                    "data": rows,
                })
    return tables


def _build_default_paper_json(artifacts: SessionArtifacts, journal_format: str) -> dict:
    sources, data_description = _summarize_data_sources(artifacts)
    data_provenance = _build_data_provenance_text(artifacts)
    primary_question = artifacts.user_prompts[0] if artifacts.user_prompts else "Exploratory astronomical data analysis"
    discussion_citations = artifacts.bibcodes[:5]
    intro_citations = artifacts.bibcodes[:2]
    methods_citations = artifacts.bibcodes[2:3]
    results_text = artifacts.assistant_text[-1] if artifacts.assistant_text else "The session produced a set of exploratory findings that should be reviewed and refined before submission."

    # PART AI #5/#6: 当本 session 含 fit_line_lfr 成功结果时, 自动插入
    # 一段 readiness/claimability/Bayesian/lensing 完整诊断 — 让 paper
    # draft 能复现审稿人想看的 publication_readiness 证据链.
    fit_lfr_section = build_fit_line_lfr_diagnostics_section(artifacts)

    return {
        "title": primary_question[:120],
        "abstract": (
            "This draft summarizes an interactive Standard Astro analysis session. "
            f"The investigation focused on {primary_question[:120]}. "
            f"Data were gathered from {', '.join(sources) if sources else 'multiple astronomical archives'} "
            "and inspected with a combination of catalog queries, AI-assisted reasoning, and reproducible analysis steps. "
            "The present draft should be treated as a structured starting point for a full manuscript, with explicit attention to "
            "sample definitions, uncertainties, and comparison to prior work."
        ),
        **({"fit_line_lfr_diagnostics": fit_lfr_section} if fit_lfr_section else {}),
        "introduction": {
            "text": (
                f"This session was motivated by the question: {primary_question}. "
                "The goal of this draft is to convert the interactive exploration into a manuscript-ready narrative."
            ),
            "citations": intro_citations,
        },
        "data_and_methods": {
            "data_sources": data_description,
            "analysis_methods": (
                f"The analysis used {len(artifacts.search_calls)} catalog search steps, "
                f"{len(artifacts.adql_calls)} ADQL queries, {len(artifacts.python_calls)} Python analysis steps, "
                f"and {len(artifacts.pipeline_calls)} pipeline-related operations."
            ),
            "citations": methods_citations,
        },
        "data_provenance": data_provenance,
        "results": {
            "text": results_text,
            "figures": artifacts.figure_refs,
            "tables": _build_results_tables(artifacts),
        },
        "discussion": {
            "text": (
                "The current discussion should be expanded with quantitative uncertainty estimates, comparison to the literature, "
                "and explicit caveats tied to sample selection and data quality."
            ),
            "citations": discussion_citations,
        },
        "conclusions": (
            "The session establishes a reproducible foundation for a paper draft, but the final manuscript should confirm all "
            "numerical claims, uncertainties, and literature context before submission."
        ),
        "acknowledgments": _build_acknowledgments(sources, artifacts.provenance_datasets),
        "journal_format": journal_format,
    }


def _render_authors_and_affiliations(authors: list[dict] | None, format_key: str) -> list[str]:
    """D5.3 — emit \\author + \\affiliation blocks per aastex631 convention.

    authors is a list of ``{name, affiliation, orcid}`` dicts.  Falls
    back to a single generic author when the list is empty so the
    document still compiles.
    """
    if not authors:
        return [r"\author{Standard Astro User}"]
    out: list[str] = []
    for a in authors:
        name = _escape_latex(str(a.get("name") or "Anonymous Author"))
        aff = _escape_latex(str(a.get("affiliation") or ""))
        orcid = str(a.get("orcid") or "").strip()
        if format_key == "mnras":
            if aff:
                out.append(rf"\author[{name}]{{{name}\thanks{{{aff}}}}}")
            else:
                out.append(rf"\author{{{name}}}")
        else:
            # aastex631 / aa style
            out.append(rf"\author{{{name}}}")
            if orcid:
                out.append(rf"\altaffiliation{{ORCID: {orcid}}}")
            if aff:
                out.append(rf"\affiliation{{{aff}}}")
    return out


def _split_into_citet_citep(text: str, known_bibcodes: list[str]) -> str:
    """D5.2 — transform "... (Smith 2020) ..." → "... \\citep{Smith2020} ..."
    and "Smith (2020)" → "\\citet{Smith2020}".

    Matching is heuristic: for each known bibcode we build a citekey
    like ``Smith2020`` and scan for either form.  This is deliberately
    conservative — we never invent citations, only promote phrases the
    literature engine already knows about.
    """
    import re as _re
    for bib in known_bibcodes:
        # A bibcode looks like 2020ApJ...900....1S — authors/year not
        # derivable without ADS; use the bibcode itself as the key
        # fallback (aastex accepts any string in \cite{}).
        bibkey = _re.sub(r"[^A-Za-z0-9]", "", bib)[:20]
        if not bibkey:
            continue
        # Parenthetical pattern: "(bib)" or "(bibcode)"
        text = _re.sub(
            rf"\(\s*{_re.escape(bib)}\s*\)",
            rf"\\citep{{{bibkey}}}",
            text,
        )
        text = _re.sub(
            rf"\b{_re.escape(bib)}\b",
            rf"\\citet{{{bibkey}}}",
            text,
        )
    return text


def render_latex(paper_json: dict, format: str = "aastex",
                 authors: list[dict] | None = None,
                 known_bibcodes: list[str] | None = None) -> str:
    format_key = format.lower()
    if format_key == "mnras":
        documentclass = r"\documentclass[a4paper,fleqn,usenatbib]{mnras}"
        bib_style = r"\bibliographystyle{mnras}"
    elif format_key in {"aa", "a&a"}:
        documentclass = r"\documentclass{aa}"
        bib_style = r"\bibliographystyle{aa}"
    else:
        documentclass = r"\documentclass[twocolumn]{aastex631}"
        bib_style = r"\bibliographystyle{aasjournal}"

    lines = [
        documentclass,
        r"\usepackage{graphicx}",
        r"\usepackage{booktabs}",
        r"\usepackage{longtable}",
        r"\usepackage{hyperref}",
        r"\begin{document}",
        rf"\title{{{_escape_latex(paper_json.get('title', 'Standard Astro Paper Draft'))}}}",
    ]
    # D5.3: authors + affiliations.
    lines.extend(_render_authors_and_affiliations(
        authors or paper_json.get("authors"), format_key,
    ))
    lines.extend([
        r"\begin{abstract}",
        _escape_latex(paper_json.get("abstract", "")),
        r"\end{abstract}",
        r"\maketitle",
    ])
    data_provenance = str(paper_json.get("data_provenance") or "").strip()
    if data_provenance:
        for line in data_provenance.splitlines():
            lines.append("% Data provenance: " + line)

    def _section(title: str, body: str):
        lines.append(rf"\section{{{_escape_latex(title)}}}")
        lines.append(_escape_latex(body))
        lines.append("")

    intro = paper_json.get("introduction", {})
    methods = paper_json.get("data_and_methods", {})
    results = paper_json.get("results", {})
    discussion = paper_json.get("discussion", {})

    _section("Introduction", str(intro.get("text", "")))
    _section(
        "Data and Methods",
        f"{methods.get('data_sources', '')}\n\n{methods.get('analysis_methods', '')}",
    )
    if data_provenance:
        _section("Data provenance", data_provenance)
    _section("Results", str(results.get("text", "")))

    for figure in results.get("figures", [])[:5]:
        figure_ref = _escape_latex(str(figure.get("session_figure_ref", "session_figure")))
        lines.extend([
            r"\begin{figure}",
            r"\centering",
            rf"\fbox{{\parbox{{0.9\columnwidth}}{{Placeholder for {figure_ref}}}}}",
            rf"\caption{{{_escape_latex(str(figure.get('caption', 'Session figure')))}}}",
            r"\end{figure}",
        ])

    for table in results.get("tables", [])[:3]:
        rows = table.get("data", [])
        if not rows:
            continue
        column_count = len(rows[0])
        colspec = "l" * max(1, column_count)
        lines.extend([
            r"\begin{table}",
            r"\centering",
            rf"\caption{{{_escape_latex(str(table.get('caption', 'Session table')))}}}",
            rf"\begin{{tabular}}{{{colspec}}}",
            r"\toprule",
            " & ".join(_escape_latex(str(cell)) for cell in rows[0]) + r" \\",
            r"\midrule",
        ])
        for row in rows[1:]:
            lines.append(" & ".join(_escape_latex(_format_table_cell(cell)) for cell in row) + r" \\")
        lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])

    _section("Discussion", str(discussion.get("text", "")))
    _section("Conclusions", str(paper_json.get("conclusions", "")))
    _section("Acknowledgments", str(paper_json.get("acknowledgments", "")))

    # D5.1 — real bibliography slot + bibliographystyle.  Sibling .bib
    # file is written alongside by the export pipeline.  Without this
    # block pdflatex + bibtex would produce a document with citation
    # placeholders instead of resolved references.
    lines.extend([
        bib_style,
        r"\bibliography{references}",
    ])
    lines.append(r"\end{document}")

    text = "\n".join(lines)
    # D5.2 — promote known bibcodes into \citet / \citep after the body
    # is rendered so our escaping doesn't eat the backslashes.
    if known_bibcodes:
        text = _split_into_citet_citep(text, list(known_bibcodes))
    return text


async def generate_reproducibility_appendix(session_id: str, db: AsyncSession) -> str:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    artifacts = _extract_actions(session.messages or [])
    query_lines: list[str] = []
    for action in artifacts.search_calls:
        query_lines.append(f"- Search: {action.get('query', '')} | sources={action.get('sources', [])}")
    for action in artifacts.adql_calls:
        query_lines.append(f"- ADQL ({action.get('service', 'gaia')}): {str(action.get('query', ''))[:200]}")

    pipeline_lines = [
        f"- Pipeline action: {action.get('action', '')}"
        for action in artifacts.pipeline_calls
    ] or ["- No pipeline DAGs recorded in this session."]

    code_lines = [
        str(action.get("code") or (action.get("tool_input") or {}).get("code") or "")[:600]
        for action in artifacts.python_calls
    ] or ["No custom Python analysis was recorded."]

    versions = {"platform": "Standard Astro"}
    for package in ("numpy", "scipy", "astropy"):
        try:
            versions[package] = version(package)
        except Exception:
            versions[package] = "unknown"

    appendix = [
        r"\appendix",
        r"\section{Reproducibility Appendix}",
        r"\subsection{Queries Executed}",
        _escape_latex("\n".join(query_lines or ["No catalog queries were recorded."])),
        r"\subsection{Pipeline DAG Description}",
        _escape_latex("\n".join(pipeline_lines)),
        r"\subsection{Python Code}",
        _escape_latex("\n\n".join(code_lines)),
        r"\subsection{Software Versions}",
        _escape_latex(json.dumps(versions, indent=2)),
        r"\subsection{Random Seeds}",
        "No explicit stochastic seeds were captured in this session.",
    ]
    return "\n".join(appendix)


@track_event("export.paper_draft")
async def generate_paper_draft(session_id: str, journal_format: str, db: AsyncSession) -> dict:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    format_key = (journal_format or "aastex").strip().lower()
    if format_key not in {"aastex", "mnras", "aa", "a&a"}:
        raise HTTPException(status_code=400, detail="Unsupported journal format")

    artifacts = _extract_actions(session.messages or [])
    artifacts.session = session
    paper_json = _build_default_paper_json(artifacts, format_key)
    appendix = await generate_reproducibility_appendix(str(session.id), db)
    latex_source = render_latex(paper_json, format_key) + "\n\n" + appendix

    bibtex_entries: list[str] = []
    for bibcode in artifacts.bibcodes[:30]:
        try:
            bibtex_entries.append(_get_bibtex_sync(bibcode))
        except Exception:
            bibtex_entries.append(f"% BibTeX lookup failed for {bibcode}")
    bibtex = "\n\n".join(entry for entry in bibtex_entries if entry.strip()) or "% No citations were found in this session."

    return {
        "paper_json": paper_json,
        "latex_source": latex_source,
        "bibtex": bibtex,
        "session_history": {
            "message_count": len(session.messages or []),
            "queries": len(artifacts.search_calls) + len(artifacts.adql_calls),
            "pipelines": len(artifacts.pipeline_calls),
            "figures": len(artifacts.figure_refs),
            "citations": len(artifacts.bibcodes),
        },
    }
