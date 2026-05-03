"""Research-mode planning, execution summaries, and evidence graph helpers."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from app.services.cosmology_likelihoods import (
    build_likelihood_config,
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
        get_cosmology_dataset(key).to_dict()
        for key in datasets
        if _dataset_exists(key)
    ]
    dataset_status = [_dataset_plan_status(entry) for entry in dataset_entries]
    matrix = _proposed_experiment_matrix(datasets, models, text)
    blocking_gaps = _blocking_gaps(dataset_status, models)
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
            run = run_likelihood_chain(
                model=model,
                dataset_keys=cell_datasets,
                random_seed=base_seed + index,
                n_samples=n_samples,
            )
            not_run = run.get("datasets_not_run")
            all_requested_datasets_used = not isinstance(not_run, list) or len(not_run) == 0
            cell_ready = bool(run.get("publication_ready")) and all_requested_datasets_used
            config = build_likelihood_config(
                model=model,
                dataset_keys=cell_datasets,
                output_format="cobaya",
            )
            cells.append({
                "label": label,
                "model": model,
                "dataset_keys": cell_datasets,
                "publication_ready": cell_ready,
                "runnable": cell_ready,
                "execution_level": "compressed_preliminary"
                if cell_ready else "config_only",
                "result": run,
                "config_hash": config.get("config_hash"),
                "warnings": [
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
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if ready else "PARTIAL",
        "analysis_status": "RESEARCH_MATRIX_READY" if ready else "RESEARCH_MATRIX_PARTIAL",
        "publication_ready": bool(ready),
        "claim_scope": "research_matrix_compressed_preliminary",
        "research_plan": plan,
        "matrix": cells,
        "matrix_size": len(cells),
        "ready_cells": len(ready),
        "warnings": [
            "Research matrix uses executable compressed-likelihood cells where available; config-only cells are not numerical evidence.",
        ],
        "__message_to_model__": (
            "Summarize only publication_ready cells as compressed-likelihood preliminary. "
            "For other cells, describe the missing runner/config gap."
        ),
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
                        "scope": result.get("claim_scope"),
                    })
                    nodes.append({"id": claim_id, "type": "claim", "parameter": param})
                    edges.append({"from": claim_id, "to": tool_id, "kind": "supported_by"})

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


def export_research_report(
    *,
    research_plan: dict[str, Any] | None = None,
    evidence_graph: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """Generate a lightweight auditable research report draft."""
    plan = research_plan or {}
    graph = evidence_graph or {}
    tool_results = tool_results or []
    report_title = title or plan.get("research_question") or "Standard Astro research report"
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
    lines.extend([
        "",
        "## Claim Provenance",
        f"- Claimable parameters: {', '.join(graph.get('claimable_parameters', [])) if isinstance(graph, dict) else 'none'}",
        "",
        "## Limitations",
    ])
    for gap in plan.get("blocking_gaps", []) if isinstance(plan.get("blocking_gaps"), list) else []:
        lines.append(f"- {gap}")
    markdown = "\n".join(lines).strip() + "\n"
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "RESEARCH_REPORT_READY",
        "publication_ready": False,
        "format": "markdown",
        "markdown": markdown,
        "word_count": len(markdown.split()),
        "__message_to_model__": "This is a report draft. It does not create new scientific evidence.",
    }


def _required_probes(text: str) -> list[str]:
    prompt = text.lower()
    probes: list[str] = []
    if any(tok in prompt for tok in ("bao", "baryon acoustic", "desi", "sdss", "boss", "eboss", "6df")):
        probes.append("BAO")
    if any(tok in prompt for tok in ("sn", "supernova", "pantheon", "des-sn", "union3")):
        probes.append("SN")
    if any(tok in prompt for tok in ("cmb", "planck", "act", "spt")):
        probes.append("CMB")
    if any(tok in prompt for tok in ("weak lensing", "cosmic shear", "kids", "des y3", "hsc", "s8", "sigma8")):
        probes.append("WL")
    if any(tok in prompt for tok in ("h0", "h₀", "sh0es", "trgb", "h0licow", "megamaser")):
        probes.append("H0")
    if "chronometer" in prompt or "h(z)" in prompt:
        probes.append("HZ")
    if any(tok in prompt for tok in ("[cii]", "fwhm", "line", "lfr", "线宽")):
        probes.append("line_measurements")
    if not probes and any(tok in prompt for tok in ("cosmology", "宇宙学", "dark energy", "暗能量")):
        probes = ["BAO", "SN", "CMB"]
    return list(dict.fromkeys(probes))


def _candidate_datasets(probes: list[str], text: str) -> list[str]:
    prompt = text.lower()
    keys: list[str] = []
    if "BAO" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["pre_desi_bao"] if "pre-desi" in prompt or "pre desi" in prompt else COSMOLOGY_PROBE_DATASETS["bao"])
    if "SN" in probes:
        if any(tok in prompt for tok in ("compare", "比较", "robust", "consistency", "union3", "des-sn")):
            keys.extend(COSMOLOGY_PROBE_DATASETS["sn_comparison"])
        else:
            keys.extend(COSMOLOGY_PROBE_DATASETS["sn"])
    if "CMB" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["cmb"])
        if "act" in prompt:
            keys.extend(COSMOLOGY_PROBE_DATASETS["cmb_lensing"])
    if "WL" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["wl"])
    if "H0" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["h0_anchors"] if "compare" in prompt or "比较" in prompt else COSMOLOGY_PROBE_DATASETS["h0"])
    if "HZ" in probes:
        keys.extend(COSMOLOGY_PROBE_DATASETS["hz"])
    return _clean_dataset_keys(keys)


def _models_from_question(text: str) -> list[str]:
    prompt = text.lower()
    models: list[str] = []
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
    if "line_measurements" in probes:
        hypotheses.append("Compile cited measurement rows before fitting any line-luminosity relation.")
    return hypotheses or ["Determine which parts of the requested research question are executable with registered data products."]


def _proposed_experiment_matrix(dataset_keys: list[str], models: list[str], text: str) -> list[dict[str, Any]]:
    keys = _clean_dataset_keys(dataset_keys)
    if not keys:
        return []
    first_model = _clean_models(models)[0]
    combos: list[tuple[str, list[str]]] = [("All selected probes", keys)]
    bao = [key for key in keys if get_cosmology_dataset(key).probe == "bao"]
    sn = [key for key in keys if get_cosmology_dataset(key).probe == "sn"]
    cmb = [key for key in keys if get_cosmology_dataset(key).probe in {"cmb_compressed", "cmb_lensing"}]
    wl = [key for key in keys if get_cosmology_dataset(key).probe == "weak_lensing"]
    h0 = [key for key in keys if get_cosmology_dataset(key).probe == "h0_prior"]
    if bao:
        combos.append(("BAO only", bao[:1]))
        if sn:
            combos.append(("BAO + SN", bao[:1] + sn[:1]))
        if cmb:
            combos.append(("BAO + CMB", bao[:1] + cmb[:1]))
        if wl:
            combos.append(("BAO + WL", bao[:1] + wl))
        if sn and cmb:
            combos.append(("BAO + SN + CMB", bao[:1] + sn[:1] + cmb[:1]))
        if h0:
            combos.append(("BAO + H0 prior", bao[:1] + h0[:1]))
    seen: set[tuple[str, ...]] = set()
    matrix: list[dict[str, Any]] = []
    for label, combo in combos:
        cleaned = _clean_dataset_keys(combo)
        marker = tuple(cleaned)
        if not cleaned or marker in seen:
            continue
        seen.add(marker)
        matrix.append({"label": label, "dataset_keys": cleaned, "model": first_model})
    return matrix


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
    spec = entry.get("compressed_likelihood")
    if isinstance(spec, dict):
        return list(spec.get("parameters") or [])
    if entry.get("key") == "desi_dr1_bao":
        return ["H0", "omegam", "rd", "H0_rd"]
    return []


def _blocking_gaps(dataset_status: list[dict[str, Any]], models: list[str]) -> list[str]:
    gaps: list[str] = []
    for entry in dataset_status:
        if entry.get("execution_level") == "config_only":
            gaps.append(f"{entry.get('display_name') or entry.get('key')} requires external Cobaya/CosmoSIS or a future runner before posterior claims.")
    if any(model != "lcdm" for model in models):
        gaps.append("Extended models are planned/configurable, but phase-1 compressed execution is publication-ready for ΛCDM only.")
    return gaps


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
        "tension": r"tension|σ tension|sigma tension|张力",
        "slope": r"slope|斜率|β",
        "scatter": r"scatter|intrinsic scatter|离散",
        "p_value": r"p\s*[=<>]",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, text, re.I):
            tokens.append(name)
    return tokens


def _dataset_exists(key: str) -> bool:
    try:
        get_cosmology_dataset(key)
        return True
    except Exception:
        return False


def _clean_dataset_keys(keys: list[Any]) -> list[str]:
    result: list[str] = []
    for key in keys:
        text = str(key or "").strip()
        if text and text not in result and _dataset_exists(text):
            result.append(text)
    return result


def _clean_models(models: list[Any]) -> list[str]:
    allowed = {"lcdm", "wcdm", "w0wa_cdm", "ok_lcdm", "ok_wcdm", "ok_w0wa_cdm", "lcdm_mnu", "w0wa_cdm_mnu"}
    result: list[str] = []
    for model in models:
        text = str(model or "").strip()
        if text in allowed and text not in result:
            result.append(text)
    return result or ["lcdm"]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
