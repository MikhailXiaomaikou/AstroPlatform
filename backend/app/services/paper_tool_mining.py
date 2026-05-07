"""Paper-to-tool mining helpers.

This module intentionally mines *infrastructure requirements* from papers,
not scientific conclusions.  It turns cited method/table/equation passages
into ToolSpec-like records, then summarizes ontology/gap information against
the tools Standard Astro already exposes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any


TOOL_CATEGORIES = {
    "data_loader",
    "table_extractor",
    "likelihood",
    "sampler",
    "fitter",
    "diagnostic",
    "plotter",
    "exporter",
    "validator",
}


PLATFORM_CAPABILITIES: dict[str, dict[str, Any]] = {
    "literature_search": {
        "status": "available",
        "tool_names": ["search_literature"],
        "note": "Paper/abstract discovery only; not measurement evidence.",
    },
    "arxiv_reader": {
        "status": "partial",
        "tool_names": ["read_arxiv_paper"],
        "note": "PDF text preview is truncated; use table extraction for measurement rows.",
    },
    "literature_table_extraction": {
        "status": "partial",
        "tool_names": ["extract_literature_tables"],
        "note": "arXiv/ar5iv/LaTeX sources only; no OCR-heavy PDF tables.",
    },
    "line_relation_fit": {
        "status": "available",
        "tool_names": ["fit_line_lfr"],
        "note": "Requires cited line-measurement cache.",
    },
    "distance_modulus_mcmc": {
        "status": "available",
        "tool_names": ["fit_cosmology_mcmc"],
        "note": "Works for z/mu/sigma_mu tables.",
    },
    "compressed_cosmology_likelihood": {
        "status": "partial",
        "tool_names": ["run_cosmology_likelihood_chain", "run_research_matrix"],
        "note": "Compressed Gaussian likelihoods only, not full external likelihood packages.",
    },
    "likelihood_config_builder": {
        "status": "available",
        "tool_names": ["build_cosmology_likelihood", "build_cosmology_robustness_matrix"],
        "note": "Config generation only; not posterior evidence.",
    },
    "generic_statistics": {
        "status": "available",
        "tool_names": ["astro_statistics_toolbox"],
        "note": "Deterministic statistics on explicit arrays.",
    },
    "mcmc_sampler": {
        "status": "partial",
        "tool_names": ["fit_cosmology_mcmc"],
        "note": "emcee path exists for typed distance-modulus tables; general paper likelihoods missing.",
    },
    "nested_sampler": {
        "status": "available",
        "tool_names": ["run_nested_sampler"],
        "note": "Controlled dynesty interface for typed Gaussian likelihood summaries; full external nested likelihood packages remain future work.",
    },
    "chain_diagnostics": {
        "status": "available",
        "tool_names": [
            "evaluate_chain_diagnostics",
            "run_cosmology_likelihood_chain",
            "fit_cosmology_mcmc",
        ],
        "note": "Controlled R-hat/ESS/MCSE diagnostic kernel exists; richer visual dashboard remains iterative UI work.",
    },
    "robustness_matrix": {
        "status": "available",
        "tool_names": ["run_research_matrix", "run_cosmology_robustness_matrix"],
        "note": "Uses registered compressed cells where available.",
    },
    "fact_verification": {
        "status": "available",
        "tool_names": ["build_evidence_graph", "verify_research_facts"],
        "note": "Checks claims against current-turn evidence.",
    },
    "research_export": {
        "status": "available",
        "tool_names": ["export_research_report", "generate_paper_draft"],
        "note": "Research report package exports Markdown, BibTeX, dataset/citation summaries, and reproducibility manifest; richer archive bundling can iterate.",
    },
    "generic_data_loader": {
        "status": "partial",
        "tool_names": [
            "search_objects",
            "run_adql",
            "run_sdss_sql",
            "query_vo_service",
            "load_cosmology_data_product",
        ],
        "note": "Archive/query loaders and registered cosmology data-product ingestion exist; more survey-specific formats remain partial.",
    },
    "external_likelihood_runner": {
        "status": "missing",
        "tool_names": [],
        "note": "Full Cobaya/CosmoSIS external likelihood execution is future work.",
    },
}


DATASET_ALIASES: dict[str, str] = {
    "desi": "DESI BAO",
    "bao": "BAO data vector",
    "pantheon": "Pantheon+ supernovae",
    "pantheon+": "Pantheon+ supernovae",
    "supernova": "supernova distance table",
    "sne": "supernova distance table",
    "planck": "Planck compressed/CMB likelihood",
    "act": "ACT CMB lensing",
    "kids": "KiDS weak-lensing data",
    "des y3": "DES Y3 3x2pt",
    "hsc": "HSC cosmic shear",
    "sh0es": "SH0ES H0 prior",
    "alma": "ALMA observation metadata",
    "alpine": "ALPINE [CII] measurements",
    "rebels": "REBELS [CII] measurements",
    "sdss": "SDSS/eBOSS/6dF BAO",
    "boss": "BOSS/eBOSS BAO",
    "6df": "6dF BAO",
}


PATTERNS: list[dict[str, Any]] = [
    {
        "category": "data_loader",
        "method": "Load cited observational data products",
        "capability": "generic_data_loader",
        "regex": r"\b(data release|catalog(?:ue)?|sample|data vector|measurements?|observations?)\b",
        "inputs": ["dataset identifier", "selection cuts"],
        "outputs": ["typed rows or data vector"],
    },
    {
        "category": "table_extractor",
        "method": "Extract machine-readable paper tables",
        "capability": "literature_table_extraction",
        "regex": r"\b(table|machine[- ]readable|catalog(?:ue)? table|appendix table|covariance matrix)\b",
        "inputs": ["paper source", "table label"],
        "outputs": ["structured rows", "row-level citation"],
    },
    {
        "category": "likelihood",
        "method": "Evaluate Gaussian/covariance likelihood",
        "capability": "compressed_cosmology_likelihood",
        "regex": r"\b(likelihood|chi[- ]?square|χ2|χ\^2|covariance|data vector|log[- ]?likelihood)\b",
        "inputs": ["data vector", "covariance", "model prediction"],
        "outputs": ["log_likelihood", "chi2", "posterior-ready summary"],
        "formula": "logL = -0.5 * Δ^T C^{-1} Δ",
    },
    {
        "category": "likelihood",
        "method": "Run full external likelihood package",
        "capability": "external_likelihood_runner",
        "regex": r"\b(full likelihood|external likelihood|official likelihood|likelihood package|planck likelihood|cobaya likelihood|cosmosis likelihood)\b",
        "inputs": ["registered likelihood config", "dataset release files", "priors"],
        "outputs": ["posterior chain", "evidence or fit statistics"],
    },
    {
        "category": "sampler",
        "method": "Run posterior sampler",
        "capability": "mcmc_sampler",
        "regex": r"\b(mcmc|markov chain|emcee|cobaya|cosmosis|monte carlo)\b",
        "inputs": ["likelihood", "priors", "random seed"],
        "outputs": ["posterior chain", "diagnostics"],
    },
    {
        "category": "sampler",
        "method": "Run nested sampler",
        "capability": "nested_sampler",
        "regex": r"\b(nested sampling|multinest|polychord|dynesty|evidence)\b",
        "inputs": ["likelihood", "priors"],
        "outputs": ["posterior samples", "Bayesian evidence"],
    },
    {
        "category": "fitter",
        "method": "Fit regression or scaling relation",
        "capability": "line_relation_fit",
        "regex": r"\b(linear regression|bayesian regression|scaling relation|linmix|slope|intercept|intrinsic scatter|fwhm|luminosity relation)\b",
        "inputs": ["x measurements", "y measurements", "measurement errors"],
        "outputs": ["slope", "intercept", "intrinsic scatter"],
    },
    {
        "category": "diagnostic",
        "method": "Compute convergence/model-comparison diagnostics",
        "capability": "chain_diagnostics",
        "regex": r"\b(r[- ]?hat|ess|effective sample|trace|aic|bic|delta chi|δχ|bayes factor|tension|sigma tension|posterior predictive)\b",
        "inputs": ["chains or posterior summaries"],
        "outputs": ["diagnostic table", "tension estimate"],
    },
    {
        "category": "plotter",
        "method": "Render analysis figures",
        "capability": "generic_statistics",
        "regex": r"\b(corner plot|contour|triangle plot|trace plot|hubble diagram|residual plot|credible region|confidence contour)\b",
        "inputs": ["posterior samples", "fit results", "data rows"],
        "outputs": ["figure", "plot metadata"],
    },
    {
        "category": "validator",
        "method": "Run robustness/null-systematics checks",
        "capability": "robustness_matrix",
        "regex": r"\b(robustness|null test|jackknife|leave[- ]one[- ]out|systematic|blinding|ablation|split sample|subsample)\b",
        "inputs": ["analysis variants", "dataset combinations"],
        "outputs": ["robustness matrix", "failure modes"],
    },
    {
        "category": "exporter",
        "method": "Export reproducibility package",
        "capability": "research_export",
        "regex": r"\b(chain files?|data release|reproducib|configuration files?|yaml|bibtex|software package|public code)\b",
        "inputs": ["tool runs", "citations", "configs"],
        "outputs": ["report", "config", "tables", "BibTeX"],
    },
    {
        "category": "validator",
        "method": "Verify claim provenance",
        "capability": "fact_verification",
        "regex": r"\b(citation|provenance|acknowledg|reference|bibcode|doi|arxiv)\b",
        "inputs": ["claims", "tool results", "source metadata"],
        "outputs": ["fact-check report", "safe rewrites"],
    },
]


def mine_paper_tools(
    *,
    arxiv_id: str | None = None,
    doi: str | None = None,
    bibcode: str | None = None,
    paper_url: str | None = None,
    paper_metadata: dict[str, Any] | None = None,
    paper_text: str | None = None,
    source_sections: dict[str, str] | list[dict[str, Any]] | None = None,
    max_tools: int = 80,
) -> dict[str, Any]:
    """Extract ToolSpec records from paper text/sections.

    Abstract-only evidence is deliberately low confidence.  A paper identifier
    without body text produces a blocked metadata-only record, not tool claims.
    """
    metadata = _normalize_metadata(
        arxiv_id=arxiv_id,
        doi=doi,
        bibcode=bibcode,
        paper_url=paper_url,
        paper_metadata=paper_metadata,
    )
    spans = _collect_source_spans(
        paper_text=paper_text,
        source_sections=source_sections,
        abstract=str(metadata.get("abstract") or ""),
    )
    full_text_spans = [span for span in spans if span.get("evidence_type") != "abstract"]
    if not spans or not full_text_spans:
        blocked = _blocked_metadata_spec(metadata)
        return {
            "success": True,
            "__tool_status__": "PARTIAL",
            "analysis_status": "PAPER_TOOL_MINING_PARTIAL",
            "publication_ready": False,
            "__do_not_claim__": True,
            "paper_metadata": metadata,
            "tool_specs": [blocked] if blocked else [],
            "tool_count": 1 if blocked else 0,
            "blocked_reason": "full_text_required",
            "warnings": [
                "Only metadata/abstract was available. High-confidence ToolSpec extraction requires methods/tables/equations/full text.",
            ],
            "__message_to_model__": (
                "This is paper-infrastructure mining, not a scientific result. "
                "Do not claim the paper requires a tool unless a source span supports it."
            ),
        }

    specs_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for span in spans:
        span_text = str(span.get("text") or "")
        if not span_text.strip():
            continue
        for pattern in PATTERNS:
            if not re.search(str(pattern["regex"]), span_text, re.I):
                continue
            datasets = _datasets_from_text(span_text)
            capability = str(pattern["capability"])
            platform = PLATFORM_CAPABILITIES.get(capability, {"status": "missing", "tool_names": []})
            category = str(pattern["category"])
            method_name = _method_name(pattern, datasets)
            key = (category, capability, _canonical_method(method_name))
            evidence = _span_payload(span)
            if key not in specs_by_key:
                confidence = _confidence_for_span(span, pattern, datasets)
                specs_by_key[key] = {
                    "tool_id": _tool_id(metadata, category, capability, method_name),
                    "paper_id": metadata.get("paper_id"),
                    "paper_metadata": metadata,
                    "scientific_task": _scientific_task_from_metadata(metadata),
                    "tool_category": category,
                    "method_name": method_name,
                    "canonical_capability": capability,
                    "inputs": list(pattern.get("inputs") or []),
                    "outputs": list(pattern.get("outputs") or []),
                    "datasets": datasets,
                    "formula_or_likelihood": pattern.get("formula") or _formula_from_text(span_text),
                    "implementation_status": str(platform.get("status") or "missing"),
                    "platform_tools": list(platform.get("tool_names") or []),
                    "platform_note": platform.get("note"),
                    "confidence": confidence,
                    "source_spans": [evidence],
                    "validation_example": _validation_example(category, method_name, datasets),
                    "citations": _citations_from_metadata(metadata),
                }
            else:
                spec = specs_by_key[key]
                spec["source_spans"].append(evidence)
                spec["datasets"] = sorted(set(spec["datasets"]) | set(datasets))
                spec["confidence"] = min(
                    0.95,
                    max(float(spec["confidence"]), _confidence_for_span(span, pattern, datasets))
                    + min(0.08, 0.02 * len(spec["source_spans"])),
                )
    specs = sorted(
        specs_by_key.values(),
        key=lambda item: (-float(item["confidence"]), str(item["tool_category"]), str(item["method_name"])),
    )[: max(1, min(int(max_tools or 80), 200))]
    status = "COMPLETED" if specs else "PARTIAL"
    return {
        "success": True,
        "__tool_status__": status,
        "analysis_status": "PAPER_TOOL_MINING_READY" if specs else "PAPER_TOOL_MINING_PARTIAL",
        "publication_ready": False,
        "__do_not_claim__": True,
        "paper_metadata": metadata,
        "tool_specs": specs,
        "tool_count": len(specs),
        "category_counts": dict(Counter(str(spec["tool_category"]) for spec in specs)),
        "implementation_counts": dict(Counter(str(spec["implementation_status"]) for spec in specs)),
        "warnings": [
            "ToolSpecs describe infrastructure requirements mined from paper spans; they are not scientific results.",
        ],
        "__message_to_model__": (
            "Use these ToolSpecs to discuss platform capability gaps only. Do not "
            "quote posterior/fit/science conclusions from this mining result."
        ),
    }


def run_paper_tool_mining_batch(
    *,
    papers: list[dict[str, Any]],
    domain_tag: str = "observational_cosmology",
    max_papers: int = 50,
) -> dict[str, Any]:
    """Mine ToolSpecs from a batch of paper payloads."""
    limited = [p for p in papers if isinstance(p, dict)][: max(1, min(int(max_papers or 50), 100))]
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    all_specs: list[dict[str, Any]] = []
    for index, paper in enumerate(limited):
        try:
            mined = mine_paper_tools(
                arxiv_id=paper.get("arxiv_id"),
                doi=paper.get("doi"),
                bibcode=paper.get("bibcode"),
                paper_url=paper.get("paper_url") or paper.get("url"),
                paper_metadata=paper.get("paper_metadata") if isinstance(paper.get("paper_metadata"), dict) else paper,
                paper_text=paper.get("paper_text") or paper.get("text"),
                source_sections=paper.get("source_sections") or paper.get("sections"),
            )
            results.append({
                "paper_id": mined.get("paper_metadata", {}).get("paper_id"),
                "analysis_status": mined.get("analysis_status"),
                "tool_count": mined.get("tool_count", 0),
                "category_counts": mined.get("category_counts", {}),
                "implementation_counts": mined.get("implementation_counts", {}),
            })
            all_specs.extend([s for s in mined.get("tool_specs", []) if isinstance(s, dict)])
        except Exception as exc:
            failures.append({
                "index": index,
                "paper_id": paper.get("arxiv_id") or paper.get("doi") or paper.get("bibcode") or paper.get("title"),
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            })
    ontology = build_tool_ontology(tool_specs=all_specs)
    gap = build_tool_gap_matrix(tool_specs=all_specs)
    queue = rank_tool_implementation_queue(gap_matrix=gap.get("gap_matrix", []))
    return {
        "success": True,
        "__tool_status__": "COMPLETED" if all_specs else "PARTIAL",
        "analysis_status": "PAPER_TOOL_MINING_BATCH_READY" if all_specs else "PAPER_TOOL_MINING_BATCH_PARTIAL",
        "publication_ready": False,
        "__do_not_claim__": True,
        "domain_tag": domain_tag,
        "paper_count": len(limited),
        "mined_paper_count": sum(1 for r in results if int(r.get("tool_count") or 0) > 0),
        "tool_spec_count": len(all_specs),
        "paper_summaries": results,
        "failures": failures,
        "coverage_stats": {
            "category_counts": dict(Counter(str(s.get("tool_category")) for s in all_specs)),
            "implementation_counts": dict(Counter(str(s.get("implementation_status")) for s in all_specs)),
            "top_missing_capabilities": gap.get("top_missing", [])[:5],
            "top_partial_capabilities": gap.get("top_partial", [])[:5],
        },
        "ontology": ontology.get("ontology", {}),
        "gap_matrix": gap.get("gap_matrix", []),
        "implementation_queue": queue.get("implementation_queue", []),
        "warnings": [
            "Batch mining output is an infrastructure map. It should be stored locally and reviewed before implementation.",
        ],
    }


def build_tool_ontology(*, tool_specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Cluster ToolSpecs by category/canonical capability."""
    clusters: dict[str, dict[str, Any]] = {}
    for spec in _clean_specs(tool_specs):
        category = str(spec.get("tool_category") or "unknown")
        capability = str(spec.get("canonical_capability") or _canonical_method(str(spec.get("method_name") or "")))
        cluster_id = f"{category}:{capability}"
        cluster = clusters.setdefault(cluster_id, {
            "cluster_id": cluster_id,
            "tool_category": category,
            "canonical_capability": capability,
            "method_names": [],
            "papers": [],
            "datasets": [],
            "implementation_statuses": [],
            "spec_ids": [],
            "evidence_count": 0,
        })
        _append_unique(cluster["method_names"], str(spec.get("method_name") or capability))
        _append_unique(cluster["papers"], str(spec.get("paper_id") or "unknown"))
        for dataset in spec.get("datasets") or []:
            _append_unique(cluster["datasets"], str(dataset))
        _append_unique(cluster["implementation_statuses"], str(spec.get("implementation_status") or "missing"))
        _append_unique(cluster["spec_ids"], str(spec.get("tool_id") or "unknown"))
        cluster["evidence_count"] += len(spec.get("source_spans") or [])
    categories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for cluster in clusters.values():
        cluster["paper_count"] = len(cluster["papers"])
        cluster["status"] = _combined_status(cluster["implementation_statuses"])
        categories[str(cluster["tool_category"])].append(cluster)
    ontology = {
        "categories": {
            category: sorted(items, key=lambda item: (-int(item["paper_count"]), str(item["canonical_capability"])))
            for category, items in sorted(categories.items())
        },
        "cluster_count": len(clusters),
        "category_counts": {category: len(items) for category, items in categories.items()},
    }
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "TOOL_ONTOLOGY_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "ontology": ontology,
        "cluster_count": len(clusters),
        "__message_to_model__": "This ontology summarizes platform tool requirements, not science results.",
    }


def build_tool_gap_matrix(*, tool_specs: list[dict[str, Any]], current_tools: list[str] | None = None) -> dict[str, Any]:
    """Summarize implementation gaps from ToolSpecs."""
    ontology = build_tool_ontology(tool_specs=tool_specs)["ontology"]
    rows: list[dict[str, Any]] = []
    for clusters in ontology.get("categories", {}).values():
        for cluster in clusters:
            status = _combined_status(cluster.get("implementation_statuses") or [])
            paper_count = int(cluster.get("paper_count") or 0)
            evidence_count = int(cluster.get("evidence_count") or 0)
            datasets = list(cluster.get("datasets") or [])
            score = _priority_score(status, paper_count, evidence_count, datasets)
            rows.append({
                "capability": cluster.get("canonical_capability"),
                "tool_category": cluster.get("tool_category"),
                "method_names": cluster.get("method_names", [])[:5],
                "required_datasets": datasets[:10],
                "paper_count": paper_count,
                "evidence_count": evidence_count,
                "current_status": status,
                "available_platform_tools": _platform_tools_for_capability(str(cluster.get("canonical_capability") or "")),
                "implementation_gap": _implementation_gap(str(cluster.get("canonical_capability") or ""), status),
                "research_value": _research_value(cluster),
                "priority": _priority_label(score),
                "priority_score": score,
                "evidence_spec_ids": cluster.get("spec_ids", [])[:8],
            })
    rows.sort(key=lambda item: (str(item["priority"]), -float(item["priority_score"]), str(item["capability"])))
    top_missing = [row for row in rows if row["current_status"] == "missing"]
    top_partial = [row for row in rows if row["current_status"] == "partial"]
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "TOOL_GAP_MATRIX_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "gap_matrix": rows,
        "gap_count": len(rows),
        "top_missing": top_missing,
        "top_partial": top_partial,
        "current_tools_considered": current_tools or [],
        "__message_to_model__": (
            "Use the gap matrix to prioritize engineering. It is not evidence for scientific claims."
        ),
    }


def rank_tool_implementation_queue(*, gap_matrix: list[dict[str, Any]], max_items: int = 20) -> dict[str, Any]:
    """Rank gaps by priority and research value."""
    rows = [row for row in gap_matrix if isinstance(row, dict)]
    filtered = [
        row for row in rows
        if str(row.get("current_status")) in {"missing", "partial", "blocked"}
    ]
    filtered.sort(key=lambda item: (str(item.get("priority") or "P9"), -float(item.get("priority_score") or 0)))
    queue: list[dict[str, Any]] = []
    for index, row in enumerate(filtered[: max(1, min(int(max_items or 20), 50))], start=1):
        queue.append({
            "rank": index,
            "capability": row.get("capability"),
            "tool_category": row.get("tool_category"),
            "priority": row.get("priority"),
            "priority_score": row.get("priority_score"),
            "current_status": row.get("current_status"),
            "why": row.get("research_value"),
            "next_engineering_step": _next_engineering_step(row),
            "required_datasets": row.get("required_datasets", []),
            "paper_count": row.get("paper_count"),
        })
    return {
        "success": True,
        "__tool_status__": "COMPLETED",
        "analysis_status": "TOOL_IMPLEMENTATION_QUEUE_READY",
        "publication_ready": False,
        "__do_not_claim__": True,
        "implementation_queue": queue,
        "queue_size": len(queue),
        "__message_to_model__": "Report this as an engineering implementation queue, not a science result.",
    }


def _normalize_metadata(
    *,
    arxiv_id: str | None,
    doi: str | None,
    bibcode: str | None,
    paper_url: str | None,
    paper_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    raw = dict(paper_metadata or {})
    aid = _clean_text(arxiv_id or raw.get("arxiv_id") or _arxiv_from_url(paper_url or raw.get("url") or raw.get("paper_url")))
    did = _clean_text(doi or raw.get("doi"))
    bib = _clean_text(bibcode or raw.get("bibcode"))
    url = _clean_text(paper_url or raw.get("paper_url") or raw.get("url") or (f"https://arxiv.org/abs/{aid}" if aid else ""))
    title = _clean_text(raw.get("title")) or aid or did or bib or "unknown paper"
    authors = raw.get("authors") if isinstance(raw.get("authors"), list) else []
    paper_id = aid or did or bib or _stable_hash({"title": title, "url": url})
    return {
        "paper_id": paper_id,
        "title": title,
        "authors": [str(a) for a in authors[:12]],
        "year": raw.get("year"),
        "arxiv_id": aid,
        "doi": did,
        "bibcode": bib,
        "source_url": url,
        "abstract": _clean_text(raw.get("abstract")),
    }


def _collect_source_spans(
    *,
    paper_text: str | None,
    source_sections: dict[str, str] | list[dict[str, Any]] | None,
    abstract: str,
) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    if abstract.strip():
        spans.append({
            "locator": "abstract",
            "section": "Abstract",
            "text": abstract.strip()[:1200],
            "evidence_type": "abstract",
        })
    if isinstance(source_sections, dict):
        for section, text in source_sections.items():
            for idx, para in enumerate(_split_paragraphs(str(text))):
                spans.append({
                    "locator": f"{section}:{idx + 1}",
                    "section": str(section),
                    "text": para,
                    "evidence_type": _evidence_type_for_section(str(section)),
                })
    elif isinstance(source_sections, list):
        for idx, item in enumerate(source_sections):
            if not isinstance(item, dict):
                continue
            section = str(item.get("section") or item.get("label") or f"section_{idx + 1}")
            text = str(item.get("text") or item.get("content") or "")
            if text.strip():
                spans.append({
                    "locator": str(item.get("locator") or f"{section}:{idx + 1}"),
                    "section": section,
                    "text": text.strip()[:1800],
                    "evidence_type": str(item.get("evidence_type") or _evidence_type_for_section(section)),
                })
    elif paper_text:
        for idx, para in enumerate(_split_paragraphs(paper_text)):
            section = _section_guess(para)
            spans.append({
                "locator": f"body:{idx + 1}",
                "section": section,
                "text": para,
                "evidence_type": _evidence_type_for_section(section),
            })
    return spans


def _split_paragraphs(text: str) -> list[str]:
    chunks = re.split(r"\n\s*\n|(?<=\.)\s{2,}", str(text or ""))
    cleaned = [re.sub(r"\s+", " ", chunk).strip() for chunk in chunks]
    return [chunk[:1800] for chunk in cleaned if len(chunk) >= 40]


def _evidence_type_for_section(section: str) -> str:
    lower = section.lower()
    if "abstract" in lower:
        return "abstract"
    if any(tok in lower for tok in ("method", "analysis", "likelihood", "data", "sample", "appendix", "table")):
        return "method_or_data"
    if any(tok in lower for tok in ("result", "discussion", "conclusion")):
        return "result_context"
    return "body"


def _section_guess(text: str) -> str:
    lower = text.lower()
    if any(tok in lower for tok in ("likelihood", "covariance", "mcmc", "method")):
        return "Methods"
    if any(tok in lower for tok in ("data", "sample", "table", "catalog")):
        return "Data"
    if any(tok in lower for tok in ("result", "constraint", "posterior")):
        return "Results"
    return "Body"


def _blocked_metadata_spec(metadata: dict[str, Any]) -> dict[str, Any] | None:
    if not any(metadata.get(key) for key in ("paper_id", "title", "arxiv_id", "doi", "bibcode")):
        return None
    return {
        "tool_id": _tool_id(metadata, "validator", "full_text_required", "Full-text paper mining"),
        "paper_id": metadata.get("paper_id"),
        "paper_metadata": metadata,
        "scientific_task": _scientific_task_from_metadata(metadata),
        "tool_category": "validator",
        "method_name": "Acquire full paper text before tool mining",
        "canonical_capability": "full_text_required",
        "inputs": ["full paper text", "method sections", "tables or equations"],
        "outputs": ["high-confidence ToolSpec records"],
        "datasets": [],
        "formula_or_likelihood": "",
        "implementation_status": "blocked",
        "platform_tools": ["read_arxiv_paper", "extract_literature_tables"],
        "platform_note": "Metadata/abstract cannot support high-confidence tool extraction.",
        "confidence": 0.25,
        "source_spans": [],
        "validation_example": "Run read_arxiv_paper or provide source_sections before mining tools.",
        "citations": _citations_from_metadata(metadata),
    }


def _datasets_from_text(text: str) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for token, label in DATASET_ALIASES.items():
        if token in lower:
            _append_unique(found, label)
    return found


def _method_name(pattern: dict[str, Any], datasets: list[str]) -> str:
    base = str(pattern["method"])
    if datasets and pattern["category"] in {"data_loader", "likelihood"}:
        return f"{base} ({', '.join(datasets[:3])})"
    return base


def _canonical_method(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:80] or "unknown"


def _tool_id(metadata: dict[str, Any], category: str, capability: str, method_name: str) -> str:
    payload = {
        "paper": metadata.get("paper_id"),
        "category": category,
        "capability": capability,
        "method": method_name,
    }
    return "tool_spec_" + _stable_hash(payload)


def _span_payload(span: dict[str, Any]) -> dict[str, Any]:
    text = re.sub(r"\s+", " ", str(span.get("text") or "")).strip()
    return {
        "locator": span.get("locator"),
        "section": span.get("section"),
        "evidence_type": span.get("evidence_type"),
        "text": text[:420],
    }


def _confidence_for_span(span: dict[str, Any], pattern: dict[str, Any], datasets: list[str]) -> float:
    evidence_type = str(span.get("evidence_type") or "")
    if evidence_type == "abstract":
        base = 0.35
    elif evidence_type == "method_or_data":
        base = 0.78
    elif evidence_type == "result_context":
        base = 0.58
    else:
        base = 0.62
    text = str(span.get("text") or "")
    if datasets:
        base += 0.05
    if re.search(r"\b(table\s+\d+|appendix|equation|eq\.|covariance matrix)\b", text, re.I):
        base += 0.06
    if pattern.get("formula"):
        base += 0.03
    return round(min(base, 0.95), 3)


def _formula_from_text(text: str) -> str:
    if re.search(r"χ2|χ\^2|chi[- ]?square", text, re.I):
        return "chi-square objective mentioned in source span"
    if re.search(r"log[- ]?likelihood|ln\s*L", text, re.I):
        return "log-likelihood mentioned in source span"
    return ""


def _validation_example(category: str, method_name: str, datasets: list[str]) -> str:
    if category == "likelihood":
        return "Run a small known-data vector through the likelihood and verify chi2 against the paper/config."
    if category == "sampler":
        return "Run a fixed-seed short chain and inspect convergence diagnostics."
    if category == "table_extractor":
        return "Extract the cited table and verify row/column counts against the paper caption."
    if category == "fitter":
        return "Fit a cited measurement table and compare slope/intercept units and pivots."
    if category == "data_loader":
        label = datasets[0] if datasets else "the cited dataset"
        return f"Load {label} and verify version, columns, units, and covariance/source URLs."
    return f"Validate {method_name} against the cited source span."


def _citations_from_metadata(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    citation = {
        "paper_id": metadata.get("paper_id"),
        "title": metadata.get("title"),
        "year": metadata.get("year"),
        "arxiv_id": metadata.get("arxiv_id"),
        "doi": metadata.get("doi"),
        "bibcode": metadata.get("bibcode"),
        "source_url": metadata.get("source_url"),
    }
    return [citation]


def _scientific_task_from_metadata(metadata: dict[str, Any]) -> str:
    blob = " ".join(str(metadata.get(key) or "") for key in ("title", "abstract")).lower()
    if any(tok in blob for tok in ("bao", "dark energy", "cosmology", "hubble", "supernova", "cmb", "weak lensing")):
        return "observational cosmology workflow mining"
    if any(tok in blob for tok in ("[cii]", "c ii", "fwhm", "line", "alpine", "rebels")):
        return "spectral-line relation workflow mining"
    return "paper infrastructure workflow mining"


def _clean_specs(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [spec for spec in tool_specs if isinstance(spec, dict) and spec.get("tool_category")]


def _combined_status(statuses: list[str]) -> str:
    values = {str(s) for s in statuses if s}
    if not values:
        return "missing"
    if "missing" in values:
        return "missing"
    if "blocked" in values:
        return "blocked"
    if "partial" in values:
        return "partial"
    if "available" in values:
        return "available"
    return sorted(values)[0]


def _priority_score(status: str, paper_count: int, evidence_count: int, datasets: list[str]) -> float:
    status_weight = {"missing": 4.0, "partial": 3.0, "blocked": 2.5, "available": 1.0}.get(status, 2.0)
    dataset_bonus = min(2.0, 0.2 * len(datasets))
    return round(status_weight * (1.0 + paper_count * 0.7 + evidence_count * 0.15) + dataset_bonus, 3)


def _priority_label(score: float) -> str:
    if score >= 10:
        return "P0"
    if score >= 6:
        return "P1"
    if score >= 3:
        return "P2"
    return "P3"


def _platform_tools_for_capability(capability: str) -> list[str]:
    entry = PLATFORM_CAPABILITIES.get(capability)
    return list(entry.get("tool_names") or []) if entry else []


def _implementation_gap(capability: str, status: str) -> str:
    entry = PLATFORM_CAPABILITIES.get(capability, {})
    if status == "available":
        return "No immediate implementation gap; keep regression tests and provenance coverage."
    if status == "partial":
        return str(entry.get("note") or "Partial implementation; extend precision/coverage before paper-class use.")
    if status == "blocked":
        return "Blocked by missing full-text/table/equation evidence."
    return str(entry.get("note") or "Missing controlled backend tool and validation fixtures.")


def _research_value(cluster: dict[str, Any]) -> str:
    papers = int(cluster.get("paper_count") or 0)
    category = str(cluster.get("tool_category") or "tool")
    datasets = list(cluster.get("datasets") or [])
    ds = f" across {len(datasets)} dataset family/families" if datasets else ""
    return f"Appears in {papers} mined paper(s) as a {category} capability{ds}."


def _next_engineering_step(row: dict[str, Any]) -> str:
    capability = str(row.get("capability") or "")
    status = str(row.get("current_status") or "")
    if status == "partial":
        return "Add missing data formats/covariance handling and build paper-backed regression fixtures."
    if capability == "nested_sampler":
        return "Design a controlled dynesty/PolyChord-style interface with fixed config schema and diagnostics."
    if capability == "external_likelihood_runner":
        return "Add backend Cobaya/CosmoSIS job runner for registered configs only."
    if status == "blocked":
        return "Acquire full text/table/equation source spans before implementation."
    return "Create a typed service interface, provenance schema, and paper-backed acceptance tests."


def _append_unique(items: list[Any], value: Any) -> None:
    if value not in items:
        items.append(value)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _arxiv_from_url(url: Any) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", str(url or ""), re.I)
    return match.group(1) if match else ""


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
