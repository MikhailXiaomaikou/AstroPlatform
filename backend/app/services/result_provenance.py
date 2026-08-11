"""Result provenance and safety helpers for AI tool outputs.
The chat agent must distinguish between real archive data, cached real data,
user-supplied data, synthetic demonstrations, and unavailable data.  These
helpers keep that contract consistent across tools and make it harder for a
simulated example to be presented as a scientific measurement.
Phase 1 / R1 — reproducibility envelope:
Every tool return additionally carries a minimal reproducibility envelope
(`run_id`, `tool_version`, `query_hash`, `timestamp_utc`, optional
`random_seed`).  A user who later wants to replay an analysis can feed
these fields back in and get the same result (modulo archive updates
captured in `archive_version`).
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from typing import Any
REAL_ARCHIVE = "real_archive"
CACHED_REAL = "cached_real"
USER_UPLOADED = "user_uploaded"
SYNTHETIC = "synthetic"
UNAVAILABLE = "unavailable"
COMPLETED = "completed"
PARTIAL = "partial"
SIMULATED_DEMO = "simulated_demo"
FAILED = "failed"
# F2.1: explicit status for a tool that ran cleanly but produced no data
# (e.g. ADQL returned 0 rows, search_objects returned [], run_python had
# empty stdout and no figures).  Distinct from FAILED so the UI / LLM /
# metrics can tell the two apart.
EMPTY = "empty"
# M2: the tool ran but the fit method actually used differs from the
# method_requested by the caller (e.g. user asked for Bayesian two-axis
# errors but the tool fell back to OLS because err columns were missing
# or the Bayesian backend is unavailable).  UI should paint this red so
# the methodology downgrade is never silent.
METHOD_DOWNGRADED = "method_downgraded"
# Cosmology MCMC tier (2026-05-20): chain ran with claimable input but ESS or
# R-hat is below publication threshold. Posterior diagnostics remain visible
# in structured results, but values are withheld from ordinary reply prose,
# MUST NOT be cited, and are excluded from the bibcode pool. Distinct from
# PARTIAL so consumers can distinguish diagnostics from a hard run failure.
EXPLORATORY = "exploratory"
_VALID_ORIGINS = {REAL_ARCHIVE, CACHED_REAL, USER_UPLOADED, SYNTHETIC, UNAVAILABLE}

# Generic lifecycle statuses + domain-specific statuses that downstream
# panels / claim_validator branch on. The set is read by ``result_contract``
# in `attach_provenance` — any tool-emitted status NOT in this set silently
# gets rewritten to PARTIAL, which makes the frontend panel render an empty
# card with only a "data warning" chip. So whenever a tool's contract uses
# a non-generic status string, it MUST be listed here.
_VALID_STATUS = {
    COMPLETED, PARTIAL, SIMULATED_DEMO, FAILED, EMPTY, METHOD_DOWNGRADED,
    EXPLORATORY,
    # research_program workflow statuses (research_program.py)
    "RESEARCH_PLAN_READY",
    "RESEARCH_MATRIX_READY",
    "RESEARCH_MATRIX_PARTIAL",
    "EVIDENCE_GRAPH_READY",
    "FACT_CHECK_READY",
    "RESEARCH_REPORT_READY",
    # paper-tool-mining workflow statuses (research_program.py)
    "PAPER_TOOL_MINING_READY",
    "PAPER_TOOL_MINING_LOOP_EMPTY",
    "PAPER_MINING_CANDIDATE_POOL_READY",
    "PAPER_MINING_CANDIDATE_POOL_EMPTY",
    "TOOL_ONTOLOGY_READY",
    "TOOL_GAP_MATRIX_READY",
    "TOOL_IMPLEMENTATION_QUEUE_READY",
    # cosmology likelihood runner statuses (cosmology_likelihoods.py)
    "COMPRESSED_CHAIN_READY",
    "COMPRESSED_ROBUSTNESS_READY",
    "NO_COMPRESSED_LIKELIHOOD",
    "CONFIG_READY",
    # external Cobaya backend statuses (cobaya_runner.py): publication-ready
    # external chain / structured not-run failure envelope. Without these the
    # flagship EXTERNAL_COBAYA_ENABLED full-likelihood result was rewritten to
    # "partial", contradicting __tool_status__=COMPLETED / publication_ready=True
    # and erasing the machine-readable READY vs NOT_RUN distinction. Also the
    # marker claim_validator._full_external_likelihood_ready_available keys on.
    "EXTERNAL_COBAYA_READY",
    "EXTERNAL_COBAYA_NOT_RUN",
    # data product loader statuses (cosmology_data_products.py)
    "COSMOLOGY_DATA_PRODUCT_READY",
    "COSMOLOGY_DATA_PRODUCT_PARTIAL",
    "COSMOLOGY_DATA_PRODUCT_UNAVAILABLE",
    "COSMOLOGY_COMPRESSED_DATA_PRODUCT_READY",
    # Alcock-Paczynski geometric test
    "ALCOCK_PACZYNSKI_READY",
    # Cobaya background job intermediate state (cosmology_mcmc.py)
    "QUEUED",
    "UNAVAILABLE",
    # Async-tool runtime intermediate state (async_tool_runtime.py).
    # Returned while a Celery job is mid-execution so the AI can tell
    # "still running, poll again" from "completed but with caveat" (PARTIAL).
    "RUNNING",
    # Uppercase form emitted by cosmology runners. The module-level EXPLORATORY
    # constant is lowercase ("exploratory"), but the actual emit sites use the
    # uppercase literal — keep both so result_contract() doesn't downgrade them
    # to "partial" and silently void the three-tier chain_tier semantic.
    "EXPLORATORY",
    # chain_diagnostics.py status
    "CHAIN_DIAGNOSTICS_READY",
    # paper_tool_mining / paper_tool_mining_loop intermediate + ready statuses.
    # Without these the ResearchProgramPanel.startsWith("PAPER_TOOL_MINING")
    # branches never match and the populated view falls through to the empty
    # state card.
    "PAPER_TOOL_MINING_PARTIAL",
    "PAPER_TOOL_MINING_BATCH_READY",
    "PAPER_TOOL_MINING_BATCH_PARTIAL",
    "PAPER_TOOL_MINING_LOOP_ROUND_READY",
}
# Immutable tool version used by scientific receipts.  A Docker build can set
# TOOL_VERSION explicitly, while Render injects RENDER_GIT_COMMIT for each
# deployed service.  GIT_COMMIT is the portable fallback for other hosts.
_UNRESOLVED_TOOL_VERSIONS = {"", "dev", "unknown", "unset", "none"}


def _runtime_is_production() -> bool:
    return (
        os.getenv("ENV", "").strip().lower() == "production"
        or bool(os.getenv("RENDER_SERVICE_ID", "").strip())
        or bool(os.getenv("RENDER_SERVICE_NAME", "").strip())
    )


def _tool_version_with_source() -> tuple[str, str]:
    for env_name in ("TOOL_VERSION", "RENDER_GIT_COMMIT", "GIT_COMMIT"):
        value = os.getenv(env_name, "").strip()
        if value.lower() not in _UNRESOLVED_TOOL_VERSIONS:
            return value, env_name.lower()
    if _runtime_is_production():
        return "unknown", "unresolved_production"
    return "dev", "local_default"


def _tool_version() -> str:
    return _tool_version_with_source()[0]
def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
def compute_query_hash(tool_name: str, tool_input: Any) -> str:
    """Deterministic short SHA256 of (tool_name, tool_input)."""
    try:
        payload = json.dumps({"tool": tool_name, "input": tool_input},
                              sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr((tool_name, tool_input))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
# L20 (audit 2026-04-20): every genuinely stochastic tool must receive its
# seed before execution.  Some tools use the historical field name ``seed``
# and others use ``random_seed``; keeping the mapping here prevents the receipt
# layer from inventing a post-hoc seed that the science kernel never saw.
_STOCHASTIC_TOOL_SEED_FIELDS: dict[str, str] = {
    "run_pipeline": "random_seed",
    "fit_rv_orbit": "random_seed",
    "fit_isochrone": "random_seed",
    # fit_line_lfr may route to linmix/bootstrap; the deterministic OLS path
    # still accepts the seed for a stable provenance contract.
    "fit_line_lfr": "seed",
    "astro_statistics_toolbox": "seed",
    "fit_cosmology_mcmc": "random_seed",
    "run_cobaya_cosmology": "random_seed",
    "run_cosmology_likelihood_chain": "random_seed",
    "run_cosmology_robustness_matrix": "random_seed",
    "run_cmb_rotation_likelihood": "random_seed",
    "run_nested_sampler": "random_seed",
    "audit_published_constraint": "random_seed",
    "run_research_matrix": "random_seed",
}
_STOCHASTIC_TOOLS: frozenset[str] = frozenset(_STOCHASTIC_TOOL_SEED_FIELDS)


def prepare_reproducible_tool_input(
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], int | None, str | None]:
    """Return the exact input to execute plus its effective seed metadata.

    The seed is derived from the caller's seed-free input, then injected into a
    copy before the tool runs.  Explicit caller seeds always win.  The original
    input is never mutated.
    """
    prepared = dict(tool_input or {})
    seed_field = _STOCHASTIC_TOOL_SEED_FIELDS.get(tool_name)
    if seed_field is None:
        return prepared, None, None

    supplied = prepared.get(seed_field)
    if supplied is not None:
        try:
            seed = int(supplied)
        except (TypeError, ValueError):
            # Preserve the caller's value so the tool's normal validation path
            # can return a structured failure.  Provenance must not turn a bad
            # seed into an uncaught dispatcher exception.
            return prepared, None, None
        prepared[seed_field] = seed
        return prepared, seed, "user_provided"

    seed = int(compute_query_hash(tool_name, prepared)[:8], 16)
    prepared[seed_field] = seed
    return prepared, seed, "auto_from_input"


def reproducibility_envelope(
    tool_name: str,
    tool_input: Any,
    *,
    random_seed: int | None = None,
    random_seed_source: str | None = None,
    archive_version: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Return the minimal metadata needed to replay a tool call.
    Every tool result carries this so later analyses (golden-path tests,
    user-triggered replays, audit-log inspection) can verify that the same
    input against the same archive version would produce the same output.
    L20: the dispatcher derives and injects missing stochastic seeds *before*
    execution, then supplies the effective seed here.  This function never
    invents a seed after the science kernel has already run.
    """
    tool_version, tool_version_source = _tool_version_with_source()
    envelope: dict[str, Any] = {
        "run_id": run_id or str(uuid.uuid4()),
        "tool_version": tool_version,
        "tool_version_source": tool_version_source,
        "query_hash": compute_query_hash(tool_name, tool_input),
        "timestamp_utc": _now_utc_iso(),
    }
    if random_seed is not None:
        envelope["random_seed"] = int(random_seed)
        envelope["random_seed_source"] = random_seed_source or "user_provided"
    else:
        # Compatibility for direct normalizer callers that already placed an
        # explicit seed in the invocation input.  Missing seeds are deliberately
        # not derived here because this layer runs after execution.
        seed_field = _STOCHASTIC_TOOL_SEED_FIELDS.get(tool_name)
        if isinstance(tool_input, dict) and seed_field and tool_input.get(seed_field) is not None:
            try:
                envelope["random_seed"] = int(tool_input[seed_field])
                envelope["random_seed_source"] = "user_provided"
            except (TypeError, ValueError):
                pass
    if archive_version:
        envelope["archive_version"] = str(archive_version)
    return envelope
def result_contract(
    *,
    data_origin: str,
    analysis_status: str,
    source_urls: list[str] | None = None,
    archive_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build a normalized provenance contract."""
    origin = data_origin if data_origin in _VALID_ORIGINS else UNAVAILABLE
    status = analysis_status if analysis_status in _VALID_STATUS else PARTIAL
    warning_list = [str(item) for item in (warnings or []) if str(item).strip()]
    if origin == SYNTHETIC and status != SIMULATED_DEMO:
        status = SIMULATED_DEMO
        warning_list.append("Synthetic data may only be used as a demonstration, not as a research result.")
    if origin == UNAVAILABLE and status == COMPLETED:
        status = FAILED
    return {
        "data_origin": origin,
        "analysis_status": status,
        "source_urls": [str(item) for item in (source_urls or []) if str(item).strip()],
        "archive_ids": [str(item) for item in (archive_ids or []) if str(item).strip()],
        "warnings": warning_list,
    }
def attach_provenance(
    payload: dict[str, Any],
    *,
    data_origin: str,
    analysis_status: str,
    source_urls: list[str] | None = None,
    archive_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Return a copy of payload with provenance keys set if absent."""
    result = dict(payload)
    existing_warnings = result.get("warnings")
    merged_warnings: list[str] = []
    if isinstance(existing_warnings, list):
        merged_warnings.extend(str(item) for item in existing_warnings if str(item).strip())
    elif isinstance(existing_warnings, str) and existing_warnings.strip():
        merged_warnings.append(existing_warnings)
    merged_warnings.extend(warnings or [])
    contract = result_contract(
        data_origin=str(result.get("data_origin") or data_origin),
        analysis_status=str(result.get("analysis_status") or analysis_status),
        source_urls=list(result.get("source_urls") or source_urls or []),
        archive_ids=list(result.get("archive_ids") or archive_ids or []),
        warnings=merged_warnings,
    )
    result.update(contract)
    return _attach_nested_provenance(result)
def _attach_nested_provenance(result: dict[str, Any]) -> dict[str, Any]:
    """Add the v2 nested provenance object without replacing v1 fields."""
    existing = result.get("provenance")
    provenance = dict(existing) if isinstance(existing, dict) else {}
    extracted_field_bibcodes, extracted_field_coverage = _extract_field_bibcodes(result)
    provenance["reproducibility"] = dict(
        provenance.get("reproducibility")
        if isinstance(provenance.get("reproducibility"), dict)
        else result.get("reproducibility")
        if isinstance(result.get("reproducibility"), dict)
        else {}
    )
    datasets = _collect_datasets(result, provenance)
    provenance["datasets"] = datasets
    _fill_top_level_provenance_from_datasets(result, datasets)
    if provenance["reproducibility"] and "archive_version" not in provenance["reproducibility"]:
        archive_version = _archive_version_from_datasets(datasets)
        if archive_version:
            provenance["reproducibility"]["archive_version"] = archive_version
    field_bibcodes = provenance.get(
        "field_bibcodes",
        result.get("field_bibcodes", extracted_field_bibcodes),
    )
    if hasattr(field_bibcodes, "to_dict"):
        field_bibcodes = field_bibcodes.to_dict()
    provenance["field_bibcodes"] = field_bibcodes if isinstance(field_bibcodes, dict) else None
    coverage = provenance.get("coverage", result.get("coverage"))
    coverage_dict = dict(coverage) if isinstance(coverage, dict) else {}
    coverage_dict.setdefault("field_level", extracted_field_coverage)
    coverage_dict.setdefault(
        "primary_citation_source",
        _primary_citation_source(provenance["field_bibcodes"], provenance["datasets"]),
    )
    provenance["coverage"] = coverage_dict
    result["provenance"] = provenance
    return result
def _extract_field_bibcodes(result: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Best-effort field-bibcode extraction from row or columnar payloads."""
    try:
        from app.services.provenance_v2.field_bibcode_extractor import FieldBibcodeExtractor
    except Exception:
        return None, None
    columns, rows = _rows_for_field_bibcode_extraction(result)
    if not columns or not rows:
        return None, None
    try:
        field_bibcodes, coverage = FieldBibcodeExtractor().extract(columns, rows)
    except Exception:
        return None, None
    coverage_dict = coverage.to_dict()
    return (
        field_bibcodes.to_dict() if coverage.available else None,
        coverage_dict,
    )
def _field_bibcodes_available(field_bibcodes: Any) -> bool:
    if not isinstance(field_bibcodes, dict):
        return False
    columns = field_bibcodes.get("columns")
    if not isinstance(columns, dict):
        return False
    return any(isinstance(values, list) and any(str(v).strip() for v in values) for values in columns.values())
def _collect_datasets(result: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    existing = provenance.get("datasets", result.get("datasets", []))
    if isinstance(existing, list):
        candidates.extend(existing)
    elif isinstance(existing, dict):
        candidates.append(existing)
    candidates.extend(_datasets_from_rows(result))
    service_dataset = _dataset_from_service_hint(result.get("service") or result.get("source"))
    if service_dataset:
        candidates.append(service_dataset)
    return _dedupe_datasets(candidates)
def _datasets_from_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    datasets: list[dict[str, Any]] = []
    for row_collection in (result.get("results"), result.get("rows"), result.get("data")):
        if not isinstance(row_collection, list):
            continue
        for row in row_collection:
            if not isinstance(row, dict):
                continue
            explicit = _row_provenance_dataset(row)
            if explicit:
                datasets.append(explicit)
            fallback = _dataset_from_service_hint(row.get("source") or row.get("service"))
            if fallback:
                datasets.append(fallback)
    return datasets
def _row_provenance_dataset(row: dict[str, Any]) -> dict[str, Any] | None:
    for container in (row, row.get("extra") if isinstance(row.get("extra"), dict) else None):
        if not isinstance(container, dict):
            continue
        dataset = container.get("_provenance_dataset") or container.get("provenance_dataset")
        if isinstance(dataset, dict):
            return dict(dataset)
    return None
def _dataset_from_service_hint(hint: Any) -> dict[str, Any] | None:
    if hint in (None, ""):
        return None
    try:
        from app.services.provenance_v2.registry_loader import dataset_from_registry
        return dataset_from_registry(str(hint))
    except Exception:
        return None
def _dedupe_datasets(candidates: list[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        dataset = {str(key): value for key, value in candidate.items() if value not in (None, "", [])}
        key = (
            str(dataset.get("ivoid") or "").lower(),
            str(dataset.get("service_key") or dataset.get("service_name") or "").lower(),
            str(dataset.get("archive_version") or "").lower(),
            str(dataset.get("article") or "").lower(),
        )
        if not any(key) or key in seen:
            continue
        seen.add(key)
        result.append(dataset)
    return result
def _fill_top_level_provenance_from_datasets(result: dict[str, Any], datasets: list[dict[str, Any]]) -> None:
    if not result.get("source_urls"):
        urls: list[str] = []
        for dataset in datasets:
            for value in dataset.get("source_urls") or []:
                if value:
                    urls.append(str(value))
            for key in ("reference_url", "credits_page_url"):
                if dataset.get(key):
                    urls.append(str(dataset[key]))
        result["source_urls"] = _dedupe_strings(urls)
    if not result.get("archive_ids"):
        ids: list[str] = []
        for dataset in datasets:
            for value in dataset.get("archive_ids") or []:
                if value:
                    ids.append(str(value))
            for key in ("ivoid", "service_key"):
                if dataset.get(key):
                    ids.append(str(dataset[key]))
        result["archive_ids"] = _dedupe_strings(ids)
def _archive_version_from_datasets(datasets: list[dict[str, Any]]) -> str | None:
    versions = _dedupe_strings(str(dataset.get("archive_version")) for dataset in datasets if dataset.get("archive_version"))
    if not versions:
        return None
    return versions[0] if len(versions) == 1 else ", ".join(versions)
def _primary_citation_source(field_bibcodes: Any, datasets: list[dict[str, Any]]) -> str:
    if _field_bibcodes_available(field_bibcodes):
        return "field_level"
    if not datasets:
        return "none"
    if any(str(dataset.get("source_authority") or "") != "project_registry" for dataset in datasets):
        return "table_level"
    return "registry"
def _dedupe_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
def _rows_for_field_bibcode_extraction(result: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    columns = [str(column) for column in result.get("columns", []) if str(column).strip()]
    rows = result.get("rows")
    if isinstance(rows, list) and rows:
        if isinstance(rows[0], dict):
            row_dicts = [row for row in rows if isinstance(row, dict)]
            if not columns and row_dicts:
                columns = [str(column) for column in row_dicts[0].keys()]
            return columns, row_dicts
        if columns:
            return columns, [
                {
                    column: row[index] if isinstance(row, (list, tuple)) and index < len(row) else None
                    for index, column in enumerate(columns)
                }
                for row in rows
            ]
    data = result.get("data")
    if isinstance(data, list) and data:
        if isinstance(data[0], dict):
            row_dicts = [row for row in data if isinstance(row, dict)]
            if not columns and row_dicts:
                columns = [str(column) for column in row_dicts[0].keys()]
            return columns, row_dicts
        if columns:
            return columns, [
                {
                    column: row[index] if isinstance(row, (list, tuple)) and index < len(row) else None
                    for index, column in enumerate(columns)
                }
                for row in data
            ]
    if isinstance(data, dict) and data:
        if not columns:
            columns = [str(column) for column in data.keys()]
        lengths = [
            len(values)
            for values in data.values()
            if isinstance(values, list)
        ]
        row_count = max(lengths, default=0)
        return columns, [
            {
                column: (
                    values[index]
                    if isinstance(values := data.get(column), list) and index < len(values)
                    else None
                )
                for column in columns
            }
            for index in range(row_count)
        ]
    results = result.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        row_dicts = [row for row in results if isinstance(row, dict)]
        columns = [str(column) for column in row_dicts[0].keys()] if row_dicts else []
        return columns, row_dicts
    return [], []
# Tool-name → default classification mapping for current HEAD tools.
# Keep in sync with TOOLS in app/services/ai_tools.py.  The union of the three
# sets MUST equal the tool registry, or unclassified tools silently fall
# through to UNAVAILABLE/PARTIAL and the LLM downgrades their results.
_DATA_TOOLS = {
    "search_objects", "run_adql", "get_object_info", "get_object_dossier",
    "query_transients", "search_lightcurve", "crossmatch_catalogs",
    "describe_tap_table", "get_last_search_results", # F6.1 / F6.2: new high-level astro helpers
    "query_gaia_cluster", "get_extinction",
    # J3: direct SDSS SkyServer connection — same category as run_adql, both are data fetches
    "run_sdss_sql",
    # MW v_esc helper — focused Gaia DR3 high-velocity candidate fetch
    "query_high_velocity_stars",
}
_COMPUTE_TOOLS = {
    "run_python", "generate_pipeline", "run_pipeline", "validate_analysis",
    "generate_paper_draft", "fit_isochrone", "estimate_photo_z",
    "analyze_spectrum", "analyze_spectrum_pro",
    "sensitivity_analysis", "reduce_ccd_image",
    "solve_astrometry", "extract_photometry", "extract_sources",
    "classify_transient", "compute_galaxy_sfr", "fit_rv_orbit", "fit_sersic_morphology",
    "x_ray_spectral_fit", "pulsar_derived_quantities",
    "analyze_cross_wavelength", "fit_cosmology_mcmc", "run_cobaya_cosmology",
    "get_cosmology_run_status",
    "get_async_job_status",
    "run_user_tool",
    "load_cosmology_data_product",
    "build_cosmology_likelihood", "build_cosmology_robustness_matrix",
    "run_cosmology_likelihood_chain", "run_cosmology_robustness_matrix",
    "run_dark_energy_evidence_matrix",
    "run_cmb_rotation_likelihood",
    "run_nested_sampler", "evaluate_chain_diagnostics", "run_research_matrix",
    # 2026-05-28 backfill: DESI BAO Alcock-Paczynski per-bin diagnostic.
    # Added in f0622ca but never classified; was falling through to
    # UNAVAILABLE/PARTIAL at runtime.
    "assess_bao_bin_anomaly",
    # audit_published_constraint (2026-05-27): reproduces a published constraint
    # and reports n-sigma tension; COMPUTE because it runs the likelihood chain.
    "audit_published_constraint",
    # compute_theory_cmb_spectrum (2026-05-31, M1-A): CAMB forward theory CMB
    # power spectrum; COMPUTE — a model prediction, not a measurement.
    "compute_theory_cmb_spectrum",
    # PART Y Batch 1 (audit follow-up): newer compute tools — line-relation
    # fitting on cited literature tables, luminosity-distance comparison
    # across cosmologies, gravitational-lensing demagnification of a sample.
    "prepare_spectral_measurements", "fit_line_lfr",
    "astro_statistics_toolbox",
    "verify_scalar_derivation",
    "compare_luminosity_distances", "demagnify_sample",
}
_REFERENCE_TOOLS = {
    "search_literature", "read_arxiv_paper", "extract_literature_tables", "research_workflow", "generate_proposal", "get_followup_recommendation",
    "list_user_tools",
    "classify_literature_relevance", "verify_research_facts",
    # PART Y Batch 1 (audit follow-up): export_sample_table emits a
    # citable machine-readable table from a cached sample, no new analysis.
    "export_sample_table",
    "list_cosmology_datasets", "plan_research_program",
    "build_evidence_graph", "export_research_report",
    "mine_paper_tools", "run_paper_tool_mining_batch",
    "build_tool_ontology", "build_tool_gap_matrix",
    "rank_tool_implementation_queue", "build_paper_mining_candidate_pool",
    "run_paper_tool_mining_loop",
    # Formal-registry control-plane tools expose a catalog or queue an Audit;
    # neither returns a scientific measurement.  Keep them in the reference
    # class so a successful queue operation cannot be mistaken for completed
    # scientific computation.  Their own payloads additionally set
    # publication_ready=false and __do_not_claim__=true.
    "discover_registered_workflows", "start_registered_workflow",
}
# Introspection helper for tests / CI: full known tool set.
ALL_KNOWN_TOOLS = _DATA_TOOLS | _COMPUTE_TOOLS | _REFERENCE_TOOLS


def _taint_unverified_run_python_randomness(
    tool_name: str,
    result: dict[str, Any],
    tool_input: Any,
) -> dict[str, Any]:
    """Fail closed when a Python cell mixes real-data access with bare RNG.

    ``_exec_run_python`` normally applies the same decision before execution,
    but normalization is also called directly by dispatchers and tests.  A
    successful compute result must therefore not default to ``real_archive``
    solely because its code touched an archive helper before printing an
    unrelated random value.  Structurally recognised MCMC/bootstrap usage is
    exempt and retains the normal provenance path.
    """
    if tool_name != "run_python" or result.get("success") is False or result.get("error"):
        return result
    if not isinstance(tool_input, dict):
        return result
    code = tool_input.get("code")
    if not isinstance(code, str) or not code.strip():
        return result
    try:
        from app.services.synthetic_code_detector import analyze

        detection = analyze(code)
    except Exception:
        return result
    if not detection.has_np_random or detection.actual_mcmc_usage:
        return result

    warning = (
        "run_python used random-number generation outside a recognised "
        "MCMC/bootstrap workflow; its numerical output is non-claimable."
    )
    tainted = dict(result)
    warnings = tainted.get("warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    else:
        warnings = list(warnings)
    if warning not in warnings:
        warnings.append(warning)
    tainted.update({
        "__tool_status__": "SYNTHETIC",
        "__do_not_claim__": True,
        "__message_to_model__": (
            "This run_python cell used random-number generation outside a "
            "recognised MCMC/bootstrap workflow. Its numerical output MUST "
            "NOT be presented as an observational or publication-ready result."
        ),
        "data_origin": SYNTHETIC,
        "analysis_status": SIMULATED_DEMO,
        "warnings": warnings,
    })
    return tainted


def _taint_synthetic_transient_classifier(
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Prevent the synthetic-training transient demo from being laundered.

    ``classify_transient`` currently has no fitted artefact trained and
    validated on labelled survey observations.  Even if a caller strips the
    classifier's own provenance fields or supplies contradictory
    ``real_archive/completed/publication_ready`` fields, normalization must
    preserve the scientific limitation of the model itself.
    """
    if (
        tool_name != "classify_transient"
        or result.get("error")
        or result.get("success") is False
    ):
        return result

    warning = (
        "Transient candidate labels and scores come from a model trained only "
        "on generated feature distributions; they are uncalibrated, "
        "non-claimable software-demo output."
    )
    warnings = result.get("warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    else:
        warnings = list(warnings)
    if warning not in warnings:
        warnings.append(warning)

    forced = {
        "__tool_status__": "SYNTHETIC",
        "__do_not_claim__": True,
        "__message_to_model__": (
            "The transient candidate class and confidence were produced by a "
            "model trained entirely on generated feature distributions. You "
            "MUST NOT report either value as a scientific classification, "
            "observational conclusion, calibrated probability, preliminary "
            "finding, or publication result."
        ),
        "data_origin": SYNTHETIC,
        "analysis_status": SIMULATED_DEMO,
        "publication_ready": False,
        "preliminary_ready": False,
        "scientific_conclusion_ready": False,
        "classification_claimable": False,
        "confidence_calibrated": False,
        "warnings": warnings,
    }
    # Put the warning banner first in JSON iteration order and do not let
    # contradictory keys in the original payload overwrite it.
    tainted = dict(forced)
    tainted.update({key: value for key, value in result.items() if key not in forced})
    return tainted


def _append_reproducibility_warning(result: dict[str, Any], warning: str) -> None:
    warnings = result.get("warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    else:
        warnings = list(warnings)
    if warning not in warnings:
        warnings.append(warning)
    result["warnings"] = warnings


def _reported_random_seed(result: dict[str, Any]) -> int | None:
    for key in ("random_seed", "seed"):
        value = result.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _reconcile_reproducibility_seed(result: dict[str, Any]) -> dict[str, Any]:
    """Keep a tool-reported seed and its receipt identical, failing closed on drift."""
    envelope_raw = result.get("reproducibility")
    if not isinstance(envelope_raw, dict):
        return result
    reported_seed = _reported_random_seed(result)
    recorded_seed = envelope_raw.get("random_seed")
    if reported_seed is None or recorded_seed is None:
        return result
    try:
        recorded_seed_int = int(recorded_seed)
    except (TypeError, ValueError):
        recorded_seed_int = -1
    if reported_seed == recorded_seed_int:
        return result

    tainted = dict(result)
    envelope = dict(envelope_raw)
    envelope["requested_random_seed"] = recorded_seed
    envelope["random_seed"] = reported_seed
    envelope["random_seed_source"] = "tool_reported_mismatch"
    tainted["reproducibility"] = envelope
    warning = (
        "The science tool reported a different random seed than the dispatcher "
        "requested; this result is not reproducible and cannot support claims."
    )
    _append_reproducibility_warning(tainted, warning)
    tainted["__do_not_claim__"] = True
    tainted["publication_ready"] = False
    if tainted.get("chain_tier") == "publication":
        tainted["chain_tier"] = "blocked"
    if "scientific_conclusion_ready" in tainted:
        tainted["scientific_conclusion_ready"] = False
    diagnostics = tainted.get("chain_diagnostics")
    if isinstance(diagnostics, dict):
        diagnostics = dict(diagnostics)
        diagnostics["publication_ready"] = False
        diagnostics["publication_blocker"] = "random_seed_mismatch"
        tainted["chain_diagnostics"] = diagnostics
    publication_gate = tainted.get("publication_gate")
    if isinstance(publication_gate, dict):
        publication_gate = dict(publication_gate)
        reasons = list(publication_gate.get("reasons") or [])
        if "random_seed_mismatch" not in reasons:
            reasons.append("random_seed_mismatch")
        publication_gate["eligible"] = False
        publication_gate["reasons"] = reasons
        tainted["publication_gate"] = publication_gate
    tainted["__message_to_model__"] = warning
    return tainted


def _taint_unversioned_publication_result(result: dict[str, Any]) -> dict[str, Any]:
    """Block publication claims when a production result lacks an immutable build id."""
    if not _runtime_is_production():
        return result
    envelope = result.get("reproducibility")
    if not isinstance(envelope, dict):
        return result
    version = str(envelope.get("tool_version") or "").strip().lower()
    runtime_version, _runtime_version_source = _tool_version_with_source()
    if (
        version not in _UNRESOLVED_TOOL_VERSIONS
        and runtime_version.strip().lower() not in _UNRESOLVED_TOOL_VERSIONS
    ):
        return result
    publication_claim = (
        result.get("publication_ready") is True
        or str(result.get("chain_tier") or "").lower() == "publication"
        or result.get("scientific_conclusion_ready") is True
    )
    if not publication_claim:
        return result

    tainted = dict(result)
    warning = (
        "Production scientific output has no immutable tool/code version; "
        "publication readiness is blocked until the deployed commit is recorded."
    )
    _append_reproducibility_warning(tainted, warning)
    tainted["__do_not_claim__"] = True
    tainted["publication_ready"] = False
    if str(tainted.get("chain_tier") or "").lower() == "publication":
        tainted["chain_tier"] = "blocked"
    if "scientific_conclusion_ready" in tainted:
        tainted["scientific_conclusion_ready"] = False
    diagnostics = tainted.get("chain_diagnostics")
    if isinstance(diagnostics, dict):
        diagnostics = dict(diagnostics)
        diagnostics["publication_ready"] = False
        diagnostics["publication_blocker"] = "unversioned_tool_build"
        tainted["chain_diagnostics"] = diagnostics
    publication_gate = tainted.get("publication_gate")
    if isinstance(publication_gate, dict):
        publication_gate = dict(publication_gate)
        reasons = list(publication_gate.get("reasons") or [])
        if "unversioned_tool_build" not in reasons:
            reasons.append("unversioned_tool_build")
        publication_gate["eligible"] = False
        publication_gate["reasons"] = reasons
        tainted["publication_gate"] = publication_gate
    tainted["__message_to_model__"] = warning
    return tainted


def normalize_tool_result(
    tool_name: str,
    result: Any,
    *,
    tool_input: Any = None,
    random_seed: int | None = None,
    random_seed_source: str | None = None,
    archive_version: str | None = None,
) -> dict[str, Any]:
    """Ensure every tool result is a dict with provenance metadata + envelope.
    The reproducibility envelope is added once per call so the payload
    always carries run_id / tool_version / query_hash / timestamp and (when
    supplied) random_seed + archive_version.  Existing envelope fields on
    the result are respected (idempotent — called multiple times on the
    same payload does not re-stamp).
    """
    if not isinstance(result, dict):
        result = {"value": result}
    # The dispatcher stamps the authoritative receipt for *this* execution.
    # A tool-returned envelope is untrusted payload: allowing it to overwrite
    # run_id/query_hash/tool_version/source made stale or caller-forged receipts
    # look current.  Preserve it only as explicitly labelled upstream context;
    # never merge it into the authoritative envelope.
    envelope = reproducibility_envelope(
        tool_name,
        tool_input if tool_input is not None else result.get("_tool_input"),
        random_seed=random_seed,
        random_seed_source=random_seed_source,
        archive_version=archive_version,
    )
    existing_envelope = result.get("reproducibility")
    if isinstance(existing_envelope, dict):
        envelope["upstream_receipt"] = {
            key: existing_envelope[key]
            for key in (
                "run_id",
                "tool_version",
                "tool_version_source",
                "query_hash",
                "source",
                "timestamp",
                "timestamp_utc",
                "archive_version",
            )
            if key in existing_envelope
        }
    result = dict(result)
    result["reproducibility"] = envelope
    result = _reconcile_reproducibility_seed(result)
    result = _taint_unversioned_publication_result(result)
    result = _taint_unverified_run_python_randomness(tool_name, result, tool_input)
    result = _taint_synthetic_transient_classifier(tool_name, result)
    # R4: best-effort unit + frame annotation for well-known astronomical
    # column names (ra/dec → deg + ICRS, parallax → mas, teff → K, …).
    # Idempotent; pre-existing `*_unit` siblings win.
    try:
        from app.services.measurement import annotate_known_fields
        annotate_known_fields(result)
    except Exception:
        pass
    # R3: automatically run sanity checks and fold them into the result's
    # warnings list so the UI can surface a ⚠ chip per offending field.
    # We also emit a Prometheus counter per warning class so ops can see
    # the rate of physically-suspicious values at a glance.
    sanity = numeric_sanity_warnings(result)
    if sanity:
        existing = result.get("warnings") or []
        if isinstance(existing, str):
            existing = [existing]
        existing_set = {str(w) for w in existing}
        for w in sanity:
            if w not in existing_set:
                existing.append(w)
        result["warnings"] = existing
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "sanity_warning_total", float(len(sanity)), tool=tool_name,
            )
        except Exception:
            pass
    # F2.1: detect empty tool returns BEFORE we stamp COMPLETED. An ADQL
    # query that returned 0 rows is a legitimate "no data" outcome, not a
    # success — the LLM must not derive any claims from it.  We inject a
    # machine-readable banner so the model literally cannot miss it.
    is_empty = _is_empty_payload(tool_name, result)
    if is_empty:
        result = _inject_empty_banner(result, tool_name)
        try:
            from app.observability.metrics import record_counter
            record_counter("empty_tool_result_total", 1.0, tool=tool_name)
        except Exception:
            pass
    if "data_origin" in result and "analysis_status" in result:
        # Still inject the banner if we detected empty, even on a pre-stamped result
        return _attach_nested_provenance(result)
    if result.get("error"):
        if _has_partial_payload(tool_name, result) and result.get("__do_not_claim__") is not True:
            result = _inject_partial_banner(result, tool_name)
            return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=PARTIAL)
        result = _inject_failed_banner(result, tool_name)
        return attach_provenance(result, data_origin=UNAVAILABLE, analysis_status=FAILED)
    if is_empty:
        # Clean-ran-but-no-data path
        if tool_name in _COMPUTE_TOOLS:
            origin = REAL_ARCHIVE
        elif tool_name in _DATA_TOOLS or tool_name in _REFERENCE_TOOLS:
            origin = REAL_ARCHIVE
        else:
            origin = UNAVAILABLE
        return attach_provenance(result, data_origin=origin, analysis_status=EMPTY)
    if tool_name in _DATA_TOOLS:
        return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=COMPLETED)
    if tool_name in _COMPUTE_TOOLS:
        success = bool(result.get("success", True))
        # Compute tools almost always operate on data that originated in a real
        # archive (e.g. run_python analyzes the Gaia cache; fit_isochrone reads
        # SDSS photometry).  Default the origin to REAL_ARCHIVE; any tool that
        # genuinely operates on user-uploaded data (read_fits_header, FITS
        # reduction pipeline) should set data_origin explicitly in its result.
        origin = REAL_ARCHIVE if success else UNAVAILABLE
        status = COMPLETED if success else FAILED
        if not success:
            if _has_partial_payload(tool_name, result) and result.get("__do_not_claim__") is not True:
                result = _inject_partial_banner(result, tool_name)
                return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=PARTIAL)
            result = _inject_failed_banner(result, tool_name)
        return attach_provenance(result, data_origin=origin, analysis_status=status)
    if tool_name in _REFERENCE_TOOLS:
        return attach_provenance(result, data_origin=REAL_ARCHIVE, analysis_status=COMPLETED)
    return attach_provenance(result, data_origin=UNAVAILABLE, analysis_status=PARTIAL)
def _is_empty_payload(tool_name: str, result: dict[str, Any]) -> bool:
    """F2.1: decide whether a tool return has no data to back any claim.
    Conservative — we mark empty only on signals that are unambiguously
    "no result", not on merely small results.
    """
    if result.get("error"):
        return False  # FAILED path handles this
    # ADQL / VO / TAP
    if "row_count" in result:
        try:
            if int(result["row_count"]) == 0:
                return True
        except (ValueError, TypeError):
            pass
    rows = result.get("rows")
    if isinstance(rows, list) and not rows and result.get("row_count", 0) == 0:
        return True
    # search_objects / crossmatch / query_transients
    results_field = result.get("results")
    if isinstance(results_field, list) and not results_field:
        return True
    # run_python — empty stdout and no figures and no variables
    if tool_name == "run_python":
        stdout = result.get("stdout", "") or ""
        figures = result.get("figures") or []
        variables = result.get("variables") or {}
        if result.get("success") is True and not stdout.strip() and not figures and not variables:
            return True
    return False
def _has_partial_payload(tool_name: str, result: dict[str, Any]) -> bool:
    """Return True when a failed tool still produced citeable partial output.
    This is intentionally narrow for now.  A failed `run_python` cell can still
    print useful statistics or create figures before a later statement raises;
    treating that as a total failure hides the useful output from the model and
    the user.
    """
    if tool_name != "run_python":
        return False
    stdout = str(result.get("stdout") or "").strip()
    figures = result.get("figures") or []
    variables = result.get("variables") or {}
    return bool(stdout or figures or variables)
# G1.5c: "retry"/"fallback"/"simulate" etc. appearing in a tool error string
# can be read by the LLM as instructions ("prompt injection via error text").
# Sanitise them before we splice error messages into __message_to_model__.
_INSTRUCTION_LIKE_TOKENS = {
    r"\bretry\b": "(the tool failed; you cannot retry it this turn)",
    r"\btry\s+again\b": "(the tool failed; you cannot try it again this turn)",
    r"\bnarrower\s+parameters\b": "(the call was rejected)",
    r"\bfall[-\s]?back\b": "(the call failed)",
    r"\bfallback\b": "(the call failed)",
    r"\bsimulat(?:e|ed|ion)\b": "[REDACTED — generating synthetic data is forbidden]",
    r"\bsynthetic\b": "[REDACTED]",
    r"\bmock(?:\s+data)?\b": "[REDACTED]",
    r"\bgenerate\s+(?:realistic|example|synthetic|fake)\b": "[REDACTED]",
}
def _sanitize_error_message(err: str) -> str:
    """G1.5c: neutralise error-string phrasings that the LLM might read as
    instructions.  Preserves the original error if sanitization is a no-op.
    """
    import re as _re
    if not err:
        return ""
    cleaned = str(err)
    for pat, replacement in _INSTRUCTION_LIKE_TOKENS.items():
        cleaned = _re.sub(pat, replacement, cleaned, flags=_re.IGNORECASE)
    return cleaned
def _inject_empty_banner(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """F2.1: prepend a machine-readable banner so the LLM cannot miss it.
    Model sees __tool_status__, __do_not_claim__, __message_to_model__,
    __suggested_next_step__ as the first keys of the tool_result dict.
    """
    banner = {
        "__tool_status__": "EMPTY",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Tool `{tool_name}` ran but returned no data (0 rows / empty "
            f"result). You MUST NOT claim any numerical result derived from "
            f"this call. Emit a <tools_returned_nothing/> structured "
            f"abstention instead of generating synthetic data to substitute."
        ),
        "__suggested_next_step__": _suggest_next_step(tool_name),
    }
    # Banner keys go FIRST so anything the model reads left-to-right hits
    # them before the actual (empty) payload.
    new_result = dict(banner)
    new_result.update(result)
    return new_result
def _inject_failed_banner(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """F2.1 + G1.5c: same idea as empty, but for explicit tool failures.
    Error strings are passed through _sanitize_error_message to remove
    instruction-like tokens that could be read by the LLM as commands.
    """
    raw_err = str(result.get("error") or "unknown").strip()
    err = _sanitize_error_message(raw_err)
    banner = {
        "__tool_status__": "FAILED",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"Tool `{tool_name}` failed with: {err!r}. You MUST NOT claim "
            f"any numerical result derived from this call, and you MUST "
            f"NOT substitute synthetic data to 'complete' the analysis. "
            f"Emit a <tools_returned_nothing/> structured abstention instead."
        ),
        "__suggested_next_step__": _suggest_next_step(tool_name, error=err),
    }
    new_result = dict(banner)
    new_result.update(result)
    return new_result
def _inject_partial_banner(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Prepend a banner for failed tools that produced usable partial output."""
    raw_err = str(result.get("error") or "unknown").strip()
    err = _sanitize_error_message(raw_err)
    banner = {
        "__tool_status__": "PARTIAL",
        "__partial_output__": True,
        "__message_to_model__": (
            f"Tool `{tool_name}` produced partial output before failing with: {err!r}. "
            "You MAY cite only values that appear directly in stdout, figures, or "
            "variables from this tool result, and you MUST state that a later "
            "step in the same Python cell failed. Do NOT infer any missing rows, "
            "plots, or follow-up values from the failed part."
        ),
        "__suggested_next_step__": _suggest_next_step(tool_name, error=err),
    }
    new_result = dict(banner)
    new_result.update(result)
    return new_result
def _suggest_next_step(tool_name: str, error: str | None = None) -> str:
    """Tool-specific next-step hint that the model can echo into a
    <tools_returned_nothing suggested_next_step="..."/> tag.
    G1.5c: avoid the word "retry" in these strings — the LLM reads
    tool error text as context and can interpret "retry" as an
    instruction to just try again (often with synthetic data). Use
    neutral phrasing that describes what the USER should do, not what
    the model can do.
    """
    if error:
        lower = error.lower()
        if "timed out" in lower or "timeout" in lower:
            return "The user can narrow the scope (smaller radius, fewer sources) and submit again, or wait for the service to recover."
        if "oom" in lower or "memoryerror" in lower:
            return f"The user can reduce the data volume before calling `{tool_name}` again."
        if "import" in lower:
            return "The requested library is not available in this sandbox."
    if tool_name == "run_adql":
        return "The user can widen the query (larger cone radius / looser quality cuts), verify the table + column names exist, or try a different archive."
    if tool_name == "run_python":
        return "The user should check that required inputs are present and that the code produces a print statement or figure."
    if tool_name in {"search_objects", "crossmatch_catalogs", "query_transients"}:
        return "The user can widen the cone radius, relax magnitude cuts, or confirm the target name / coordinates."
    if tool_name == "search_literature":
        return "The user can broaden the keyword list or try a different archive (ADS vs arXiv)."
    return "The user can adjust parameters or provide the target values explicitly."
def numeric_sanity_warnings(payload: Any) -> list[str]:
    """Find common physically suspicious numeric outputs.
    Phase 1 / R3 extends the original two checks (zero uncertainty, zero
    abundance) with:
    - negative parallax (unphysical; Gaia allows it but the derived
      distance is meaningless without a prior)
    - RA out of [0, 360)
    - Dec out of [-90, 90]
    - redshift below -0.01 (blueshifts inside the Local Group are ~-0.001;
      anything more negative is usually a unit error)
    - log g outside [0, 6] (stars span ~0-5; WDs up to ~9 but flagged)
    - absolute magnitudes fainter than +20 (likely error-bar leak)
    - negative masses / radii / luminosities
    Each warning references the dotted path so the UI can highlight the
    exact offending field.
    """
    warnings: list[str] = []
    def _walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                key_l = str(key).lower()
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    num = float(item)
                    if math.isfinite(num):
                        # Zero-valued uncertainty (original check)
                        if num == 0.0 and any(token in key_l for token in ("noise", "sigma", "uncert", "error")):
                            warnings.append(f"{child_path} is exactly zero; check units and noise propagation.")
                        # Zero abundance ratios
                        if num == 0.0 and key_l in {"c_o", "n_o", "c/o", "n/o"}:
                            warnings.append(f"{child_path} is exactly zero; check abundance accounting.")
                        # Negative parallax (physical but suspicious)
                        if "parallax" in key_l and num < 0:
                            warnings.append(
                                f"{child_path} = {num} is negative; Gaia allows it but derived distance is meaningless without a prior."
                            )
                        # RA range
                        if key_l in {"ra", "ra_deg", "raj2000"} and not (0.0 <= num < 360.0):
                            warnings.append(f"{child_path} = {num} is outside [0, 360).")
                        # Dec range
                        if key_l in {"dec", "dec_deg", "dej2000"} and not (-90.0 <= num <= 90.0):
                            warnings.append(f"{child_path} = {num} is outside [-90, 90].")
                        # Redshift floor
                        if key_l in {"z", "redshift", "rvz_redshift"} and num < -0.01:
                            warnings.append(f"{child_path} = {num}: blueshift this large is usually a unit or sign error.")
                        # log g
                        if key_l in {"log_g", "logg"} and not (0.0 <= num <= 6.5):
                            warnings.append(f"{child_path} = {num} is outside [0, 6.5]; likely a unit mistake.")
                        # Magnitudes: sanity not strictly bounded, but
                        # absolute magnitudes > 20 or < -30 are unphysical
                        if "abs_mag" in key_l and (num < -30 or num > 20):
                            warnings.append(f"{child_path} = {num} is outside [-30, 20]; check distance modulus.")
                        # Negative physical scalars
                        if key_l in {"mass", "mass_solar", "radius_solar", "radius", "luminosity", "l_bol"} and num < 0:
                            warnings.append(f"{child_path} = {num} is negative; unphysical.")
                _walk(item, child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value[:200]):
                _walk(item, f"{path}[{index}]")
    _walk(payload)
    return warnings
