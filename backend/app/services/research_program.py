"""Research-mode planning, execution summaries, and evidence graph helpers."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from app.common.regex import ARXIV_ID_RE, BIBCODE_RE, DOI_RE
from app.services.cmb_rotation_likelihoods import (
    cmb_rotation_dataset_exists,
    default_cmb_rotation_dataset_keys,
    get_cmb_rotation_dataset,
    run_cmb_rotation_likelihood,
)
from app.services.cosmology_likelihoods import (
    build_likelihood_config,
    compute_model_comparison,
    get_cosmology_dataset,
    run_likelihood_chain,
)


COSMOLOGY_PROBE_DATASETS: dict[str, list[str]] = {
    "bao": ["desi_dr1_bao"],
    "pre_desi_bao": ["sdss_6df_bao"],
    "sn": ["pantheon_plus"],
    "sn_comparison": ["pantheon_plus", "des_sn5yr", "union3"],
    "cmb": ["planck2018_compressed"],
    "cmb_lensing": ["act_dr6_lensing"],
    "wl": ["kids1000_wl", "des_y3_3x2pt", "hsc_y1_cosmic_shear"],
    "h0": ["shoes_h0_riess22"],
    "h0_anchors": [
        "shoes_h0_riess22",
        "trgb_h0_freedman19",
        "h0licow_h0",
        "megamaser_h0_pesce20",
    ],
    "hz": ["cosmic_chronometers"],
    "rsd": ["eboss_dr16_rsd"],
}


def plan_research_program(
    *,
    question: str,
    paper_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a deterministic v1 research plan from a science question."""
    text = str(question or "").strip()
    probes = _required_probes(text)
    datasets = _candidate_datasets(probes, text)
    models = _models_from_question(text)
    hypotheses = _hypotheses_from_question(text, probes, models)
    dataset_entries = [
        entry
        for key in datasets
        if (entry := _dataset_entry_for_plan(key)) is not None
    ]
    dataset_status = [_dataset_plan_status(entry) for entry in dataset_entries]
    matrix = _proposed_experiment_matrix(datasets, models, text)
    blocking_gaps = _blocking_gaps(dataset_status, models)
    blocking_gaps.extend(_question_specific_blocking_gaps(text, probes))
    capability_gap_matrix = _capability_gap_matrix(
        text=text,
        probes=probes,
        dataset_status=dataset_status,
        models=models,
        blocking_gaps=blocking_gaps,
    )
    partial_pass_readiness = _partial_pass_readiness(
        probes=probes,
        dataset_status=dataset_status,
        matrix=matrix,
        blocking_gaps=blocking_gaps,
        capability_gap_matrix=capability_gap_matrix,
    )
    executable_level = _overall_executable_level(dataset_status)
    plan_id = "research_plan_" + uuid.uuid4().hex
    plan = {
        "id": plan_id,
        "research_question": text,
        "paper_metadata": paper_metadata or {},
        "hypotheses": hypotheses,
        "required_probes": probes,
        "candidate_dataset_keys": datasets,
        "candidate_datasets": dataset_status,
        "model_families": models,
        "executable_level": executable_level,
        "blocking_gaps": blocking_gaps,
        "capability_gap_matrix": capability_gap_matrix,
        "partial_pass_readiness": partial_pass_readiness,
        "proposed_experiment_matrix": matrix,
        "required_output_sections": [
            "What can be tested now",
            "Executed analyses",
            "Preliminary findings",
            "Robustness",
            "What drives the result",
            "What is not yet supported",
            "Next experiment",
        ],
        "alpha_test_protocol": {
            "supported_scope": (
                "Exploratory research over registered observational-cosmology "
                "data products and controlled runners."
            ),
            "not_supported": (
                "Arbitrary paper reproduction, full external likelihood claims, "
                "or posterior numbers from config-only / abstract-only evidence."
            ),
            "required_artifacts": [
                "research plan",
                "executed tool matrix",
                "runnable / not-runnable cells",
                "evidence graph",
                "fact check report",
                "local diagnostic bundle for blind tests",
            ],
        },
    }
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "RESEARCH_PLAN_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "research_plan": plan,
        "plan_id": plan_id,
        "dataset_count": len(dataset_status),
        "matrix_size": len(matrix),
        "warnings": [
            "Research plan only; execute runnable cells before reporting posterior or fit numbers.",
        ],
        "__message_to_model__": (
            "Use this plan to decide which tools can run. Do not report scientific "
            "posterior/tension/fit numbers from the plan itself."
        ),
    }


def run_research_matrix(
    *,
    research_plan: dict[str, Any] | None = None,
    question: str | None = None,
    dataset_keys: list[str] | None = None,
    models: list[str] | None = None,
    random_seed: int | None = None,
    n_samples: int = 4000,
) -> dict[str, Any]:
    """Run the executable subset of a research plan."""
    if research_plan:
        plan = dict(research_plan)
    else:
        plan = plan_research_program(question=str(question or ""))["research_plan"]
    plan_question = str(question or plan.get("research_question") or "")
    if _is_primary_cmb_rotation_question(plan_question.lower()):
        rotation_keys = [
            key
            for key in _clean_dataset_keys(dataset_keys or plan.get("candidate_dataset_keys") or [])
            if cmb_rotation_dataset_exists(key)
        ] or default_cmb_rotation_dataset_keys()
        run = run_cmb_rotation_likelihood(
            dataset_keys=rotation_keys,
            model="isotropic_beta",
            random_seed=random_seed,
            n_samples=n_samples,
        )
        gap = (
            "CMB polarization-rotation inference is not executable with Planck compressed distance priors. "
            "A valid run requires EB/TB bandpowers or maps, their covariance, an instrument-angle prior, "
            "and a dedicated rotation-angle likelihood. Late-time BAO/SN/H0 compressed runners are not "
            "valid substitutes for this rotation-angle workflow."
        )
        existing_gaps = plan.get("blocking_gaps")
        if isinstance(existing_gaps, list) and gap not in existing_gaps:
            plan["blocking_gaps"] = [*existing_gaps, gap]
        run_warnings = run.get("warnings", []) if isinstance(run.get("warnings"), list) else []
        if run.get("publication_ready") is not True and gap not in run_warnings:
            run_warnings = [*run_warnings, gap]
        rotation_cells = [{
            "label": "CMB rotation — isotropic beta",
            "model": "isotropic_beta",
            "dataset_keys": rotation_keys,
            "publication_ready": bool(run.get("publication_ready")),
            "runnable": bool(run.get("publication_ready")),
            "execution_level": "compressed_preliminary"
            if run.get("publication_ready") is True
            else "config_only",
            "result": run,
            "warnings": run.get("warnings", []),
        }]
        return {
            "success": True,
            "__tool_status__": "PARTIAL",
            "analysis_status": "RESEARCH_MATRIX_PARTIAL",
            "publication_ready": bool(run.get("publication_ready")),
            "claim_scope": "cmb_rotation_compressed_preliminary"
            if run.get("publication_ready") is True
            else "research_matrix_scope_gap",
            "research_plan": plan,
            "partial_pass_readiness": plan.get("partial_pass_readiness"),
            "capability_gap_matrix": plan.get("capability_gap_matrix"),
            "matrix": rotation_cells,
            "research_charts": _research_matrix_charts(rotation_cells),
            "matrix_size": 1,
            "ready_cells": 1 if run.get("publication_ready") is True else 0,
            "warnings": run_warnings or [gap],
            "failure_categories": []
            if run.get("publication_ready") is True
            else ["runner missing", "wrong routing"],
            "__do_not_claim__": True if run.get("publication_ready") is not True else False,
            "__message_to_model__": (
                "Summarize only the CMB rotation result. Do not report BAO/SN/H0 posterior numbers "
                "for this CMB polarization-rotation request. If publication_ready=false, state the "
                "missing EB/TB/covariance/calibration-prior/rotation-likelihood gap."
            ),
        }
    datasets = _clean_dataset_keys(dataset_keys or plan.get("candidate_dataset_keys") or [])
    model_list = _clean_models(models or plan.get("model_families") or ["lcdm"])
    matrix = plan.get("proposed_experiment_matrix")
    if not isinstance(matrix, list) or not matrix:
        matrix = _proposed_experiment_matrix(datasets, model_list, str(question or plan.get("research_question") or ""))

    base_seed = int(random_seed if random_seed is not None else 20260503)
    cells: list[dict[str, Any]] = []
    for index, cell in enumerate(matrix):
        cell_datasets = _clean_dataset_keys(cell.get("dataset_keys") if isinstance(cell, dict) else [])
        if not cell_datasets:
            continue
        model = str(cell.get("model") if isinstance(cell, dict) else model_list[0])
        label = str(cell.get("label") if isinstance(cell, dict) else "Research cell")
        try:
            rotation_keys = [key for key in cell_datasets if cmb_rotation_dataset_exists(key)]
            if rotation_keys:
                run = run_cmb_rotation_likelihood(
                    dataset_keys=rotation_keys,
                    model="isotropic_beta",
                    random_seed=base_seed + index,
                    n_samples=n_samples,
                )
                cells.append({
                    "label": label,
                    "model": "isotropic_beta",
                    "dataset_keys": rotation_keys,
                    "publication_ready": bool(run.get("publication_ready")),
                    "runnable": bool(run.get("publication_ready")),
                    "execution_level": "compressed_preliminary"
                    if run.get("publication_ready") is True
                    else "config_only",
                    "result": run,
                    "warnings": run.get("warnings", []) if isinstance(run.get("warnings"), list) else [],
                })
                continue
            config = build_likelihood_config(
                model=model,
                dataset_keys=cell_datasets,
                output_format="cobaya",
            )
            if model != "lcdm":
                cells.append({
                    "label": label,
                    "model": model,
                    "dataset_keys": cell_datasets,
                    "publication_ready": False,
                    "runnable": False,
                    "execution_level": "config_only",
                    "config_hash": config.get("config_hash"),
                    "requested_model_branch": bool(cell.get("requested_model_branch")),
                    "warnings": [
                        "Requested extended-model branch was not numerically run. Phase-1 research matrices only promote ΛCDM compressed baselines to claimable results.",
                    ],
                })
                continue
            non_executable = _non_executable_dataset_keys(cell_datasets)
            if non_executable:
                cells.append({
                    "label": label,
                    "model": model,
                    "dataset_keys": cell_datasets,
                    "publication_ready": False,
                    "runnable": False,
                    "execution_level": "config_only",
                    "config_hash": config.get("config_hash"),
                    "non_executable_dataset_keys": non_executable,
                    "warnings": [
                        "This research cell was not numerically run because it includes config-only or external-only datasets.",
                    ],
                })
                continue
            run = run_likelihood_chain(
                model=model,
                dataset_keys=cell_datasets,
                random_seed=base_seed + index,
                n_samples=n_samples,
            )
            not_run = run.get("datasets_not_run")
            all_requested_datasets_used = not isinstance(not_run, list) or len(not_run) == 0
            cell_ready = bool(run.get("publication_ready")) and all_requested_datasets_used
            datasets_used = run.get("datasets_used")
            any_dataset_used = isinstance(datasets_used, list) and len(datasets_used) > 0
            chain_diagnostics = run.get("chain_diagnostics")
            if cell_ready:
                execution_level = "compressed_preliminary"
            elif not all_requested_datasets_used and any_dataset_used:
                execution_level = "partial_dataset_run"
            elif isinstance(chain_diagnostics, dict):
                execution_level = "executed_not_ready"
            else:
                execution_level = "config_only"
            cells.append({
                "label": label,
                "model": model,
                "dataset_keys": cell_datasets,
                "publication_ready": cell_ready,
                "runnable": cell_ready,
                "execution_level": execution_level,
                "baseline_only": bool(cell.get("baseline_only")),
                "result_publication_ready": bool(run.get("publication_ready")),
                "result": run,
                "config_hash": config.get("config_hash"),
                "warnings": [
                    *(
                        [
                            "ΛCDM baseline only; this cell does not test the requested extended model."
                        ]
                        if cell.get("baseline_only")
                        else []
                    ),
                    *(run.get("warnings", []) if isinstance(run.get("warnings"), list) else []),
                    *([] if all_requested_datasets_used else [
                        "This research cell is not ready because at least one requested dataset was not numerically included.",
                    ]),
                ],
            })
        except Exception as exc:
            cells.append({
                "label": label,
                "model": model,
                "dataset_keys": cell_datasets,
                "publication_ready": False,
                "runnable": False,
                "execution_level": "not_available",
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            })

    ready = [cell for cell in cells if cell.get("publication_ready")]
    # Model comparison: pair the ΛCDM baseline cell with each extended-model cell
    # computed on the SAME datasets (Δχ²/ΔAIC/ΔBIC). Reuses cells already fit —
    # no extra chains — and answers "is wCDM/w0wa preferred over ΛCDM here".
    model_comparisons: list[dict[str, Any]] = []
    cells_by_data: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for cell in cells:
        result = cell.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("fit_statistics"), dict):
            continue
        cells_by_data.setdefault(tuple(sorted(cell.get("dataset_keys") or [])), []).append(cell)
    for data_key, group in cells_by_data.items():
        baseline = next((c for c in group if str(c.get("model")) == "lcdm"), None)
        if baseline is None:
            continue
        for cell in group:
            if str(cell.get("model")) == "lcdm":
                continue
            comparison = compute_model_comparison(baseline["result"], cell["result"])
            comparison["dataset_keys"] = list(data_key)
            model_comparisons.append(comparison)
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if ready else "PARTIAL",
        "analysis_status": "RESEARCH_MATRIX_READY" if ready else "RESEARCH_MATRIX_PARTIAL",
        "publication_ready": bool(ready),
        "claim_scope": "research_matrix_compressed_preliminary",
        "research_plan": plan,
        "partial_pass_readiness": plan.get("partial_pass_readiness"),
        "capability_gap_matrix": plan.get("capability_gap_matrix"),
        "matrix": cells,
        "research_charts": _research_matrix_charts(cells),
        "model_comparisons": model_comparisons,
        "matrix_size": len(cells),
        "ready_cells": len(ready),
        "warnings": [
            "Research matrix uses executable compressed-likelihood cells where available; config-only cells are not numerical evidence.",
        ],
        "failure_categories": [
            "data unavailable",
            "runner missing",
            "wrong routing",
            "unsupported numeric claim",
            "source mismatch",
            "formula/constant mismatch",
            "result differs but explainable",
            "hallucination",
            "UI/process failure",
        ],
        "__message_to_model__": (
            "Summarize only publication_ready cells as compressed-likelihood preliminary. "
            "For other cells, describe the missing runner/config gap."
        ),
    }


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _cell_status_for_chart(cell: dict[str, Any]) -> str:
    if cell.get("publication_ready"):
        return "ready"
    level = str(cell.get("execution_level") or "not_available")
    if level == "partial_dataset_run":
        return "partial"
    if level == "executed_not_ready":
        return "not_ready"
    if level == "config_only":
        return "config_only"
    return "missing"


def _posterior_source(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("parameters", "posterior_summary", "derived_params"):
        value = result.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _summary_bounds(summary: dict[str, Any]) -> tuple[float | None, float | None]:
    low = _as_float(summary.get("hdi_low_94"))
    high = _as_float(summary.get("hdi_high_94"))
    if low is not None and high is not None:
        return low, high
    hdi = summary.get("hdi_94")
    if isinstance(hdi, (list, tuple)) and len(hdi) >= 2:
        return _as_float(hdi[0]), _as_float(hdi[1])
    median = _as_float(summary.get("median"))
    std = _as_float(summary.get("std"))
    if median is not None and std is not None:
        return median - std, median + std
    return None, None


def _research_matrix_charts(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Deterministic chart payload for Research Mode.

    This is deliberately derived from already-computed matrix cells rather than
    from an extra LLM-authored Python step. It restores visual diagnostics while
    preserving the Research Mode evidence/fact-check contract.
    """

    status_counts: dict[str, int] = {}
    matrix_status: list[dict[str, Any]] = []
    posterior_forest: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    preferred_params = ("H0", "S8", "omegam", "Omega_m", "sigma8", "w0", "wa", "beta_deg")

    for index, cell in enumerate(cells):
        label = str(cell.get("label") or f"Cell {index + 1}")
        status = _cell_status_for_chart(cell)
        status_counts[status] = status_counts.get(status, 0) + 1
        matrix_status.append({
            "label": label,
            "status": status,
            "execution_level": cell.get("execution_level") or "not_available",
            "publication_ready": bool(cell.get("publication_ready")),
            "dataset_keys": cell.get("dataset_keys") or [],
            "model": cell.get("model"),
        })

        result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
        if isinstance(result, dict):
            params = _posterior_source(result)
            for param in preferred_params:
                summary = params.get(param)
                if not isinstance(summary, dict):
                    continue
                median = _as_float(summary.get("median"))
                if median is None:
                    continue
                low, high = _summary_bounds(summary)
                posterior_forest.append({
                    "label": label,
                    "parameter": "omegam" if param == "Omega_m" else param,
                    "median": median,
                    "low": low,
                    "high": high,
                    "publication_ready": bool(cell.get("publication_ready")),
                    "execution_level": cell.get("execution_level") or "not_available",
                })

            chain = result.get("chain_diagnostics")
            if isinstance(chain, dict):
                ess = _as_float(chain.get("proposal_ess") or chain.get("ess_bulk") or chain.get("posterior_ess"))
                rhat = _as_float(chain.get("rhat"))
                thresholds = chain.get("thresholds") if isinstance(chain.get("thresholds"), dict) else {}
                diagnostics.append({
                    "label": label,
                    "ess": ess,
                    "rhat": rhat,
                    "publication_ready": bool(cell.get("publication_ready")),
                    "execution_level": cell.get("execution_level") or "not_available",
                    "ess_threshold": _as_float(thresholds.get("ess_min")) or 400.0,
                    "rhat_threshold": _as_float(thresholds.get("rhat_max")) or 1.05,
                })

    return {
        "chart_version": 1,
        "matrix_status": matrix_status,
        "status_counts": status_counts,
        "posterior_forest": posterior_forest[:24],
        "diagnostics": diagnostics[:24],
        "notes": [
            "Charts are deterministic renderings of current-turn Research Matrix cells.",
            "They are visual diagnostics only; claimability still follows publication_ready and Fact Check.",
        ],
    }


def build_evidence_graph(
    *,
    tool_results: list[dict[str, Any]] | None = None,
    final_reply: str | None = None,
) -> dict[str, Any]:
    """Build a compact claim-support graph from tool results and optional prose."""
    tool_results = tool_results or []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    claimable_params: set[str] = set()
    supported_claims: list[dict[str, Any]] = []
    unsupported_claims: list[dict[str, Any]] = []

    for idx, item in enumerate(tool_results):
        tool = str(item.get("tool") or item.get("name") or f"tool_{idx}")
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        tool_id = f"tool_run:{idx}:{tool}"
        nodes.append({
            "id": tool_id,
            "type": "tool_run",
            "tool": tool,
            "publication_ready": bool(result.get("publication_ready")) if isinstance(result, dict) else False,
            "analysis_status": result.get("analysis_status") if isinstance(result, dict) else None,
            "input_hash": _stable_hash(item.get("input") or item.get("tool_input") or {}),
        })
        if isinstance(result, dict):
            result_id = f"result:{idx}:{tool}"
            if result.get("publication_ready") is True:
                nodes.append({
                    "id": result_id,
                    "type": "result",
                    "tool": tool,
                    "analysis_status": result.get("analysis_status"),
                    "claim_scope": result.get("claim_scope"),
                    "runner_hash": result.get("runner_hash") or result.get("config_hash"),
                    "sampler": result.get("sampler"),
                    "publication_ready": True,
                })
                edges.append({"from": result_id, "to": tool_id, "kind": "produced_by"})
            for dataset in _datasets_from_result(result):
                ds_id = f"dataset:{dataset.get('key') or dataset.get('display_name')}"
                if not any(node["id"] == ds_id for node in nodes):
                    nodes.append({
                        "id": ds_id,
                        "type": "dataset",
                        "key": dataset.get("key"),
                        "display_name": dataset.get("display_name"),
                        "version": dataset.get("version"),
                        "execution_mode": dataset.get("execution_mode"),
                        "citations": dataset.get("citations", []),
                    })
                edges.append({"from": tool_id, "to": ds_id, "kind": "uses_dataset"})
            if result.get("publication_ready") is True:
                for param in _claimable_parameters_from_result(result):
                    claim_id = f"claim:{param}:{len(supported_claims)}"
                    claimable_params.add(param)
                    supported_claims.append({
                        "id": claim_id,
                        "parameter": param,
                        "supporting_tool_run": tool_id,
                        "supporting_result": result_id,
                        "scope": result.get("claim_scope"),
                        "evidence_path": [claim_id, result_id, tool_id],
                    })
                    nodes.append({"id": claim_id, "type": "claim", "parameter": param})
                    edges.append({"from": claim_id, "to": result_id, "kind": "supported_by"})

    if final_reply:
        for param in _numeric_claim_tokens(final_reply):
            if param not in claimable_params:
                unsupported_claims.append({"parameter": param, "reason": "no publication-ready current-turn support"})

    graph = {
        "nodes": nodes,
        "edges": edges,
        "supported_claims": supported_claims,
        "unsupported_claims": unsupported_claims,
        "claimable_parameters": sorted(claimable_params),
    }
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if not unsupported_claims else "PARTIAL",
        "analysis_status": "EVIDENCE_GRAPH_READY",
        "publication_ready": not unsupported_claims,
        "evidence_graph": graph,
        "claimable_parameters": sorted(claimable_params),
        "unsupported_claim_count": len(unsupported_claims),
        "__message_to_model__": (
            "Use supported_claims only. Unsupported claims must be removed or replaced with a scope-gap statement."
        ),
    }


def verify_research_facts(
    *,
    tool_results: list[dict[str, Any]] | None = None,
    final_reply: str | None = None,
    evidence_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify research-output facts against current-turn evidence.

    This layer does not create new scientific evidence. It classifies the
    claims already present in a draft reply against publication-ready tool
    runs, extracted tables, registry metadata, and source identifiers.
    """
    tool_results = tool_results or []
    graph = evidence_graph or _latest_evidence_graph(tool_results)
    if not graph:
        graph_result = build_evidence_graph(tool_results=tool_results)
        graph = graph_result.get("evidence_graph") if isinstance(graph_result, dict) else {}
    if not isinstance(graph, dict):
        graph = {}

    claimable = {str(item).lower() for item in graph.get("claimable_parameters") or []}
    payload_text = _tool_payload_text(tool_results)
    dataset_index = _dataset_index_from_tool_results(tool_results)
    ready_tools = _publication_ready_tool_ids(tool_results)
    numeric_support = _numeric_support_from_tool_results(tool_results)
    claims = _fact_claims_from_reply(
        str(final_reply or ""),
        claimable=claimable,
        payload_text=payload_text,
        dataset_index=dataset_index,
        ready_tools=ready_tools,
        numeric_support=numeric_support,
    )

    if not claims:
        claims.append({
            "text": "No strong scientific fact claim detected in the checked reply.",
            "kind": "scope",
            "status": "verified",
            "support_level": "tool_run" if ready_tools else "not_applicable",
            "evidence_ids": sorted(ready_tools)[:5],
            "safe_rewrite": "",
        })

    checked_sources = _checked_sources_from_payload(payload_text, dataset_index)
    blocked = any(claim.get("status") == "contradicted" for claim in claims) or any(
        claim.get("status") == "unsupported" and claim.get("kind") == "numeric"
        for claim in claims
    )
    warning = any(claim.get("status") in {"unsupported", "partial"} for claim in claims)
    status = "blocked" if blocked else "warning" if warning else "passed"
    unsupported = [c for c in claims if c.get("status") in {"unsupported", "contradicted"}]
    report = {
        "status": status,
        "claims": claims,
        "checked_sources": checked_sources,
        "unsupported_claim_count": len(unsupported),
        "verified_claim_count": sum(1 for c in claims if c.get("status") == "verified"),
        "safe_rewrites": [c.get("safe_rewrite") for c in claims if c.get("safe_rewrite")],
    }
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if status == "passed" else "PARTIAL",
        "analysis_status": "FACT_CHECK_READY",
        "publication_ready": status == "passed",
        "fact_check_report": report,
        "status": status,
        "claims": claims,
        "checked_sources": checked_sources,
        "unsupported_claim_count": len(unsupported),
        "verified_claim_count": sum(1 for c in claims if c.get("status") == "verified"),
        "warnings": [
            "Fact verification checks claims against current-turn evidence; it does not create new evidence.",
        ],
        "__message_to_model__": (
            "Use verified claims as written. Unsupported or contradicted claims must be removed "
            "or replaced by their safe_rewrite."
        ),
    }


def export_research_report(
    *,
    research_plan: dict[str, Any] | None = None,
    evidence_graph: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Generate an auditable research report package and paper draft."""
    plan = research_plan or {}
    graph = evidence_graph or {}
    tool_results = tool_results or []
    report_title = title or plan.get("research_question") or "Standard Astro research report"
    datasets = _report_datasets(tool_results)
    citations = _report_citations(tool_results)
    manifest = _report_reproducibility_manifest(tool_results)
    fact_report = _latest_fact_check_report(tool_results)
    fact_blocked = (
        isinstance(fact_report, dict) and str(fact_report.get("status") or "") == "blocked"
    )
    ready_findings = _report_ready_findings(tool_results)
    blocked_cells = _report_blocked_cells(tool_results)
    matrix_rows = _report_matrix_rows(tool_results)
    lines = [
        f"# {report_title}",
        "",
        "## Research Question",
        str(plan.get("research_question") or "Not provided."),
        "",
        "## Planned Tests",
    ]
    for cell in plan.get("proposed_experiment_matrix", []) if isinstance(plan.get("proposed_experiment_matrix"), list) else []:
        lines.append(f"- {cell.get('label')}: {', '.join(cell.get('dataset_keys', []))} ({cell.get('model')})")
    lines.extend(["", "## Executed Results"])
    for item in tool_results:
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        status = result.get("analysis_status") or result.get("__tool_status__")
        ready = result.get("publication_ready")
        lines.append(f"- {item.get('tool') or item.get('name')}: {status}; publication_ready={ready}")
    if matrix_rows:
        lines.extend(["", "## Robustness Matrix Details"])
        if fact_blocked:
            lines.append("> Fact check BLOCKED — values below are unverified, shown for process transparency only, not as citable results.")
            lines.append("")
        lines.append("| Cell | Model | Datasets | Status | Key outputs | Diagnostics |")
        lines.append("|---|---|---|---|---|---|")
        for row in matrix_rows:
            lines.append(
                "| {label} | {model} | {datasets} | {status} | {outputs} | {diagnostics} |".format(
                    label=row["label"],
                    model=row["model"],
                    datasets=row["datasets"],
                    status=row["status"],
                    outputs=row["outputs"],
                    diagnostics=row["diagnostics"],
                )
            )
    lines.extend(["", "## Preliminary Findings"])
    if not ready_findings:
        lines.append("- No publication-ready or compressed-preliminary numerical result was present in the supplied tool results.")
    elif fact_blocked:
        lines.append(
            "- Fact check is BLOCKED for this turn — no numerical finding is cleared for the Results section. "
            "Values are listed under \"Needs Verification\" below and must not be cited as established results."
        )
        lines.extend(["", "## Needs Verification (fact check blocked)"])
        for finding in ready_findings:
            lines.append(f"- {finding}")
        lines.append("- Resolve the blocking fact-check issues before treating any value above as a result.")
    else:
        for finding in ready_findings:
            lines.append(f"- {finding}")
        lines.append("- All numerical findings above are compressed-likelihood preliminary unless a full external likelihood is explicitly listed.")
    if blocked_cells:
        lines.extend(["", "## Runnable Gaps"])
        for cell in blocked_cells:
            lines.append(f"- {cell}")
    lines.extend(["", "## Datasets and Citations"])
    if datasets:
        for dataset in datasets:
            source = f" — {dataset.get('source_url')}" if dataset.get("source_url") else ""
            lines.append(f"- {dataset.get('key')}: {dataset.get('display_name')} ({dataset.get('version')}){source}")
    else:
        lines.append("- No dataset metadata was present in the supplied tool results.")
    if citations:
        lines.append("")
        lines.append("## Bibliography")
        for citation in citations:
            label = citation.get("label") or citation.get("title") or citation.get("arxiv") or citation.get("doi")
            bits = [str(label)]
            if citation.get("year"):
                bits.append(str(citation["year"]))
            if citation.get("arxiv"):
                bits.append(f"arXiv:{citation['arxiv']}")
            if citation.get("doi"):
                bits.append(f"doi:{citation['doi']}")
            lines.append("- " + "; ".join(bits))
    lines.extend([
        "",
        "## Claim Provenance",
        f"- Claimable parameters: {', '.join(graph.get('claimable_parameters', [])) if isinstance(graph, dict) else 'none'}",
        "",
        "## Fact Verification",
        f"- Status: {fact_report.get('status', 'not_run') if fact_report else 'not_run'}",
        f"- Verified claims: {fact_report.get('verified_claim_count', 0) if fact_report else 0}",
        f"- Unsupported/contradicted claims: {fact_report.get('unsupported_claim_count', 0) if fact_report else 0}",
    ])
    if isinstance(fact_report, dict) and isinstance(fact_report.get("claims"), list):
        for claim in fact_report["claims"][:8]:
            if not isinstance(claim, dict):
                continue
            lines.append(
                f"- {claim.get('status')}: {claim.get('text')} "
                f"(support={claim.get('support_level')})"
            )
    lines.extend(["", "## Reproducibility Manifest", f"- Tool runs recorded: {len(manifest)}"])
    for entry in manifest:
        lines.append(
            "- {tool}: status={status}; ready={ready}; run_id={run_id}; seed={seed}; version={version}".format(
                tool=entry.get("tool"),
                status=entry.get("analysis_status"),
                ready=entry.get("publication_ready"),
                run_id=entry.get("run_id") or "not recorded",
                seed=entry.get("random_seed") or "not recorded",
                version=entry.get("tool_version") or "not recorded",
            )
        )
    lines.extend(["", "## Limitations"])
    for gap in plan.get("blocking_gaps", []) if isinstance(plan.get("blocking_gaps"), list) else []:
        lines.append(f"- {gap}")
    markdown = "\n".join(lines).strip() + "\n"
    paper_draft = _paper_draft_from_report_parts(
        title=str(report_title),
        plan=plan,
        datasets=datasets,
        citations=citations,
        ready_findings=ready_findings,
        blocked_cells=blocked_cells,
        matrix_rows=matrix_rows,
        fact_report=fact_report,
        manifest=manifest,
    )
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "RESEARCH_REPORT_READY",
        "publication_ready": False,
        "format": "markdown",
        "markdown": markdown,
        "paper_draft_markdown": paper_draft,
        "bibtex": _bibtex_from_citations(citations),
        "datasets": datasets,
        "citations": citations,
        "reproducibility_manifest": manifest,
        "report_package": {
            "files": [
                {"path": "research_report.md", "content_type": "text/markdown", "bytes": len(markdown.encode("utf-8"))},
                {"path": "paper_draft.md", "content_type": "text/markdown", "bytes": len(paper_draft.encode("utf-8"))},
                {"path": "references.bib", "content_type": "text/x-bibtex"},
                {"path": "reproducibility_manifest.json", "content_type": "application/json"},
                {"path": "fact_check_report.json", "content_type": "application/json"},
            ],
            "unsupported_claim_policy": "omit_from_results_or_move_to_limitations",
        },
        "word_count": len(markdown.split()),
        "__message_to_model__": "This is a report draft. It does not create new scientific evidence.",
    }


def _report_ready_findings(tool_results: list[dict[str, Any]]) -> list[str]:
    findings: list[str] = []
    for item in tool_results:
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        if isinstance(result.get("matrix"), list):
            for cell in result["matrix"]:
                if not isinstance(cell, dict) or cell.get("publication_ready") is not True:
                    continue
                cell_result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
                params = cell_result.get("parameters") if isinstance(cell_result.get("parameters"), dict) else {}
                diagnostics = (
                    cell_result.get("chain_diagnostics")
                    if isinstance(cell_result.get("chain_diagnostics"), dict)
                    else {}
                )
                bits = [
                    str(cell.get("label") or "Unnamed cell"),
                    f"model={cell.get('model') or 'unknown'}",
                    "datasets=" + ", ".join(str(k) for k in cell.get("dataset_keys", []) if k),
                ]
                param_bits: list[str] = []
                for name in ("H0", "omegam", "Omega_m", "S8", "sigma8", "w0", "wa"):
                    value = params.get(name)
                    if isinstance(value, dict) and value.get("median") is not None:
                        param_bits.append(f"{name}={_format_report_number(value.get('median'))}")
                if param_bits:
                    bits.append("parameters: " + ", ".join(param_bits))
                diag_bits: list[str] = []
                for key in ("proposal_ess", "ess_bulk", "rhat", "acceptance_fraction"):
                    if diagnostics.get(key) is not None:
                        diag_bits.append(f"{key}={_format_report_number(diagnostics.get(key))}")
                if diag_bits:
                    bits.append("diagnostics: " + ", ".join(diag_bits))
                findings.append("; ".join(bits))
        elif result.get("publication_ready") is True:
            params = result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
            param_bits = []
            for name, value in params.items():
                if isinstance(value, dict) and value.get("median") is not None:
                    param_bits.append(f"{name}={_format_report_number(value.get('median'))}")
            if param_bits:
                findings.append(
                    f"{item.get('tool') or item.get('name') or 'tool result'}; "
                    + ", ".join(param_bits)
                )
    return findings


def _report_matrix_rows(tool_results: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in tool_results:
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict) or not isinstance(result.get("matrix"), list):
            continue
        for cell in result["matrix"]:
            if not isinstance(cell, dict):
                continue
            is_ready = cell.get("publication_ready") is True
            cell_result = cell.get("result") if isinstance(cell.get("result"), dict) else {}
            params = cell_result.get("parameters") if isinstance(cell_result.get("parameters"), dict) else {}
            diagnostics = (
                cell_result.get("chain_diagnostics")
                if isinstance(cell_result.get("chain_diagnostics"), dict)
                else {}
            )
            outputs: list[str] = []
            if is_ready:
                for name in ("H0", "omegam", "S8", "sigma8", "rd", "H0_rd", "w0", "wa"):
                    value = params.get(name)
                    if isinstance(value, dict) and value.get("median") is not None:
                        std = value.get("std")
                        if std is not None:
                            outputs.append(f"{name}={_format_report_number(value.get('median'))} +/- {_format_report_number(std)}")
                        else:
                            outputs.append(f"{name}={_format_report_number(value.get('median'))}")
            if not is_ready or (not outputs and cell.get("warnings")):
                outputs.append("not claimable")
            diag_bits: list[str] = []
            for key in ("proposal_ess", "ess_bulk", "rhat", "acceptance_fraction"):
                if diagnostics.get(key) is not None:
                    diag_bits.append(f"{key}={_format_report_number(diagnostics.get(key))}")
            rows.append({
                "label": _md_table_cell(str(cell.get("label") or "Unnamed cell")),
                "model": _md_table_cell(str(cell.get("model") or "unknown")),
                "datasets": _md_table_cell(", ".join(str(k) for k in cell.get("dataset_keys", []) if k) or "none"),
                "status": _md_table_cell(
                    "ready" if cell.get("publication_ready") is True else str(cell.get("execution_level") or "not ready")
                ),
                "outputs": _md_table_cell("; ".join(outputs) if outputs else "no numerical output"),
                "diagnostics": _md_table_cell("; ".join(diag_bits) if diag_bits else "not available"),
            })
    return rows


def _md_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _report_blocked_cells(tool_results: list[dict[str, Any]]) -> list[str]:
    blocked: list[str] = []
    for item in tool_results:
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict) or not isinstance(result.get("matrix"), list):
            continue
        for cell in result["matrix"]:
            if not isinstance(cell, dict) or cell.get("publication_ready") is True:
                continue
            label = str(cell.get("label") or "Unnamed cell")
            level = str(cell.get("execution_level") or "unknown")
            warnings = cell.get("warnings") if isinstance(cell.get("warnings"), list) else []
            reason = "; ".join(str(w) for w in warnings[:2]) if warnings else "not publication-ready"
            blocked.append(f"{label}: {level}; {reason}")
    return blocked


def _format_report_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number) >= 100:
        return f"{number:.3g}"
    if abs(number) >= 1:
        return f"{number:.3f}".rstrip("0").rstrip(".")
    return f"{number:.4g}"


def _paper_draft_from_report_parts(
    *,
    title: str,
    plan: dict[str, Any],
    datasets: list[dict[str, Any]],
    citations: list[dict[str, Any]],
    ready_findings: list[str],
    blocked_cells: list[str],
    matrix_rows: list[dict[str, str]],
    fact_report: dict[str, Any],
    manifest: list[dict[str, Any]],
) -> str:
    """Create a conservative paper draft from verified report ingredients."""
    fact_status = str(fact_report.get("status") or "not_run") if isinstance(fact_report, dict) else "not_run"
    fact_blocked = fact_status == "blocked"
    dataset_text = (
        "\n".join(
            "- "
            + f"{dataset.get('display_name') or dataset.get('key')} "
            + f"({dataset.get('version') or 'version not recorded'}). "
            + (f"Source: {dataset.get('source_url')}." if dataset.get("source_url") else "")
            for dataset in datasets
        )
        if datasets
        else "- No dataset metadata was available in this draft package."
    )
    if not ready_findings:
        results_text = "- No numerical result is currently supported by a publication-ready or compressed-preliminary tool run."
    elif fact_blocked:
        results_text = (
            "- Fact check is BLOCKED for this turn — no numerical finding is cleared for Results. "
            "The values are listed under Section 5 (Robustness and Scope -> Needs Verification) and "
            "must not be cited as established results."
        )
    else:
        results_text = "\n".join(f"- {finding}" for finding in ready_findings)
    matrix_text = (
        "\n".join(
            "- {label}: {status}; {outputs}; {diagnostics}".format(**row)
            for row in matrix_rows
        )
        if matrix_rows
        else "- No research matrix cells were recorded in this draft package."
    )
    limitation_text = "\n".join(f"- {gap}" for gap in plan.get("blocking_gaps", []) if isinstance(gap, str))
    if blocked_cells:
        limitation_text += ("\n" if limitation_text else "") + "\n".join(f"- {cell}" for cell in blocked_cells)
    if fact_blocked and ready_findings:
        limitation_text += ("\n" if limitation_text else "") + "\n".join(
            ["### Needs Verification (fact check blocked)"]
            + [f"- {finding}" for finding in ready_findings]
            + ["- Resolve the blocking fact-check issues before treating any value above as a result."]
        )
    if not limitation_text:
        limitation_text = "- No blocking gap was recorded by the research planner."
    reference_text = (
        "\n".join(
            f"- {citation.get('label') or citation.get('title') or citation.get('arxiv') or citation.get('doi')}"
            for citation in citations
        )
        if citations
        else "- No bibliography entries were extracted from the supplied tool results."
    )
    return "\n".join([
        f"# Paper Draft: {title}",
        "",
        "## Abstract",
        (
            "We present an auditable Standard Astro research-mode analysis of the "
            "registered cosmology data combinations selected by the research plan. "
            "The draft reports only current-turn tool-supported claims, marks "
            "config-only or unavailable likelihood branches as limitations, and "
            f"records fact verification status as {fact_status}. Results labelled "
            "compressed-likelihood preliminary should not be treated as full "
            "external likelihood reproductions."
        ),
        "",
        "## 1. Introduction",
        str(plan.get("research_question") or "The research question was not recorded."),
        "",
        "## 2. Data",
        dataset_text,
        "",
        "## 3. Methods",
        (
            "The analysis follows the generated ResearchPlan. Each requested "
            "dataset combination is classified as runnable, config-only, or not "
            "available. Runnable compressed Gaussian likelihood cells are executed "
            "with recorded random seeds and diagnostics; config-only datasets are "
            "listed as scope gaps and are not used to support posterior, tension, "
            "or model-selection claims."
        ),
        "",
        "### Experiment Matrix",
        matrix_text,
        "",
        "## 4. Results",
        results_text,
        "",
        "## 5. Robustness and Scope",
        limitation_text,
        "",
        "## 6. Reproducibility",
        f"This draft records {len(manifest)} tool-run manifest entries. Use the accompanying reproducibility manifest before citing any numerical result.",
        "",
        "## References",
        reference_text,
        "",
    ]).strip() + "\n"


def _latest_fact_check_report(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(tool_results):
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        if isinstance(result.get("fact_check_report"), dict):
            return result["fact_check_report"]
        if result.get("analysis_status") == "FACT_CHECK_READY":
            return result
    return {}


def _iter_report_result_items(tool_results: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return top-level tool results plus nested research-matrix cell results."""
    items: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        items.append((item, result))
        if isinstance(result.get("matrix"), list):
            for idx, cell in enumerate(result["matrix"]):
                if not isinstance(cell, dict) or not isinstance(cell.get("result"), dict):
                    continue
                cell_item = {
                    "tool": f"{item.get('tool') or item.get('name') or 'run_research_matrix'}:{cell.get('label') or idx}",
                    "input": {
                        "dataset_keys": cell.get("dataset_keys"),
                        "model": cell.get("model"),
                    },
                }
                items.append((cell_item, cell["result"]))
    return items


def _report_datasets(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    datasets: list[dict[str, Any]] = []
    for _, result in _iter_report_result_items(tool_results):
        for key in ("datasets_used", "datasets_not_run"):
            for dataset in result.get(key, []) if isinstance(result.get(key), list) else []:
                if not isinstance(dataset, dict):
                    continue
                dataset_key = str(dataset.get("key") or dataset.get("dataset_key") or dataset.get("display_name") or "")
                if not dataset_key or dataset_key in seen:
                    continue
                seen.add(dataset_key)
                datasets.append({
                    "key": dataset_key,
                    "display_name": dataset.get("display_name") or dataset_key,
                    "version": dataset.get("version") or "",
                    "source_url": dataset.get("source_url") or "",
                    "citations": dataset.get("citations", []),
                })
        provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
        for dataset in provenance.get("datasets", []) if isinstance(provenance.get("datasets"), list) else []:
            if not isinstance(dataset, dict):
                continue
            dataset_key = str(dataset.get("service_key") or dataset.get("ivoid") or dataset.get("publisher") or "")
            if dataset_key and dataset_key not in seen:
                seen.add(dataset_key)
                datasets.append({
                    "key": dataset_key,
                    "display_name": dataset.get("service_name") or dataset_key,
                    "version": dataset.get("archive_version") or "",
                    "source_url": dataset.get("reference_url") or dataset.get("credits_page_url") or "",
                })
    return datasets


def _report_citations(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    citations: list[dict[str, Any]] = []
    for _, result in _iter_report_result_items(tool_results):
        for citation in result.get("citations", []) if isinstance(result.get("citations"), list) else []:
            if isinstance(citation, dict):
                key = str(citation.get("bibcode") or citation.get("arxiv") or citation.get("doi") or citation.get("label") or "")
                if key and key not in seen:
                    seen.add(key)
                    citations.append(dict(citation))
        for dataset in result.get("datasets_used", []) if isinstance(result.get("datasets_used"), list) else []:
            if not isinstance(dataset, dict):
                continue
            for citation in dataset.get("citations", []) if isinstance(dataset.get("citations"), list) else []:
                if isinstance(citation, dict):
                    key = str(citation.get("bibcode") or citation.get("arxiv") or citation.get("doi") or citation.get("label") or "")
                    if key and key not in seen:
                        seen.add(key)
                        citations.append(dict(citation))
    return citations


def _report_reproducibility_manifest(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for item, result in _iter_report_result_items(tool_results):
        envelope = result.get("reproducibility") if isinstance(result.get("reproducibility"), dict) else {}
        manifest.append({
            "tool": item.get("tool") or item.get("name") or result.get("tool") or "unknown",
            "analysis_status": result.get("analysis_status") or result.get("__tool_status__"),
            "publication_ready": bool(result.get("publication_ready")),
            "run_id": envelope.get("run_id"),
            "query_hash": envelope.get("query_hash") or result.get("runner_hash") or result.get("config_hash"),
            "random_seed": envelope.get("random_seed") or result.get("random_seed"),
            "tool_version": envelope.get("tool_version"),
        })
    return manifest


def _bibtex_from_citations(citations: list[dict[str, Any]]) -> str:
    entries: list[str] = []
    for index, citation in enumerate(citations, start=1):
        key = str(citation.get("bibcode") or citation.get("arxiv") or citation.get("doi") or f"ref{index}")
        key = re.sub(r"[^A-Za-z0-9_:-]", "_", key)
        title = str(citation.get("label") or citation.get("title") or key)
        year = str(citation.get("year") or "")
        fields = [f"  title = {{{title}}}"]
        if year:
            fields.append(f"  year = {{{year}}}")
        if citation.get("doi"):
            fields.append(f"  doi = {{{citation['doi']}}}")
        if citation.get("arxiv"):
            fields.append(f"  eprint = {{{citation['arxiv']}}}")
        entries.append("@misc{" + key + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def _required_probes(text: str) -> list[str]:
    prompt = text.lower()
    probes: list[str] = []
    if _is_cmb_rotation_question(prompt):
        probes.append("CMB_POLARIZATION_ROTATION")
        if _is_primary_cmb_rotation_question(prompt):
            return probes
    if _is_primordial_feature_question(prompt):
        probes.append("CMB_PRIMORDIAL_FEATURES")
    if any(tok in prompt for tok in ("bao", "baryon acoustic", "desi", "sdss", "boss", "eboss", "6df")):
        probes.append("BAO")
    if any(tok in prompt for tok in ("sn", "supernova", "pantheon", "des-sn", "union3")):
        probes.append("SN")
    if any(tok in prompt for tok in ("cmb", "planck", "act", "spt")):
        probes.append("CMB")
    if any(tok in prompt for tok in ("weak lensing", "weak-lensing", "cosmic shear", "kids", "des y3", "hsc", "s8", "sigma8")):
        probes.append("WL")
    if any(tok in prompt for tok in ("h0", "h₀", "sh0es", "trgb", "h0licow", "megamaser")):
        probes.append("H0")
    if "chronometer" in prompt or "h(z)" in prompt:
        probes.append("HZ")
    if any(tok in prompt for tok in (
        "rsd", "fσ8", "fσ₈", "fsigma8", "f sigma8", "f-sigma8",
        "growth rate", "growth of structure", "structure growth",
        "redshift-space", "redshift space distortion",
    )):
        probes.append("RSD")
    if _is_early_dark_energy_question(prompt) and not any(probe in probes for probe in ("BAO", "SN", "CMB", "WL", "H0", "HZ", "RSD")):
        probes.append("CMB")
    if (
        "[cii]" in prompt
        or "fwhm" in prompt
        or "lfr" in prompt
        or "线宽" in prompt
        or re.search(r"\bline(?:width|[-\s]width|[-\s]luminosity|[-\s]relation|[-\s]measurement|[-\s]flux|[-\s]property|[-\s]properties)?\b", prompt)
    ):
        probes.append("line_measurements")
    if not probes and any(tok in prompt for tok in ("cosmology", "宇宙学", "dark energy", "暗能量")):
        probes = ["BAO", "SN", "CMB"]
    return list(dict.fromkeys(probes))


def _candidate_datasets(probes: list[str], text: str) -> list[str]:
    prompt = text.lower()
    keys: list[str] = []
    if "CMB_POLARIZATION_ROTATION" in probes:
        keys.extend(default_cmb_rotation_dataset_keys())
    if "BAO" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["pre_desi_bao"] if "pre-desi" in prompt or "pre desi" in prompt else COSMOLOGY_PROBE_DATASETS["bao"])
    if "SN" in probes:
        if any(tok in prompt for tok in ("compare", "比较", "robust", "consistency", "union3", "des-sn")):
            keys.extend(COSMOLOGY_PROBE_DATASETS["sn_comparison"])
        else:
            keys.extend(COSMOLOGY_PROBE_DATASETS["sn"])
    if (
        "CMB" in probes
        and "CMB_PRIMORDIAL_FEATURES" not in probes
        and not _is_primary_cmb_rotation_question(prompt)
    ):
        keys.extend(COSMOLOGY_PROBE_DATASETS["cmb"])
        if "act" in prompt:
            keys.extend(COSMOLOGY_PROBE_DATASETS["cmb_lensing"])
    if "WL" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["wl"])
    if "H0" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["h0_anchors"] if "compare" in prompt or "比较" in prompt else COSMOLOGY_PROBE_DATASETS["h0"])
    if "HZ" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["hz"])
    if "RSD" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["rsd"])
    return _clean_dataset_keys(keys)


def _models_from_question(text: str) -> list[str]:
    prompt = text.lower()
    models: list[str] = []
    if _is_primary_cmb_rotation_question(prompt):
        return ["isotropic_beta"]
    if "lcdm" in prompt or "λcdm" in prompt:
        models.append("lcdm")
    if "wcdm" in prompt:
        models.append("wcdm")
    if "w0wa" in prompt or "cpl" in prompt:
        models.append("w0wa_cdm")
    if "curvature" in prompt or "omega_k" in prompt or "曲率" in prompt:
        models.append("ok_lcdm")
    if "mnu" in prompt or "neutrino" in prompt or "中微子" in prompt:
        models.append("lcdm_mnu")
    if not models:
        models = ["lcdm"]
    if any(tok in prompt for tok in ("dark energy", "暗能量", "model comparison", "模型比较")):
        for model in ("lcdm", "wcdm", "w0wa_cdm"):
            if model not in models:
                models.append(model)
    if any(model != "lcdm" for model in models) and "lcdm" not in models:
        models.insert(0, "lcdm")
    return _clean_models(models)


def _hypotheses_from_question(text: str, probes: list[str], models: list[str]) -> list[str]:
    prompt = text.lower()
    hypotheses: list[str] = []
    if "tension" in prompt or "张力" in prompt or "consistency" in prompt:
        hypotheses.append("Quantify whether selected probes are mutually consistent within registered likelihood approximations.")
    if "dark energy" in prompt or "暗能量" in prompt or "w0wa" in models:
        hypotheses.append("Test whether extended dark-energy models are supported beyond flat ΛCDM by runnable data combinations.")
    if "H0" in probes:
        hypotheses.append("Compare late-universe H0 anchors against any selected early-universe or BAO-calibrated constraints.")
    if "WL" in probes or "s8" in prompt:
        hypotheses.append("Check whether weak-lensing S8 summaries shift relative to CMB compressed constraints.")
    if "CMB_POLARIZATION_ROTATION" in probes:
        hypotheses.append(
            "Assess whether registered CMB-polarization EB/TB data products and angle-calibration priors are available before any rotation-angle inference."
        )
    if "line_measurements" in probes:
        hypotheses.append("Compile cited measurement rows before fitting any line-luminosity relation.")
    return hypotheses or ["Determine which parts of the requested research question are executable with registered data products."]


def _is_cmb_rotation_question(prompt: str) -> bool:
    """Detect CMB-polarization rotation/birefringence requests.

    Planck compressed distance priors are valid CMB *distance* summaries, but
    they cannot support EB/TB parity-odd polarization rotation claims.  Keep
    this detector intentionally narrow so ordinary BAO+CMB robustness prompts
    still route to the compressed Planck likelihood.
    """
    has_rotation_intent = any(
        tok in prompt
        for tok in (
            "birefringence",
            "polarization rotation",
            "polarization-rotation",
            "polarisation rotation",
            "polarisation-rotation",
            "rotation angle",
            "rotation-angle",
            "rotation field",
            "rotation-field",
            "偏振旋转",
            "旋转角",
            "宇称破缺",
            "parity violation",
            "parity-violating",
            "parity violating",
            "instrument-angle",
            "instrument angle",
        )
    )
    has_polarization_observable = any(
        tok in prompt
        for tok in (
            "polarization",
            "polarisation",
            "b-mode",
            "b mode",
            "eb",
            "tb",
            "cmb",
            "map",
            "maps",
            "bandpower",
            "bandpowers",
            "estimator",
            "偏振",
            "奇宇称",
        )
    )
    return has_rotation_intent and has_polarization_observable


def _is_primary_cmb_rotation_question(prompt: str) -> bool:
    """Return True when polarization rotation is the actual requested result.

    Blind-test prompts often mention DESI/Pantheon+/SH0ES as datasets present
    in a paper, but ask specifically for EB/TB spectra, calibration priors, and
    rotation-angle support.  In that situation BAO/SN/H0 compressed runners are
    the wrong object: they can constrain distances, not CMB polarization
    rotation.  Only keep late-time probes when the user explicitly asks for an
    expansion/H0/dark-energy posterior in addition to the rotation workflow.
    """
    if not _is_cmb_rotation_question(prompt):
        return False
    rotation_workflow_terms = (
        "birefringence",
        "polarization rotation",
        "polarization-rotation",
        "polarisation rotation",
        "polarisation-rotation",
        "eb/tb",
        "tb/eb",
        "eb and tb",
        "tb and eb",
        "anisotropic",
        "isotropic",
        "parity-odd",
        "parity odd",
        "angle-calibration",
        "angle calibration",
        "instrument-angle",
        "instrument angle",
        "distance priors as polarization evidence",
        "rotation likelihood",
        "rotation-angle likelihood",
        "rotation angle likelihood",
    )
    explicit_late_time_objective = (
        "late-time expansion",
        "late time expansion",
        "expansion history",
        "h0 tension",
        "h₀ tension",
        "compare h0",
        "compare h₀",
        "infer h0",
        "infer h₀",
        "h0 posterior",
        "h₀ posterior",
        "h0 and dark energy",
        "h₀ and dark energy",
        "dark-energy posterior",
        "dark energy posterior",
        "dark-energy constraints",
        "dark energy constraints",
        "distance posterior",
        "bao+sn+cmb",
        "bao + sn + cmb",
    )
    return any(term in prompt for term in rotation_workflow_terms) and not any(
        term in prompt for term in explicit_late_time_objective
    )


def _is_primordial_feature_question(prompt: str) -> bool:
    has_feature_intent = any(
        tok in prompt
        for tok in (
            "primordial feature",
            "primordial-feature",
            "sharp-feature",
            "resonant-feature",
            "oscillatory feature",
            "oscillatory primordial",
            "primordial oscillation",
            "oscillatory residual",
            "feature template",
            "feature-search",
            "look-elsewhere",
            "look elsewhere",
            "frequency scan",
            "frequency/phase scan",
            "inflationary anomaly",
            "inflationary anomalies",
            "inflationary feature",
            "振荡",
            "原初",
        )
    )
    has_spectra_context = any(
        tok in prompt
        for tok in (
            "cmb",
            "temperature",
            "polarization",
            "polarisation",
            "tt",
            "te",
            "ee",
            "spectra",
            "spectrum",
            "power spectrum",
            "power-spectrum",
            "bandpower",
            "功率谱",
        )
    )
    return has_feature_intent and has_spectra_context


def _is_early_dark_energy_question(prompt: str) -> bool:
    return any(
        tok in prompt
        for tok in (
            "early dark energy",
            "early-dark-energy",
            "early energy",
            "early-energy",
            "transient early-energy",
            "transient early energy",
            "pre-recombination",
            "before recombination",
            " ede",
            "ede ",
            "axion-like early",
            "axion like early",
        )
    )


def _is_modified_gravity_question(prompt: str) -> bool:
    return any(
        tok in prompt
        for tok in (
            "modified gravity",
            "modified-gravity",
            "modified-gravity interpretation",
            "gravity model",
            "growth model",
            "growth-rate",
            "growth-index",
            "growth index",
            "growth likelihood",
            "growth than planck",
            "differs from gr",
            "f(r)",
            "mu(k",
            "mg likelihood",
            "修正引力",
        )
    )


def _is_physical_dark_energy_history_question(prompt: str) -> bool:
    return any(tok in prompt for tok in ("thawing", "emergent", "mirage", "physical dark-energy", "physical dark energy"))


def _proposed_experiment_matrix(dataset_keys: list[str], models: list[str], text: str) -> list[dict[str, Any]]:
    keys = _clean_dataset_keys(dataset_keys)
    if not keys:
        return []
    prompt = text.lower()
    rotation_keys = [key for key in keys if cmb_rotation_dataset_exists(key)]
    non_rotation_keys = [key for key in keys if not cmb_rotation_dataset_exists(key)]
    if _is_cmb_rotation_question(prompt) and not rotation_keys:
        rotation_keys = default_cmb_rotation_dataset_keys()
    if _is_primary_cmb_rotation_question(prompt) or (rotation_keys and not non_rotation_keys):
        return [{
            "label": "CMB rotation — isotropic beta",
            "dataset_keys": rotation_keys,
            "model": "isotropic_beta",
        }]
    matrix_keys = non_rotation_keys if rotation_keys else keys
    model_list = [model for model in _clean_models(models) if model != "isotropic_beta"] or ["lcdm"]
    extended_models = [model for model in model_list if model != "lcdm"]
    special_model_gap = _has_dedicated_model_gap(prompt)
    baseline_model = "lcdm" if extended_models or special_model_gap else model_list[0]
    combos: list[tuple[str, list[str]]] = []
    bao = [key for key in matrix_keys if get_cosmology_dataset(key).probe == "bao"]
    sn = [key for key in matrix_keys if get_cosmology_dataset(key).probe == "sn"]
    cmb = [key for key in matrix_keys if get_cosmology_dataset(key).probe in {"cmb_compressed", "cmb_lensing"}]
    wl = [key for key in matrix_keys if get_cosmology_dataset(key).probe == "weak_lensing"]
    h0 = [key for key in matrix_keys if get_cosmology_dataset(key).probe == "h0_prior"]
    cc = [key for key in matrix_keys if get_cosmology_dataset(key).probe == "hz"]
    rsd = [key for key in matrix_keys if get_cosmology_dataset(key).probe == "rsd"]
    if bao:
        combos.append(("BAO only", bao[:1]))
    if sn:
        combos.append(("SN only", sn[:1]))
    if cmb:
        combos.append(("CMB only", cmb[:1]))
    if cc:
        combos.append(("CC H(z) only", cc[:1]))
    if rsd:
        combos.append(("RSD fσ8 only", rsd[:1]))
    if bao and cmb:
        combos.append(("BAO + CMB", bao[:1] + cmb[:1]))
    if bao and sn:
        combos.append(("BAO + SN", bao[:1] + sn[:1]))
    if sn and cmb:
        combos.append(("SN + CMB", sn[:1] + cmb[:1]))
    if bao and wl:
        combos.append(("BAO + WL", bao[:1] + wl))
    if bao and cc:
        combos.append(("BAO + CC H(z)", bao[:1] + cc[:1]))
    if bao and rsd:
        combos.append(("BAO + RSD fσ8", bao[:1] + rsd[:1]))
    if bao and sn and cmb:
        combos.append(("BAO + SN + CMB", bao[:1] + sn[:1] + cmb[:1]))
    if h0:
        for label, combo in list(combos):
            if h0[0] not in combo:
                combos.append((f"{label} + H0 prior", combo + h0[:1]))
    if not combos:
        combos.append(("All selected probes", matrix_keys))
    seen: set[tuple[str, ...]] = set()
    matrix: list[dict[str, Any]] = []
    for label, combo in combos:
        cleaned = _clean_dataset_keys(combo)
        marker = tuple(cleaned)
        if not cleaned or marker in seen:
            continue
        seen.add(marker)
        cell_label = f"ΛCDM baseline — {label}" if extended_models or special_model_gap else label
        matrix.append({
            "label": cell_label,
            "dataset_keys": cleaned,
            "model": baseline_model,
            "baseline_only": bool(extended_models or special_model_gap),
        })
    if extended_models:
        for model in extended_models:
                matrix.append({
                    "label": f"Requested {model} branch",
                    "dataset_keys": matrix_keys,
                    "model": model,
                    "requested_model_branch": True,
                })
    if rotation_keys:
        matrix.append({
            "label": "CMB rotation — isotropic beta",
            "dataset_keys": rotation_keys,
            "model": "isotropic_beta",
        })
    return matrix


def _has_dedicated_model_gap(prompt: str) -> bool:
    return (
        _is_early_dark_energy_question(prompt)
        or _is_modified_gravity_question(prompt)
        or _is_physical_dark_energy_history_question(prompt)
    )


def _dataset_plan_status(entry: dict[str, Any]) -> dict[str, Any]:
    mode = str(entry.get("execution_mode") or "config_only")
    has_compressed = bool(entry.get("compressed_likelihood")) or entry.get("key") == "desi_dr1_bao"
    level = "compressed_preliminary" if mode == "compressed_gaussian" or has_compressed else "config_only"
    if mode in {"external_cobaya", "external_cosmosis"} and not has_compressed:
        level = "config_only"
    return {
        "key": entry.get("key"),
        "display_name": entry.get("display_name"),
        "probe": entry.get("probe"),
        "execution_mode": mode,
        "execution_level": level,
        "claimable_parameters": _claimable_params_for_entry(entry),
        "version": entry.get("version"),
        "citations": entry.get("citations", []),
    }


def _claimable_params_for_entry(entry: dict[str, Any]) -> list[str]:
    if isinstance(entry.get("claimable_parameters"), list):
        return [str(param) for param in entry.get("claimable_parameters") or []]
    spec = entry.get("compressed_likelihood")
    if isinstance(spec, dict):
        return list(spec.get("parameters") or [])
    # Executable-without-compressed-likelihood probes (dedicated data-vector χ²
    # paths in cosmology_likelihoods): the runner produces quotable posteriors
    # for these even though they carry no CompressedLikelihoodSpec.
    executable_claimables = {
        "desi_dr1_bao": ["H0", "omegam", "rd", "H0_rd"],
        "sdss_6df_bao": ["H0", "omegam", "rd", "H0_rd"],
        "cosmic_chronometers": ["H0", "omegam"],
        "eboss_dr16_rsd": ["omegam", "sigma8"],
    }
    return executable_claimables.get(str(entry.get("key")), [])


def _non_executable_dataset_keys(dataset_keys: list[str]) -> list[str]:
    """Datasets that must not be silently approximated in a matrix cell."""
    blocked: list[str] = []
    for key in dataset_keys:
        if cmb_rotation_dataset_exists(key):
            if not get_cmb_rotation_dataset(key).is_executable:
                blocked.append(key)
            continue
        try:
            entry = get_cosmology_dataset(key).to_dict()
        except Exception:
            blocked.append(key)
            continue
        if _dataset_plan_status(entry).get("execution_level") == "config_only":
            blocked.append(key)
    return blocked


def _blocking_gaps(dataset_status: list[dict[str, Any]], models: list[str]) -> list[str]:
    gaps: list[str] = []
    for entry in dataset_status:
        if entry.get("execution_level") == "config_only":
            gaps.append(f"{entry.get('display_name') or entry.get('key')} requires external Cobaya/CosmoSIS or a future runner before posterior claims.")
    if any(model != "lcdm" for model in models):
        gaps.append("Extended models are planned/configurable, but phase-1 compressed execution is publication-ready for ΛCDM only.")
    return gaps


def _question_specific_blocking_gaps(text: str, probes: list[str]) -> list[str]:
    prompt = text.lower()
    gaps: list[str] = []
    if "CMB_POLARIZATION_ROTATION" in probes or _is_cmb_rotation_question(prompt):
        gaps.append(
            "CMB polarization-rotation inference is not executable with Planck compressed distance priors. "
            "A valid run requires EB/TB bandpowers or maps, their covariance, an instrument-angle calibration prior, "
            "and a dedicated rotation-angle likelihood."
        )
    if "CMB_PRIMORDIAL_FEATURES" in probes or _is_primordial_feature_question(prompt):
        gaps.append(
            "Primordial-feature inference is not executable with Planck compressed distance priors or ACT lensing summaries. "
            "A valid run requires TT/TE/EE spectra, the spectra covariance, an explicit oscillatory-feature template, "
            "a frequency/phase scan, and a look-elsewhere calibration."
        )
    if _is_early_dark_energy_question(prompt):
        gaps.append(
            "Early-dark-energy / axion-like EDE inference is not executable in phase-1 compressed runners. "
            "It requires an EDE model implementation in a Boltzmann solver plus a controlled full CMB/BAO/SN likelihood."
        )
    if _is_modified_gravity_question(prompt):
        gaps.append(
            "Modified-gravity growth inference is not executable in the phase-1 compressed runners. "
            "It requires a model-specific growth solver, growth-rate/shear likelihoods, and validated covariance products."
        )
    if _is_physical_dark_energy_history_question(prompt):
        gaps.append(
            "Thawing, emergent, or mirage dark-energy histories are not executable in phase-1 compressed runners. "
            "They require dedicated parameterizations or scalar-field model runners before posterior claims."
        )
    return gaps


def _capability_gap_matrix(
    *,
    text: str,
    probes: list[str],
    dataset_status: list[dict[str, Any]],
    models: list[str],
    blocking_gaps: list[str],
) -> list[dict[str, Any]]:
    """Explain why a blind-test prompt is runnable, partial, or blocked.

    The matrix is intentionally about *capabilities*, not paper conclusions.  A
    B-level partial pass in the blind-test harness means the system identified
    the right scientific workflow and either ran a controlled baseline or named
    the exact missing likelihood/data-vector pieces.  It does not mean the
    hidden paper result was reproduced.
    """
    prompt = text.lower()
    rows: list[dict[str, Any]] = []
    for entry in dataset_status:
        level = str(entry.get("execution_level") or "config_only")
        rows.append({
            "component": f"dataset:{entry.get('key')}",
            "category": "dataset",
            "status": "available" if level == "compressed_preliminary" else "config_only",
            "details": (
                f"{entry.get('display_name') or entry.get('key')} "
                f"({entry.get('execution_mode') or 'unknown mode'})"
            ),
            "claim_support": (
                "can support compressed-preliminary claims"
                if level == "compressed_preliminary"
                else "metadata/config only; cannot support posterior claims"
            ),
        })

    if "CMB_POLARIZATION_ROTATION" in probes or _is_cmb_rotation_question(prompt):
        for key in default_cmb_rotation_dataset_keys():
            entry = get_cmb_rotation_dataset(key)
            rows.append({
                "component": f"cmb_rotation_data:{key}",
                "category": "data_product",
                "status": "available" if entry.is_executable else "registered_config_only",
                "details": entry.display_name,
                "claim_support": (
                    "can support beta_deg only through the CMB rotation runner"
                    if entry.is_executable
                    else "registered planning target; missing executable EB/TB likelihood pieces"
                ),
            })
            rows.extend([
                {
                    "component": f"{key}:covariance",
                    "category": "covariance",
                    "status": "available" if entry.covariance_provided else "missing",
                    "details": "EB/TB covariance for rotation-angle inference",
                    "claim_support": "required before beta_deg claims",
                },
                {
                    "component": f"{key}:calibration_prior",
                    "category": "calibration",
                    "status": "available" if entry.calibration_prior else "missing",
                    "details": "instrument-angle calibration prior",
                    "claim_support": "required before beta_deg claims",
                },
                {
                    "component": f"{key}:rotation_likelihood",
                    "category": "likelihood",
                    "status": "available" if entry.is_executable else "missing",
                    "details": "dedicated EB/TB rotation-angle likelihood",
                    "claim_support": "required before beta_deg claims",
                },
            ])

    if "CMB_PRIMORDIAL_FEATURES" in probes or _is_primordial_feature_question(prompt):
        rows.extend([
            _missing_capability("cmb_spectra:tt_te_ee", "data_product", "TT/TE/EE spectra or bandpowers for feature searches"),
            _missing_capability("cmb_spectra:covariance", "covariance", "spectra covariance for template fitting"),
            _missing_capability("primordial_feature:template_bank", "model", "sharp/resonant/oscillatory feature templates"),
            _missing_capability("primordial_feature:frequency_phase_scan", "sampler", "frequency/phase scan with look-elsewhere accounting"),
            _missing_capability("primordial_feature:look_elsewhere", "diagnostic", "global significance calibration"),
        ])

    if _is_early_dark_energy_question(prompt):
        rows.extend([
            _missing_capability("ede:boltzmann_model", "model", "EDE/axion-like component in a controlled Boltzmann solver"),
            _missing_capability("ede:full_cmb_likelihood", "likelihood", "full CMB likelihood or validated emulator"),
            _partial_capability("ede:lcdm_baseline", "baseline", "ΛCDM compressed baseline may run, but it is not an EDE test"),
        ])

    if _is_modified_gravity_question(prompt):
        rows.extend([
            _missing_capability("modified_gravity:growth_solver", "model", "model-specific growth solver"),
            _missing_capability("modified_gravity:growth_likelihood", "likelihood", "RSD/shear likelihood wired to the modified-gravity model"),
            _partial_capability("modified_gravity:lcdm_baseline", "baseline", "background/WL/RSD compressed summaries can expose a baseline gap only"),
        ])

    if _is_physical_dark_energy_history_question(prompt):
        rows.extend([
            _missing_capability("physical_dark_energy:history_runner", "model", "thawing/emergent/mirage dark-energy history runner"),
            _partial_capability("physical_dark_energy:distance_baseline", "baseline", "BAO/SN/CMB distance baselines are available only as compressed preliminary tests"),
        ])

    if any(model != "lcdm" and model != "isotropic_beta" for model in models):
        rows.append({
            "component": "extended_model_publication_runner",
            "category": "model",
            "status": "partial",
            "details": "Extended models are planned/configurable; phase-1 claimable matrices promote ΛCDM compressed baselines.",
            "claim_support": "does not support headline extended-model posterior claims",
        })

    if blocking_gaps and not rows:
        for idx, gap in enumerate(blocking_gaps[:5], start=1):
            rows.append({
                "component": f"blocking_gap:{idx}",
                "category": "scope",
                "status": "missing",
                "details": gap,
                "claim_support": "gap statement only",
            })
    return rows


def _missing_capability(component: str, category: str, details: str) -> dict[str, Any]:
    return {
        "component": component,
        "category": category,
        "status": "missing",
        "details": details,
        "claim_support": "cannot support numerical claims until implemented/registered",
    }


def _partial_capability(component: str, category: str, details: str) -> dict[str, Any]:
    return {
        "component": component,
        "category": category,
        "status": "partial",
        "details": details,
        "claim_support": "can support scope/baseline statements only",
    }


def _partial_pass_readiness(
    *,
    probes: list[str],
    dataset_status: list[dict[str, Any]],
    matrix: list[dict[str, Any]],
    blocking_gaps: list[str],
    capability_gap_matrix: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a transparent blind-test B-level readiness assessment.

    This is not a scientific score.  It encodes the acceptance rule for the
    hidden-paper blind-test harness: a partial pass is allowed when Standard
    Astro either executes a relevant controlled baseline or produces an exact
    not-runnable gap map without unsupported numerical claims.
    """
    recognized_workflow = bool(probes)
    runnable_candidates = [
        str(entry.get("key"))
        for entry in dataset_status
        if entry.get("execution_level") == "compressed_preliminary"
    ]
    domain_gap_rows = [
        row
        for row in capability_gap_matrix
        if str(row.get("status")) in {"missing", "registered_config_only", "config_only", "partial"}
    ]
    has_domain_gap_map = bool(domain_gap_rows and blocking_gaps)
    has_experiment_matrix = bool(matrix)
    meets_partial = recognized_workflow and (
        bool(runnable_candidates) or has_domain_gap_map or has_experiment_matrix
    )

    criteria_met: list[str] = []
    missing_criteria: list[str] = []
    if recognized_workflow:
        criteria_met.append("recognized science workflow/probe family")
    else:
        missing_criteria.append("recognized science workflow/probe family")
    if runnable_candidates:
        criteria_met.append("at least one controlled compressed/preliminary dataset candidate")
    elif has_domain_gap_map:
        criteria_met.append("domain-specific capability gap map with named missing pieces")
    elif has_experiment_matrix:
        criteria_met.append("experiment matrix generated for follow-up execution")
    else:
        missing_criteria.append("runnable baseline or precise missing-capability matrix")

    return {
        "target": "B_OR_BETTER_PARTIAL_PASS_95",
        "meets_partial_pass": meets_partial,
        "score_floor": "B" if meets_partial else "C",
        "coverage_status": (
            "runnable_baseline_available"
            if runnable_candidates
            else "domain_gap_mapped"
            if has_domain_gap_map
            else "matrix_planned"
            if has_experiment_matrix
            else "not_enough_evidence"
        ),
        "criteria_met": criteria_met,
        "missing_criteria": missing_criteria,
        "runnable_candidate_keys": runnable_candidates,
        "gap_component_count": len(domain_gap_rows),
        "important_note": (
            "B-level partial pass means correct routing plus controlled baseline or exact scope gap; "
            "it does not mean the hidden paper conclusion was reproduced."
        ),
    }


def _overall_executable_level(dataset_status: list[dict[str, Any]]) -> str:
    if not dataset_status:
        return "not_available"
    levels = {str(item.get("execution_level")) for item in dataset_status}
    if levels == {"compressed_preliminary"}:
        return "compressed_preliminary"
    if "compressed_preliminary" in levels:
        return "mixed"
    return "config_only"


def _datasets_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    for key in ("datasets", "datasets_used", "datasets_not_run"):
        value = result.get(key)
        if isinstance(value, list):
            datasets.extend(item for item in value if isinstance(item, dict))
    plan = result.get("research_plan")
    if isinstance(plan, dict):
        datasets.extend(item for item in plan.get("candidate_datasets", []) if isinstance(item, dict))
    matrix = result.get("matrix")
    if isinstance(matrix, list):
        for cell in matrix:
            if isinstance(cell, dict) and isinstance(cell.get("result"), dict):
                datasets.extend(_datasets_from_result(cell["result"]))
    return datasets


def _claimable_parameters_from_result(result: dict[str, Any]) -> list[str]:
    params: list[str] = []
    for source_key in ("parameters", "posterior_summary", "derived_params"):
        source = result.get(source_key)
        if isinstance(source, dict):
            params.extend(str(key) for key in source)
    for tension in result.get("pairwise_tensions", []) if isinstance(result.get("pairwise_tensions"), list) else []:
        if isinstance(tension, dict) and tension.get("parameter"):
            params.append(str(tension["parameter"]) + "_tension")
    matrix = result.get("matrix")
    if isinstance(matrix, list):
        for cell in matrix:
            if (
                isinstance(cell, dict)
                and cell.get("publication_ready") is True
                and isinstance(cell.get("result"), dict)
            ):
                params.extend(_claimable_parameters_from_result(cell["result"]))
    return list(dict.fromkeys(params))


def _numeric_claim_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    patterns = {
        "H0": r"\bH\s*0\b|\bH0\b|H₀",
        "omegam": r"Ωm|Omega_m|omegam",
        "S8": r"\bS8\b",
        "sigma8": r"sigma8|σ8",
        "w0": r"\bw0\b|w_0",
        "wa": r"\bwa\b|w_a",
        "beta_deg": r"\b(?:beta|β|alpha|α)(?:_?deg)?\b",
        "A_CB": r"\bA[_\s-]?CB\b",
        "slope": r"slope|斜率",
        "scatter": r"scatter|intrinsic scatter|离散",
        "p_value": r"\bp(?:[-\s]?value)?\s*[=<>]",
    }
    for name, pattern in patterns.items():
        token_key = name.lower()
        if re.search(pattern, text, re.I) and _numeric_value_for_token(text, token_key) is not None:
            tokens.append(name)
    if _has_tension_significance_claim(text):
        tokens.append("tension")
    return tokens


def _has_tension_significance_claim(text: str) -> bool:
    """Return True only for an actual n-sigma *tension* claim, not an error bar.

    A bare "N sigma" — e.g. "67.3 +/- 1sigma", "< 1sigma apart", "within
    2sigma" — is an uncertainty / agreement statement, NOT a tension. We only
    treat an n-sigma number as a tension-significance claim when explicit
    tension / discrepancy wording is present in the same line. This stops
    uncertainty notation (the dominant noise source) from being flagged.
    """
    if not re.search(r"tension|张力|discrepan", text, re.I):
        return False
    return bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:σ|sigma)\b", text, re.I))


def _fact_claims_from_reply(
    text: str,
    *,
    claimable: set[str],
    payload_text: str,
    dataset_index: dict[str, dict[str, Any]],
    ready_tools: set[str],
    numeric_support: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    if not text.strip():
        return claims
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _line_is_markdown_structure(stripped):
            continue
        lowered = stripped.lower()
        if _line_is_gap_statement(lowered):
            continue
        for token in _numeric_claim_tokens(stripped):
            token_key = token.lower()
            if token_key in claimable or _token_supported_by_alias(token_key, claimable):
                claimed_value = _numeric_value_for_token(stripped, token_key)
                value_status, value_rewrite, evidence_ids = _verify_claimed_numeric_value(
                    token_key,
                    claimed_value,
                    numeric_support,
                    sorted(ready_tools)[:5],
                )
                claims.append(_fact_claim(
                    stripped,
                    "numeric",
                    value_status,
                    "tool_run",
                    evidence_ids,
                    value_rewrite,
                ))
            else:
                claims.append(_fact_claim(
                    stripped,
                    "numeric",
                    "unsupported",
                    "not_applicable",
                    [],
                    f"Remove the {token} value/significance claim, or state that it was not determined by this turn's publication-ready tools.",
                ))
        if re.search(r"\bfull\s+(?:external\s+)?(?:planck|act|cobaya|cosmosis|likelihood)\b", stripped, re.I):
            has_full = "full_external_likelihood" in claimable
            claims.append(_fact_claim(
                stripped,
                "scope",
                "verified" if has_full else "contradicted",
                "tool_run" if has_full else "not_applicable",
                sorted(ready_tools)[:5] if has_full else [],
                "" if has_full else "Say this is compressed-likelihood preliminary, not a full external likelihood reproduction.",
            ))
        elif re.search(r"\bcompressed[-\s]?likelihood preliminary\b", stripped, re.I):
            claims.append(_fact_claim(
                stripped,
                "scope",
                "verified" if ready_tools else "partial",
                "tool_run" if ready_tools else "dataset_registry",
                sorted(ready_tools)[:5],
                "" if ready_tools else "Keep the compressed-preliminary caveat, but avoid posterior numbers until a publication-ready run exists.",
            ))
        for dataset_key, dataset in dataset_index.items():
            display = str(dataset.get("display_name") or dataset_key)
            if display and display.lower() in lowered:
                claims.append(_fact_claim(
                    stripped,
                    "dataset",
                    "verified",
                    "dataset_registry",
                    [f"dataset:{dataset_key}"],
                    "",
                ))
    for pattern, kind in (
        (ARXIV_ID_RE, "source"),
        (DOI_RE, "source"),
        (BIBCODE_RE, "source"),
    ):
        for match in pattern.finditer(text):
            value = match.group(0)
            supported = value.lower() in payload_text
            claims.append(_fact_claim(
                value,
                kind,
                "verified" if supported else "unsupported",
                "paper_metadata" if supported else "not_applicable",
                _source_evidence_ids(value, payload_text),
                "" if supported else f"Remove citation/source {value}, or run a source lookup/table extraction that returns it this turn.",
            ))
    return _dedupe_fact_claims(claims)


def _fact_claim(
    text: str,
    kind: str,
    status: str,
    support_level: str,
    evidence_ids: list[str],
    safe_rewrite: str,
) -> dict[str, Any]:
    return {
        "text": text,
        "kind": kind,
        "status": status,
        "support_level": support_level,
        "evidence_ids": evidence_ids,
        "safe_rewrite": safe_rewrite,
    }


def _line_is_gap_statement(line: str) -> bool:
    return any(
        phrase in line
        for phrase in (
            "cannot support",
            "does not support",
            "cannot use",
            "did not determine",
            "not determined",
            "not yet supported",
            "not publication-ready",
            "not publication-grade",
            "no publication-ready",
            "cannot claim",
            "requires external",
            "requires future runner",
            "requires a dedicated",
            "requires dedicated",
            "would be needed",
            "would be required",
            "would require",
            "would need",
            "is needed for",
            "are needed for",
            "needed for",
            "missing covariance",
            "missing likelihood",
            "missing runner",
            "were not run",
            "was not run",
            "was not used",
            "were not used",
            "not run here",
            "no full",
            "not the full",
            "not a full external",
            "not a full weak-lensing",
            "not a full weak lensing",
            "not a full shear",
            "not a birefringence constraint",
            "not a primordial-feature constraint",
            "not a feature-template constraint",
            "not making",
            "not treating",
            "not a publication-grade",
            "not publication grade",
            "not full external",
            "should not be treated as full",
            "only a compressed-likelihood",
            "supports only a compressed-likelihood",
            "still outside",
            "outside the compressed preliminary layer",
            "config-only",
            "scope gap",
            "exploratory only",
            "not claimable",
        )
    )


def _line_is_markdown_structure(line: str) -> bool:
    """Markdown headings and table separator rows are layout, not claims.

    e.g. "## 1.2 Executed cells", "### Cell A — DESI BAO", or "|---|:--:|---|".
    Skipping them stops the fact verifier from extracting a heading or a table
    rule as if it were a scientific claim.
    """
    if re.match(r"^#{1,6}\s", line):
        return True
    if re.match(r"^\|?[\s:|-]+\|?$", line) and ("-" in line or ":" in line):
        return True
    return False


def _token_supported_by_alias(token: str, claimable: set[str]) -> bool:
    aliases = {
        "omegam": {"omegam", "omega_m", "om0", "Ωm".lower()},
        "omega_m": {"omegam", "omega_m", "om0", "Ωm".lower()},
        "h0": {"h0", "H0".lower()},
        "beta_deg": {"beta_deg", "beta", "β", "alpha", "α"},
        "a_cb": {"a_cb", "acb"},
        "tension": {"tension", "s8_tension", "h0_tension", "omegam_tension"},
        "p_value": {"p_value", "pearson_p", "spearman_p"},
    }
    candidates = aliases.get(token, {token})
    return bool(candidates & claimable)


def _numeric_support_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Collect scalar summaries from publication-ready current-turn results."""
    support: dict[str, list[dict[str, Any]]] = {}
    for idx, item in enumerate(tool_results):
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        if result.get("publication_ready") is not True or result.get("__do_not_claim__") is True:
            continue
        tool_id = str(item.get("id") or f"tool_run:{idx}:{item.get('tool') or item.get('name') or 'tool'}")
        _collect_numeric_support_from_result(result, tool_id, support)
    return support


def _collect_numeric_support_from_result(
    result: dict[str, Any],
    tool_id: str,
    support: dict[str, list[dict[str, Any]]],
) -> None:
    for source_name in ("parameters", "posterior_summary", "derived_params"):
        source = result.get(source_name)
        if not isinstance(source, dict):
            continue
        for raw_name, summary in source.items():
            if not isinstance(summary, dict):
                continue
            median = _coerce_float(
                summary.get("median")
                if summary.get("median") is not None
                else summary.get("mean")
                if summary.get("mean") is not None
                else summary.get("value")
            )
            if median is None:
                continue
            record = {
                "parameter": str(raw_name),
                "median": median,
                "hdi_94": summary.get("hdi_94"),
                "evidence_id": tool_id,
                "source": source_name,
            }
            for alias in _parameter_aliases(str(raw_name)):
                support.setdefault(alias, []).append(record)
    pairwise_tensions = result.get("pairwise_tensions")
    if isinstance(pairwise_tensions, list):
        for index, item in enumerate(pairwise_tensions):
            if not isinstance(item, dict):
                continue
            sigma = _coerce_float(item.get("sigma"))
            parameter = str(item.get("parameter") or "").strip()
            if sigma is None or not parameter:
                continue
            record = {
                "parameter": f"{parameter}_tension",
                "median": sigma,
                "hdi_94": None,
                "evidence_id": f"{tool_id}:pairwise_tension:{index}",
                "source": "pairwise_tensions",
            }
            support.setdefault("tension", []).append(record)
            for alias in _parameter_aliases(f"{parameter}_tension"):
                support.setdefault(alias, []).append(record)
    matrix = result.get("matrix")
    if isinstance(matrix, list):
        for cell_index, cell in enumerate(matrix):
            if (
                isinstance(cell, dict)
                and cell.get("publication_ready") is True
                and isinstance(cell.get("result"), dict)
            ):
                _collect_numeric_support_from_result(
                    cell["result"],
                    f"{tool_id}:cell:{cell_index}",
                    support,
                )


def _parameter_aliases(name: str) -> set[str]:
    key = name.lower().replace("omega_m", "omegam").replace("om0", "omegam")
    aliases = {key}
    if key in {"h0", "h_0"}:
        aliases.update({"h0", "h_0"})
    if key in {"omegam", "ωm"}:
        aliases.update({"omegam", "omega_m", "om0", "ωm"})
    if key == "s8":
        aliases.add("s8")
    if key in {"sigma8", "σ8"}:
        aliases.update({"sigma8", "σ8"})
    if key in {"w0", "w_0"}:
        aliases.update({"w0", "w_0"})
    if key in {"wa", "w_a"}:
        aliases.update({"wa", "w_a"})
    if key in {"beta_deg", "beta", "β", "alpha", "α"}:
        aliases.update({"beta_deg", "beta", "β", "alpha", "α"})
    if key in {"a_cb", "acb"}:
        aliases.update({"a_cb", "acb"})
    if key.endswith("_tension"):
        aliases.add(key)
        aliases.add("tension")
    return aliases


def _numeric_value_for_token(line: str, token: str) -> float | None:
    token_patterns = {
        "h0": r"H\s*_?\s*0|H₀",
        "omegam": r"Ω\s*_?\s*m|Omega\s*_?\s*m|omegam|Om0",
        "omega_m": r"Ω\s*_?\s*m|Omega\s*_?\s*m|omegam|Om0",
        "s8": r"S\s*_?\s*8",
        "sigma8": r"sigma\s*_?\s*8|σ\s*_?\s*8",
        "w0": r"w\s*_?\s*0",
        "wa": r"w\s*_?\s*a",
        "beta_deg": r"beta(?:_?deg)?|β|alpha(?:_?deg)?|α",
        "a_cb": r"A\s*[_-]?\s*CB",
        "slope": r"slope|斜率",
        "scatter": r"scatter|intrinsic scatter|离散",
        "tension": r"(?:tension|张力|σ|sigma)",
    }
    if token == "tension":
        match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:σ|sigma)\b", line, re.I)
        return _coerce_float(match.group(1)) if match else None
    if token == "p_value":
        match = re.search(
            r"\bp(?:[-\s]?value)?\s*(?:=|≈|~|<|>|≤|≥)\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
            line,
            re.I,
        )
        return _coerce_float(match.group(1)) if match else None
    pattern = token_patterns.get(token)
    if not pattern:
        return None
    match = re.search(
        rf"(?:{pattern})\s*(?:=|≈|~|is|:)?\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)",
        line,
        re.I,
    )
    if not match:
        return None
    return _coerce_float(match.group(1))


def _verify_claimed_numeric_value(
    token: str,
    claimed_value: float | None,
    numeric_support: dict[str, list[dict[str, Any]]],
    fallback_evidence_ids: list[str],
) -> tuple[str, str, list[str]]:
    supported = numeric_support.get(token) or numeric_support.get(token.lower()) or []
    if not supported:
        if claimed_value is None:
            return "verified", "", fallback_evidence_ids
        return (
            "unsupported",
            f"Remove the quoted {token} value, or rerun a publication-ready tool that returns a scalar summary for {token}.",
            fallback_evidence_ids,
        )
    evidence_ids = sorted({str(item.get("evidence_id")) for item in supported if item.get("evidence_id")})
    if claimed_value is None:
        return "verified", "", evidence_ids or fallback_evidence_ids
    for item in supported:
        if _numeric_value_matches_summary(claimed_value, item):
            return "verified", "", evidence_ids or fallback_evidence_ids
    median = supported[0].get("median")
    return (
        "contradicted",
        f"Replace the quoted {token} value with the current-turn tool value near {median}, or omit the number.",
        evidence_ids or fallback_evidence_ids,
    )


def _numeric_value_matches_summary(value: float, summary: dict[str, Any]) -> bool:
    hdi = summary.get("hdi_94")
    if isinstance(hdi, list | tuple) and len(hdi) >= 2:
        low = _coerce_float(hdi[0])
        high = _coerce_float(hdi[1])
        if low is not None and high is not None:
            width = max(abs(high - low), 1e-12)
            return (low - 0.05 * width) <= value <= (high + 0.05 * width)
    median = _coerce_float(summary.get("median"))
    if median is None:
        return False
    tolerance = max(abs(median) * 0.02, 0.02)
    return abs(value - median) <= tolerance


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_evidence_graph(tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    for item in reversed(tool_results):
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if isinstance(result, dict) and isinstance(result.get("evidence_graph"), dict):
            return result["evidence_graph"]
    return {}


def _tool_payload_text(tool_results: list[dict[str, Any]]) -> str:
    try:
        return json.dumps(tool_results, default=str, sort_keys=True).lower()
    except Exception:
        return str(tool_results).lower()


def _dataset_index_from_tool_results(tool_results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        for dataset in _datasets_from_result(result):
            key = str(dataset.get("key") or dataset.get("service_key") or dataset.get("display_name") or "")
            if key:
                index[key] = dataset
    return index


def _publication_ready_tool_ids(tool_results: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for idx, item in enumerate(tool_results):
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if isinstance(result, dict) and result.get("publication_ready") is True and result.get("__do_not_claim__") is not True:
            ids.add(str(item.get("id") or f"tool_run:{idx}:{item.get('tool') or item.get('name') or 'tool'}"))
    return ids


def _checked_sources_from_payload(payload_text: str, dataset_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "dataset_count": len(dataset_index),
        "arxiv_ids": sorted({m.group(0) for m in ARXIV_ID_RE.finditer(payload_text)})[:20],
        "dois": sorted({m.group(0) for m in DOI_RE.finditer(payload_text)})[:20],
        "bibcodes": sorted({m.group(0) for m in BIBCODE_RE.finditer(payload_text)})[:20],
    }


def _source_evidence_ids(value: str, payload_text: str) -> list[str]:
    return [f"source:{value}"] if value.lower() in payload_text else []


def _dedupe_fact_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for claim in claims:
        key = (str(claim.get("text")), str(claim.get("kind")), str(claim.get("status")))
        if key in seen:
            continue
        seen.add(key)
        result.append(claim)
    return result


def _dataset_exists(key: str) -> bool:
    if cmb_rotation_dataset_exists(key):
        return True
    try:
        get_cosmology_dataset(key)
        return True
    except Exception:
        return False


def _dataset_entry_for_plan(key: str) -> dict[str, Any] | None:
    if cmb_rotation_dataset_exists(key):
        return get_cmb_rotation_dataset(key).to_dict()
    try:
        return get_cosmology_dataset(key).to_dict()
    except Exception:
        return None


def _clean_dataset_keys(keys: list[Any]) -> list[str]:
    result: list[str] = []
    for key in keys:
        text = str(key or "").strip()
        if text and text not in result and _dataset_exists(text):
            result.append(text)
    return result


def _clean_models(models: list[Any]) -> list[str]:
    allowed = {"lcdm", "wcdm", "w0wa_cdm", "ok_lcdm", "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu", "w0wa_cdm_mnu", "isotropic_beta"}
    result: list[str] = []
    for model in models:
        text = str(model or "").strip()
        if text in allowed and text not in result:
            result.append(text)
    return result or ["lcdm"]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
