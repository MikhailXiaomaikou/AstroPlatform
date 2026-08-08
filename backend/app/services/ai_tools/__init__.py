"""Tool definitions and executors for the Claude AI research agent.

Each tool is a function Claude can call. The agent loop in chat.py
handles the tool_use → result → next message cycle automatically.
"""

import asyncio
import hashlib
import logging
import math
import threading
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.pipeline.storage_auth import (
    PipelineStorageInputError,
    bind_pipeline_storage_inputs,
)

logger = logging.getLogger(__name__)


# ── Tool Definitions (Anthropic tool_use format) ──

# In-memory cache for search/query results per runtime session (keyed by a simple token).
# Stores tuples of (value, timestamp) for TTL-based expiry.
_search_result_cache: dict[str, tuple[Any, float]] = {}
# Physical cache keys are deliberately kept for compatibility with the Python
# sandbox, but ownership is tracked separately.  A suffix alone is not a safe
# ownership boundary (session ids are user-controlled strings and one id can be
# a suffix of another), so readers must consult this map instead of scanning or
# falling back to a bare ``latest`` key.
_search_result_cache_owners: dict[str, str | None] = {}
_search_result_cache_lock = threading.RLock()
MAX_ADQL_RESULT_HISTORY = 8
_CACHE_TTL_SECONDS = 1800  # 30 minutes


def _store_cache_value(key: str, results: Any, owner: str | None) -> None:
    with _search_result_cache_lock:
        _search_result_cache[key] = (results, time.time())
        _search_result_cache_owners[key] = owner
        # Evict expired entries first
        now = time.time()
        expired = [
            k
            for k, (_, ts) in _search_result_cache.items()
            if now - ts > _CACHE_TTL_SECONDS
        ]
        for k in expired:
            del _search_result_cache[k]
            _search_result_cache_owners.pop(k, None)
        # Keep only the latest entries; multiple keys are used per session.
        if len(_search_result_cache) > 200:
            oldest = next(iter(_search_result_cache))
            del _search_result_cache[oldest]
            _search_result_cache_owners.pop(oldest, None)


def store_search_results(key: str, results: Any) -> None:
    """Cache a value in the legacy/default namespace.

    Runtime tool code must use :func:`store_session_results`.  This primitive
    remains public for old single-user integrations and tests that intentionally
    seed the isolated ``default`` namespace.
    """
    _store_cache_value(key, results, None)


def get_cached_results(key: str) -> Any | None:
    with _search_result_cache_lock:
        entry = _search_result_cache.get(key)
        if entry is None:
            return None
        value, timestamp = entry
        if time.time() - timestamp > _CACHE_TTL_SECONDS:
            del _search_result_cache[key]
            _search_result_cache_owners.pop(key, None)
            return None
        return value


def _normalized_session_id(session_id: str | None) -> str | None:
    sid = str(session_id or "").strip()
    return sid if sid and sid != "default" else None


def build_trusted_python_session_id(
    *,
    user_id: str | None,
    chat_session_id: str | None,
    requested_session_id: str | None,
    anonymous_scope: str | None = None,
) -> str:
    """Derive an opaque runtime namespace from server-trusted ownership.

    ``requested_session_id`` originates in the browser and is never an
    authority by itself.  Authenticated calls are separated by user and (when
    present) the already ownership-validated chat id. Anonymous chat requests
    must supply their server-generated request scope, intentionally giving up
    cross-request cache persistence rather than sharing another visitor's data.

    Identity-less internal callers retain the legacy default/named namespace;
    HTTP entry points must always provide either ``user_id`` or
    ``anonymous_scope``.
    """
    principal = str(user_id or anonymous_scope or "").strip()
    if not principal:
        return str(requested_session_id or "default").strip() or "default"
    raw = str(requested_session_id or "default").strip() or "default"
    chat = str(chat_session_id or "").strip()
    digest = hashlib.sha256(
        b"standard-astro-python-session-v2\0"
        + principal.encode("utf-8")
        + b"\0"
        + chat.encode("utf-8")
        + b"\0"
        + raw.encode("utf-8")
    ).hexdigest()
    prefix = "trusted-v2" if user_id else "anonymous-v2"
    return f"{prefix}-{digest}"


def _session_cache_key(prefix: str, session_id: str | None) -> str | None:
    """Return the physical key for a non-default session.

    The idempotent case supports tool results that return their physical cache
    key and are then passed back to a later tool in the same session.
    """
    sid = _normalized_session_id(session_id)
    if sid is None:
        return None
    suffix = f":{sid}"
    return prefix if prefix.endswith(suffix) else f"{prefix}{suffix}"


def _resolved_session_cache_key(key: str, session_id: str | None) -> str:
    return _session_cache_key(key, session_id) or key


def store_session_results(key: str, session_id: str | None, results: Any) -> str:
    """Store ``results`` in exactly one session namespace and return its key."""
    physical_key = _resolved_session_cache_key(key, session_id)
    owner = _normalized_session_id(session_id)
    if owner and owner.startswith("trusted-v2-"):
        from app.services.code_executor import is_session_deleted

        if is_session_deleted(owner):
            clear_session_cached_results(owner)
            return physical_key
    _store_cache_value(physical_key, results, owner)
    return physical_key


def get_session_cached_results(key: str, session_id: str | None) -> Any | None:
    """Read only data owned by ``session_id``; never fall back globally."""
    physical_key = _resolved_session_cache_key(key, session_id)
    expected_owner = _normalized_session_id(session_id)
    if expected_owner and expected_owner.startswith("trusted-v2-"):
        from app.services.code_executor import is_session_deleted

        if is_session_deleted(expected_owner):
            clear_session_cached_results(expected_owner)
            return None
    with _search_result_cache_lock:
        if _search_result_cache_owners.get(physical_key) != expected_owner:
            return None
        return get_cached_results(physical_key)


def session_cache_items(session_id: str | None) -> dict[str, Any]:
    """Return a safe snapshot of cache entries visible to one session.

    Non-default sessions see only entries carrying their exact owner marker.
    The legacy default namespace sees only owner-less entries, never a named
    session's values.  Logical aliases are added by ``code_executor``.
    """
    expected_owner = _normalized_session_id(session_id)
    visible: dict[str, Any] = {}
    if expected_owner and expected_owner.startswith("trusted-v2-"):
        from app.services.code_executor import is_session_deleted

        if is_session_deleted(expected_owner):
            clear_session_cached_results(expected_owner)
            return visible
    with _search_result_cache_lock:
        for key in list(_search_result_cache):
            if _search_result_cache_owners.get(key) != expected_owner:
                continue
            value = get_cached_results(key)
            if value is not None:
                visible[key] = value
    return visible


def clear_session_cached_results(session_id: str | None) -> int:
    """Erase every cache entry owned by a named session."""
    owner = _normalized_session_id(session_id)
    if owner is None:
        return 0
    with _search_result_cache_lock:
        keys = [
            key
            for key, value in _search_result_cache_owners.items()
            if value == owner
        ]
        for key in keys:
            _search_result_cache.pop(key, None)
            _search_result_cache_owners.pop(key, None)
    return len(keys)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        numeric = float(value)
        if numeric != numeric:  # NaN
            return None
        return numeric
    except (TypeError, ValueError):
        return None


def _augment_adql_row(row: dict[str, Any]) -> dict[str, Any]:
    # Preserve original archive column names while also injecting lowercase aliases.
    # SDSS SkyServer uses mixed-case names like objID / petroMag_r / zErr, but
    # legacy analysis code often uses lowercase. Keeping both avoids KeyError
    # without hiding the real schema.
    enriched = dict(row)
    for k, v in row.items():
        enriched.setdefault(str(k).lower(), v)
    if "bp_rp" not in enriched or enriched.get("bp_rp") is None:
        bp = _coerce_float(enriched.get("phot_bp_mean_mag"))
        rp = _coerce_float(enriched.get("phot_rp_mean_mag"))
        if bp is not None and rp is not None:
            enriched["bp_rp"] = bp - rp
    if "abs_g_mag" not in enriched or enriched.get("abs_g_mag") is None:
        g_mag = _coerce_float(enriched.get("phot_g_mean_mag"))
        parallax = _coerce_float(enriched.get("parallax"))
        if g_mag is not None and parallax is not None and parallax > 0:
            distance_pc = 1000.0 / parallax
            enriched["abs_g_mag"] = g_mag - 5.0 * (math.log10(distance_pc) - 1.0)
    return enriched


def build_adql_rows(
    columns: list[str],
    data: dict[str, list[Any]],
    row_count: int,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(min(row_count, limit)):
        row = {
            col: (data.get(col, [None] * row_count)[index] if index < len(data.get(col, [])) else None)
            for col in columns
        }
        rows.append(_augment_adql_row(row))
    return rows


def augment_adql_payload(
    columns: list[str],
    data: dict[str, list[Any]],
    row_count: int,
    limit: int = 1000,
) -> tuple[list[str], dict[str, list[Any]], list[dict[str, Any]]]:
    rows = build_adql_rows(columns, data, row_count, limit=limit)
    augmented_columns = list(columns)
    derived_columns = ["bp_rp", "abs_g_mag"]
    for col in derived_columns:
        if col not in augmented_columns and any(row.get(col) is not None for row in rows):
            augmented_columns.append(col)
            data[col] = [row.get(col) for row in rows]
    return augmented_columns, data, rows


def build_adql_result_set(
    *,
    service: str,
    query: str,
    columns: list[str],
    data: dict[str, list[Any]],
    row_count: int,
    limit: int = 1000,
) -> dict[str, Any]:
    augmented_columns, augmented_data, rows = augment_adql_payload(
        list(columns),
        dict(data),
        row_count,
        limit=limit,
    )
    return {
        "service": service,
        "query": query,
        "row_count": row_count,
        "columns": augmented_columns,
        "rows": rows,
        "data": {col: augmented_data.get(col, [])[: min(row_count, limit)] for col in augmented_columns},
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }


def replace_adql_result_sets(session_id: str | None, result_sets: list[dict[str, Any]]) -> None:
    normalized = [dict(item) for item in result_sets[-MAX_ADQL_RESULT_HISTORY:] if isinstance(item, dict)]
    latest = normalized[-1] if normalized else None
    latest_rows = list(latest.get("rows", [])) if latest else []

    store_session_results("latest_adql_sets", session_id, normalized)
    if latest is not None:
        store_session_results("latest_adql_set", session_id, latest)
        store_session_results("latest_adql", session_id, latest_rows)


def store_adql_result_set(session_id: str | None, result_set: dict[str, Any]) -> None:
    existing = get_session_cached_results("latest_adql_sets", session_id)
    history = list(existing) if isinstance(existing, list) else []
    history.append(dict(result_set))
    replace_adql_result_sets(session_id, history)


# ── H2 split (2026-07-03): the ~7,700-line executor body of this file moved
# into domain modules inside this package (mirrors the H1 external split at
# the TOOLS.extend calls below). Each module keeps its schema block next to
# its executors and exports TOOL_SCHEMAS; TOOLS is reassembled below in the
# exact pre-split order so the wire-visible tool list is byte-for-byte
# unchanged. The dispatcher (_execute_tool_inner) stays here, and every
# pre-split public name is re-imported so `from app.services.ai_tools
# import X` keeps working for all existing callers and tests.
from app.services.ai_tools.catalog_queries import (  # noqa: E402
    TOOL_SCHEMAS as _CATALOG_QUERIES_TOOL_SCHEMAS,
    _auto_select_sources,  # noqa: F401  (re-export)
    _build_high_velocity_stars_query,  # noqa: F401  (re-export)
    _exec_adql,
    _exec_describe_tap_table,
    _exec_get_extinction,
    _exec_object_info,
    _exec_query_gaia_cluster,
    _exec_query_high_velocity_stars,
    _exec_run_sdss_sql,
    _exec_search,
    _suggest_actions_for_results,  # noqa: F401  (re-export)
)
from app.services.ai_tools.analysis_tools import (  # noqa: E402
    TOOL_SCHEMAS as _ANALYSIS_TOOLS_TOOL_SCHEMAS,
    _exec_analyze,
    _exec_analyze_spectrum_pro,
    _exec_pipeline,
    _exec_sensitivity_analysis,
)
from app.services.ai_tools.literature import (  # noqa: E402
    TOOL_SCHEMAS as _LITERATURE_TOOL_SCHEMAS,
    _exec_classify_literature_relevance,
    _exec_literature,
    _extract_and_cache_paper_measurements,  # noqa: F401  (re-export)
)
from app.services.ai_tools.literature_tables import (  # noqa: E402
    TOOL_SCHEMAS as _LITERATURE_TABLES_TOOL_SCHEMAS,
    _LITERATURE_SCHEMA_VERSION,  # noqa: F401  (re-export)
    _V2_MEASUREMENT_KEYS,  # noqa: F401  (re-export)
    _arxiv_retryable_exceptions,  # noqa: F401  (re-export)
    _arxiv_tool_calls,  # noqa: F401  (re-export)
    _cached_extract_arxiv_tables_payload,  # noqa: F401  (re-export)
    _exec_extract_literature_tables,
    _extract_arxiv_tables_payload_with_retry,  # noqa: F401  (re-export)
    _literature_table_cache_payload,  # noqa: F401  (re-export)
    _measurement_rows_from_cache_payload,  # noqa: F401  (re-export)
    _normalize_measurement_to_v2,  # noqa: F401  (re-export)
    _resolve_literature_measurement_cache,  # noqa: F401  (re-export)
)
from app.services.ai_tools.spectral_measurements import (  # noqa: E402
    TOOL_SCHEMAS as _SPECTRAL_MEASUREMENTS_TOOL_SCHEMAS,
    _bootstrap_ols_betas,  # noqa: F401  (re-export)
    _exec_astro_statistics_toolbox,
    _exec_prepare_spectral_measurements,
    _split_rows_by_redshift,  # noqa: F401  (re-export)
    _subsample_significance_from_betas,  # noqa: F401  (re-export)
)
from app.services.ai_tools.scalar_verification import (  # noqa: E402
    TOOL_SCHEMAS as _SCALAR_VERIFICATION_TOOL_SCHEMAS,
    execute_scalar_verification as _exec_verify_scalar_derivation,
)
from app.services.ai_tools.sample_export import (  # noqa: E402
    TOOL_SCHEMAS as _SAMPLE_EXPORT_TOOL_SCHEMAS,
    _cosmology_manifest_for,  # noqa: F401  (re-export)
    _exec_compare_luminosity_distances,
    _exec_demagnify_sample,
    _exec_export_sample_table,
)
from app.services.ai_tools.line_fitting import (  # noqa: E402
    TOOL_SCHEMAS as _LINE_FITTING_TOOL_SCHEMAS,
    _exec_fit_line_lfr,  # noqa: F401  (re-export)
    _exec_fit_line_lfr_async,
)
from app.services.ai_tools.run_python import (  # noqa: E402
    TOOL_SCHEMAS as _RUN_PYTHON_TOOL_SCHEMAS,
    _PLATFORM_REAL_DATA_READER_TOKENS,  # noqa: F401  (re-export)
    _REAL_DATA_SOURCE_PATTERNS,  # noqa: F401  (re-export)
    _VALID_DATA_SOURCES,  # noqa: F401  (re-export)
    _apply_cmd_sanity_guard,  # noqa: F401  (re-export)
    _bump_run_python_attempt_idx,  # noqa: F401  (re-export)
    _classify_sandbox_error,  # noqa: F401  (re-export)
    _exec_run_python,
    _missing_key_from_error,  # noqa: F401  (re-export)
    _session_run_python_count,  # noqa: F401  (re-export)
)
from app.services.ai_tools.research_workflow import (  # noqa: E402
    TOOL_SCHEMAS as _RESEARCH_WORKFLOW_TOOL_SCHEMAS,
    _exec_generate_paper_draft,
    _exec_generate_proposal,
    _exec_get_cached_results,
    _exec_query_transients,
    _exec_read_paper,
    _exec_research_workflow,
    _exec_run_pipeline,
    _exec_validate_analysis,
)
from app.services.ai_tools.stellar_tools import (  # noqa: E402
    TOOL_SCHEMAS as _STELLAR_TOOLS_TOOL_SCHEMAS,
    _estimate_age_from_turnoff,  # noqa: F401  (re-export)
    _exec_classify_transient,
    _exec_crossmatch_catalogs,
    _exec_estimate_photo_z,
    _exec_fit_isochrone,
    _exec_get_async_job_status,
    _exec_search_lightcurve,
    _validate_manual_attestation,  # noqa: F401  (re-export)
)
from app.services.ai_tools.object_physics import (  # noqa: E402
    TOOL_SCHEMAS as _OBJECT_PHYSICS_TOOL_SCHEMAS,
    _exec_compute_galaxy_sfr,
    _exec_fit_rv_orbit,
    _exec_fit_sersic,
    _exec_pulsar_derived,
    _exec_x_ray_spectral_fit,
)

# list_user_tools / run_user_tool have no dedicated executor module — they are
# dispatched inline to app.services.user_tools — so their schemas stay here.
_USER_TOOL_SCHEMAS = [
    {
        "name": "list_user_tools",
        "description": (
            "List tool macros created by the current user. These are safe wrappers "
            "around existing Standard Astro tools; use this before run_user_tool "
            "when the user asks for their saved custom tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "run_user_tool",
        "description": (
            "Run one user-created tool macro by tool_id with JSON arguments. "
            "A user tool cannot execute arbitrary backend code; it expands into "
            "existing audited platform tools and returns every nested tool result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_id": {"type": "string", "description": "Saved user tool id, e.g. 'quick_gaia_lookup'"},
                "arguments": {"type": "object", "description": "Arguments matching the saved user tool input_schema"},
            },
            "required": ["tool_id"],
            "additionalProperties": False,
        },
    },
]

# Exact pre-split TOOLS order (the base list before the H1 TOOLS.extend calls
# below). Verified byte-for-byte against the pre-split JSON dump of TOOLS.
_BASE_TOOL_ORDER = [
    "search_objects",
    "run_adql",
    "list_user_tools",
    "run_user_tool",
    "query_high_velocity_stars",
    "run_sdss_sql",
    "get_object_info",
    "analyze_spectrum",
    "generate_pipeline",
    "search_literature",
    "classify_literature_relevance",
    "get_last_search_results",
    "validate_analysis",
    "generate_paper_draft",
    "run_pipeline",
    "run_python",
    "generate_proposal",
    "query_transients",
    "read_arxiv_paper",
    "extract_literature_tables",
    "prepare_spectral_measurements",
    "fit_line_lfr",
    "astro_statistics_toolbox",
    "compare_luminosity_distances",
    "export_sample_table",
    "demagnify_sample",
    "research_workflow",
    "estimate_photo_z",
    "fit_isochrone",
    "get_async_job_status",
    "crossmatch_catalogs",
    "search_lightcurve",
    "classify_transient",
    "analyze_spectrum_pro",
    "sensitivity_analysis",
    "compute_galaxy_sfr",
    "fit_rv_orbit",
    "fit_sersic_morphology",
    "x_ray_spectral_fit",
    "pulsar_derived_quantities",
    "describe_tap_table",
    "query_gaia_cluster",
    "get_extinction",
]

_BASE_TOOL_SCHEMAS_BY_NAME = {
    _schema["name"]: _schema
    for _schema in (
        _USER_TOOL_SCHEMAS
        + _CATALOG_QUERIES_TOOL_SCHEMAS
        + _ANALYSIS_TOOLS_TOOL_SCHEMAS
        + _LITERATURE_TOOL_SCHEMAS
        + _LITERATURE_TABLES_TOOL_SCHEMAS
        + _SPECTRAL_MEASUREMENTS_TOOL_SCHEMAS
        + _SCALAR_VERIFICATION_TOOL_SCHEMAS
        + _SAMPLE_EXPORT_TOOL_SCHEMAS
        + _LINE_FITTING_TOOL_SCHEMAS
        + _RUN_PYTHON_TOOL_SCHEMAS
        + _RESEARCH_WORKFLOW_TOOL_SCHEMAS
        + _STELLAR_TOOLS_TOOL_SCHEMAS
        + _OBJECT_PHYSICS_TOOL_SCHEMAS
    )
}

TOOLS = [_BASE_TOOL_SCHEMAS_BY_NAME[_name] for _name in _BASE_TOOL_ORDER]
TOOLS.extend(_SCALAR_VERIFICATION_TOOL_SCHEMAS)


# ── H1 split (2026-05-26): cosmology tools (schemas + executors) extracted to
# ai_tools_cosmology.py; schemas injected via TOOLS.extend, execution delegated
# via dispatch_cosmology (mirrors solar_system / exoplanet).
from app.services.ai_tools_cosmology import (  # noqa: E402
    COSMOLOGY_TOOL_NAMES as _COSMOLOGY_TOOL_NAMES,
    COSMOLOGY_TOOL_SCHEMAS as _COSMOLOGY_TOOL_SCHEMAS,
)
TOOLS.extend(_COSMOLOGY_TOOL_SCHEMAS)

from app.services.ai_tools_paper_mining import (  # noqa: E402
    PAPER_MINING_TOOL_NAMES as _PAPER_MINING_TOOL_NAMES,
    PAPER_MINING_TOOL_SCHEMAS as _PAPER_MINING_TOOL_SCHEMAS,
)
TOOLS.extend(_PAPER_MINING_TOOL_SCHEMAS)

from app.services.ai_tools_dossier import (  # noqa: E402
    DOSSIER_TOOL_NAMES as _DOSSIER_TOOL_NAMES,
    DOSSIER_TOOL_SCHEMAS as _DOSSIER_TOOL_SCHEMAS,
)
TOOLS.extend(_DOSSIER_TOOL_SCHEMAS)

from app.services.ai_tools_imaging import (  # noqa: E402
    IMAGING_TOOL_NAMES as _IMAGING_TOOL_NAMES,
    IMAGING_TOOL_SCHEMAS as _IMAGING_TOOL_SCHEMAS,
)
TOOLS.extend(_IMAGING_TOOL_SCHEMAS)

from app.services.ai_tools_research import (  # noqa: E402
    RESEARCH_TOOL_NAMES as _RESEARCH_TOOL_NAMES,
    RESEARCH_TOOL_SCHEMAS as _RESEARCH_TOOL_SCHEMAS,
)
TOOLS.extend(_RESEARCH_TOOL_SCHEMAS)

from app.services.ai_tools_registered_workflows import (  # noqa: E402
    REGISTERED_WORKFLOW_TOOL_NAMES as _REGISTERED_WORKFLOW_TOOL_NAMES,
    REGISTERED_WORKFLOW_TOOL_SCHEMAS as _REGISTERED_WORKFLOW_TOOL_SCHEMAS,
)
TOOLS.extend(_REGISTERED_WORKFLOW_TOOL_SCHEMAS)


# ── Tool Executors ──

# User-controlled storage paths accepted by the tool layer.  Authorization is
# centralized here because the domain executors are a mix of sync/async code
# and several historically opened paths directly.  Every listed input is
# normalized and matched to a DataFile row for the authenticated user before
# dispatch; downstream code therefore never sees an unowned key.
_OWNED_STORAGE_TOOL_FIELDS: dict[str, tuple[str, ...]] = {
    "analyze_spectrum": ("fits_path",),
    "analyze_spectrum_pro": ("fits_path",),
    "fit_line_lfr": ("user_file",),
    "process_image": ("fits_path", "fits_paths"),
    "read_fits_header": ("fits_path",),
    "run_pipeline": ("input_data_id",),
    "reduce_ccd_image": ("science_fits_path", "bias_paths", "dark_paths", "flat_paths"),
    "solve_astrometry": ("fits_path",),
    "extract_photometry": ("fits_path",),
    "extract_sources": ("fits_path",),
    "fit_sersic_morphology": ("fits_path",),
    "x_ray_spectral_fit": ("pha_path",),
}


def _storage_access_failure(error_class: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "error_class": error_class,
        "__tool_status__": "FAILED",
        "__do_not_claim__": True,
        "__message_to_model__": (
            f"User-file access was refused: {message} Do not infer, replace, "
            "or fabricate data from the unavailable file."
        ),
    }


async def _authorize_pipeline_dag_storage_inputs(
    tool_input: dict,
    *,
    user_id: str | None,
) -> tuple[dict, dict[str, Any] | None]:
    """Owner-resolve every storage capability embedded in an AI pipeline DAG."""

    safe_input = deepcopy(tool_input)
    dag = safe_input.get("dag")
    if not isinstance(dag, dict):
        return safe_input, None

    try:
        from app.storage import resolve_owned_storage_key

        async def _owned(path: str) -> str:
            return await resolve_owned_storage_key(path, owner_id=user_id)

        bound_dag, bound_default = await bind_pipeline_storage_inputs(
            dag=dag,
            input_data_id=safe_input.get("input_data_id", ""),
            resolve_key=_owned,
        )
        safe_input["dag"] = bound_dag
        safe_input["input_data_id"] = bound_default
    except (PipelineStorageInputError, ValueError):
        return safe_input, _storage_access_failure(
            "invalid_storage_path", "A supplied storage path is invalid."
        )
    except Exception as exc:
        from app.storage import StorageOwnerRequired, StorageOwnershipError

        if isinstance(exc, StorageOwnerRequired):
            return safe_input, _storage_access_failure(
                "storage_owner_context_required",
                "Authenticated owner context is required for this file read.",
            )
        if isinstance(exc, StorageOwnershipError):
            return safe_input, _storage_access_failure(
                "storage_file_not_found",
                "A supplied file does not exist or is not owned by the current user.",
            )
        logger.exception("run_pipeline nested storage ownership lookup failed")
        return safe_input, _storage_access_failure(
            "storage_authorization_unavailable",
            "File ownership could not be verified safely.",
        )
    return safe_input, None


async def _authorize_tool_storage_inputs(
    tool_name: str,
    tool_input: dict,
    *,
    user_id: str | None,
) -> tuple[dict, dict[str, Any] | None]:
    """Return normalized, owner-authorized tool input or a fail-closed result."""
    if tool_name == "run_pipeline":
        return await _authorize_pipeline_dag_storage_inputs(
            tool_input,
            user_id=user_id,
        )
    fields = _OWNED_STORAGE_TOOL_FIELDS.get(tool_name, ())

    # run_python can embed arbitrary filesystem reads in source code, so an
    # owner check on its declared data_source cannot bind the actual path used
    # by pandas/astropy.  Verify the declaration when possible, then fail
    # closed until the sandbox accepts an immutable allow-list of object keys.
    data_source = str(tool_input.get("data_source") or "").strip()
    if tool_name == "run_python" and data_source.startswith(("fits:", "user_file:")):
        declared_path = data_source.split(":", 1)[1].strip()
        if not user_id:
            logger.warning("run_python user-file read refused: missing owner context")
            return tool_input, _storage_access_failure(
                "storage_owner_context_required",
                "Authenticated owner context is required for this file read.",
            )
        try:
            from app.storage import resolve_owned_storage_key

            await resolve_owned_storage_key(declared_path, owner_id=user_id)
        except ValueError:
            return tool_input, _storage_access_failure(
                "invalid_storage_path", "The declared storage path is invalid."
            )
        except Exception as exc:
            from app.storage import StorageOwnerRequired, StorageOwnershipError

            if isinstance(exc, (StorageOwnerRequired, StorageOwnershipError)):
                return tool_input, _storage_access_failure(
                    "storage_file_not_found",
                    "The declared file does not exist or is not owned by the current user.",
                )
            logger.exception("run_python storage ownership lookup failed")
            return tool_input, _storage_access_failure(
                "storage_authorization_unavailable",
                "File ownership could not be verified safely.",
            )
        logger.warning(
            "run_python owner-verified file still refused: sandbox path cannot be bound to allow-list"
        )
        return tool_input, _storage_access_failure(
            "unbound_user_file_execution",
            "The code sandbox cannot yet bind embedded file reads to the verified object key.",
        )

    if not fields:
        return tool_input, None

    safe_input = deepcopy(tool_input)
    requested: list[tuple[str, int | None, str]] = []
    for field in fields:
        value = safe_input.get(field)
        if isinstance(value, str) and value.strip():
            requested.append((field, None, value))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str) and item.strip():
                    requested.append((field, index, item))

    # process_image may use a second user FITS file as the target WCS.
    params = safe_input.get("params")
    if tool_name == "process_image" and isinstance(params, dict):
        target_wcs = params.get("target_wcs_fits")
        if isinstance(target_wcs, str) and target_wcs.strip():
            requested.append(("params.target_wcs_fits", None, target_wcs))

    if not requested:
        return safe_input, None
    if not user_id:
        logger.warning("%s user-file read refused: missing owner context", tool_name)
        return safe_input, _storage_access_failure(
            "storage_owner_context_required",
            "Authenticated owner context is required for this file read.",
        )

    try:
        from app.storage import resolve_owned_storage_key

        resolved: list[tuple[str, int | None, str]] = []
        for field, index, path in requested:
            key = await resolve_owned_storage_key(path, owner_id=user_id)
            resolved.append((field, index, key))
    except ValueError:
        return safe_input, _storage_access_failure(
            "invalid_storage_path", "A supplied storage path is invalid."
        )
    except Exception as exc:
        from app.storage import StorageOwnerRequired, StorageOwnershipError

        if isinstance(exc, (StorageOwnerRequired, StorageOwnershipError)):
            return safe_input, _storage_access_failure(
                "storage_file_not_found",
                "A supplied file does not exist or is not owned by the current user.",
            )
        logger.exception("%s storage ownership lookup failed", tool_name)
        return safe_input, _storage_access_failure(
            "storage_authorization_unavailable",
            "File ownership could not be verified safely.",
        )

    for field, index, key in resolved:
        if field == "params.target_wcs_fits":
            updated_params = dict(safe_input.get("params") or {})
            updated_params["target_wcs_fits"] = key
            safe_input["params"] = updated_params
        elif index is None:
            safe_input[field] = key
        else:
            values = list(safe_input.get(field) or [])
            values[index] = key
            safe_input[field] = values
    return safe_input, None

async def execute_tool(
    tool_name: str,
    tool_input: dict,
    api_key: str = "",
    provider_api_keys: dict[str, str] | None = None,
    python_session_id: str = "default",
    user_id: str | None = None,
    chat_session_id: str | None = None,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
    _python_session_scope_is_trusted: bool = False,
) -> dict:
    """Execute a tool call and return the result as a dict."""
    runtime_python_session_id = (
        python_session_id
        if _python_session_scope_is_trusted
        else build_trusted_python_session_id(
            user_id=user_id,
            chat_session_id=chat_session_id,
            requested_session_id=python_session_id,
        )
    )

    async def _erase_runtime_session() -> None:
        from app.services.code_executor import (
            delete_user_session_registration_strict,
            mark_session_deleted,
        )

        await asyncio.to_thread(mark_session_deleted, runtime_python_session_id)
        if user_id:
            await asyncio.to_thread(
                delete_user_session_registration_strict,
                user_id,
                runtime_python_session_id,
            )

    if user_id:
        from app.services.account_deletion import account_runtime_is_active

        if not await asyncio.to_thread(account_runtime_is_active, user_id):
            await _erase_runtime_session()
            return {
                "success": False,
                "error": "Account deletion requested; tool execution was cancelled.",
                "error_class": "account_deletion_requested",
                "__tool_status__": "FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
            }
        from app.services.code_executor import register_user_session

        await asyncio.to_thread(
            register_user_session, user_id, runtime_python_session_id
        )
        # Registration closes the delete-vs-first-tool discovery race. If the
        # deletion tombstone appeared after the first check, either the purge
        # now sees this index entry or this second check erases it ourselves.
        if not await asyncio.to_thread(account_runtime_is_active, user_id):
            await _erase_runtime_session()
            return {
                "success": False,
                "error": "Account deletion requested; tool execution was cancelled.",
                "error_class": "account_deletion_requested",
                "__tool_status__": "FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
            }
    # Action 3 (telemetry, 2026-05-08): count every tool invocation so
    # we have real-world usage data for the cosmology-focus surgery
    # decisions (which tools to drop from allowlist after 7 days of
    # production traffic).  Reuses the module-level Prometheus
    # registry; cardinality is bounded by ~87 tool names.
    try:
        from app.observability.metrics import record_counter
        record_counter("tool_invoked_total", 1.0, tool=tool_name)
    except Exception:
        # Telemetry must never break the actual tool call.
        pass

    from app.services.result_provenance import (
        normalize_tool_result,
        prepare_reproducible_tool_input,
    )
    execution_input, execution_seed, seed_source = prepare_reproducible_tool_input(
        tool_name,
        tool_input,
    )
    result = await _execute_tool_inner(
        tool_name, execution_input, api_key, provider_api_keys,
        runtime_python_session_id, user_id, chat_session_id, progress_callback,
    )
    pending_chain_artifacts: dict | None = None
    if tool_name == "run_cosmology_likelihood_chain":
        from app.services.ai_tools_cosmology import pop_pending_chain_artifacts

        result, pending_chain_artifacts = pop_pending_chain_artifacts(result)

    # 2026-05-20: write ai.tool_called to user_events so the telemetry/tool_usage
    # endpoint has data. The consumer (admin_stats.py) was already implemented
    # but the producer was missing. Keep this event deliberately coarse: tool
    # parameters and even their field names are research metadata and must not
    # enter product analytics.
    try:
        from app.services.event_collector import event_collector
        await event_collector.track(
            event_type="ai.tool_called",
            event_data={
                "tool_name": tool_name,
                "success": not (isinstance(result, dict) and result.get("success") is False),
            },
            user_id=user_id,
            session_id=chat_session_id,
        )
    except Exception:
        # Telemetry must never break the actual tool call.
        pass

    # R1/L20: hash the exact invocation parameters and stamp the same effective
    # seed that was injected before execution.  Never derive a receipt-only seed
    # after a stochastic kernel has already run.
    normalize_kwargs: dict[str, Any] = {"tool_input": execution_input}
    if execution_seed is not None:
        normalize_kwargs["random_seed"] = execution_seed
        normalize_kwargs["random_seed_source"] = seed_source
    normalized_result = normalize_tool_result(tool_name, result, **normalize_kwargs)
    if pending_chain_artifacts is not None:
        from app.services.ai_tools_cosmology import finalize_chain_artifacts

        normalized_result = finalize_chain_artifacts(
            normalized_result,
            pending_chain_artifacts,
            user_id=user_id,
        )
    if user_id:
        from app.services.account_deletion import (
            AccountArtifactOwnerInactive,
            account_runtime_is_active,
            dispose_deleted_account_result,
            register_result_artifacts,
            stage_result_artifacts_for_registration,
        )

        try:
            await asyncio.to_thread(
                stage_result_artifacts_for_registration,
                user_id=user_id,
                result=normalized_result,
            )
            await asyncio.to_thread(
                register_result_artifacts,
                user_id=user_id,
                result=normalized_result,
            )
        except AccountArtifactOwnerInactive:
            await asyncio.to_thread(
                dispose_deleted_account_result,
                user_id=user_id,
                result=normalized_result,
            )
            await _erase_runtime_session()
            return {
                "success": False,
                "error": "Account deletion requested; late tool output was erased.",
                "error_class": "account_deletion_requested",
                "__tool_status__": "FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
            }
        except Exception:
            # A result without a durable owner ledger must never be returned.
            # Do not eagerly delete here: database commit may have succeeded
            # even if its acknowledgement was lost.  A true failure leaves the
            # separately committed cleanup row; an acknowledged commit removes
            # it atomically with the trusted DataFile ledger.
            raise
        if not await asyncio.to_thread(account_runtime_is_active, user_id):
            await asyncio.to_thread(
                dispose_deleted_account_result,
                user_id=user_id,
                result=normalized_result,
            )
            await _erase_runtime_session()
            return {
                "success": False,
                "error": "Account deletion requested; late tool output was erased.",
                "error_class": "account_deletion_requested",
                "__tool_status__": "FAILED",
                "publication_ready": False,
                "__do_not_claim__": True,
            }
    return normalized_result


async def _execute_tool_inner(
    tool_name: str,
    tool_input: dict,
    api_key: str = "",
    provider_api_keys: dict[str, str] | None = None,
    python_session_id: str = "default",
    user_id: str | None = None,
    chat_session_id: str | None = None,
    progress_callback: Callable[[dict], Awaitable[None]] | None = None,
) -> dict:
    """Inner dispatch — called by execute_tool, result wrapped with provenance."""
    try:
        tool_input, storage_error = await _authorize_tool_storage_inputs(
            tool_name, tool_input, user_id=user_id
        )
        if storage_error is not None:
            return storage_error
        if tool_name == "search_objects":
            return await _exec_search(tool_input, python_session_id)
        elif tool_name == "run_adql":
            return await _exec_adql(tool_input, python_session_id, progress_callback)
        elif tool_name == "query_high_velocity_stars":
            return await _exec_query_high_velocity_stars(tool_input, python_session_id, progress_callback)
        elif tool_name == "run_sdss_sql":
            return await _exec_run_sdss_sql(tool_input, python_session_id)
        elif tool_name == "get_object_info":
            return await _exec_object_info(tool_input)
        elif tool_name == "list_user_tools":
            from app.services.user_tools import list_user_tools, owner_scope

            scope = owner_scope(user_id, chat_session_id)
            return {
                "success": True,
                "__tool_status__": "COMPLETED",
                "tools": list_user_tools(scope),
                "__message_to_model__": (
                    "These are user-created tool macros. Use run_user_tool with "
                    "a listed tool_id if the user's request matches one."
                ),
            }
        elif tool_name == "run_user_tool":
            from app.services.user_tools import execute_user_tool, owner_scope

            scope = owner_scope(user_id, chat_session_id)
            return await execute_user_tool(
                scope=scope,
                tool_id=str(tool_input.get("tool_id") or ""),
                arguments=tool_input.get("arguments") if isinstance(tool_input.get("arguments"), dict) else {},
                user_id=user_id,
                chat_session_id=chat_session_id,
                python_session_id=python_session_id,
            )
        elif tool_name == "analyze_spectrum":
            return await _exec_analyze(tool_input, api_key, provider_api_keys)
        elif tool_name == "generate_pipeline":
            return _exec_pipeline(tool_input)
        elif tool_name == "search_literature":
            return await _exec_literature(tool_input)
        elif tool_name == "classify_literature_relevance":
            # Stage 6 P0c-C (2026-05-19): hard barrier — LLM must call this to classify
            # papers; otherwise claim_validator.unclassified_literature_violations blocks the reply.
            return await _exec_classify_literature_relevance(tool_input, python_session_id)
        elif tool_name == "extract_literature_tables":
            return await _exec_extract_literature_tables(
                tool_input, python_session_id,
                user_id=user_id, chat_session_id=chat_session_id,
            )
        elif tool_name == "prepare_spectral_measurements":
            return _exec_prepare_spectral_measurements(tool_input, python_session_id)
        elif tool_name == "fit_line_lfr":
            # Stage 6.3 (2026-05-20 sink): fit_line_lfr now accepts an optional arxiv_id;
            # internally calls the LLM extractor to pull measurements + ±1% cell verification
            # + write cache, then proceeds with the original fitting flow.
            return await _exec_fit_line_lfr_async(tool_input, python_session_id, api_key)
        elif tool_name == "astro_statistics_toolbox":
            return _exec_astro_statistics_toolbox(tool_input)
        elif tool_name == "verify_scalar_derivation":
            return await _exec_verify_scalar_derivation(tool_input)
        elif tool_name == "demagnify_sample":
            return _exec_demagnify_sample(tool_input, python_session_id)
        elif tool_name == "compare_luminosity_distances":
            return _exec_compare_luminosity_distances(tool_input, python_session_id)
        elif tool_name == "export_sample_table":
            return _exec_export_sample_table(tool_input, python_session_id)
        elif tool_name == "run_python":
            return await _exec_run_python(tool_input, python_session_id)
        elif tool_name == "get_last_search_results":
            return _exec_get_cached_results(tool_input, python_session_id)
        elif tool_name == "validate_analysis":
            return await _exec_validate_analysis(tool_input, chat_session_id or python_session_id, user_id=user_id)
        elif tool_name == "generate_paper_draft":
            return await _exec_generate_paper_draft(tool_input, chat_session_id or python_session_id, user_id=user_id)
        elif tool_name == "run_pipeline":
            return await _exec_run_pipeline(tool_input, owner_scope=user_id)
        elif tool_name == "generate_proposal":
            return await _exec_generate_proposal(tool_input)
        elif tool_name == "query_transients":
            return await _exec_query_transients(tool_input)
        elif tool_name == "read_arxiv_paper":
            return await _exec_read_paper(tool_input)
        elif tool_name == "research_workflow":
            return await _exec_research_workflow(tool_input)
        elif tool_name == "estimate_photo_z":
            return await _exec_estimate_photo_z(tool_input)
        elif tool_name == "fit_isochrone":
            return await _exec_fit_isochrone(tool_input, python_session_id)
        elif tool_name == "get_async_job_status":
            return _exec_get_async_job_status(tool_input, user_id=user_id)
        # ── H1 split (2026-05-26): cosmology centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep this inventory in sync with COSMOLOGY_TOOL_NAMES:
        # "fit_cosmology_mcmc", "run_cobaya_cosmology",
        # "get_cosmology_run_status", "list_cosmology_datasets",
        # "load_cosmology_data_product", "build_cosmology_likelihood",
        # "run_cosmology_likelihood_chain", "run_cmb_rotation_likelihood",
        # "run_nested_sampler", "evaluate_chain_diagnostics",
        # "build_cosmology_robustness_matrix", "run_cosmology_robustness_matrix",
        # "run_dark_energy_evidence_matrix",
        # "assess_bao_bin_anomaly", "audit_published_constraint",
        # "compute_theory_cmb_spectrum".
        elif tool_name in _COSMOLOGY_TOOL_NAMES:
            from app.services.ai_tools_cosmology import dispatch_cosmology
            return await dispatch_cosmology(
                tool_name,
                tool_input,
                python_session_id,
                user_id=user_id,
                chat_session_id=chat_session_id,
            )
        # ── H1 split (2026-05-26): research-core 5-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with RESEARCH_TOOL_NAMES:
        # "plan_research_program", "run_research_matrix",
        # "build_evidence_graph", "verify_research_facts",
        # "export_research_report".
        elif tool_name in _RESEARCH_TOOL_NAMES:
            from app.services.ai_tools_research import dispatch_research
            return await dispatch_research(tool_name, tool_input)
        # Stable Formal Registry tools. Candidate Catalog entries are never
        # included in this dispatcher or in the model-visible result.
        # "discover_registered_workflows", "start_registered_workflow".
        elif tool_name in _REGISTERED_WORKFLOW_TOOL_NAMES:
            from app.services.ai_tools_registered_workflows import (
                dispatch_registered_workflow_tool,
            )

            return await dispatch_registered_workflow_tool(
                tool_name,
                tool_input,
                user_id=user_id,
            )
        # ── H1 split (2026-05-26): paper-mining 7-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with PAPER_MINING_TOOL_NAMES:
        # "mine_paper_tools", "run_paper_tool_mining_batch",
        # "build_tool_ontology", "build_tool_gap_matrix",
        # "rank_tool_implementation_queue", "build_paper_mining_candidate_pool",
        # "run_paper_tool_mining_loop".
        elif tool_name in _PAPER_MINING_TOOL_NAMES:
            from app.services.ai_tools_paper_mining import dispatch_paper_mining
            return await dispatch_paper_mining(tool_name, tool_input)
        # ── H1 split (2026-05-26): dossier 3-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with DOSSIER_TOOL_NAMES:
        # "get_object_dossier", "get_followup_recommendation",
        # "analyze_cross_wavelength".
        elif tool_name in _DOSSIER_TOOL_NAMES:
            from app.services.ai_tools_dossier import dispatch_dossier
            return await dispatch_dossier(tool_name, tool_input)
        elif tool_name == "crossmatch_catalogs":
            return await _exec_crossmatch_catalogs(tool_input, python_session_id)
        elif tool_name == "search_lightcurve":
            return await _exec_search_lightcurve(tool_input)
        # ── H1 split (2026-05-26): imaging 4-tool centralized dispatch ──
        # Deployment-readiness introspection scans this function body for
        # quoted tool names. Keep in sync with IMAGING_TOOL_NAMES:
        # "reduce_ccd_image", "solve_astrometry",
        # "extract_photometry", "extract_sources".
        elif tool_name in _IMAGING_TOOL_NAMES:
            from app.services.ai_tools_imaging import dispatch_imaging
            return await dispatch_imaging(tool_name, tool_input)
        elif tool_name == "classify_transient":
            return await _exec_classify_transient(tool_input)
        elif tool_name == "analyze_spectrum_pro":
            return await _exec_analyze_spectrum_pro(tool_input)
        elif tool_name == "sensitivity_analysis":
            return await _exec_sensitivity_analysis(tool_input, python_session_id)
        elif tool_name == "query_vo_service":
            from app.services.vo_services import query_sia, query_ssa, federated_tap, discover_services
            protocol = tool_input.get("protocol", "tap")
            if protocol == "sia":
                url = tool_input.get("service_url", "https://irsa.ipac.caltech.edu/SIA")
                return query_sia(url, tool_input.get("ra", 0), tool_input.get("dec", 0), tool_input.get("radius", 0.1))
            elif protocol == "ssa":
                url = tool_input.get("service_url", "https://archive.stsci.edu/ssap/search")
                return query_ssa(url, tool_input.get("ra", 0), tool_input.get("dec", 0), tool_input.get("radius", 0.01))
            elif protocol == "tap":
                return federated_tap(tool_input.get("adql", ""), tool_input.get("services"))
            elif protocol == "discover":
                return discover_services(tool_input.get("keyword", ""), tool_input.get("service_type", "tap"))
            return {"error": f"Unknown protocol: {protocol}"}
        elif tool_name == "process_image":
            from app.services import image_processing_pro as ipp
            op = tool_input.get("operation")
            path = tool_input.get("fits_path", "")
            p = tool_input.get("params", {})
            if op == "reproject":
                return ipp.reproject_image(path, **p)
            elif op == "mosaic":
                return ipp.mosaic_images(tool_input.get("fits_paths", [path]), **p)
            elif op == "psf_match":
                return ipp.psf_match(path, **p)
            elif op == "deblend":
                return ipp.deblend_sources(path, **p)
            elif op == "cutout":
                return ipp.cutout_service(path, p.get("ra", 0), p.get("dec", 0), p.get("size_arcsec", 60))
            return {"error": f"Unknown operation: {op}"}
        # ── Time-Domain Tools ──
        elif tool_name == "gp_detrend_lightcurve":
            from app.services.time_domain_pro import gp_detrend
            return gp_detrend(
                tool_input["time"], tool_input["flux"],
                tool_input.get("flux_err"), tool_input.get("kernel", "matern32"),
            )
        elif tool_name == "fit_transit_model":
            from app.services.time_domain_pro import fit_transit
            return fit_transit(
                tool_input["time"], tool_input["flux"],
                tool_input.get("flux_err"), tool_input["period"],
                tool_input.get("t0"), tool_input.get("rp_rs", 0.1),
            )
        elif tool_name == "detect_stellar_flares":
            from app.services.time_domain_pro import detect_flares
            return detect_flares(
                tool_input["time"], tool_input["flux"],
                tool_input.get("flux_err"), tool_input.get("nsigma", 3.0),
            )
        elif tool_name == "transit_search_bls":
            # Long-running: full-sector BLS sweeps over 0.5–20 day periods on
            # a 50k-cadence light curve routinely run 60-300s. The 45s default
            # tool deadline kills them mid-sweep, so for large inputs we
            # off-load to the Celery worker via the async-tool runtime.
            # in_async_worker() suppresses re-submission when we're already
            # inside the worker draining its own queue.
            from app.services.async_tool_runtime import in_async_worker, submit_async_job

            _time_len = len(tool_input.get("time") or [])
            _period_min = float(tool_input.get("period_min", 0.5))
            _period_max = float(tool_input.get("period_max", 20.0))
            _async_threshold = _time_len >= 50_000 or (_period_max - _period_min) > 50
            _force_background = bool(tool_input.get("background", False))
            if not in_async_worker() and (_async_threshold or _force_background):
                return submit_async_job(
                    "transit_search_bls",
                    tool_input,
                    user_id=user_id,
                    session_id=chat_session_id,
                )

            from app.services.time_domain_pro import transit_search_bls as _bls
            return _bls(
                tool_input["time"], tool_input["flux"],
                period_range=(_period_min, _period_max),
            )
        # ── Team/Workspace Tools ──
        elif tool_name == "share_with_team":
            return {
                "action": "share",
                "resource_type": tool_input["resource_type"],
                "resource_id": tool_input["resource_id"],
                "target_email": tool_input["email"],
                "permission": tool_input.get("permission", "view"),
                "user_id": user_id,
                "note": "Use the Team page to complete this sharing action.",
            }
        elif tool_name == "invite_team_member":
            return {
                "action": "invite",
                "email": tool_input["email"],
                "role": tool_input.get("role", "viewer"),
                "user_id": user_id,
                "note": "Use the Team page to send the invitation.",
            }
        # ── FITS/Export/Provenance Tools ──
        elif tool_name == "read_fits_header":
            from astropy.io import fits as pyfits
            from app.storage import download_fits
            import io
            data = download_fits(tool_input["fits_path"])
            with pyfits.open(io.BytesIO(data)) as hdul:
                hdu_idx = tool_input.get("hdu", 0)
                info: dict[str, Any] = {"n_hdus": len(hdul), "hdus": []}
                for i, h in enumerate(hdul):
                    hdu_info: dict[str, Any] = {"index": i, "name": h.name, "type": type(h).__name__}
                    if hasattr(h, "columns") and h.columns:
                        hdu_info["columns"] = [c.name for c in h.columns]
                    if h.data is not None:
                        hdu_info["shape"] = list(h.data.shape)
                    if i == hdu_idx:
                        hdu_info["header"] = {k: str(v) for k, v in h.header.items() if k}
                    info["hdus"].append(hdu_info)
                return info
        elif tool_name == "export_results":
            return {
                "export_url": f"/api/export/run/{tool_input['run_id']}/{tool_input['format']}",
                "note": "Download from this URL",
            }
        elif tool_name == "get_provenance":
            from app.services.provenance import get_lineage, get_reproducibility_package, generate_doi_metadata
            action = tool_input.get("action", "lineage")
            eid = tool_input["entity_id"]
            # Provenance parameters and environment manifests are private
            # research data. Never perform the legacy unscoped lookup.
            if not user_id:
                return {
                    "error": "Provenance record not found",
                    "error_class": "not_found",
                }
            lineage = get_lineage(eid, owner_id=str(user_id))
            if not lineage.get("nodes"):
                # Uniform response prevents entity-id enumeration across
                # accounts (and does not reveal whether another owner has it).
                return {
                    "error": "Provenance record not found",
                    "error_class": "not_found",
                }
            if action == "lineage":
                return lineage
            elif action == "reproduce":
                return get_reproducibility_package(eid, owner_id=str(user_id))
            elif action == "doi_metadata":
                return generate_doi_metadata(eid, owner_id=str(user_id))
            return {"error": f"Unknown provenance action: {action}"}
        # ── Photo-Z Pro + Batch Tools ──
        elif tool_name == "estimate_photo_z_pro":
            from app.services.photo_z_pro import fit_template_enhanced
            return fit_template_enhanced(
                tool_input["magnitudes"],
                tool_input.get("mag_errors"),
                z_range=(0, tool_input.get("z_max", 6.0)),
                prior=tool_input.get("prior", "flat"),
            )
        elif tool_name == "batch_object_search":
            targets = tool_input.get("targets", [])
            sources = tool_input.get("sources", ["simbad"])
            radius = tool_input.get("radius", 0.1)
            aggregated = []
            for target in targets:
                res = await _exec_search({"query": target, "sources": sources, "radius": radius}, python_session_id)
                aggregated.append({"target": target, "results": res.get("results", []), "total": res.get("total", 0)})
            return {"searches": aggregated, "total_targets": len(targets)}
        elif tool_name == "workspace_export":
            import csv as _csv
            import io as _io
            rows = tool_input.get("data", [])
            fmt = tool_input.get("format", "csv")
            columns = tool_input.get("columns")
            if fmt == "csv":
                buf = _io.StringIO()
                if rows:
                    cols = columns or (list(rows[0].keys()) if isinstance(rows[0], dict) else None)
                    if cols:
                        writer = _csv.DictWriter(buf, fieldnames=cols)
                        writer.writeheader()
                        for r in rows:
                            writer.writerow({c: r.get(c, "") if isinstance(r, dict) else "" for c in cols})
                    else:
                        writer_list = _csv.writer(buf)
                        for r in rows:
                            writer_list.writerow(r if isinstance(r, list) else [r])
                return {"format": "csv", "content": buf.getvalue(), "row_count": len(rows)}
            elif fmt == "votable":
                lines = ['<?xml version="1.0" encoding="UTF-8"?>',
                         '<VOTABLE version="1.4" xmlns="http://www.ivoa.net/xml/VOTable/v1.4">',
                         '<RESOURCE><TABLE>']
                if rows and isinstance(rows[0], dict):
                    cols = columns or list(rows[0].keys())
                    for c in cols:
                        lines.append(f'<FIELD name="{c}" datatype="char" arraysize="*"/>')
                    lines.append("<DATA><TABLEDATA>")
                    for r in rows:
                        cells = "".join(f"<TD>{r.get(c, '')}</TD>" for c in cols)
                        lines.append(f"<TR>{cells}</TR>")
                    lines.append("</TABLEDATA></DATA>")
                lines.extend(["</TABLE></RESOURCE>", "</VOTABLE>"])
                return {"format": "votable", "content": "\n".join(lines), "row_count": len(rows)}
            return {"error": f"Unsupported format: {fmt}"}
        elif tool_name == "classify_transient_spectrum":
            from app.services.transient_classifier import classify_with_spectroscopy, enrich_with_host_galaxy
            result = classify_with_spectroscopy(tool_input.get("wavelength", []), tool_input.get("flux", []), tool_input.get("redshift", 0))
            if tool_input.get("ra") is not None and tool_input.get("dec") is not None:
                result["host_galaxy"] = enrich_with_host_galaxy(tool_input["ra"], tool_input["dec"])
            return result
        elif tool_name == "literature_review":
            from app.services.literature_engine import synthesize_bibliography, build_citation_network
            result = synthesize_bibliography(tool_input.get("topic", ""), tool_input.get("findings", ""), tool_input.get("max_papers", 10))
            if tool_input.get("build_network") and tool_input.get("seed_bibcodes"):
                result["citation_network"] = build_citation_network(tool_input["seed_bibcodes"], depth=1)
            return result
        elif tool_name == "radio_analysis":
            from app.connectors.radio import RadioAnalysis
            op = tool_input.get("operation")
            if op == "spectral_index":
                return RadioAnalysis.spectral_index(tool_input.get("flux_1_mJy", 0), tool_input.get("freq_1_MHz", 0), tool_input.get("flux_2_mJy", 0), tool_input.get("freq_2_MHz", 0))
            elif op == "luminosity":
                return RadioAnalysis.radio_luminosity(tool_input.get("flux_mJy", 0), tool_input.get("redshift", 0), tool_input.get("freq_MHz", 1400))
            elif op == "crossmatch":
                return RadioAnalysis.multi_frequency_crossmatch(tool_input.get("ra", 0), tool_input.get("dec", 0))
            return {"error": f"Unknown operation: {op}"}
        elif tool_name == "full_research_report":
            session_id = tool_input.get("session_id", "")
            title = tool_input.get("title", "Standard Astro Analysis Report")
            journal = tool_input.get("journal", "aastex")
            return {
                "publication_package": {
                    "paper_draft_url": f"/api/paper/generate?session_id={session_id}&format={journal}",
                    "notebook_url": f"/api/integration/jupyter/from-chat?session_id={session_id}",
                    "bibtex_url": f"/api/export/bibtex/{session_id}",
                },
                "title": title,
                "journal_format": journal,
                "instructions": "Download the paper draft, notebook, and BibTeX from the URLs above. The paper includes all analysis results, figures, and citations from this session.",
            }
        # ── P1/P2 workflow tools (knowledge base expansion) ──
        elif tool_name == "compute_galaxy_sfr":
            return _exec_compute_galaxy_sfr(tool_input)
        elif tool_name == "fit_rv_orbit":
            return await _exec_fit_rv_orbit(tool_input)
        elif tool_name == "fit_sersic_morphology":
            return await _exec_fit_sersic(tool_input)
        elif tool_name == "x_ray_spectral_fit":
            return await _exec_x_ray_spectral_fit(tool_input)
        elif tool_name == "pulsar_derived_quantities":
            return _exec_pulsar_derived(tool_input)
        elif tool_name == "describe_tap_table":
            return await _exec_describe_tap_table(tool_input)
        elif tool_name == "query_gaia_cluster":
            return await _exec_query_gaia_cluster(tool_input, python_session_id)
        elif tool_name == "get_extinction":
            return await _exec_get_extinction(tool_input)
        else:
            # R6-NEW-2: return a concrete error_class + available tool list for unknown
            # tools so the AI can self-correct on the next turn (no more hallucinated
            # tool names). TOOLS is the module-level list; each entry is
            # {"name": ..., "description": ...}.
            try:
                available = sorted(t.get("name", "") for t in TOOLS if t.get("name"))
            except Exception:
                available = []
            return {
                "error": (
                    f"Unknown tool: {tool_name!r}. "
                    f"Available tools: {', '.join(available) if available else 'n/a'}"
                ),
                "error_class": "unknown_tool",
                "attempted_tool": tool_name,
                "available_tools": available,
            }
    except Exception as e:
        logger.warning("Tool %s failed: %s", tool_name, e)
        return {"error": str(e)}
